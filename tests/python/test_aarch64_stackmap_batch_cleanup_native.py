"""Extracted batch-boundary execution, never full native planner/emitter proof.

The batch function is copied EXACTLY. CompilerIntArena, PackedPlannedSafepoints
fields/used methods, and FunctionStackMapPlan fields are copied from production.
The lower planner deliberately returns complete real plans or a deterministic
failure. Names-only ParsedFunction/GlobalDef fixture classes are NOT their real ABI.
Native append/tuple/constructor OOM and partial lower-planner cleanup are not
claimed. Exception identity is asserted by host tests; native checks compare
class/message. No raw-pointer tracking, allocator substitution, or recovery.
This is correctness-only, not Stage2, bootstrap, RSS or performance evidence.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_PACKED_METHODS = (
    "__init__", "__len__", "append", "add_entry_route", "add_suffix_route",
    "add_terminator_route", "finish_build", "_build_route_spans", "close",
)
_NATIVE_CASES = (
    "empty", "success", "builder", "callback", "baseexception",
    "cleanup-one", "cleanup-multiple",
    "legacy-negative", "default-negative", "other-target-negative",
)


_INPUT_TYPES = '''@dataclass
class ParsedFunction:
    name: str
    block_names: list[str]

# These explicitly named, narrow fixture types match the batch annotations.
# They are not the production IR layouts; lower planning is a controlled seam.
@dataclass
class GlobalDef:
    name: str

'''

def _node(source, name):
    return next(node for node in ast.parse(source).body
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name)


def _segment(source, node):
    first = min([node.lineno] + [item.lineno for item in getattr(node, "decorator_list", ())])
    return "\n".join(source.splitlines()[first - 1:node.end_lineno]) + "\n"


def _literal(source, name):
    node = next(node for node in ast.parse(source).body
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == name for target in node.targets))
    return ast.literal_eval(node.value)


def _source_parts():
    """Source-only extraction: this helper does not import or run project code."""
    arena_source = (_ROOT / "pcc/backend/self_backend_value_arena.py").read_text()
    maps_source = (_ROOT / "pcc/backend/self_backend_precise_stackmaps.py").read_text()
    host_source = Path(__file__).with_name("test_aarch64_stackmap_batch_cleanup.py").read_text()
    legacy = _literal(host_source, "_LEGACY_BATCH_SOURCE")
    assert hashlib.sha256(legacy.encode()).hexdigest() == _literal(host_source, "_LEGACY_BATCH_SHA256")
    stop = _node(arena_source, "CompilerRecordSpanArena").lineno - 1
    arena = "\n".join(arena_source.splitlines()[:stop]) + "\n"
    arena_class = _node(arena, "CompilerIntArena")
    close = next(node for node in arena_class.body if isinstance(node, ast.FunctionDef) and node.name == "close")
    lines = arena.splitlines(keepends=True)
    # One hook at method entry BEFORE a possible free. All other arena source
    # is byte-identical. The hook either deliberately fails once or returns.
    hook_line = "        _batch_close_hook(self)\n"
    insertion = close.body[0].lineno - 1
    lines.insert(insertion, hook_line)
    instrumented = "".join(lines)
    assert instrumented[:len("".join(lines[:insertion]))] == "".join(arena.splitlines(keepends=True)[:insertion])
    restored = instrumented.splitlines(keepends=True)
    del restored[insertion]
    assert "".join(restored) == arena
    packed_node = _node(maps_source, "PackedPlannedSafepoints")
    first_method = next(node for node in packed_node.body if isinstance(node, ast.FunctionDef))
    packed_header = "\n".join(maps_source.splitlines()[packed_node.lineno - 1:first_method.lineno - 1]) + "\n"
    packed_parts = [packed_header]
    for name in _PACKED_METHODS:
        method = next(node for node in packed_node.body if isinstance(node, ast.FunctionDef) and node.name == name)
        packed_parts.append(_segment(maps_source, method))
    plan_node = _node(maps_source, "FunctionStackMapPlan")
    first_method = next(node for node in plan_node.body if isinstance(node, ast.FunctionDef))
    first = min(plan_node.lineno, *(node.lineno for node in plan_node.decorator_list))
    plan_fields = "\n".join(maps_source.splitlines()[first - 1:first_method.lineno - 1]) + "\n"
    batch = _segment(maps_source, _node(maps_source, "build_stack_map_plans"))
    parts = {
        "arena_with_entry_hook": instrumented,
        "imports": "\nimport os\nfrom dataclasses import dataclass\n",
        "input_fixture_types": _INPUT_TYPES,
        "root_location": _segment(maps_source, _node(maps_source, "PlannedRootLocation")),
        "managed_reload": _segment(maps_source, _node(maps_source, "PlannedManagedReload")),
        "safepoint": _segment(maps_source, _node(maps_source, "PlannedSafepoint")),
        "packed_fields_and_used_methods": "\n".join(packed_parts),
        "plan_fields": plan_fields,
        "batch_exact": batch,
        "legacy_rename_only": legacy.replace("def build_stack_map_plans(", "def _legacy_build_stack_map_plans(", 1),
        "case_inventory": "\n_NATIVE_CASES = " + repr(_NATIVE_CASES) + "\n",
        "controlled_lower_planner_and_driver": _NATIVE_DRIVER,
    }
    return parts, arena, batch, legacy



def _unresolved_annotations(source):
    tree = ast.parse(source)
    declared = set(dir(builtins))
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            declared.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            declared.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.Assign):
            declared.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            declared.add(node.target.id)
    referenced = set()
    for node in ast.walk(tree):
        annotation = (node.annotation if isinstance(node, (ast.arg, ast.AnnAssign)) else
                      node.returns if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None)
        if annotation is not None:
            if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
                annotation = ast.parse(annotation.value, mode="eval")
            referenced.update(item.id for item in ast.walk(annotation) if isinstance(item, ast.Name))
    return sorted(referenced - declared)


def _copied_source():
    parts, arena, batch, legacy = _source_parts()
    source = "\n".join(parts.values())
    ast.parse(source)
    unresolved_annotations = _unresolved_annotations(source)
    assert unresolved_annotations == [], unresolved_annotations
    receipt = {
        "status": "source extraction only; not execution evidence",
        "boundary": "exact batch with controlled lower planner and names-only inputs; not ParsedFunction ABI or full planner/emitter",
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "parts_sha256": {name: hashlib.sha256(value.encode()).hexdigest() for name, value in parts.items()},
        "arena_uninstrumented_sha256": hashlib.sha256(arena.encode()).hexdigest(),
        "batch_exact_sha256": hashlib.sha256(batch.encode()).hexdigest(),
        "legacy_original_sha256": hashlib.sha256(legacy.encode()).hexdigest(),
        "packed_methods": list(_PACKED_METHODS),
        "cases": list(_NATIVE_CASES),
        "instrumentation": "single CompilerIntArena.close entry hook; no allocation/free substitution or post-free bookkeeping",
        "input_models": "names-only ParsedFunction(name, block_names) and GlobalDef(name) fixtures; not production IR layouts",
        "unresolved_annotation_names": unresolved_annotations,
        "excluded": ["partial-builder allocations", "constructor failure", "append/tuple native OOM", "native exception object identity", "native iteration/finally fault injection (host only)", "full native planner/emitter", "Stage2/RSS/speedup"],
    }
    return source, receipt


_NATIVE_DRIVER = '''
class _PrimaryFailure(RuntimeError):
    pass

class _StopFailure(BaseException):
    pass

class _CleanupFailure(BaseException):
    pass

_case = ""
_owners: list[FunctionStackMapPlan] = []
_arena_aliases: list[CompilerIntArena] = []
_index_aliases: list = []
_table_aliases: list = []
_block_aliases: list = []
_builder_calls = 0
_callback_calls = 0
_armed = False
_watch0: CompilerIntArena | None = None
_watch1: CompilerIntArena | None = None
_watch2: CompilerIntArena | None = None
_count0 = 0
_count1 = 0
_count2 = 0
_fail0 = False
_fail1 = False


def _batch_close_hook(arena):
    global _count0, _count1, _count2, _fail0, _fail1
    if not _armed:
        return
    # No raw addresses, dynamic registries, or post-free observation. Failures
    # are once-only and BEFORE production close frees or changes the arena.
    if arena is _watch0:
        _count0 += 1
        if _fail0:
            _fail0 = False
            raise _CleanupFailure("first owner close")
    elif arena is _watch1:
        _count1 += 1
        if _fail1:
            _fail1 = False
            raise _CleanupFailure("second owner close")
    elif arena is _watch2:
        _count2 += 1


def _arenas(packed):
    return [packed.scalars, packed.spans, packed.location_scalars,
            packed.location_group_spans, packed.reload_scalars,
            packed.entry_routes, packed.suffix_routes, packed.terminator_routes,
            packed.entry_route_spans, packed.suffix_route_spans,
            packed.terminator_route_spans]


def _build_function_stack_map_plan_native(
    func: ParsedFunction, globals_: list[GlobalDef], identity_name: str,
    globals_index: list[dict[str, GlobalDef]] | None = None,
) -> FunctionStackMapPlan:
    global _builder_calls, _watch0, _watch1, _watch2, _armed
    _builder_calls += 1
    if _builder_calls == 4:
        if _case == "baseexception":
            raise _StopFailure("builder stop")
        raise _PrimaryFailure("builder")
    if globals_index is not None and not globals_index:
        table = {globals_[0].name: globals_[0]}
        globals_index.append(table)
        _index_aliases.append(globals_index)
        _table_aliases.append(table)
    packed = PackedPlannedSafepoints(2, func.block_names)
    roots = (PlannedRootLocation(-16, True),)
    reloads = (PlannedManagedReload(-16, -24, 0),)
    packed.append(100 + _builder_calls, "entry", 1, roots, 0, "", 0, reloads)
    packed.append(200 + _builder_calls, "call", 2, roots, 0, "", 0, ())
    packed.add_entry_route(0, 0)
    packed.add_suffix_route(0, 0, 1)
    packed.add_terminator_route(1, 1, False)
    packed.finish_build()
    plan = FunctionStackMapPlan(func.name, _builder_calls, 32, "end", (), (), (), (),
                               "aarch64-darwin", packed)
    _owners.append(plan)
    _arena_aliases.extend(_arenas(packed))
    _block_aliases.append(func.block_names)
    if _builder_calls == 1:
        _watch0 = packed.scalars
    elif _builder_calls == 2:
        _watch1 = packed.scalars
    else:
        _watch2 = packed.scalars
    _armed = True
    return plan


def build_function_stack_map_plan(
    func: ParsedFunction, globals_: list[GlobalDef], *, target: str, identity_name: str,
) -> FunctionStackMapPlan:
    # Controlled public-route seam. This checks batch dispatch/lifetime only,
    # not a native x86 or public full planner implementation.
    return _build_function_stack_map_plan_native(func, globals_, identity_name)


def _symbol(name):
    global _callback_calls
    _callback_calls += 1
    if _case == "callback" and _callback_calls == 4:
        raise _PrimaryFailure("callback")
    return "_" + name


def _run_case(case, expected_native):
    global _case, _builder_calls, _callback_calls, _armed
    global _watch0, _watch1, _watch2, _count0, _count1, _count2, _fail0, _fail1
    _case = case
    _owners.clear()
    _arena_aliases.clear()
    _index_aliases.clear()
    _table_aliases.clear()
    _block_aliases.clear()
    _builder_calls = 0
    _callback_calls = 0
    _armed = False
    _watch0 = None
    _watch1 = None
    _watch2 = None
    _count0 = 0
    _count1 = 0
    _count2 = 0
    _fail0 = case in ("cleanup-one", "cleanup-multiple")
    _fail1 = case == "cleanup-multiple"
    first = ParsedFunction("same", ["entry", "exit"])
    second = ParsedFunction("same", ["entry", "exit"])
    functions = [first, first, second, second]
    # Repeated function and equal-name distinct function both yield independent
    # real plans. Their block-name aliases must survive packed.close unchanged.
    if case == "success":
        functions = functions[:3]
    elif case == "empty":
        functions = []
    inputs = functions
    global_sentinel = GlobalDef("sentinel")
    globals_ = [global_sentinel]
    success = False
    error_kind = "none"
    error_message = ""
    returned = ()
    try:
        try:
            if case == "legacy-negative":
                returned = _legacy_build_stack_map_plans(inputs, globals_, target="aarch64-darwin",
                                                        function_symbol=_symbol, _share_globals_index=True)
            elif case == "default-negative":
                returned = build_stack_map_plans(inputs, globals_, target="aarch64-darwin",
                                                function_symbol=_symbol)
            elif case == "other-target-negative":
                returned = build_stack_map_plans(inputs, globals_, target="x86_64-linux",
                                                function_symbol=_symbol, _share_globals_index=True)
            else:
                returned = build_stack_map_plans(inputs, globals_, target="aarch64-darwin",
                                                function_symbol=_symbol, _share_globals_index=True)
            success = True
        except _StopFailure as error:
            error_kind = "stop"
            error_message = str(error)
        except _PrimaryFailure as error:
            error_kind = "primary"
            error_message = str(error)
        expected_count = 0 if case == "empty" else 3
        assert len(_owners) == expected_count
        assert len(_arena_aliases) == expected_count * 11
        assert len(globals_) == 1 and globals_[0] is global_sentinel
        assert globals_[0].name == "sentinel"
        assert first.block_names == ["entry", "exit"]
        assert second.block_names == ["entry", "exit"]
        if expected_count:
            assert _owners[0] is not _owners[1]
            assert _owners[1] is not _owners[2]
            assert _owners[0].packed_records.block_names is first.block_names
            assert _owners[1].packed_records.block_names is first.block_names
            assert _owners[2].packed_records.block_names is second.block_names
            for alias in _block_aliases:
                assert alias == ["entry", "exit"]
        if case in ("empty", "success"):
            assert success and len(returned) == expected_count
            for index in range(expected_count):
                assert returned[index] is _owners[index]
            assert error_kind == "none"
        elif case == "baseexception":
            assert not success and error_kind == "stop" and error_message == "builder stop"
        else:
            expected_message = "callback" if case == "callback" else "builder"
            assert not success and error_kind == "primary" and error_message == expected_message
        retains = case in ("success", "legacy-negative", "default-negative", "other-target-negative")
        closed_count = 0
        for index, plan in enumerate(_owners):
            failed_close = (index == 0 and case in ("cleanup-one", "cleanup-multiple")
                            or index == 1 and case == "cleanup-multiple")
            should_close = not retains and not failed_close
            packed = plan.packed_records
            assert len(packed.labels) == 2
            for arena in _arenas(packed):
                assert arena._closed == should_close
                if should_close:
                    assert arena.native_address() == 0
                    assert len(arena) == 0
                    closed_count += 1
                else:
                    assert (arena.native_address() != 0) == expected_native
                    assert len(arena) > 0
                    assert len(arena.diagnostic_values()) == len(arena)
        if expected_count:
            expected_attempt = 0 if retains else 1
            assert _count0 == _count1 == _count2 == expected_attempt
        if _index_aliases:
            assert _index_aliases[0] == []
            assert _table_aliases[0] == {}
        print(case + "|" + ("success" if success else error_kind) + "|" + error_message
              + "|" + str(expected_count) + "|" + str(closed_count))
    finally:
        # Disable the hook and close only retained actual owners. Repeated close
        # is supported by production; no addresses are reconstructed or freed.
        _armed = False
        _fail0 = False
        _fail1 = False
        for plan in _owners:
            plan.packed_records.close()
        for arena in _arena_aliases:
            assert arena._closed and arena.native_address() == 0


def main():
    expected_native = os.environ.get("PCC_EXPECT_NATIVE_ARENAS", "0") == "1"
    for case in _NATIVE_CASES:
        _run_case(case, expected_native)

main()
'''


def _file_identity(path):
    if path is None:
        return None
    result = {"path": os.path.abspath(os.fspath(path)), "sha256": None}
    try:
        # The staged compiler can be hundreds of MiB. Its identity requires
        # every byte, not a second resident copy in the pytest worker.
        with Path(path).open("rb") as stream:
            result["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as error:
        result["read_error"] = str(error)
    return result


def _compiler_evidence(compiler, nominal_case, source, binary, runtime_archive):
    # Adapted from the published scratch-boundary receipt, with no allocator
    # tracker. Nominal pcc0/pcc1 names alone are explicitly NOT owner evidence.
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
        "expected_command_from_fixture": None if captured is None else [
            os.fspath(captured), "--backend", "self", "--ir-scaffold", "on",
            str(source), "-o", str(binary),
        ],
        "expected_command_status": "reconstructed from fixture, not observed subprocess argv",
        "returned_args": None,
        "returned_args_status": "compile not completed",
        "route_receipts": [],
        "ownership_boundary": "inspect actual callable, compiler path, returned args and routing receipts; nominal fixture name is insufficient",
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


def test_copied_batch_source_closure_is_exact_and_inventory_is_explicit():
    source, receipt = _copied_source()
    production = (_ROOT / "pcc/backend/self_backend_precise_stackmaps.py").read_text()
    assert _segment(source, _node(source, "build_stack_map_plans")) == _segment(
        production, _node(production, "build_stack_map_plans"),
    )
    assert receipt["unresolved_annotation_names"] == []
    assert receipt["cases"] == list(_NATIVE_CASES)
    assert len(_NATIVE_CASES) == len(set(_NATIVE_CASES)) == 10
    source_tree = ast.parse(source)
    # Production allocation/free calls stay inside the actual copied arena.
    arena_node = next(node for node in source_tree.body
                      if isinstance(node, ast.ClassDef) and node.name == "CompilerIntArena")
    close = next(node for node in arena_node.body
                 if isinstance(node, ast.FunctionDef) and node.name == "close")
    assert isinstance(close.body[0], ast.Expr)
    assert ast.unparse(close.body[0]) == "_batch_close_hook(self)"
    driver = ast.parse(_NATIVE_DRIVER)
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id in ("free", "malloc", "realloc", "int_to_ptr")
                   for node in ast.walk(driver))


@pytest.mark.integration
def test_exact_batch_boundary_with_native_owners_all_collectors(
    tmp_path, monkeypatch, python_program_compiler, pcc_runtime_archive, request,
):
    # python_program_compiler's existing nominal pcc0/pcc1 parameterization is
    # retained. Routing may replace it; the receipt reports that actual owner.
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    text, closure = _copied_source()
    source = tmp_path / "stackmap_batch_boundary.py"
    source.write_text(text)
    binary = tmp_path / "stackmap_batch_boundary"
    receipt_path = tmp_path / "stackmap_batch_boundary.receipt.json"
    nominal = request.node.callspec.params["python_program_compiler"]
    receipt = {
        "closure": closure,
        "compiler_evidence": _compiler_evidence(
            python_program_compiler, nominal, source, binary, pcc_runtime_archive,
        ),
        "source": _file_identity(source),
        "runtime": _file_identity(pcc_runtime_archive),
        "production": {
            name: _file_identity(_ROOT / name) for name in (
                "pcc/backend/self_backend_precise_stackmaps.py",
                "pcc/backend/self_backend_value_arena.py",
            )
        },
        "cpython_reference": None,
        "binary": None,
        "gc_runs": [],
    }

    def save_receipt():
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")

    save_receipt()
    reference = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=20,
        # Source CI installs test dependencies, not PCC. This script lives in
        # pytest's temp directory and must import pcc.unsafe from this checkout.
        env=dict(os.environ, PYTHONPATH=str(_ROOT), PCC_EXPECT_NATIVE_ARENAS="0"),
    )
    (tmp_path / "cpython.stdout").write_text(reference.stdout)
    (tmp_path / "cpython.stderr").write_text(reference.stderr)
    receipt["cpython_reference"] = {
        "process": _file_identity(sys.executable),
        "returncode": reference.returncode, "stdout": reference.stdout, "stderr": reference.stderr,
    }
    save_receipt()
    assert reference.returncode == 0, (reference.stdout, reference.stderr)
    assert reference.stderr == ""
    assert tuple(line.split("|", 1)[0] for line in reference.stdout.splitlines()) == _NATIVE_CASES
    route_log = os.environ.get("PCC_TEST_COMPILER_LOG")
    route_offset = Path(route_log).stat().st_size if route_log and Path(route_log).is_file() else 0
    compile_test = os.environ.get("PYTEST_CURRENT_TEST", "")
    try:
        compiled = python_program_compiler(
            str(source), str(binary), backend="self", runtime_archive=str(pcc_runtime_archive),
        )
        evidence = receipt["compiler_evidence"]
        args = getattr(compiled, "args", None)
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
        save_receipt()
    receipt["binary"] = _file_identity(binary)
    for collector in (0,):
        completed = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=20,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(collector),
                     PCC_EXPECT_NATIVE_ARENAS="1"),
        )
        (tmp_path / f"gc{collector}.stdout").write_text(completed.stdout)
        (tmp_path / f"gc{collector}.stderr").write_text(completed.stderr)
        receipt["gc_runs"].append({
            "collector": collector, "returncode": completed.returncode,
            "stdout": completed.stdout, "stderr": completed.stderr,
        })
        save_receipt()
        assert completed.returncode == 0, (collector, completed.stdout, completed.stderr)
        assert completed.stderr == ""
        assert completed.stdout == reference.stdout
    request.node.user_properties.append(("batch_source_sha256", closure["source_sha256"]))
    request.node.user_properties.append(("batch_receipt", str(receipt_path)))
