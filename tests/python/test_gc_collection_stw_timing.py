"""Collection-window timing, separate from inclusive step/call measurements.

Execute the production public/automatic collection bodies over finite clock,
world-stop and collector providers. These tests cover timing ownership and
exit paths on five backend selections, not full GC semantics or throughput.
The existing phase-counter fixture supplies exact-width state and real reset.
"""
from __future__ import annotations

import ast
import platform
import re
import subprocess
from unittest.mock import patch

import pytest

from pcc.frontends.python.codegen.runtime_abi import (
    FREESTANDING_GC_I32_GLOBALS,
    FREESTANDING_GC_I64_GLOBALS,
    FREESTANDING_GC_CROSS_OBJECT_SIGNATURES,
)
from tests.python.test_gc_phase_counter_widths import (
    ROOT, RUNTIME, SCHEDULER, _definitions, _function, _host_functions,
)


METRICS = {
    "pcc_gc_metric_collection_stw_count": (119, "COUNT"),
    "pcc_gc_metric_collection_stw_sum_us": (120, "SUM_US"),
    "pcc_gc_metric_collection_stw_max_us": (121, "MAX_US"),
    "pcc_gc_metric_collection_stw_hist0": (122, "HIST_LT_100US"),
    "pcc_gc_metric_collection_stw_hist1": (123, "HIST_LT_1MS"),
    "pcc_gc_metric_collection_stw_hist2": (124, "HIST_LT_10MS"),
    "pcc_gc_metric_collection_stw_hist3": (125, "HIST_GE_10MS"),
}
RECORD = "pcc_gc_record_collection_stw_pause"


def install(namespace, path, *names):
    nodes = [_function(path, name) for name in names]
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)


def counter_functions():
    memory, namespace = _host_functions()

    def compare_exchange(name, offset, expected, replacement, success, failure):
        assert (success, failure) == ("relaxed", "relaxed")
        observed = memory.load(name, offset, 8)
        if observed == expected:
            memory.store(name, offset, replacement, 8)
        return observed

    namespace["atomic_cas_i64"] = compare_exchange
    return memory, namespace


def test_collection_metrics_have_distinct_wide_public_storage_and_provider():
    definitions = _definitions()
    header = (ROOT / "pcc/runtime/include/py_runtime.h").read_text()
    for name, (metric, suffix) in METRICS.items():
        assert definitions[name] == ("define_global_i64", 0)
        assert name in FREESTANDING_GC_I64_GLOBALS
        assert name not in FREESTANDING_GC_I32_GLOBALS
        assert re.search(rf"PCC_GC_COUNTER_COLLECTION_STW_{suffix}\s*=\s*{metric}\b", header)
    assert FREESTANDING_GC_CROSS_OBJECT_SIGNATURES[RECORD] == (
        ("c_int64", "c_int64"), "c_void",
    )


def test_collection_counter_boundaries_width_reset_and_step_separation():
    memory, ns = counter_functions()
    install(ns, SCHEDULER, RECORD)
    record = ns[RECORD]
    get = ns["pcc_gc_telemetry"]
    memory.set("pcc_gc_metric_pause_count", 17)
    memory.set("pcc_gc_metric_pause_sum_us", 99)
    memory.set("pcc_gc_metric_collection_stw_count", (1 << 31) - 1)
    memory.set("pcc_gc_metric_collection_stw_sum_us", 1 << 33)
    for duration in (0, 99, 100, 999, 1000, 9999, 10000, (1 << 32) + 3):
        record(10, 10 + duration)
    assert get(119) == (1 << 31) + 7
    assert get(120) == (1 << 33) + 22198 + (1 << 32) + 3
    assert get(121) == (1 << 32) + 3
    assert [get(index) for index in range(122, 126)] == [2, 2, 2, 2]
    assert (get(32), get(33)) == (17, 99)
    before = [get(index) for index in range(119, 126)]
    for start, end in ((0, 100), (-1, 100), (20, 19)):
        record(start, end)
    assert [get(index) for index in range(119, 126)] == before
    for name in METRICS:
        memory.set(name, (1 << 40) + 31)
    ns["pcc_gc_telemetry_reset"]()
    assert [get(index) for index in range(119, 126)] == [0] * 7
    record(10, 10)
    assert [get(index) for index in range(119, 126)] == [1, 1, 1, 1, 0, 0, 0]


