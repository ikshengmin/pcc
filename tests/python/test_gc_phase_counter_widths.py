"""Focused width proofs for existing GC step/pause telemetry.

Host tests execute complete source-extracted production functions with exact
signed 32/64-bit memory doubles and isolated configuration providers.
The ELF test uses host pcc, its owned self emitter, assembler and linker; it
executes production explicit-pause and increment bodies and telemetry getter
slices against the real state object. Only legacy ``int`` annotations in the
managed helpers are normalized to ``i64`` for that freestanding slice, and
the two private helpers receive test-only C-ABI export decorators. Their
production bodies are unchanged.

This is neither a full-runtime integration gate nor native-pcc1,
concurrency, scheduler-policy, timing-accuracy or performance evidence.
"""
from __future__ import annotations

import ast
import platform
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from pcc.frontends.python.codegen.runtime_abi import (
    FREESTANDING_GC_I32_GLOBALS,
    FREESTANDING_GC_I64_GLOBALS,
)
from tests.python.runtime_body_model import load_functions


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime/py"
STATE = RUNTIME / "freestanding_gc_state.py"
BACKEND = RUNTIME / "py_gc_backend.py"
SCHEDULER = RUNTIME / "freestanding_gc_incremental_concurrent_scheduler.py"
TELEMETRY = RUNTIME / "py_gc_telemetry.py"
ROOT_OPERATIONS = RUNTIME / "freestanding_gc_root_operations.py"
WIDE_METRICS = {
    "pcc_gc_metric_step": (5, "WORK_STEPS"),
    "pcc_gc_metric_max_pause_us": (7, "MAX_PAUSE_US"),
    "pcc_gc_metric_pause_count": (32, "PAUSE_COUNT"),
    "pcc_gc_metric_pause_sum_us": (33, "PAUSE_SUM_US"),
}
NARROW_COUNTERS = (
    "pcc_gc_metric_load",
    "pcc_gc_metric_safepoint",
)
NARROW_METRIC_IDS = (2, 3)
HISTOGRAM = tuple(f"pcc_gc_metric_pause_hist{i}" for i in range(4))


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _function(path, name):
    matches = [
        node for node in _tree(path).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(matches) == 1, (path, name)
    return matches[0]


def _global_name(node):
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "global_addr"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
    ):
        return node.args[0].value
    return None


def _definitions():
    definitions = {}
    for statement in _tree(STATE).body:
        if not isinstance(statement, ast.Expr):
            continue
        call = statement.value
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            continue
        if call.func.id not in ("define_global_i32", "define_global_i64"):
            continue
        name = ast.literal_eval(call.args[0])
        assert name not in definitions
        definitions[name] = (call.func.id, ast.literal_eval(call.args[1]))
    return definitions


def test_phase_counter_definitions_registry_and_metric_ids_agree():
    definitions = _definitions()
    header = (ROOT / "pcc/runtime/include/py_runtime.h").read_text(encoding="utf-8")
    for name, (metric, public_name) in WIDE_METRICS.items():
        assert definitions[name] == ("define_global_i64", 0)
        assert name in FREESTANDING_GC_I64_GLOBALS
        assert name not in FREESTANDING_GC_I32_GLOBALS
        assert re.search(rf"\bPCC_GC_COUNTER_{public_name}\s*=\s*{metric}\s*,", header)
    for name in NARROW_COUNTERS + HISTOGRAM:
        assert definitions[name] == ("define_global_i32", 0)
        assert name in FREESTANDING_GC_I32_GLOBALS
        assert name not in FREESTANDING_GC_I64_GLOBALS


