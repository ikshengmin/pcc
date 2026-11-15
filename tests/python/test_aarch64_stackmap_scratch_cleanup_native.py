"""Same-source execution of the actual stack-map writer and native arenas.

This integration control copies production source like the existing packed
x86 writer and record-sort controls. Only arena malloc/realloc/free call names
are instrumented; typed raw-pointer wrappers call the real unsafe intrinsics.
The writer, sorter, packed-record packer and used packed-plan methods are
unchanged. No fake arena, allocation result, Section or packed plan is used.
This is correctness evidence only, never an RSS or Stage2 performance test.
A pass would cover this extracted writer's native control flow, not the full
emitter route, a self-hosted fixed point or an end-to-end memory measurement.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend import macho_obj
from pcc.backend import precise_stackmap as wire
from pcc.backend import self_backend_precise_stackmaps as maps
from pcc.backend import self_backend_value_arena as arena_module
from tests.python.test_aarch64_stackmap_scratch_cleanup import (
    _LEGACY_WRITER_SOURCE,
    _block,
    _fixture,
    _run,
    _symbol,
)


_PACKED_METHODS = (
    "__init__", "__len__", "append", "scalar", "span", "label",
    "exceptional_block", "location_scalar", "location_group", "location_group_key",
    "add_entry_route", "add_suffix_route", "add_terminator_route",
    "finish_build", "_build_route_spans", "close",
)
_ALGORITHMS = (
    "_packed_record_is_rootless", "_validate_planned_location_offset",
    "_final_stack_map_record_greater", "_swap_final_stack_map_records",
    "_swap_final_stack_map_records_native", "_sift_down_final_stack_map_records_native",
    "_sort_final_stack_map_records", "_pack_stack_map_record_arena",
    "build_aarch64_stack_map_section",
)


def _copied_source(expected_payload, expected_relocations):
    arena = Path(arena_module.__file__).read_text().split("class CompilerRecordSpanArena:", 1)[0]
    prefix, body = arena.split("class CompilerIntArena:", 1)
    # Keep imports and intrinsic provenance unchanged. Only calls inside the
    # real class go through observers, avoiding rebinding imported intrinsic
    # names that the compiler may resolve statically. Wrappers have c_ptr
    # parameters/returns: raw pointers never cross a managed-object argument.
    instrumented = body
    for name in ("malloc", "realloc", "free"):
        instrumented = instrumented.replace(name + "(", "_observed_" + name + "(")
    restored = instrumented
    for name in ("malloc", "realloc", "free"):
        restored = restored.replace("_observed_" + name + "(", name + "(")
    assert restored == body
    text = prefix + "class CompilerIntArena:" + instrumented
    text += '''
import os
import struct
from dataclasses import dataclass
from pcc.unsafe import malloc as _real_malloc
from pcc.unsafe import realloc as _real_realloc
from pcc.unsafe import free as _real_free
from pcc.unsafe import store_i8
from pcc.unsafe import int_to_ptr as _record_int_to_ptr
from pcc.unsafe import load_i64 as _record_load_i64
from pcc.unsafe import store_i64 as _record_store_i64
'''
    # Exact production wire constants/codecs, excluding unrelated parsers.
    wire_source = Path(wire.__file__).read_text()
    text += "\n" + wire_source[wire_source.index('MAGIC = b"PCCSMAP1"'):
                               wire_source.index("def _payload_version(")]
    text += "\n" + inspect.getsource(BackendUnavailable)
    # Only constants are required from the Mach-O module. Their values come
    # from the loaded production module, never guessed ABI literals.
    text += "\nclass _MachOConstants:\n"
    for name in ("ARM64_RELOC_UNSIGNED", "DICE_KIND_DATA"):
        text += "    " + name + " = " + repr(getattr(maps._macho_spec, name)) + "\n"
    text += "\nspec = _MachOConstants()\n_macho_spec = spec\n"
    for name in ("DATA_SECTION_FLAGS", "PCC_STACKMAP_SECTION_FLAGS"):
        text += name + " = " + repr(getattr(macho_obj, name)) + "\n"
    for cls in (macho_obj.TextSymbol, macho_obj.Relocation, macho_obj.DataInCodeRegion):
        text += "\n" + inspect.getsource(cls)
    # These are exact field declarations, not replacement data models. The
    # writer does not call Section properties or plan emission/IR methods.
    text += "\n" + inspect.getsource(macho_obj.Section).split("    @property", 1)[0]
    for cls in (maps.PlannedRootLocation, maps.PlannedManagedReload, maps.PlannedSafepoint):
        text += "\n" + inspect.getsource(cls)
    text += "\n" + inspect.getsource(maps.PackedPlannedSafepoints).split("    def __init__(", 1)[0]
    for name in _PACKED_METHODS:
        text += "\n" + inspect.getsource(getattr(maps.PackedPlannedSafepoints, name))
    text += "\n" + inspect.getsource(maps.FunctionStackMapPlan).split("    def diagnostic_records(", 1)[0]
    text += '''
_STACK_MAP_HEADER_CODEC = _HEADER
_STACK_MAP_FUNCTION_CODEC = _FUNCTION
_STACK_MAP_RECORD_CODEC = _RECORD
_STACK_MAP_LOCATION_CODEC = _LOCATION
_STACK_MAP_RECORD_SCALAR_COUNT = 10
_ROOTLESS_PRUNABLE_KINDS = (SAFEPOINT_LOOP, SAFEPOINT_CALL)

def _aarch64_text_label_offsets(lines):
    # Explicitly outside this metadata-only fixture, not a fake layout result.
    raise AssertionError("the fixture must supply final target offsets")
'''
    for name in _ALGORITHMS:
        text += "\n" + inspect.getsource(getattr(maps, name))
    # Frozen pre-change negative control: rename only its callable, not any
    # ownership or error behavior. It proves the observer can detect the leak.
    text += "\n" + _LEGACY_WRITER_SOURCE.replace(
        "def build_aarch64_stack_map_section(", "def _legacy_build_aarch64_stack_map_section(", 1,
    )
    for helper in (_symbol, _block, _fixture):
        text += "\n" + inspect.getsource(helper).replace("maps.", "").replace("wire.", "")
    text += "\n_EXPECTED_PAYLOAD = " + repr(expected_payload) + "\n"
    text += "_EXPECTED_RELOCATIONS = " + repr(expected_relocations) + "\n"
    text += _NATIVE_DRIVER
    return text


_NATIVE_DRIVER = '''
class _CleanupFailure(Exception):
    pass

class _ObserverBookkeepingFailure(Exception):
    pass

_track = False
_fail_one_free = False
_inject_observer_failure = False
_observer_failure_triggered = False
_outstanding: dict[int, int] = {}
_allocations = 0
_releases = 0
_reallocations = 0
_external_frees = 0
_cleanup_faults = 0
_observer_invalid = False

def _invalidate_observer() -> None:
    global _observer_invalid, _track, _fail_one_free, _inject_observer_failure
    # Bookkeeping must never prevent a real allocation/reallocation result
    # from reaching its owning arena. Once evidence is incomplete, stop
    # observing/injecting and never recover pointers from the stale table.
    _observer_invalid = True
    _track = False
    _fail_one_free = False
    _inject_observer_failure = False

def _require_valid_observer() -> None:
    assert not _observer_invalid, "allocator observer bookkeeping failed"

def _recover_private_allocations() -> bool:
    if _observer_invalid:
        return False
    for address in list(_outstanding):
        _real_free(int_to_ptr(address))
    _outstanding.clear()
    return True

def _observed_malloc(size: int) -> c_ptr:
    global _allocations
    pointer: c_ptr = _real_malloc(size)
    if _track:
        try:
            address = ptr_to_int(pointer)
            if address != 0:
                assert address not in _outstanding
                _outstanding[address] = size
                _allocations += 1
        except BaseException:
            _invalidate_observer()
    return pointer

def _observed_realloc(pointer: c_ptr, size: int) -> c_ptr:
    global _allocations, _releases, _reallocations, _inject_observer_failure
    global _observer_failure_triggered
    old_address = 0
    if _track:
        try:
            old_address = ptr_to_int(pointer)
        except BaseException:
            _invalidate_observer()
    result: c_ptr = _real_realloc(pointer, size)
    if _track:
        try:
            address = ptr_to_int(result)
            if address != 0:
                if _inject_observer_failure:
                    _inject_observer_failure = False
                    _observer_failure_triggered = True
                    raise _ObserverBookkeepingFailure("after successful real realloc")
                assert old_address in _outstanding
                del _outstanding[old_address]
                _releases += 1
                assert address not in _outstanding
                _outstanding[address] = size
                _allocations += 1
                _reallocations += 1
        except BaseException:
            _invalidate_observer()
    return result

def _observed_free(pointer: c_ptr) -> None:
    global _releases, _external_frees, _fail_one_free, _cleanup_faults
    inject_failure = False
    if _track:
        try:
            address = ptr_to_int(pointer)
            if address in _outstanding:
                if _fail_one_free:
                    _fail_one_free = False
                    _cleanup_faults += 1
                    inject_failure = True
                else:
                    # Remove recovery authority BEFORE releasing memory.
                    del _outstanding[address]
                    _releases += 1
            elif address != 0:
                _external_frees += 1
        except BaseException:
            _invalidate_observer()
            inject_failure = False
    if inject_failure:
        # Fail BEFORE real free. The exact outstanding allocation can be
        # recovered once below without accessing or freeing released data.
        raise _CleanupFailure("injected private release failure")
    _real_free(pointer)

def _caller_arenas(packed):
    return [packed.scalars, packed.spans, packed.location_scalars,
            packed.location_group_spans, packed.reload_scalars,
            packed.entry_routes, packed.suffix_routes, packed.terminator_routes,
            packed.entry_route_spans, packed.suffix_route_spans,
            packed.terminator_route_spans]

def _caller_state(plans):
    states = []
    for plan in plans:
        packed = plan.packed_records
        for arena in _caller_arenas(packed):
            states.append((arena.native_address(), len(arena), arena._closed,
                           arena.diagnostic_values()))
        states.append((tuple(packed.labels), tuple(packed.exceptional_blocks),
                       tuple(packed.location_group_keys), packed.diagnostic_projections))
    return states

def _run_case(case: str, expected_native: bool) -> None:
    global _track, _fail_one_free, _allocations, _releases, _reallocations
    global _external_frees, _cleanup_faults
    assert not _observer_invalid
    assert not _outstanding
    _allocations = 0
    _releases = 0
    _reallocations = 0
    _external_frees = 0
    _cleanup_faults = 0
    plans, offsets = _fixture()
    alpha = plans[1]
    if case == "missing-range":
        del offsets[alpha.end_label]
    elif case == "missing-safepoint" or case == "cleanup-error":
        del offsets[_block("alpha", "call")]
    elif case == "missing-exception":
        del offsets[_block("alpha", "rescue")]
    elif case == "location-validation":
        alpha.packed_records.location_scalars.set_unchecked(0, -72)
    elif case == "second-plan-failure":
        del offsets[_block("beta", "call")]
    before = _caller_state(plans)
    for plan in plans:
        for arena in _caller_arenas(plan.packed_records):
            assert arena.uses_native_storage == expected_native
    _fail_one_free = case == "cleanup-error" and expected_native
    _track = True
    failure = ""
    try:
        try:
            section = build_aarch64_stack_map_section(
                [], plans, function_symbol=_symbol, block_label=_block,
                target_offsets=offsets,
            )
        except BackendUnavailable as error:
            failure = str(error)
        _require_valid_observer()
        if case == "success":
            assert failure == ""
            assert section.data == _EXPECTED_PAYLOAD
            relocations = []
            for relocation in section.relocations:
                relocations.append((relocation.offset, relocation.symbol,
                                    relocation.type, relocation.pcrel, relocation.length))
            assert tuple(relocations) == _EXPECTED_RELOCATIONS
            for plan in plans:
                for arena in _caller_arenas(plan.packed_records):
                    assert arena._closed
                    assert arena.native_address() == 0
                    assert len(arena) == 0
        else:
            assert _caller_state(plans) == before
            if case == "missing-range":
                assert failure == "target-final stack-map range missing for 'alpha'"
            elif case == "missing-safepoint" or case == "cleanup-error":
                assert failure == "target-final safepoint label missing: 'L_alpha_call'"
            elif case == "missing-exception":
                assert failure == "target-final exception successor label missing for 'alpha'/'rescue'"
            elif case == "location-validation":
                assert failure == "planned managed stack location -72 exceeds frame size 64"
            elif case == "second-plan-failure":
                assert failure == "target-final safepoint label missing: 'L_beta_call'"
        leaked_for_injection = 1 if case == "cleanup-error" and expected_native else 0
        assert len(_outstanding) == leaked_for_injection
        assert _cleanup_faults == leaked_for_injection
        assert _allocations == _releases + leaked_for_injection
        if expected_native:
            assert _allocations >= (2 if case == "missing-range" else 3)
            assert _external_frees == (22 if case == "success" else 0)
            if case == "success":
                assert _reallocations > 0
        else:
            assert _allocations == _releases == _external_frees == 0
        # First two fields must match the same-source CPython reference.
        # Remaining fields are native allocation receipts, not semantic output.
        print(case + "|" + (failure if failure else "success") + "|"
              + str(_allocations) + "|" + str(_releases) + "|"
              + str(_external_frees) + "|" + str(_cleanup_faults))
    finally:
        _track = False
        _fail_one_free = False
        # A failed assertion on the pre-change control may leave several
        # private allocations. Recover only the live outstanding set. The
        # real arena has no destructor; no second free can follow this release.
        # An invalid observer may retain stale/reallocated addresses. Leaking
        # until this failing process exits is safer than speculative recovery.
        _recover_private_allocations()
        for plan in plans:
            plan.packed_records.close()

def _run_legacy_control(expected_native: bool) -> None:
    global _track, _allocations, _releases, _reallocations, _external_frees, _cleanup_faults
    assert not _observer_invalid
    assert not _outstanding
    _allocations = 0
    _releases = 0
    _reallocations = 0
    _external_frees = 0
    _cleanup_faults = 0
    plans, offsets = _fixture()
    del offsets[plans[1].end_label]
    before = _caller_state(plans)
    _track = True
    failure = ""
    try:
        try:
            _legacy_build_aarch64_stack_map_section(
                [], plans, function_symbol=_symbol, block_label=_block,
                target_offsets=offsets,
            )
        except BackendUnavailable as error:
            failure = str(error)
        _require_valid_observer()
        assert failure == "target-final stack-map range missing for 'alpha'"
        assert _caller_state(plans) == before
        # The exact old writer does not release either successfully allocated
        # private global arena. Zero allocations under CPython are explicitly
        # different evidence, not a passing native leak detector.
        assert len(_outstanding) == (2 if expected_native else 0)
        assert _allocations == (2 if expected_native else 0)
        assert _releases == _reallocations == _external_frees == _cleanup_faults == 0
        print("legacy-missing-range|" + failure + "|" + str(_allocations)
              + "|" + str(_releases) + "|0|0")
    finally:
        _track = False
        _recover_private_allocations()
        for plan in plans:
            plan.packed_records.close()

def _run_invalid_observer_control(expected_native: bool) -> None:
    global _track, _inject_observer_failure, _observer_failure_triggered
    global _allocations, _releases, _reallocations, _external_frees, _cleanup_faults
    assert not _observer_invalid
    assert not _outstanding
    _allocations = 0
    _releases = 0
    _reallocations = 0
    _external_frees = 0
    _cleanup_faults = 0
    _observer_failure_triggered = False
    plans, offsets = _fixture()
    _inject_observer_failure = expected_native
    _track = True
    try:
        section = build_aarch64_stack_map_section(
            [], plans, function_symbol=_symbol, block_label=_block,
            target_offsets=offsets,
        )
        # The post-realloc observation error must not strand the real result
        # outside its arena or prevent subsequent real close/free calls.
        assert section.data == _EXPECTED_PAYLOAD
        for plan in plans:
            for arena in _caller_arenas(plan.packed_records):
                assert arena._closed
                assert arena.native_address() == 0
        assert _observer_invalid == expected_native
        assert _observer_failure_triggered == expected_native
        rejected = False
        try:
            _require_valid_observer()
        except AssertionError as error:
            assert str(error) == "allocator observer bookkeeping failed"
            rejected = True
        assert rejected == expected_native
        remaining = len(_outstanding)
        assert (remaining > 0) == expected_native
        # A normal case fails the same gate above. This explicit observer
        # negative control alone expects rejection and verifies recovery is
        # disabled rather than touching stale pre-realloc addresses.
        assert _recover_private_allocations() == (not expected_native)
        assert len(_outstanding) == remaining
        # These partial counts are explicitly invalid after injected failure,
        # never presented as allocation/free balance evidence.
        print("observer-realloc-failure|success|" + str(_allocations) + "|"
              + str(_releases) + "|" + str(_external_frees) + "|"
              + str(1 if rejected else 0) + "|"
              + ("observer-invalid" if rejected else "observer-valid"))
    finally:
        _track = False
        _inject_observer_failure = False
        _recover_private_allocations()
        for plan in plans:
            plan.packed_records.close()

def main():
    expected_native = os.environ.get("PCC_EXPECT_NATIVE_ARENAS", "0") == "1"
    _run_legacy_control(expected_native)
    for case in ("success", "missing-range", "missing-safepoint", "missing-exception",
                 "location-validation", "second-plan-failure", "cleanup-error"):
        _run_case(case, expected_native)
    # An invalid observer is never reset. Run its negative control last.
    _run_invalid_observer_control(expected_native)

main()
'''


def _semantic_rows(stdout):
    return [line.split("|")[:2] for line in stdout.splitlines()]


def _file_identity(path):
    if path is None:
        return None
    identity = {"path": os.path.abspath(os.fspath(path)), "sha256": None}
    try:
        identity["sha256"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as error:
        identity["read_error"] = str(error)
    return identity


def _compiler_evidence(compiler, nominal_case, source, binary, runtime_archive):
    # Both native routes at this revision close over their selected compiler.
    # This is stronger than the current environment: pcc1_route.install() may
    # have captured a different path before a later environment change.
    captured = inspect.getclosurevars(compiler).nonlocals.get("compiler")
    if not isinstance(captured, (str, os.PathLike)):
        captured = None
    return {
        "nominal_case": nominal_case,
        "callable_module": compiler.__module__,
        "callable_qualname": compiler.__qualname__,
        "callable_source": _file_identity(inspect.getsourcefile(compiler)),
        "host_process": _file_identity(sys.executable),
        "environment": {name: os.environ.get(name) for name in (
            "PCC_TEST_COMPILER", "PCC_CURRENT_PCC1", "PCC_TEST_COMPILER_STRICT",
            "PCC_TEST_COMPILER_LOG", "PCC_TEST_COMPILER_HASH_INPUTS",
        )},
        "captured_compiler_before": _file_identity(captured),
        "captured_compiler_after": None,
        "api_call": {"source": str(source), "output": str(binary), "backend": "self",
                     "runtime_archive": str(runtime_archive)},
        # This command is reconstructed from the inspected current fixtures,
        # not falsely presented as observed subprocess arguments.
        "expected_command_from_fixture": None if captured is None else [
            os.fspath(captured), "--backend", "self", "--ir-scaffold", "on",
            str(source), "-o", str(binary),
        ],
        "returned_args": None,
        "returned_args_status": "compile not completed",
        "route_receipts": [],
        "ownership_boundary": "nominal_case is not execution-owner evidence; use callable, captured path, returned args and route receipts",
    }


def _route_receipts(path, offset, test_name):
    if path is None or not Path(path).is_file():
        return []
    records = []
    with Path(path).open("rb") as stream:
        stream.seek(offset)
        for line in stream:
            try:
                record = json.loads(line)
            except (ValueError, UnicodeDecodeError) as error:
                records.append({"unparsed_route_line": str(error)})
                continue
            if record.get("test") == test_name and record.get("kind") == "compile_python":
                records.append(record)
    return records


@pytest.mark.integration
def test_exact_writer_releases_native_scratch_under_all_collectors(
    tmp_path, monkeypatch, python_program_compiler, pcc_runtime_archive, request,
):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    oracle = _run(monkeypatch, legacy=True)
    assert oracle["error"] is None
    expected = oracle["result"]
    relocations = tuple((item.offset, item.symbol, item.type, item.pcrel, item.length)
                        for item in expected.relocations)
    source = tmp_path / "stackmap_scratch_native.py"
    source.write_text(_copied_source(expected.data, relocations))
    binary = tmp_path / "stackmap_scratch_native"
    receipt_path = tmp_path / "stackmap_scratch_native.receipt.json"
    receipt = {
        "compiler_evidence": _compiler_evidence(
            python_program_compiler, request.node.callspec.params["python_program_compiler"],
            source, binary, pcc_runtime_archive,
        ),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "writer_sha256": hashlib.sha256(inspect.getsource(
            maps.build_aarch64_stack_map_section,
        ).encode()).hexdigest(),
        "legacy_writer_sha256": hashlib.sha256(_LEGACY_WRITER_SOURCE.encode()).hexdigest(),
        "runtime_archive": str(pcc_runtime_archive),
        "runtime_sha256": hashlib.sha256(Path(pcc_runtime_archive).read_bytes()).hexdigest(),
        "instrumentation": "real arena malloc/realloc/free call seams; unchanged writer and used helpers",
        "production_sha256": {
            str(Path(module.__file__)): hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for module in (arena_module, maps, wire, macho_obj)
        },
        "cpython_reference": None,
        "gc_runs": [],
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    reference = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=20,
        env=dict(os.environ, PCC_EXPECT_NATIVE_ARENAS="0"),
    )
    (tmp_path / "cpython.stdout").write_text(reference.stdout)
    (tmp_path / "cpython.stderr").write_text(reference.stderr)
    receipt["cpython_reference"] = {"returncode": reference.returncode, "stdout": reference.stdout,
                                    "stderr": reference.stderr}
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    assert reference.returncode == 0, (reference.stdout, reference.stderr)
    assert reference.stderr == ""
    assert len(_semantic_rows(reference.stdout)) == 9
    route_log = os.environ.get("PCC_TEST_COMPILER_LOG")
    route_offset = Path(route_log).stat().st_size if route_log and Path(route_log).is_file() else 0
    compile_test = os.environ.get("PYTEST_CURRENT_TEST", "")
    try:
        compiled = python_program_compiler(
            str(source), str(binary), backend="self",
            runtime_archive=str(pcc_runtime_archive),
        )
        args = getattr(compiled, "args", None)
        evidence = receipt["compiler_evidence"]
        if args is not None:
            evidence["returned_args"] = ([os.fsdecode(value) for value in args]
                                         if isinstance(args, (tuple, list)) else os.fsdecode(args))
            evidence["returned_args_status"] = "exposed by compiler call result"
        else:
            evidence["returned_args_status"] = "fixture returned no subprocess arguments"
    finally:
        evidence = receipt["compiler_evidence"]
        captured = evidence["captured_compiler_before"]
        if captured is not None:
            evidence["captured_compiler_after"] = _file_identity(captured["path"])
        evidence["route_receipts"] = _route_receipts(route_log, route_offset, compile_test)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    receipt["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    for collector in range(5):
        completed = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=20,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(collector),
                     PCC_EXPECT_NATIVE_ARENAS="1"),
        )
        (tmp_path / f"gc{collector}.stdout").write_text(completed.stdout)
        (tmp_path / f"gc{collector}.stderr").write_text(completed.stderr)
        receipt["gc_runs"].append({"collector": collector, "returncode": completed.returncode,
                                   "stdout": completed.stdout, "stderr": completed.stderr})
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        assert completed.returncode == 0, (collector, completed.stdout, completed.stderr)
        assert completed.stderr == ""
        assert _semantic_rows(completed.stdout) == _semantic_rows(reference.stdout)
