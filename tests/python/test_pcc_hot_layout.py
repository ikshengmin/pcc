"""scripts/pcc_hot_layout.py: which symbols share a cache line with a hot word.

The pure functions run on a synthetic symbol table: a written word that
shares a line with a word every thread reads is a conflict, padding and
folded module variables are not neighbours, and a two-build comparison
names the words whose neighbours changed. The linked case reads a real
executable's symbol table with pcc's own Mach-O parser.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime


ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "pcc_hot_layout", ROOT / "scripts/pcc_hot_layout.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL = _load()
BASE = 0x1000


def _table(*entries):
    """(offset, name) pairs relative to a line-aligned base, sorted."""
    return sorted((BASE + offset, name) for offset, name in entries)


def test_a_written_word_on_a_read_words_line_is_a_conflict():
    symbols = _table(
        (0, "py_gc_enabled"),
        (4, "pcc_gc_list_container_mode"),
        (8, "py_gc_tracked_count"),
        (128, "unrelated"),
    )
    report = TOOL.line_neighbours(symbols)
    assert report["py_gc_tracked_count"]["offset"] == 8
    assert set(report["py_gc_tracked_count"]["neighbours"]) == {
        "py_gc_enabled", "pcc_gc_list_container_mode",
    }
    found = TOOL.conflicts(report)
    assert ("py_gc_tracked_count", "pcc_gc_list_container_mode",
            "every thread reads it") in found
    assert ("py_gc_tracked_count", "py_gc_enabled", "every thread reads it") in found


def test_words_of_one_group_may_share_a_line():
    symbols = _table(
        (0, "pcc_gc_continuation_root_head"),
        (8, "pcc_gc_scheduler_root_head"),
        (16, "pcc_gc_root_registry_revision"),
    )
    assert TOOL.conflicts(TOOL.line_neighbours(symbols)) == []


def test_two_written_groups_on_one_line_conflict():
    symbols = _table((0, "pcc_gc_debt_bytes"), (8, "pcc_gc_live_bytes"))
    found = TOOL.conflicts(TOOL.line_neighbours(symbols))
    assert ("pcc_gc_debt_bytes", "pcc_gc_live_bytes", "another written group") in found
    assert ("pcc_gc_live_bytes", "pcc_gc_debt_bytes", "another written group") in found


def test_padding_and_folded_module_variables_are_not_neighbours():
    symbols = _table(
        (0, "pcc_gc_tracked_count_isolation_before"),
        (128, "py_gc_tracked_count"),
        (136, "pcc_gc_tracked_after_collect"),
        (144, "pcc_gc_tracked_count_isolation_after"),
        (272, ".modvar.kernel._LIMIT_initialized"),
        (280, "pcc_gc_backend_selected"),
    )
    report = TOOL.line_neighbours(symbols)
    assert TOOL.is_inert("pcc_gc_tracked_count_isolation_after")
    assert TOOL.is_inert(".modvar.kernel._LIMIT_initialized")
    assert not TOOL.is_inert("pcc_gc_backend_selected")
    assert TOOL.conflicts(report) == []
    # The padding is still reported as occupying the line.
    assert "pcc_gc_tracked_count_isolation_after" in report["py_gc_tracked_count"]["neighbours"]


def test_a_symbol_that_extends_onto_the_line_is_a_neighbour():
    # An array that starts on the previous line and ends on this one.
    symbols = _table((64, "pcc_gc_tracked_shards"), (200, "py_gc_tracked_count"))
    report = TOOL.line_neighbours(symbols)
    assert report["py_gc_tracked_count"]["neighbours"] == ["pcc_gc_tracked_shards"]


@pytest.mark.parametrize("shift, changed", [(0, False), (128, False), (96, True)])
def test_comparison_reports_only_a_changed_line_phase(shift, changed):
    def build(offset):
        # The data before these words grew by ``offset`` bytes: all three
        # move together, and the pool count sits just past a line boundary.
        return TOOL.line_neighbours(_table(
            (offset + 100, "pcc_gc_mark_active"),
            (offset + 120, "pcc_gc_object_node_free_head"),
            (offset + 136, "pcc_gc_object_node_free_count"),
        ))

    changes = TOOL.neighbour_changes(build(0), build(shift))
    assert bool(changes) is changed
    if changed:
        names = [name for name, _, _ in changes]
        assert "pcc_gc_mark_active" in names


def test_every_hot_word_has_a_role_and_a_group():
    for name, (role, group) in TOOL.HOT_WORDS.items():
        assert role in ("written", "read"), name
        assert group, name


def test_isolated_groups_have_no_conflict_in_a_linked_executable(
    tmp_path, threaded_pcc_runtime_archive,
):
    binary = tmp_path / "hot_layout"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/allocator_lock_contention.c", binary,
        runtime_archive=Path(threaded_pcc_runtime_archive),
    )
    report = TOOL.line_neighbours(TOOL.data_symbols(binary))
    isolated = {
        "tracked-count", "root-registry", "allocator-lock", "graph-lock",
        "foreign-lease", "poll-flags", "hot-config",
    }
    present = {entry["group"] for entry in report.values()}
    assert isolated <= present
    for name, other, reason in TOOL.conflicts(report):
        involved = {report[name]["group"], TOOL.HOT_WORDS[other][1]}
        assert not (involved & isolated), (name, other, reason)