@pytest.mark.parametrize("contending_value", [50, 150])
def test_collection_maximum_retries_changed_atomic_slot(contending_value):
    memory, ns = counter_functions()
    install(ns, SCHEDULER, RECORD)
    original = ns["atomic_cas_i64"]
    attempts = []

    def contend_once(name, offset, expected, replacement, success, failure):
        attempts.append(expected)
        if len(attempts) == 1:
            memory.store(name, offset, contending_value, 8)
        return original(name, offset, expected, replacement, success, failure)

    ns["atomic_cas_i64"] = contend_once
    ns[RECORD](10, 110)
    assert memory.get("pcc_gc_metric_collection_stw_max_us") == max(100, contending_value)
    assert attempts == ([0, 50] if contending_value == 50 else [0])


def test_collection_counter_accesses_are_atomic_and_read_scope_is_explicit():
    for path in (SCHEDULER, RUNTIME / "py_gc_telemetry.py", RUNTIME / "py_gc_backend.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            addresses = [argument for argument in node.args
                         if isinstance(argument, ast.Call)
                         and isinstance(argument.func, ast.Name)
                         and argument.func.id == "global_addr"
                         and argument.args[0].value in METRICS]
            if addresses:
                assert node.func.id in {"atomic_load_i64", "atomic_store_i64", "atomic_rmw_i64"}
    header = (ROOT / "pcc/runtime/include/py_runtime.h").read_text()
    assert "Direct pcc_gc_step/pcc_gc_collect_tracing stops" in header
    assert "collectors quiescent" in header


