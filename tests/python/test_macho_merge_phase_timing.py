"""Host-only contracts for time-only native merge attribution, not a speed test."""

import ast
import io
import types
from pathlib import Path

import pytest

from pcc.backend import indexed_phase_timing as timing
from pcc.backend import macho_exec, macho_link, macho_obj, macho_spec
from pcc.backend import precise_stackmap as wire
from pcc.backend.native_object import (
    NativeObject,
    decode_packed_native_object,
    encode_native_object,
)


PHASES = ("macho_merge_inputs", "macho_merge_stackmaps", "macho_merge_final_view")
TRUTH = ("1", "true", "yes", "on")


@pytest.fixture(autouse=True)
def forbid_gc_sampling(monkeypatch):
    reads = []
    def forbidden(*args):
        reads.append(args)
        raise AssertionError("time-only child must never read GC telemetry")
    monkeypatch.setattr(timing, "_pcc_gc_backend", forbidden)
    monkeypatch.setattr(timing, "_pcc_gc_telemetry", forbidden)
    yield
    # The observer catches BaseException: raising alone would be a false pass.
    assert reads == []


def _policy(monkeypatch, *, owner="pcc", platform="darwin", **values):
    environment = {
        "PCC_PY_FRONTEND_WORKER_TIMING": "1",
        "PCC_DIRECT_INDEXED_KERNEL_EMIT": "1",
    }
    environment.update(values)
    # Replace this module's providers, not the process-wide sys/platform owner.
    monkeypatch.setattr(timing, "sys", types.SimpleNamespace(
        implementation=types.SimpleNamespace(name=owner), platform=platform,
    ))
    monkeypatch.setattr(timing, "os", types.SimpleNamespace(environ=environment))
    return environment


@pytest.mark.parametrize("flag", (
    "PCC_PY_FRONTEND_WORKER_TIMING", "PCC_DIRECT_INDEXED_KERNEL_EMIT",
))
@pytest.mark.parametrize("value", (*TRUTH, " TRUE ", "0", "false", "", None))
def test_merge_policy_uses_existing_normalized_coordinator_flags(monkeypatch, flag, value):
    _policy(monkeypatch, **{flag: value})
    assert timing.native_macho_merge_timing_enabled() is (
        str(value or "").strip().lower() in TRUTH
    )


@pytest.mark.parametrize("flag", (
    "PCC_DEFER_SELF_LINK_PLAN", "PCC_DEFER_FRONTEND_CODEGEN_PLAN",
))
@pytest.mark.parametrize("value", ("", " \t", None, "plan.json", "0"))
def test_merge_policy_requires_both_deferred_plans_blank(monkeypatch, flag, value):
    _policy(monkeypatch, **{flag: value})
    assert timing.native_macho_merge_timing_enabled() is (not str(value or "").strip())


def test_merge_policy_requires_native_darwin_and_is_fail_open(monkeypatch):
    for owner, platform in (("cpython", "darwin"), ("pcc", "linux"), ("pcc", "win32")):
        _policy(monkeypatch, owner=owner, platform=platform)
        assert not timing.native_macho_merge_timing_enabled()
    environment = _policy(monkeypatch, PCC_DIRECT_INDEXED_NATIVE_OBJECT="0")
    # The existing parent does not gate its timer on native-object selection.
    assert timing.native_macho_merge_timing_enabled()
    environment.pop("PCC_PY_FRONTEND_WORKER_TIMING")
    assert not timing.native_macho_merge_timing_enabled()
    class BrokenEnvironment:
        def get(self, *args):
            raise KeyboardInterrupt("optional policy read")
    monkeypatch.setattr(timing.os, "environ", BrokenEnvironment())
    assert not timing.native_macho_merge_timing_enabled()