def test_phase_counter_direct_accesses_use_declared_width():
    accesses = {name: set() for name in WIDE_METRICS}
    # The step increment resolves a slot through _counter_global rather than
    # directly naming it in a load/store. Its full-width write is exercised
    # by both execution tests below.
    for path in (BACKEND, SCHEDULER, TELEMETRY):
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id not in ("load_i32", "load_i64", "store_i32", "store_i64"):
                continue
            name = _global_name(node.args[0])
            if name not in accesses:
                continue
            assert node.func.id in ("load_i64", "store_i64"), (
                path.name, node.lineno, name, node.func.id,
            )
            accesses[name].add(node.func.id)
    assert accesses.pop("pcc_gc_metric_step") == {"load_i64"}
    assert all(kinds == {"load_i64", "store_i64"} for kinds in accesses.values())


class _Memory:
    """Exact signed loads and truncating stores, with declared object bounds."""

    def __init__(self):
        self.cells = {name: bytearray(4) for name in FREESTANDING_GC_I32_GLOBALS}
        self.cells.update({name: bytearray(8) for name in FREESTANDING_GC_I64_GLOBALS})
        for name, (kind, initial) in _definitions().items():
            width = 8 if kind == "define_global_i64" else 4
            self.cells[name] = bytearray(width)
            self.store(name, 0, initial, width)
        self.cells["pcc_gc_metric_shards"] = bytearray(64 * 128)

    def load(self, name, offset, width):
        cell = self.cells[name]
        assert 0 <= offset <= len(cell) - width, (name, offset, width)
        return int.from_bytes(cell[offset:offset + width], "little", signed=True)

    def store(self, name, offset, value, width):
        cell = self.cells[name]
        assert 0 <= offset <= len(cell) - width, (name, offset, width)
        encoded = (value & ((1 << (width * 8)) - 1)).to_bytes(width, "little")
        cell[offset:offset + width] = encoded

    def rmw_i64(self, operation, name, offset, delta, order):
        assert operation == "add" and order == "relaxed"
        before = self.load(name, offset, 8)
        self.store(name, offset, before + delta, 8)
        return before

    def get(self, name):
        return self.load(name, 0, len(self.cells[name]))

    def set(self, name, value):
        self.store(name, 0, value, len(self.cells[name]))


def _host_functions():
    memory = _Memory()
    noop = lambda *args: None
    namespace = {
        "i64": int,
        "global_addr": lambda name: name,
        "load_i32": lambda name, offset: memory.load(name, offset, 4),
        "load_i64": lambda name, offset: memory.load(name, offset, 8),
        "atomic_load_i64": lambda name, offset, order: memory.load(name, offset, 8),
        "store_i32": lambda name, offset, value: memory.store(name, offset, value, 4),
        "store_i64": lambda name, offset, value: memory.store(name, offset, value, 8),
        "atomic_store_i64": lambda name, offset, value, order: memory.store(name, offset, value, 8),
        "_atomic_i32_store": lambda name, value: memory.store(name, 0, value, 4),
        "gc_backend_current": lambda: 0,
        "pcc_gc_live_bytes_flush": noop,
        "pcc_gc_metric_allocation_count": lambda: sum(
            memory.load("pcc_gc_metric_shards", index * 128 + 8, 8)
            for index in range(64)
        ),
        "pcc_gc_metric_shard": lambda: 3 * 128,
        "atomic_rmw_i64": memory.rmw_i64,
        "pcc_gc_metric_store_count": lambda: sum(
            memory.load("pcc_gc_metric_shards", index * 128 + 16, 8)
            for index in range(64)
        ),
        "pcc_gc_metric_pin_balance": lambda: sum(
            memory.load("pcc_gc_metric_shards", index * 128, 8)
            for index in range(64)
        ),
        "pcc_gc_metric_shard_reset": lambda field: [
            memory.store("pcc_gc_metric_shards", index * 128 + field, 0, 8)
            for index in range(64)
        ],
    }
    groups = (
        (BACKEND, ("_counter_global", "_counter_inc")),
        (SCHEDULER, ("pcc_gc_record_explicit_pause",)),
        (TELEMETRY, ("pcc_gc_telemetry",)),
    )
    for path, names in groups:
        load_functions(path, names, namespace)
    return memory, namespace