class CollectionWindow:
    def __init__(self, backend, *, retries=0, gc0_exit="normal"):
        self.memory, self.ns = counter_functions()
        self.memory.cells["pcc_diagnostics_runtime_log_fast_state"] = bytearray(4)
        self.backend = backend
        self.clock = 100
        self.events = []
        self.world = False
        self.retries = retries
        self.start = None
        self.recorded = []
        self.steps = 0
        self.pending = False
        self.memory.set("pcc_gc_backend_selected", backend)
        install(self.ns, SCHEDULER, RECORD)
        actual_record = self.ns[RECORD]

        def record(start, end):
            assert self.world, "collection timing must publish before resume"
            self.recorded.append((start, end))
            self.events.append("record")
            actual_record(start, end)

        self.ns.update({
            RECORD: record,
            "pcc_runtime_monotonic_us": lambda: self.clock,
            "pcc_platform_monotonic_us": lambda: self.clock,
            "pcc_gc_record_explicit_pause": self.ns["pcc_gc_tracing_record_pause"],
            "_gc_backend_fast": lambda: backend,
            "_pcc_gc_fire_callbacks": self.callback,
            "cstr": lambda value: value,
            "pcc_stop_the_world": self.stop,
            "pcc_resume_world": self.resume,
            "pcc_thread_safepoint": lambda: self.advance("retry", 41),
            "pcc_gc_reclaim_retired_index_tables": lambda: self.advance("reclaim", 3),
            "pcc_gc_begin_explicit_tracing_collect": self.begin,
            "pcc_gc_end_explicit_tracing_collect": self.end,
            "pcc_gc_step": self.step,
            "pcc_gc_sweep_owed": lambda: int(self.pending),
            "pcc_gc_collect_tracing": self.sweep,
            "pcc_gc_external_resource_poll": lambda: self.advance("poll", 101),
            "null": lambda: None,
            "pcc_gc_default_table_lock": lambda: self.advance("table_lock", 2),
            "pcc_gc_default_table_unlock": lambda: self.advance("table_unlock", 2),
            "pcc_gc_tracked_materialize": lambda: -1 if gc0_exit == "materialize" else 0,
            "pcc_gc_tracked_dematerialize": lambda: self.advance("dematerialize", 2),
            "pcc_gc_default_drain_deferred_nodes": lambda: self.advance("deferred", 2),
            "_finish_subset": lambda: self.advance("subset_finish", 2),
            "_prepare_subset": lambda pairs, count: count,
            "_recompute_reachability": lambda: self.advance("mark", 13),
            "py_weakref_invalidate_gc0_candidates": lambda: self.advance("weak", 7) or 0,
            "_maybe_finalize_unreachable": lambda unreachable, count: self.advance("finalizers", 11) or 0,
            "malloc": lambda size: None if gc0_exit == "allocation" else "scratch",
            "free": lambda ptr: self.advance("free", 2),
            "load_ptr": lambda ptr, offset: None,
            "atomic_load_i32": lambda name, offset, order: self.memory.load(name, offset, 4),
        })
        # A positive empty inventory selects the complete GC0 orchestration
        # path without exercising object geometry. Geometry/weakref/finalizer
        # semantics remain covered by their own collector suites.
        self.memory.set("py_gc_tracked_count", 0 if gc0_exit == "empty" else 1)
        self.memory.pointers["py_gc_head"] = None
        install(self.ns, RUNTIME / "py_obj.py", "pcc_gc_collect", "_gc_tracing_automatic_collect")
        install(self.ns, RUNTIME / "freestanding_gc_backend0_collector.py", "_collect", "py_gc_collect")

    def advance(self, name, duration):
        self.events.append(name)
        self.clock += duration

    def callback(self, phase, length):
        assert not self.world
        self.advance("callback_" + phase, 97)

    def stop(self):
        self.advance("stop", 53)
        if self.retries:
            self.retries -= 1
            return 1
        assert not self.world
        self.world = True
        self.start = self.clock
        return 0

    def resume(self):
        assert self.world
        self.world = False
        self.advance("resume", 101)
        return 0

    def begin(self):
        assert self.world
        self.memory.set("pcc_gc_explicit_collect_active", 1)
        self.advance("begin", 5)

    def end(self):
        assert self.world
        self.memory.set("pcc_gc_explicit_collect_active", 0)
        self.advance("end", 17)

    def step(self, budget):
        assert self.world and budget == 1024
        old = self.clock
        self.advance("step", 7)
        self.ns["pcc_gc_tracing_record_pause"](old, self.clock)
        self.steps += 1
        if self.steps == 1:
            self.pending = True
            self.memory.set("pcc_gc_tracing_finish_commits", 1)
            return 1
        return 0

    def sweep(self):
        assert self.world
        self.advance("sweep", 23)
        self.pending = False
        return 7

    def assert_one_window(self):
        assert len(self.recorded) == 1
        start, end = self.recorded[0]
        assert start == self.start and end > start
        assert self.events.index("record") < self.events.index("resume")
        get = self.ns["pcc_gc_telemetry"]
        assert [get(index) for index in (119, 120, 121)] == [1, end - start, end - start]
        assert sum(get(index) for index in range(122, 126)) == 1
        assert not self.world


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("retries", [0, 2])
def test_explicit_collection_counts_one_complete_window_on_five_backends(backend, retries):
    state = CollectionWindow(backend, retries=retries)
    assert state.ns["pcc_gc_collect"](0) == (0 if backend == 0 else 7)
    state.assert_one_window()
    assert state.ns["pcc_gc_telemetry"](32) == (1 if backend == 0 else 5)
    assert state.events[-2:] == ["poll", "callback_stop"]


@pytest.mark.parametrize("backend", range(1, 5))
@pytest.mark.parametrize("mark", [0, 1])
def test_automatic_collection_covers_mark_sweep_and_end_before_resume(backend, mark):
    state = CollectionWindow(backend)
    state.pending = not mark
    assert state.ns["_gc_tracing_automatic_collect"](mark) == 7
    state.assert_one_window()
    assert state.events.index("sweep") < state.events.index("end") < state.events.index("record")
    assert state.steps == mark


@pytest.mark.parametrize("exit_kind", ["empty", "materialize", "allocation", "wrong_backend"])
def test_gc0_early_exits_count_the_acquired_stop_once(exit_kind):
    state = CollectionWindow(0 if exit_kind != "wrong_backend" else 2, gc0_exit=exit_kind)
    pairs = "pairs" if exit_kind == "wrong_backend" else None
    assert state.ns["_collect"](pairs, 1) == 0
    state.assert_one_window()
    assert state.memory.get("py_gc_collecting") == 0