def _inputs(kind="packed", stackmaps=True, subtractor=False):
    sections = [macho_obj.Section(
        sectname="__text", segname="__TEXT",
        data=b"\xc0\x03\x5f\xd6" * 2 + b"\x00\x00\x00\x90" + b"\xc0\x03\x5f\xd6" * 13,
        align_log2=2, flags=macho_obj.TEXT_SECTION_FLAGS,
        symbols=(macho_obj.TextSymbol("_main", 0), macho_obj.TextSymbol("_other", 4)),
        relocations=(macho_obj.Relocation(
            8, "_main", macho_spec.ARM64_RELOC_PAGE21, True, 2, addend=4,
        ),),
    ), macho_obj.Section(
        sectname="__data", segname="__DATA", data=b"\0" * 16,
        align_log2=3, flags=macho_obj.DATA_SECTION_FLAGS,
        relocations=(
            macho_obj.Relocation(0, "_main", macho_spec.ARM64_RELOC_UNSIGNED,
                                 False, 3),
            macho_obj.Relocation(8, "_main", macho_spec.ARM64_RELOC_SUBTRACTOR,
                                 False, 3, minuend="_other") if subtractor else
            macho_obj.Relocation(8, "", macho_spec.ARM64_RELOC_UNSIGNED,
                                 False, 3, section=("__TEXT", "__text"), target_offset=4),
        ),
    )]
    if stackmaps:
        payload = wire.encode_stack_map(wire.PreciseStackMap(
            arch=wire.ARCH_AARCH64, functions=(wire.FunctionStackMap(
                function_id=wire.function_id("_main"), function_address=0,
                code_size=64, frame_size=32, records=(wire.SafepointRecord(
                    safepoint_id=1, instruction_offset=4,
                    kind=wire.SAFEPOINT_ENTRY, locations=(),
                ),),
            ),),
        ))
        address_offset, = wire.function_address_offsets(payload)
        sections.append(macho_obj.Section(
            sectname="__pcc_stackmaps", segname="__DATA", data=payload,
            align_log2=3, flags=macho_obj.PCC_STACKMAP_SECTION_FLAGS,
            relocations=(macho_obj.Relocation(
                address_offset, "_main", macho_spec.ARM64_RELOC_UNSIGNED, False, 3,
            ),),
        ))
    obj = NativeObject.from_sections(sections)
    if kind == "packed":
        return [decode_packed_native_object(encode_native_object(obj))]
    if kind == "macho":
        return [obj.to_macho()]
    return [obj]


def _run(inputs, **kwargs):
    return macho_link.link_relocatable_native(
        inputs, _consume_inputs=True, _source_view=True, _final_executable=True,
        **kwargs,
    )


def _observe(monkeypatch, enabled=True):
    monkeypatch.setattr(macho_link, "native_macho_merge_timing_enabled", lambda: enabled)
    sink = io.StringIO()
    monkeypatch.setattr(timing.sys, "stderr", sink)
    original = timing.CoarsePhaseTiming._gc_snapshot
    snapshots = []
    def snapshot(self):
        assert self.gc_unavailable is True
        snapshots.append(True)
        return original(self)
    monkeypatch.setattr(timing.CoarsePhaseTiming, "_gc_snapshot", snapshot)
    return sink, snapshots


def _events(sink):
    return [dict(part.split("=", 1) for part in line.split()[3:])
            for line in sink.getvalue().splitlines()]


@pytest.mark.parametrize("kind", ("native", "packed", "macho"))
@pytest.mark.parametrize("stackmaps", (False, True))
@pytest.mark.parametrize("budgeted", (False, True))
def test_merge_time_only_children_preserve_real_image_bytes(monkeypatch, kind, stackmaps, budgeted):
    expected = macho_exec.link_prepared_executable(_run(_inputs(kind, stackmaps)))
    sink, snapshots = _observe(monkeypatch)
    inputs = _inputs(kind, stackmaps)
    budget = wire.CompactStackMapBudget(
        1_000_000, 100, set_header_bytes=10, set_entry_bytes=2,
        integer_bytes=3, metadata_entry_bytes=7, allocation_quantum=1,
    ) if budgeted else None
    result = _run(inputs, compact_budget=budget)
    assert inputs == []
    assert macho_exec.link_prepared_executable(result, compact_budget=budget) == expected
    rows = _events(sink)
    assert [(r["phase"], r["event"]) for r in rows] == [
        (phase, event) for phase in PHASES for event in ("begin", "complete")
    ]
    assert len(snapshots) == len(rows) == 6
    for row in rows:
        assert row["parent"] == "macho_prepare_merge"
        assert row["scope"] == "coordinator" and row["gc_available"] == "0"
        assert not any(key.startswith("gc_") and key != "gc_available" for key in row)
        assert ("duration_ns" in row) is (row["event"] == "complete")