def test_host_source_explicit_pause_accumulation_and_max_keep_high_bits():
    memory, functions = _host_functions()
    record = functions["pcc_gc_record_explicit_pause"]
    telemetry = functions["pcc_gc_telemetry"]
    memory.set("pcc_gc_metric_pause_count", (1 << 31) - 1)
    memory.set("pcc_gc_metric_pause_sum_us", (1 << 32) + 11)
    record(10, 10 + (1 << 31) + 9)
    assert telemetry(32) == 1 << 31
    assert telemetry(33) == (1 << 32) + (1 << 31) + 20
    assert telemetry(7) == (1 << 31) + 9
    record(10, 60)
    assert telemetry(7) == (1 << 31) + 9
    record(10, 10 + (1 << 32) + 23)
    assert telemetry(7) == (1 << 32) + 23
    record(10, 10)  # A valid zero-duration pause still contributes one us.
    assert telemetry(32) == (1 << 31) + 3
    assert telemetry(33) == (1 << 33) + (1 << 31) + 94
    assert [memory.get(name) for name in HISTOGRAM] == [2, 0, 0, 2]
    before = {name: memory.get(name) for name in WIDE_METRICS}
    record(0, 100)
    record(-1, 100)
    record(20, 19)
    assert {name: memory.get(name) for name in WIDE_METRICS} == before


def test_host_source_generic_counter_writers_keep_high_bits():
    memory, functions = _host_functions()
    increment = functions["_counter_inc"]
    telemetry = functions["pcc_gc_telemetry"]
    for metric, name in zip(NARROW_METRIC_IDS, NARROW_COUNTERS):
        memory.set(name, 10 + metric)
        increment(metric, 7)
        assert memory.get(name) == telemetry(metric) == 17 + metric
    increment(1, (1 << 32) + 7)
    assert telemetry(1) == (1 << 32) + 7
    assert memory.get("pcc_gc_metric_store") == 0
    memory.set("pcc_gc_metric_step", (1 << 31) - 1)
    increment(5, 1)
    assert telemetry(5) == 1 << 31
    # _counter_global historically routes every unknown ID, including
    # negatives, to the step slot. The width branch must match that rule.
    for metric in (-1, 6, 999):
        previous = memory.get("pcc_gc_metric_step")
        increment(metric, (1 << 32) + 3)
        assert telemetry(5) == previous + (1 << 32) + 3
    increment(5, -2)
    assert telemetry(5) == (1 << 31) + 3 * (1 << 32) + 7
    assert [memory.get(name) for name in NARROW_COUNTERS] == [19, 20]


def _raw_annotations(function):
    """Adapt only legacy machine-boundary annotations for the isolated probe."""
    annotations = [argument.annotation for argument in function.args.args]
    annotations.append(function.returns)
    annotations.extend(
        node.annotation for node in ast.walk(function) if isinstance(node, ast.AnnAssign)
    )
    for annotation in annotations:
        if isinstance(annotation, ast.Name) and annotation.id == "int":
            annotation.id = "i64"
    return function