@pytest.mark.parametrize("backend", range(5))
def test_reentrant_collection_does_not_add_a_nested_window(backend):
    state = CollectionWindow(backend)
    if backend == 0:
        state.memory.set("py_gc_collecting", 1)
        assert state.ns["py_gc_collect"]() == 0
    else:
        state.memory.set("pcc_gc_explicit_collect_active", 1)
        assert state.ns["pcc_gc_collect"](0) == 0
        assert state.ns["_gc_tracing_automatic_collect"](1) == 0
    assert state.events == [] and state.recorded == []
    assert state.ns["pcc_gc_telemetry"](119) == 0


def _native_counter_source():
    record = _function(SCHEDULER, RECORD)
    telemetry = _function(RUNTIME / "py_gc_telemetry.py", "pcc_gc_telemetry")
    branches = []
    for statement in telemetry.body:
        if (isinstance(statement, ast.If)
                and isinstance(statement.test, ast.Compare)
                and isinstance(statement.test.left, ast.Name)
                and statement.test.left.id == "metric"
                and len(statement.test.comparators) == 1
                and isinstance(statement.test.comparators[0], ast.Constant)
                and statement.test.comparators[0].value in range(119, 126)):
            branches.append(statement)
    assert len(branches) == 7
    telemetry.body = branches + [ast.Return(value=ast.Constant(value=-1))]
    reset = _function(RUNTIME / "py_gc_backend.py", "pcc_gc_telemetry_reset")
    reset.body = [statement for statement in reset.body
                  if isinstance(statement, ast.Expr)
                  and isinstance(statement.value, ast.Call)
                  and statement.value.func.id == "atomic_store_i64"
                  and isinstance(statement.value.args[0], ast.Call)
                  and statement.value.args[0].args[0].value in METRICS]
    assert len(reset.body) == 7
    # Only the real counter dispatch/reset statements are extracted. Collector
    # configuration and unrelated reset owners are explicitly outside this ELF.
    module = ast.fix_missing_locations(ast.Module(
        body=[record, telemetry, reset], type_ignores=[],
    ))
    return ("from pcc import i64\nfrom pcc.extern import c_abi_export\n"
            "from pcc.unsafe import (global_addr, load_i64, store_i64, store_i32, "
            "atomic_load_i64, atomic_store_i64, atomic_rmw_i64, atomic_cas_i64)\n"
            "__pcc_freestanding__ = True\n" + ast.unparse(module) + "\n" + '''
@c_abi_export("collection_stw_probe")
def collection_stw_probe() -> i64:
    backend: i64 = 0
    while backend < 5:
        store_i32(global_addr("pcc_gc_backend_selected"), 0, backend)
        pcc_gc_telemetry_reset()
        pcc_gc_record_collection_stw_pause(10, 60)
        pcc_gc_record_collection_stw_pause(10, 10)
        if pcc_gc_telemetry(119) != 2 or pcc_gc_telemetry(120) != 51:
            return 1 + backend
        if pcc_gc_telemetry(121) != 50 or pcc_gc_telemetry(122) != 2:
            return 6 + backend
        if load_i64(global_addr("pcc_gc_metric_pause_count"), 0) != 0:
            return 11 + backend
        backend += 1
    store_i64(global_addr("pcc_gc_metric_collection_stw_count"), 0, 2147483647)
    store_i64(global_addr("pcc_gc_metric_collection_stw_sum_us"), 0, 8589934592)
    pcc_gc_record_collection_stw_pause(10, 4294967316)
    if pcc_gc_telemetry(119) != 2147483648:
        return 16
    if pcc_gc_telemetry(120) != 12884901898 or pcc_gc_telemetry(121) != 4294967306:
        return 17
    if pcc_gc_telemetry(125) != 1:
        return 18
    pcc_gc_record_collection_stw_pause(0, 10)
    pcc_gc_record_collection_stw_pause(20, 19)
    if pcc_gc_telemetry(119) != 2147483648:
        return 19
    pcc_gc_telemetry_reset()
    metric: i64 = 119
    while metric <= 125:
        if pcc_gc_telemetry(metric) != 0:
            return 20
        metric += 1
    return 0
''')


