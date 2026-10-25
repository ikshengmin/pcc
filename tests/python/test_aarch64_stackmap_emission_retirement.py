"""Host-only ownership/equivalence checks; no native execution or builds."""

from __future__ import annotations

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import self_backend_aarch64_darwin as emitter
from pcc.backend import self_backend_precise_stackmaps as maps
from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.native_object import encode_native_object_from_sections
from pcc.backend.precise_stackmap import ARCH_AARCH64, decode_stack_map
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.self_backend_parse import parse_self_backend_module
from tests.python.test_precise_stackmap_abi import (
    _stale_managed_ssa_ir,
    _target_final_stackmap_ir,
)


_EMISSION = (
    "reload_scalars", "entry_routes", "suffix_routes", "terminator_routes",
    "entry_route_spans", "suffix_route_spans", "terminator_route_spans",
)
_METADATA = ("scalars", "spans", "location_scalars", "location_group_spans")
_TARGETS = ("arm64-apple-darwin23.6.0", "aarch64-unknown-linux-gnu")
_ROUTES = (
    ("text", False), ("text", True),
    ("structured-lines", False), ("structured-lines", True),
    ("native-sink", False),
)


def _source(factory, triple):
    return factory(triple) + """

declare i64 @callee(i64)
define i64 @sibling(i64 %value) {
entry:
  %result = call i64 @callee(i64 %value)
  ret i64 %result
}
"""


class _Timing:
    def __init__(self):
        self.events = []

    def start(self):
        self.events.append(("start",))
        return len(self.events)

    def add(self, phase, started):
        self.events.append(("add", phase, started))


def _prepare(source):
    return emitter.prepare_parsed_module_for_target(
        parse_self_backend_module(source),
        aggregate_returned_indirect=emitter._aggregate_returned_indirect,
        aggregate_returned_indirect_indexed=emitter._aggregate_returned_indirect_indexed,
        materialize_legacy_slots=False,
    )


def _emit_prepared(prepared, route, optimize, timing=None):
    sections, undefined = [], []
    kwargs = {}
    if route != "text":
        kwargs["structured_sections"] = sections
    if route == "native-sink":
        kwargs.update(
            structured_counts=[], native_undefined=undefined,
            native_fallback_lines=[],
        )
    lines = emitter._emit_prepared_aarch64_darwin_lines(
        prepared, optimize=optimize, close_native_tables=False,
        phase_timing=timing, **kwargs,
    )
    if route == "text":
        sections, undefined = assemble_file("\n".join(lines) + "\n")
    elif route != "native-sink":
        # Structured metadata is already finalized against this same text.
        from pcc.backend.arm64_asm_driver import assemble_lines
        sections, undefined = assemble_lines(lines, structured_sections=sections)
    return sections, undefined, encode_native_object_from_sections(
        sections, undefined=undefined,
    )


@pytest.mark.parametrize("triple", _TARGETS)
@pytest.mark.parametrize("route,optimize", _ROUTES)
@pytest.mark.parametrize("factory", (_stale_managed_ssa_ir, _target_final_stackmap_ir))
def test_retirement_preserves_timed_sections_bytes_roots_and_callback_order(
    monkeypatch, request, triple, route, optimize, factory,
):
    monkeypatch.setenv("PCC_PRECISE_STACKMAP_TABLES", "1")
    source = _source(factory, triple)
    retire = maps.PackedPlannedSafepoints.retire_emission_state
    build = emitter.build_stack_map_plans
    emit = emitter._emit_function
    release = emitter._NativeAArch64Emission.release_captured_function
    outcomes = []
    for delayed in (True, False):
        prepared = _prepare(source)
        timing = _Timing()
        planned, emitted, retired, released = [], [], [], []

        def observe_plans(*args, **kwargs):
            plans = build(*args, **kwargs)
            planned.extend(plans)
            return plans

        def observe_emit(func, plan, **kwargs):
            assert len(planned) == len(prepared.functions) == 2
            # Eager plans and every emission-only table survive all bodies.
            assert not retired
            assert all(not getattr(plan.packed_records, field)._closed for field in _EMISSION)
            result = emit(func, plan, **kwargs)
            emitted.append(plan)
            return result

        def observe_release(sink):
            assert not retired
            release(sink)
            released.append(sink)

        def observe_retire(packed):
            assert len(emitted) == 2
            assert len(released) == (2 if route == "native-sink" else 0)
            assert all(not getattr(packed, field)._closed for field in _EMISSION + _METADATA)
            before = sum(len(getattr(packed, field)) for field in _EMISSION)
            assert before > 0
            if not delayed:
                retire(packed)
                assert all(getattr(packed, field)._closed for field in _EMISSION)
                assert all(len(getattr(packed, field)) == 0 for field in _EMISSION)
                assert all(not getattr(packed, field)._closed for field in _METADATA)
            retired.append((packed, before))

        try:
            with monkeypatch.context() as patch:
                patch.setattr(emitter, "build_stack_map_plans", observe_plans)
                patch.setattr(emitter, "_emit_function", observe_emit)
                patch.setattr(emitter._NativeAArch64Emission, "release_captured_function", observe_release)
                patch.setattr(maps.PackedPlannedSafepoints, "retire_emission_state", observe_retire)
                result = _emit_prepared(prepared, route, optimize, timing)
            assert len(retired) == 2
            assert all(
                getattr(packed, field)._closed
                for packed, _ in retired for field in _EMISSION + _METADATA
            )
            section = next(section for section in result[0] if section.sectname == "__pcc_stackmaps")
            decoded = decode_stack_map(section.data, expected_arch=ARCH_AARCH64)
            assert len(decoded.functions) == 2
            assert any(record.locations for fn in decoded.functions for record in fn.records)
            assert bool(prepared.functions[1].aarch64_tail_call_ids) is optimize
            outcomes.append((result, timing.events, [count for _, count in retired]))
        finally:
            for plan in planned + emitted:
                plan.packed_records.close()
            for func in prepared.functions:
                get_indexed_function_kernel(func).close_native_tables()
    assert outcomes[0] == outcomes[1]
    request.node.user_properties.append(("retired_emission_scalar_words", sum(outcomes[1][2])))


