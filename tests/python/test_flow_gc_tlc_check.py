"""Fail-closed result parsing; actual Java checks run through the CLI."""

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/research"))
try:
    import flow_gc_tlc_check as checker
finally:
    sys.path.pop(0)


def test_sany_requires_semantic_completion_even_on_zero_exit():
    with pytest.raises(AssertionError):
        checker.check_sany_result(0, "Parsing file FlowGCTransfer.tla")
    with pytest.raises(AssertionError):
        checker.check_sany_result(0, "Semantic processing of module FlowGCTransfer\nSemantic errors:")
    checker.check_sany_result(0, "Semantic processing of module FlowGCTransfer\n")


def test_positive_summary_requires_empty_queue_and_explicit_success():
    output = ("Model checking completed. No error has been found.\n"
              "3616 states generated, 1358 distinct states found, 0 states left on queue.\n"
              "The depth of the complete state graph search is 11.\n")
    result = checker.summarize_tlc(0, output, negative=False)
    assert result["complete_exploration"] and result["distinct_states"] == 1358
    with pytest.raises(AssertionError):
        checker.summarize_tlc(0, output.replace("0 states left", "1 states left"), negative=False)
    with pytest.raises(AssertionError):
        checker.summarize_tlc(12, output, negative=False)


def test_negative_requires_expected_invariant_exit_and_real_witness():
    output = ("Error: Invariant NoLostLiveObjects is violated.\n"
              "State 1: <Initial predicate>\nState 6: <Sweep line 86>\nmissed = {o1}\n"
              "1689 states generated, 834 distinct states found, 344 states left on queue.\n"
              "The depth of the complete state graph search is 6.\n")
    result = checker.summarize_tlc(12, output, negative=True)
    assert not result["complete_exploration"] and result["violated_invariants"] == ["NoLostLiveObjects"]
    for bad in (output.replace("NoLostLiveObjects", "TypeOK"), output.replace("missed = {o1}", "")):
        with pytest.raises(AssertionError):
            checker.summarize_tlc(12, bad, negative=True)


CONFIG = """\\* Fixed model boundary, with an ordinary line comment.
CONSTANTS
    MaxSources = 3
    ObjectIds = {o1, o2}
    UseBarrier = TRUE
SPECIFICATION Spec
INVARIANTS
    TypeOK OwnershipConserved PrefixVisible AllOwnedVisible ScanOrder
    PublishedComplete NoLostLiveObjects
"""


def test_config_boundary_is_read_and_validated_before_reporting():
    negative = CONFIG.replace("UseBarrier = TRUE", "UseBarrier = FALSE")
    assert checker.checked_config_bounds(CONFIG, negative) == {
        "max_sources": 3, "object_ids": ["o1", "o2"],
    }
    with pytest.raises(AssertionError, match="differ only"):
        checker.checked_config_bounds(CONFIG, CONFIG)


@pytest.mark.parametrize("before,after", [
    ("MaxSources = 3", "MaxSources = 4"),
    ("ObjectIds = {o1, o2}", "ObjectIds = {new1, new2}"),
    ("MaxSources = 3", "MaxSources = 3 MaxSources = 4"),
    ("INVARIANTS", "INVARIANT"),
])
def test_config_changes_cannot_silently_reuse_old_boundary(before, after):
    changed = CONFIG.replace(before, after)
    with pytest.raises(AssertionError):
        checker.checked_config_bounds(changed, changed.replace("UseBarrier = TRUE", "UseBarrier = FALSE"))
