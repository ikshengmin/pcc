"""Frontend pipeline IR for the actual freestanding release source.

This is not final runtime-archive IR or machine-code qualification.
"""
from __future__ import annotations

import re

import pytest

from tests.python.test_foreign_address_leases import RUNTIME


SSA = r"%[A-Za-z0-9_.$-]+"


def _release_cfg(text):
    match = re.search(
        r"^define [^\n]*@pcc_gc_foreign_lease_release\([^\n]*\{\n(.*?)^\}",
        text, re.MULTILINE | re.DOTALL,
    )
    assert match is not None
    blocks = {}
    label = None
    for raw in match.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if re.fullmatch(r"[A-Za-z0-9_.$-]+:", line):
            label = line[:-1]
            blocks[label] = []
        else:
            assert label is not None
            blocks[label].append(line)
    successors = {name: re.findall(r"label %([A-Za-z0-9_.$-]+)", lines[-1])
                  for name, lines in blocks.items()}
    predecessors = {name: set() for name in blocks}
    for name, targets in successors.items():
        for target in targets:
            predecessors[target].add(name)
    entry = next(iter(blocks))
    dominators = {name: ({name} if name == entry else set(blocks)) for name in blocks}
    changed = True
    while changed:
        changed = False
        for name in blocks:
            if name == entry:
                continue
            assert predecessors[name], ("unreachable emitted block", name)
            new = {name} | set.intersection(*(dominators[p] for p in predecessors[name]))
            if new != dominators[name]:
                dominators[name] = new
                changed = True
    return blocks, dominators


def _matching(blocks, pattern):
    return [(name, index, match) for name, lines in blocks.items()
            for index, line in enumerate(lines)
            if (match := re.search(pattern, line)) is not None]


def _one(blocks, pattern):
    found = _matching(blocks, pattern)
    assert len(found) == 1, (pattern, found)
    return found[0]


def _gep(blocks, base, offset):
    return _one(blocks, rf"^({SSA}) = getelementptr(?: inbounds)? i8, ptr {re.escape(base)}, i64 {offset}$")


def _zero_successor(blocks, value):
    block, position, comparison = _one(blocks, rf"^({SSA}) = icmp (eq|ne) i64 {re.escape(value)}, 0$")
    condition, predicate = comparison.groups()
    branch = re.fullmatch(
        rf"br i1 ({SSA}), label %([A-Za-z0-9_.$-]+), label %([A-Za-z0-9_.$-]+)",
        blocks[block][-1],
    )
    assert branch is not None
    branch_condition, yes, no = branch.groups()
    assert yes != no
    zero_is_true = predicate == "eq"
    if branch_condition != condition:
        # Frontend ptr_is_null comparisons normalize i1 through i64:
        # icmp -> zext -> eq/ne zero. Accept only this guard-block SSA chain.
        wide_block, wide_position, wide = _one(blocks,
            rf"^({SSA}) = zext i1 {re.escape(condition)} to i64$")
        retest_block, retest_position, retest = _one(blocks,
            rf"^({SSA}) = icmp (eq|ne) i64 {re.escape(wide.group(1))}, 0$")
        assert block == wide_block == retest_block
        assert position < wide_position < retest_position < len(blocks[block]) - 1
        assert branch_condition == retest.group(1)
        # zext preserves false=0/true=1; an eq-zero re-test inverts it.
        if retest.group(2) == "eq":
            zero_is_true = not zero_is_true
    return yes if zero_is_true else no


@pytest.mark.parametrize("predicate,retest,expected", [
    ("eq", None, "yes"), ("ne", None, "no"),
    ("eq", "ne", "yes"), ("eq", "eq", "no"),
    ("ne", "ne", "no"), ("ne", "eq", "yes"),
])
def test_zero_successor_tracks_boolean_polarity(predicate, retest, expected):
    lines = [f"%test = icmp {predicate} i64 %value, 0"]
    condition = "%test"
    if retest is not None:
        lines += ["%wide = zext i1 %test to i64",
                  f"%retest = icmp {retest} i64 %wide, 0"]
        condition = "%retest"
    lines.append(f"br i1 {condition}, label %yes, label %no")
    assert _zero_successor({"guard": lines}, "%value") == expected