@pytest.mark.parametrize("failure_phase", ("emit", "capture", "timing", "finalize-start"))
def test_retirement_waits_for_all_bodies_capture_and_callbacks(monkeypatch, failure_phase):
    source = _source(_stale_managed_ssa_ir, _TARGETS[0])
    prepared = _prepare(source)
    plans = []
    build = emitter.build_stack_map_plans
    emit = emitter._emit_function
    release = emitter._NativeAArch64Emission.release_captured_function
    count = 0

    def observe_plans(*args, **kwargs):
        result = build(*args, **kwargs)
        plans.extend(result)
        return result

    def fail_emit(func, plan, **kwargs):
        nonlocal count
        count += 1
        if count == 2 and failure_phase == "emit":
            raise BackendUnavailable("injected emission failure")
        return emit(func, plan, **kwargs)

    def fail_release(sink):
        if count == 2 and failure_phase == "capture":
            raise BackendUnavailable("injected capture failure")
        return release(sink)

    class FailTiming(_Timing):
        def start(self):
            if (failure_phase == "finalize-start" and count == 2
                    and self.events and self.events[-1][:2] == ("add", 6)):
                raise BackendUnavailable("injected finalization start failure")
            return super().start()

        def add(self, phase, started):
            if phase == 6 and count == 2 and failure_phase == "timing":
                raise BackendUnavailable("injected timing failure")
            super().add(phase, started)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(emitter, "build_stack_map_plans", observe_plans)
            patch.setattr(emitter, "_emit_function", fail_emit)
            patch.setattr(emitter._NativeAArch64Emission, "release_captured_function", fail_release)
            with pytest.raises(BackendUnavailable, match="injected .* failure"):
                _emit_prepared(prepared, "native-sink", False, FailTiming())
        assert count == len(plans) == 2
        assert all(
            not getattr(plan.packed_records, field)._closed
            for plan in plans for field in _EMISSION + _METADATA
        )
        assert not emitter._AARCH64_EMISSION_ACTIVE
        assert not emitter.direct_instruction_capture_active()
    finally:
        for plan in plans:
            plan.packed_records.close()
        for func in prepared.functions:
            get_indexed_function_kernel(func).close_native_tables()


def test_public_plans_stay_complete_until_explicit_retirement():
    prepared = _prepare(_stale_managed_ssa_ir(_TARGETS[0]))
    plan = maps.build_function_stack_map_plan(
        prepared.functions[0], prepared.globals_, target="aarch64-darwin",
    )
    packed = plan.packed_records
    try:
        records = plan.diagnostic_records()
        assert any(record.reloads for record in records)
        assert all(not getattr(packed, field)._closed for field in _EMISSION + _METADATA)
        locations = [packed.record_locations(index) for index in range(len(packed))]
        packed.retire_emission_state()
        packed.retire_emission_state()
        assert locations == [packed.record_locations(index) for index in range(len(packed))]
        assert all(getattr(packed, field)._closed for field in _EMISSION)
        assert all(not getattr(packed, field)._closed for field in _METADATA)
    finally:
        packed.close()
        get_indexed_function_kernel(prepared.functions[0]).close_native_tables()


