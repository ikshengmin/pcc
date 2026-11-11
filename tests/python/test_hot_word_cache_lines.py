"""Hot shared words that every thread writes or polls sit on lines of their own.

The linker packs a module's globals in definition order, and where a module's
data begins depends on every module before it: a change to unrelated code
moved these words by a non-multiple of the 128-byte cache line and changed
which neighbours shared a line with them. Four-worker throughput then moved
by several percent with no change to any path that ran. Padding on both
sides makes the neighbours of each group fixed.

The source test pins the definition order; the linked test reads addresses
from a real executable, as test_allocator_lock_line.py does for the allocator
lock.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pcc.backend.macho_spec import parse_object
from tests.owned_c_execution import compile_owned_c_with_runtime


ROOT = Path(__file__).resolve().parents[2]
CACHE_LINE = 128

# module, padding prefix, the words between the two padding arrays.
GROUPS = [
    (
        "pcc/runtime/py/freestanding_gc_state.py",
        "pcc_gc_tracked_count_isolation",
        ["py_gc_tracked_count", "pcc_gc_tracked_after_collect"],
    ),
    (
        "pcc/runtime/py/freestanding_gc_state.py",
        "pcc_gc_root_registry_isolation",
        [
            "pcc_gc_continuation_root_head",
            "pcc_gc_scheduler_root_head",
            "pcc_gc_root_registry_revision",
        ],
    ),
    (
        "pcc/runtime/py/freestanding_thread_kernel_pthread.py",
        "pcc_thread_stop_flags_isolation",
        ["pcc_thread_stop_requested", "pcc_thread_world_stop_py"],
    ),
]
IDS = [group[1] for group in GROUPS]


def _global_definitions(path):
    """(symbol, word count) of every module-level global, in source order.

    Thread-local definitions are left out: they live in another section.
    """
    found = []
    for node in ast.parse((ROOT / path).read_text()).body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Name):
            continue
        name = call.func.id
        if not name.startswith("define_global"):
            continue
        symbol = call.args[0].value
        words = len(call.args) - 1 if name.endswith("_array") else 1
        found.append((symbol, words, name))
    return found


@pytest.mark.parametrize("path, prefix, words", GROUPS, ids=IDS)
def test_group_is_defined_between_two_full_lines_of_padding(path, prefix, words):
    definitions = _global_definitions(path)
    names = [symbol for symbol, _, _ in definitions]
    first = names.index(prefix + "_before")
    last = names.index(prefix + "_after")
    assert names[first + 1:last] == words
    for index in (first, last):
        symbol, count, kind = definitions[index]
        assert kind == "define_global_i64_array"
        # A line of padding on each side, whatever the group's own offset.
        assert count * 8 == CACHE_LINE, symbol


@pytest.mark.parametrize("path, prefix, words", GROUPS, ids=IDS)
def test_padding_keeps_every_other_module_on_its_line_phase(path, prefix, words):
    # Two arrays of one line each: the data after the group moves by a
    # multiple of the line size, so no other word changes neighbours.
    definitions = dict(
        (symbol, count) for symbol, count, _ in _global_definitions(path)
    )
    added = (definitions[prefix + "_before"] + definitions[prefix + "_after"]) * 8
    assert added % CACHE_LINE == 0


def test_linked_groups_share_a_line_with_nothing_else(
    tmp_path, threaded_pcc_runtime_archive,
):
    binary = tmp_path / "hot_words"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/allocator_lock_contention.c", binary,
        runtime_archive=Path(threaded_pcc_runtime_archive),
    )
    rows = parse_object(binary.read_bytes()).symbols()
    symbols = sorted(
        (row["n_value"], row["name"]) for row in rows
        if (row["n_type"] & 0xE0) == 0 and (row["n_type"] & 0x0E) == 0x0E
    )
    addresses = {name: value for value, name in symbols}
    for _, prefix, words in GROUPS:
        before = addresses["_" + prefix + "_before"]
        after = addresses["_" + prefix + "_after"]
        members = {"_" + word for word in words}
        for word in members:
            address = addresses[word]
            line = address - address % CACHE_LINE
            # The whole line lies inside the padded span.
            assert before <= line and line + CACHE_LINE <= after + CACHE_LINE, word
        neighbours = [
            name for value, name in symbols
            if before < value < after + CACHE_LINE
            and name not in members and name != "_" + prefix + "_after"
        ]
        assert neighbours == [], prefix