@pytest.mark.parametrize("before,after", [
    ("br i1 %retest", "br i1 %other"),
    ("zext i1 %test", "zext i1 %other"),
    ("icmp ne i64 %wide, 0", "icmp ne i64 %unrelated, 0"),
    ("icmp ne i64 %wide, 0", "icmp ne i64 %wide, 1"),
    ("%test = icmp eq i64 %value, 0",
     "%test = icmp eq i64 %value, 0\n%duplicate = icmp eq i64 %value, 0"),
])
def test_zero_successor_rejects_unproved_boolean_chains(before, after):
    text = "\n".join([
        "%test = icmp eq i64 %value, 0",
        "%other = icmp ne i64 %unrelated, 0",
        "%wide = zext i1 %test to i64",
        "%retest = icmp ne i64 %wide, 0",
        "br i1 %retest, label %yes, label %no",
    ])
    with pytest.raises(AssertionError):
        _zero_successor({"guard": text.replace(before, after).splitlines()}, "%value")


def test_frontend_ir_release_header_guard_and_fresh_fallback_lookup(tmp_path):
    # This is the existing freestanding route used by
    # test_static_literal_kernel_compiles_with_exact_freestanding_boundaries.
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.owned_ir_validation import verify_ir_text

    output = tmp_path / "foreign_lease_release.ll"
    compile_python(
        str(RUNTIME / "freestanding_gc_root_operations.py"), str(output),
        emit_llvm_only=True, python_library=True, backend="self",
        target_triple=host_target_triple(),
    )
    text = output.read_text()
    verify_ir_text(text)
    blocks, dominators = _release_cfg(text)
    finds = _matching(blocks,
        rf"^({SSA}) = call ptr (?:\([^\n]*?\) )?@pcc_gc_object_index_find\(ptr ({SSA})\)$")
    assert len(finds) == 2
    resolver = _one(blocks,
        rf"^({SSA}) = call ptr (?:\([^\n]*?\) )?@pcc_gc_resolve_root_slot_(?:unlocked|mode)\(")
    fallback_block, resolve_position, resolve_match = resolver
    resolved = resolve_match.group(1)
    fresh = [item for item in finds if item[2].group(2) == resolved]
    assert len(fresh) == 1
    fresh_block, fresh_position, fresh_match = fresh[0]
    assert fresh_block == fallback_block and fresh_position > resolve_position
    first = next(item for item in finds if item != fresh[0])
    first_block, _, first_match = first
    tentative, obj = first_match.groups()
    new_node = fresh_match.group(1)

    # The initial exact lookup dominates every tentative-node/header access.
    free_block, _, free_address = _gep(blocks, tentative, 32)
    header_block, _, header_address = _gep(blocks, obj, 12)
    assert first_block in dominators[free_block]
    assert first_block in dominators[header_block]
    _, _, cast = _one(blocks, rf"^({SSA}) = ptrtoint ptr {re.escape(tentative)} to i64$")
    cast_value = cast.group(1)
    # A nonnull node branch is the opposite of this zero branch.
    null_block, _, null_comparison = _one(blocks,
        rf"^({SSA}) = icmp (eq|ne) i64 {re.escape(cast_value)}, 0$")
    null_targets = re.findall(r"label %([A-Za-z0-9_.$-]+)", blocks[null_block][-1])
    zero_target = _zero_successor(blocks, cast_value)
    assert zero_target == fallback_block
    nonnull_target = next(target for target in null_targets if target != zero_target)
    assert nonnull_target in dominators[free_block]
    assert nonnull_target in dominators[header_block]
    _, _, free_load = _one(blocks,
        rf"^({SSA}) = load i64, ptr {re.escape(free_address.group(1))}, align [0-9]+$")
    live_target = _zero_successor(blocks, free_load.group(1))
    assert live_target in dominators[header_block]
    _one(blocks, rf"^({SSA}) = load i32, ptr {re.escape(header_address.group(1))}, align [0-9]+$")

    # Only the newly looked-up node may feed the fallback arm of the common
    # tail. This checks actual SSA flow, not just the count of call spellings.
    merges = _matching(blocks, rf"^({SSA}) = phi ptr ")
    node_merges = [(name, index, match) for name, index, match in merges
                   if f"[ {tentative}, %" in blocks[name][index]
                   and f"[ {new_node}, %{fresh_block} ]" in blocks[name][index]]
    assert len(node_merges) == 1
    merged = node_merges[0][2].group(1)
    _gep(blocks, merged, 32)
    count_addresses = _matching(blocks, rf"^({SSA}) = getelementptr(?: inbounds)? i8, ptr ({SSA}), i64 80$")
    assert count_addresses and {item[2].group(2) for item in count_addresses} == {merged}
    lock_block, lock_position, _ = _one(blocks, r"@pcc_py_gc_minor_graph_lock\(\)")
    assert lock_block in dominators[first_block]
    if lock_block == first_block:
        assert lock_position < first[1]
    _one(blocks, r"@pcc_py_gc_minor_graph_unlock\(\)")
    assert not _matching(blocks, r"@(?:py_err_|pcc_gc_config_ensure|py_incref|py_decref)")