def _linux_x86_64():
    return platform.system() == "Linux" and platform.machine().lower() in ("x86_64", "amd64")


def test_owned_strict_collection_timing_objects_have_exact_symbol_closure(tmp_path, monkeypatch):
    """Compile both complete changed strict owners, without an external tool."""
    from pcc.backend.elf_x86_64 import SHN_UNDEF, STB_GLOBAL, parse_relocatable
    from pcc.frontends.python.pipeline import compile_python
    from pcc.tools.ir_to_obj import emit_object
    from tests.python import test_freestanding_gc_backend0_collector as collector
    from tests.python import test_freestanding_gc_incremental_concurrent_scheduler as scheduler

    monkeypatch.setenv("PCC_IR_TO_OBJ_EMITTER", "pcc")
    target = "x86_64-unknown-linux-gnu"
    # Keep the full-source compiles in one test so default xdist does not run
    # them concurrently. The existing LLVM/clang tests remain reference lanes.
    owners = ((collector.STRICT_SOURCE, collector.OWNED_SYMBOLS, collector.RAW_IMPORTS),
              (scheduler.STRICT_SOURCE, scheduler.OWNED_SYMBOLS,
               scheduler.RAW_FUNCTION_IMPORTS | scheduler.RAW_GLOBAL_IMPORTS))
    with patch.object(subprocess, "Popen", side_effect=AssertionError("external build process")):
        for source, expected_definitions, expected_imports in owners:
            ir = tmp_path / (source.stem + ".ll")
            compile_python(str(source), str(ir), emit_llvm_only=True,
                           python_library=True, backend="self", target_triple=target)
            obj = parse_relocatable(emit_object(ir.read_text(), target_triple=target))
            imports = {symbol.name for symbol in obj.symbols
                       if symbol.name and symbol.section_index == SHN_UNDEF}
            definitions = {symbol.name for symbol in obj.symbols
                           if symbol.binding == STB_GLOBAL and symbol.section_index != SHN_UNDEF}
            assert imports == expected_imports
            assert definitions == expected_definitions


@pytest.mark.pcc_gate(probe=_linux_x86_64)
def test_owned_native_collection_counter_body_on_five_backend_selections(tmp_path, monkeypatch):
    """Native counter ABI/width proof, not five complete native collectors."""
    if not _linux_x86_64():
        pytest.fail("this owned ELF requires x86-64 Linux")
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file
    from pcc.frontends.python.pipeline import compile_python
    from pcc.tools.ir_to_obj import emit_object

    monkeypatch.setenv("PCC_IR_TO_OBJ_EMITTER", "pcc")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "collection_stw_probe.py"
    source.write_text(_native_counter_source())
    target = "x86_64-unknown-linux-gnu"
    objects = []
    with patch.object(subprocess, "Popen", side_effect=AssertionError("external build process")):
        for name, input_path in (("state", RUNTIME / "freestanding_gc_state.py"), ("probe", source)):
            ir = tmp_path / (name + ".ll")
            compile_python(str(input_path), str(ir), emit_llvm_only=True,
                           python_library=True, backend="self", target_triple=target)
            objects.append(parse_relocatable(emit_object(ir.read_text(), target_triple=target)))
        symbols = {symbol.name: symbol for symbol in objects[0].symbols}
        assert all(symbols[name].size == 8 for name in METRICS)
        startup = (".intel_syntax noprefix\n.text\n.globl _start\n_start:\n"
                   " call collection_stw_probe\n mov edi, eax\n mov eax, 60\n syscall\n")
        image = link_static_executable([*objects, assemble_file(startup)])
    executable = tmp_path / "collection_stw_probe"
    executable.write_bytes(image)
    executable.chmod(0o755)
    result = subprocess.run([str(executable)], capture_output=True, timeout=10, env={"PATH": ""})
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b""), result