def _native_slice_source():
    # Allocation/pin telemetry moved to the root-operations shard table. Keep
    # this isolated ELF's provider closure source-derived, without linking a
    # collector, scheduler, allocator, thread machinery or runtime archive.
    shards = [
        statement for statement in _tree(ROOT_OPERATIONS).body
        if isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Name)
        and statement.value.func.id == "define_global_i64_array"
        and statement.value.args
        and isinstance(statement.value.args[0], ast.Constant)
        and statement.value.args[0].value == "pcc_gc_metric_shards"
    ]
    assert len(shards) == 1, "metric shard storage declaration changed"
    shard_functions = [
        _function(ROOT_OPERATIONS, name) for name in (
            "pcc_gc_metric_shard_sum", "pcc_gc_metric_pin_balance",
            "pcc_gc_metric_allocation_count", "pcc_gc_metric_store_count",
            "pcc_gc_metric_shard",
        )
    ]
    functions = [
        _raw_annotations(_function(BACKEND, "_counter_global")),
        _raw_annotations(_function(BACKEND, "_counter_inc")),
        _function(SCHEDULER, "pcc_gc_tracing_record_pause"),
        _function(SCHEDULER, "pcc_gc_record_explicit_pause"),
    ]
    for helper in functions[:2]:
        assert not helper.decorator_list
        helper.decorator_list = [ast.Call(
            func=ast.Name(id="c_abi_export", ctx=ast.Load()),
            args=[ast.Constant(value="gc_phase_test" + helper.name)], keywords=[],
        )]
    telemetry = _function(TELEMETRY, "pcc_gc_telemetry")
    wanted = set(range(6)) | {7, 32, 33, 34, 35, 36, 37}
    branches = []
    for statement in telemetry.body:
        if not isinstance(statement, ast.If):
            continue
        test = statement.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "metric"
            and len(test.ops) == len(test.comparators) == 1
            and isinstance(test.ops[0], ast.Eq)
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value in wanted
        ):
            branches.append(statement)
    assert len(branches) == len(wanted), "telemetry dispatch shape changed"
    # Preserve the selected production dispatch statements verbatim; omit
    # initialization and unrelated queries, whose owners are outside this test.
    telemetry.body = branches + [ast.Return(value=ast.Constant(value=-1))]
    functions.append(telemetry)

    module = ast.fix_missing_locations(ast.Module(
        body=shards + shard_functions + functions, type_ignores=[],
    ))
    return (
        "from pcc import i64\n"
        "from pcc.extern import c_abi_export\n"
        "from pcc.unsafe import (global_addr, load_i32, load_i64, store_i32, store_i64, "
        "atomic_load_i64, atomic_store_i64, atomic_rmw_i64, define_global_i64_array, "
        "frame_address, ptr_to_int, logical_shift_right_i64)\n"
        "__pcc_freestanding__ = True\n\n"
        + ast.unparse(module) + "\n\n" + _NATIVE_PROBE
    )


_NATIVE_PROBE = '''@c_abi_export("gc_phase_width_probe")
def gc_phase_width_probe() -> i64:
    if pcc_gc_telemetry(5) != 0 or pcc_gc_telemetry(7) != 0:
        return 1
    if pcc_gc_telemetry(32) != 0 or pcc_gc_telemetry(33) != 0:
        return 2
    store_i64(global_addr("pcc_gc_metric_step"), 0, 2147483647)
    _counter_inc(5, 1)
    if pcc_gc_telemetry(5) != 2147483648:
        return 3
    _counter_inc(-1, 2147483648)
    _counter_inc(6, 19)
    if pcc_gc_telemetry(5) != 4294967315:
        return 4
    _counter_inc(999, -3)
    if pcc_gc_telemetry(5) != 4294967312:
        return 5
    # Seed distinct real shard fields: these metrics no longer use
    # _counter_inc. Check multi-shard reads, without relying on a reset API.
    store_i64(global_addr("pcc_gc_metric_shards"), 63 * 128 + 8, 31)
    _counter_inc(1, 32)
    _counter_inc(2, 33)
    _counter_inc(3, 34)
    store_i64(global_addr("pcc_gc_metric_shards"), 17 * 128, 35)
    if pcc_gc_telemetry(0) != 31 or pcc_gc_telemetry(1) != 32:
        return 6
    if pcc_gc_telemetry(2) != 33 or pcc_gc_telemetry(3) != 34:
        return 7
    if pcc_gc_telemetry(4) != 35 or pcc_gc_telemetry(5) != 4294967312:
        return 8
    store_i64(global_addr("pcc_gc_metric_pause_count"), 0, 2147483647)
    store_i64(global_addr("pcc_gc_metric_pause_sum_us"), 0, 4294967307)
    pcc_gc_record_explicit_pause(10, 2147483667)
    if pcc_gc_telemetry(32) != 2147483648:
        return 9
    if pcc_gc_telemetry(33) != 6442450964 or pcc_gc_telemetry(7) != 2147483657:
        return 10
    pcc_gc_record_explicit_pause(10, 60)
    if pcc_gc_telemetry(7) != 2147483657:
        return 11
    pcc_gc_record_explicit_pause(10, 4294967329)
    pcc_gc_record_explicit_pause(10, 10)
    if pcc_gc_telemetry(7) != 4294967319 or pcc_gc_telemetry(32) != 2147483651:
        return 12
    if pcc_gc_telemetry(33) != 10737418334:
        return 13
    if pcc_gc_telemetry(34) != 2 or pcc_gc_telemetry(37) != 2:
        return 14
    pcc_gc_record_explicit_pause(0, 100)
    pcc_gc_record_explicit_pause(20, 19)
    if pcc_gc_telemetry(32) != 2147483651:
        return 15
    store_i64(global_addr("pcc_gc_metric_shards"), 5 * 128 + 8, 1099511627779)
    store_i64(global_addr("pcc_gc_metric_shards"), 5 * 128, 1099511627779)
    _counter_inc(1, 1099511627779)
    if pcc_gc_telemetry(0) != 1099511627810 or pcc_gc_telemetry(1) != 1099511627811:
        return 16
    if pcc_gc_telemetry(4) != 1099511627814:
        return 17
    return 0
'''