def test_final_layout_failure_keeps_metadata_and_releases_emitter_scope(monkeypatch):
    prepared = _prepare(_source(_stale_managed_ssa_ir, _TARGETS[0]))
    plans = []
    build = emitter.build_stack_map_plans

    def observe_plans(*args, **kwargs):
        result = build(*args, **kwargs)
        plans.extend(result)
        return result

    def fail_layout(builder):
        raise BackendUnavailable("injected final layout failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(emitter, "build_stack_map_plans", observe_plans)
            patch.setattr(emitter.AArch64ModuleBuilder, "finalize_text_layout", fail_layout)
            with pytest.raises(BackendUnavailable, match="injected final layout failure"):
                _emit_prepared(prepared, "native-sink", False, _Timing())
        assert len(plans) == 2
        for plan in plans:
            packed = plan.packed_records
            assert all(getattr(packed, field)._closed for field in _EMISSION)
            assert all(not getattr(packed, field)._closed for field in _METADATA)
        assert not emitter._AARCH64_EMISSION_ACTIVE
        assert not emitter.direct_instruction_capture_active()
        # The nonconsuming kernel option allows a fresh successful invocation;
        # its new private plans must not inherit the failed invocation's state.
        assert _emit_prepared(prepared, "native-sink", False, _Timing())[0]
    finally:
        for plan in plans:
            plan.packed_records.close()
        for func in prepared.functions:
            get_indexed_function_kernel(func).close_native_tables()


@pytest.mark.parametrize("rename_in_callback", (False, True))
@pytest.mark.parametrize("route", ("text", "native-sink"))
def test_reused_name_keeps_emission_storage_and_original_error(
    monkeypatch, rename_in_callback, route,
):
    # The eager dictionary intentionally keeps the last plan for a repeated
    # name. The verifier does not reject this module-wide shape. Do not consume
    # its routes at the first body, even though final layout rejects the labels.
    source = '''
target triple = "arm64-apple-darwin23.6.0"
define i64 @first(i64 %value) {
entry:
  ret i64 %value
}
define i64 @second(i64 %value) {
entry:
  ret i64 %value
}
'''
    if not rename_in_callback:
        source = source.replace("@second", "@first")
    build, emit = emitter.build_stack_map_plans, emitter._emit_function
    outcomes = []
    for delayed in (True, False):
        prepared = _prepare(source)
        plans, consumed = [], []

        def observe_plans(*args, **kwargs):
            result = build(*args, **kwargs)
            plans.extend(result)
            return result

        def observe_emit(func, plan, **kwargs):
            assert all(not getattr(plan.packed_records, field)._closed for field in _EMISSION)
            consumed.append(plan)
            return emit(func, plan, **kwargs)

        class RenameTiming(_Timing):
            def add(self, phase, started):
                if phase == 6 and len(consumed) == 1 and rename_in_callback:
                    prepared.functions[1].name = prepared.functions[0].name
                super().add(phase, started)

        try:
            with monkeypatch.context() as patch:
                patch.setattr(emitter, "build_stack_map_plans", observe_plans)
                patch.setattr(emitter, "_emit_function", observe_emit)
                if delayed:
                    patch.setattr(maps.PackedPlannedSafepoints, "retire_emission_state", lambda self: None)
                with pytest.raises(Exception) as caught:
                    _emit_prepared(prepared, route, False, RenameTiming())
            assert len(consumed) == 2 and consumed[0] is consumed[1]
            assert "duplicate" in str(caught.value)
            outcomes.append((type(caught.value), str(caught.value)))
            assert not emitter._AARCH64_EMISSION_ACTIVE
            assert not emitter.direct_instruction_capture_active()
        finally:
            for plan in plans:
                plan.packed_records.close()
            for func in prepared.functions:
                get_indexed_function_kernel(func).close_native_tables()
    assert outcomes[0] == outcomes[1]


def test_reused_prepared_module_resets_ordinary_plans_before_tail_replanning(monkeypatch):
    monkeypatch.setenv("PCC_PRECISE_STACKMAP_TABLES", "1")
    source = _source(_stale_managed_ssa_ir, _TARGETS[0])
    build = emitter.build_stack_map_plans
    outcomes = []
    for delayed in (True, False):
        prepared = _prepare(source)
        observed = []

        def observe_plans(functions, *args, **kwargs):
            assert all(func.aarch64_tail_call_ids == [] for func in functions)
            result = build(functions, *args, **kwargs)
            observed.append(result)
            return result

        try:
            with monkeypatch.context() as patch:
                patch.setattr(emitter, "build_stack_map_plans", observe_plans)
                if delayed:
                    patch.setattr(maps.PackedPlannedSafepoints, "retire_emission_state", lambda self: None)
                result = [_emit_prepared(prepared, "text", optimize, _Timing())
                          for optimize in (True, False, True)]
            assert len(observed) == 3
            assert result[0] == result[2]
            assert result[0] != result[1]
            assert all(not get_indexed_function_kernel(func).instruction_metadata._closed
                       for func in prepared.functions)
            outcomes.append(result)
        finally:
            for plans in observed:
                for plan in plans:
                    plan.packed_records.close()
            for func in prepared.functions:
                get_indexed_function_kernel(func).close_native_tables()
    assert outcomes[0] == outcomes[1]
