"""Checked pass outputs skip only redundant per-pass header revalidation.

A typed pass publishes its output as one checked run only when it validated
every input ID against the fragments' own semantics; replacements are
validated when appended. The next pass then skips its per-record header walk.
Foreign semantics and ordinary spans keep full validation, and the CFG stage
still revalidates the complete module before publication.
"""
import pytest

from pcc.backend import self_backend_aarch64_semantics as sem
from pcc.backend import self_backend_aarch64_typed_cfg as cfg
from pcc.backend import self_backend_aarch64_typed_peepholes as typed
from pcc.backend.arm64_encode import EncodeError
from pcc.backend.self_backend_value_arena import CompilerIntArena
from tests.python.test_aarch64_typed_peepholes import CASES, _fixture, _ids


def _owned_fixture(items):
    fragments, semantics, span, _lines = _fixture(items)
    # Make the provenance sidecar the fragments' own, as in the native sink.
    fragments.semantics = semantics
    return fragments, semantics, span


def _run_input(fragments, source, checked):
    ids = CompilerIntArena()
    try:
        fragments.copy_record_ids(source, ids)
        span = fragments.new_fragment()
        fragments.spans.append_ids(span, ids, checked)
        return span
    finally:
        ids.close()


@pytest.mark.parametrize("mode,case", list(enumerate(CASES, 1)), ids=[case[0] for case in CASES])
def test_checked_input_skips_exactly_the_init_header_walk(monkeypatch, mode, case):
    original = sem.AArch64InstructionSemantics.require_header
    outcomes = []
    for checked in (False, True):
        fragments, semantics, source = _owned_fixture(case[1])
        try:
            span = _run_input(fragments, source, checked)
            instructions = [rid for rid in _ids(fragments, span)
                            if fragments.records.get4_unchecked(rid).second >= 0]
            calls = []
            def counted(self, record_id):
                calls.append(record_id)
                return original(self, record_id)
            with monkeypatch.context() as patch:
                patch.setattr(sem.AArch64InstructionSemantics, "require_header", counted)
                result = typed._run(fragments, semantics, span, mode)
            outcomes.append((_ids(fragments, result), len(calls), instructions))
            semantics.require_complete(fragments.records, len(fragments.symbol_names))
        finally:
            semantics.close()
            fragments.close()
    (plain_ids, plain_calls, instructions), (checked_ids, checked_calls, _) = outcomes
    assert plain_ids == checked_ids
    assert instructions and plain_calls - checked_calls == len(instructions)


def test_changed_output_is_checked_only_for_the_fragments_own_semantics():
    for owned in (False, True):
        fragments, semantics, source, _lines = _fixture(CASES[0][1])
        if owned:
            fragments.semantics = semantics
        try:
            result = typed.forward_adjacent_stack_store_load(fragments, semantics, source)
            assert _ids(fragments, result) != _ids(fragments, source)
            assert fragments.spans.is_checked_run(result) is owned
        finally:
            semantics.close()
            fragments.close()


def test_checked_input_with_foreign_semantics_is_revalidated():
    fragments, semantics, source = _owned_fixture(CASES[0][1])
    foreign = sem.AArch64InstructionSemantics()
    try:
        span = _run_input(fragments, source, True)
        foreign.headers.extend_from(semantics.headers, 0, len(semantics.headers))
        foreign.tokens.extend_from(semantics.tokens, 0, len(semantics.tokens))
        first = next(rid for rid in _ids(fragments, span)
                     if fragments.records.get4_unchecked(rid).second >= 0)
        foreign.headers.set(first * 4, 0)
        with pytest.raises(EncodeError, match="unproven"):
            typed._run(fragments, foreign, span, 14)
    finally:
        foreign.close()
        semantics.close()
        fragments.close()


def test_cfg_stage_revalidates_the_module_after_checked_peepholes(monkeypatch):
    fragments, semantics, source = _owned_fixture(CASES[0][1] + ["L_end:", ("ret", [])])
    original = sem.AArch64InstructionSemantics.require_complete
    calls = []
    def counted(self, records, symbol_count):
        calls.append(len(records))
        return original(self, records, symbol_count)
    try:
        before = _ids(fragments, source)
        result = typed.run_aarch64_typed_peepholes(fragments, semantics, source)
        assert _ids(fragments, result) != before
        assert fragments.spans.is_checked_run(result)
        monkeypatch.setattr(sem.AArch64InstructionSemantics, "require_complete", counted)
        cfg.run_aarch64_typed_cfg_passes(fragments, semantics, result, set(), [])
        assert calls and all(count == len(fragments.records) for count in calls[:1])
    finally:
        semantics.close()
        fragments.close()