def _linux_x86_64():
    return platform.system() == "Linux" and platform.machine().lower() in ("x86_64", "amd64")


@pytest.mark.pcc_gate(probe=_linux_x86_64)
def test_host_pcc_self_emitted_elf_phase_counters_keep_high_bits(tmp_path, monkeypatch):
    if not _linux_x86_64():
        pytest.fail("executing this owned ELF probe requires x86-64 Linux")
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file
    from pcc.frontends.python.pipeline import compile_python
    from pcc.tools.ir_to_obj import emit_object

    monkeypatch.setenv("PCC_IR_TO_OBJ_EMITTER", "pcc")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "gc_phase_width_probe.py"
    source.write_text(_native_slice_source(), encoding="utf-8")
    target = "x86_64-unknown-linux-gnu"
    startup = (
        ".intel_syntax noprefix\n.text\n.globl _start\n_start:\n"
        " call gc_phase_width_probe\n mov edi, eax\n mov eax, 60\n syscall\n"
    )
    objects = []
    # No runtime archive, native bootstrap, external compiler/assembler/linker,
    # or build subprocess is allowed even accidentally through this slice.
    with patch.object(subprocess, "Popen", side_effect=AssertionError("external build process")):
        for name, input_path in (("state", STATE), ("probe", source)):
            ir_path = tmp_path / (name + ".ll")
            compile_python(
                str(input_path), str(ir_path), emit_llvm_only=True,
                python_library=True,
                backend="self", target_triple=target,
            )
            blob = emit_object(ir_path.read_text(encoding="utf-8"), target_triple=target)
            (tmp_path / (name + ".o")).write_bytes(blob)
            objects.append(parse_relocatable(blob))
        symbols = {symbol.name: symbol for symbol in objects[0].symbols}
        for name in WIDE_METRICS:
            assert symbols[name].size == 8
            assert symbols[name].value % 8 == 0
        for name in NARROW_COUNTERS + HISTOGRAM:
            assert symbols[name].size == 4
        ranges = sorted((symbols[name].section_index, symbols[name].value,
                         symbols[name].value + symbols[name].size, name)
                        for name in (*WIDE_METRICS, *NARROW_COUNTERS, *HISTOGRAM))
        for previous, following in zip(ranges, ranges[1:]):
            if previous[0] == following[0]:
                assert previous[2] <= following[1], ("overlapping state symbols", previous, following)
        image = link_static_executable([*objects, assemble_file(startup)])
    executable = tmp_path / "gc_phase_width_probe"
    executable.write_bytes(image)
    executable.chmod(0o755)
    result = subprocess.run(
        [str(executable)], capture_output=True, timeout=10, env={"PATH": ""},
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b""), (
        "isolated phase-counter probe failed at numbered check", result,
    )