def test_merge_disabled_private_routes_do_not_construct_or_call_observer(monkeypatch):
    calls = []
    monkeypatch.setattr(macho_link, "native_macho_merge_timing_enabled", lambda: calls.append("policy") or False)
    def forbidden():
        raise AssertionError("disabled timing constructed an observer")
    monkeypatch.setattr(macho_link, "CoarsePhaseTiming", forbidden)
    for consume, source, final in ((False, True, True), (True, False, True), (True, True, False)):
        macho_link.link_relocatable_native(_inputs(), _consume_inputs=consume,
                                          _source_view=source, _final_executable=final)
    assert calls == []
    _run(_inputs())
    assert calls == ["policy"]


@pytest.mark.parametrize("kind", ("native", "packed", "macho"))
def test_merge_subtractor_source_view_bytes_are_unchanged(monkeypatch, kind):
    # Final image application does not support every relocatable form. Check
    # this valid pair at the changed merge/packing boundary, not by executing
    # an unsupported final-link shape.
    def snapshot(view):
        return (view.sections(), view.symbols(), tuple(view._raw_relocations),
                tuple(bytes(view.section_data(section)) for section in view.sections()))
    expected = snapshot(_run(_inputs(kind, subtractor=True)))
    sink, _ = _observe(monkeypatch)
    assert snapshot(_run(_inputs(kind, subtractor=True))) == expected
    assert len(_events(sink)) == 6


@pytest.mark.parametrize("boundary,phase", (
    ("ordered_parallel_map", PHASES[0]),
    ("merge_stack_map_payloads", PHASES[1]),
    ("OwnedMergedSourceView", PHASES[2]),
))
def test_merge_failure_preserves_exception_identity_and_incomplete_phase(monkeypatch, boundary, phase):
    error = KeyboardInterrupt("original merge failure")
    calls = []
    def fail(*args, **kwargs):
        calls.append(boundary)
        raise error
    monkeypatch.setattr(macho_link, boundary, fail)
    for enabled in (False, True):
        with monkeypatch.context() as patch:
            sink, _ = _observe(patch, enabled)
            with pytest.raises(KeyboardInterrupt) as caught:
                _run(_inputs())
            assert caught.value is error
            rows = _events(sink)
            if enabled:
                assert (rows[-1]["phase"], rows[-1]["event"]) == (phase, "begin")
                assert not any(r["phase"] == phase and r["event"] == "complete" for r in rows)
            else:
                assert rows == []
    assert calls == [boundary, boundary]


@pytest.mark.parametrize("fault", (
    "factory", "clock", "zero_clock", "sink", "begin", "complete", "final_complete",
))
def test_merge_optional_observer_faults_leave_output_unchanged(monkeypatch, fault):
    expected = macho_exec.link_prepared_executable(_run(_inputs()))
    _observe(monkeypatch)
    def fail(*args, **kwargs):
        raise SystemExit("optional observer failure")
    if fault == "factory":
        monkeypatch.setattr(macho_link, "CoarsePhaseTiming", fail)
    elif fault in ("clock", "zero_clock"):
        monkeypatch.setattr(timing.time, "perf_counter_ns", fail if fault == "clock" else lambda: 0)
    elif fault in ("begin", "complete", "final_complete"):
        method = "begin" if fault == "begin" else "complete"
        original = getattr(timing.CoarsePhaseTiming, method)
        def fail_boundary(self, phase, *args):
            selected = PHASES[2] if fault == "final_complete" else PHASES[1]
            if phase == selected:
                fail()
            return original(self, phase, *args)
        monkeypatch.setattr(timing.CoarsePhaseTiming, method, fail_boundary)
    else:
        monkeypatch.setattr(timing.sys, "stderr", types.SimpleNamespace(write=fail, flush=fail))
    assert macho_exec.link_prepared_executable(_run(_inputs())) == expected


def test_merge_boundaries_are_fixed_outside_loops_and_constructor_reads_no_gc():
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "pcc/backend/macho_link.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "link_relocatable_native")
    calls = [n for n in ast.walk(function) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
             and n.func.value.id == "merge_timing"]
    assert [(n.func.attr, n.args[0].value) for n in sorted(calls, key=lambda n: n.lineno)] == [
        (event, phase) for phase in PHASES for event in ("begin", "complete")
    ]
    for loop in ast.walk(function):
        if isinstance(loop, (ast.For, ast.While)):
            assert not any(call in list(ast.walk(loop)) for call in calls)
    provider = ast.parse((root / "pcc/backend/indexed_phase_timing.py").read_text())
    cls = next(n for n in provider.body if isinstance(n, ast.ClassDef) and n.name == "CoarsePhaseTiming")
    constructor = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    assert not any(isinstance(n, ast.Call) for n in ast.walk(constructor))
