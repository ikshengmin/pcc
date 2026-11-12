"""Contended graph-acquisition timing over production bodies and owned ELF.

Host tests reuse the queued-lock and container-lock models without replacing
the acquire/release algorithm. Controlled providers bridge the real recorder,
telemetry and reset to exact-width metric cells. These prove accounting and
local protocol ordering, not real-thread fairness or timing accuracy.

The ELF component uses host pcc and its owned self emitter/assembler/linker.
Complete high-substrate and pthread-kernel sources are compiled separately;
the executed image exercises the three-argument recorder ABI and all twelve
real state cells. This is not full-runtime, five-GC or native-pcc1 acceptance.

The separately marked integration case reuses the complete native queued-lock
harness and its FIFO/stop/registration/reuse/depth oracles under all five GC
selections. It adds classified wait counters, not whole-collector correctness
or performance acceptance.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import platform
import re
import subprocess
from unittest.mock import patch

import pytest

from pcc.frontends.python.codegen.runtime_abi import (
    FREESTANDING_GC_CROSS_OBJECT_SIGNATURES,
    FREESTANDING_GC_I32_GLOBALS,
    FREESTANDING_GC_I64_GLOBALS,
)
from tests.python.test_container_lock_stripes import (
    Abort,
    GRAPH_UNIT,
    HELD,
    Kernel,
    REGISTERED,
)
from tests.python.test_gc_phase_counter_widths import (
    ROOT,
    RUNTIME,
    _definitions,
    _function,
    _host_functions,
)
from tests.python.test_queued_graph_lock_model import kernel_model
from tests.python.test_queued_graph_lock_native import SOURCE as QUEUED_SOURCE


SOURCE = RUNTIME / "freestanding_runtime_high_substrate.py"
KERNEL = RUNTIME / "freestanding_thread_kernel_pthread.py"
RECORD = "pcc_gc_record_graph_lock_wait"
ACQUIRE = "pcc_thread_graph_lock_acquire_for"
SITES = ("other", "barrier", "allocation", "store")
FIELDS = ("count", "sum_us", "max_us")
METRICS = {
    f"pcc_gc_metric_graph_wait_{site}_{field}": (
        146 + site_id * 3 + field_id,
        f"{site.upper()}_{field.upper()}",
    )
    for site_id, site in enumerate(SITES)
    for field_id, field in enumerate(FIELDS)
}
WORD = 9000
TLS = "pcc_tls_thread_state_py"
GRAPH_MASK = (1 << 48) - (1 << 24)
NO_PARK_MASK = (1 << 24) - 1
QUEUED = 1 << 52
TRIPWIRE = 1 << 51


def _install(namespace, source, *names):
    nodes = [_function(source, name) for name in names]
    for node in nodes:
        node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)


def _counter_functions():
    memory, namespace = _host_functions()

    def compare_exchange(name, offset, expected, replacement, success, failure):
        assert (success, failure) == ("relaxed", "relaxed")
        observed = memory.load(name, offset, 8)
        if observed == expected:
            memory.store(name, offset, replacement, 8)
        return observed

    namespace["atomic_cas_i64"] = compare_exchange
    _install(namespace, SOURCE, RECORD)
    return memory, namespace


def _values(namespace, site):
    return [namespace["pcc_gc_telemetry"](146 + 3 * site + field) for field in range(3)]


def test_graph_wait_storage_enum_and_cross_object_signature_agree():
    definitions = _definitions()
    header = (ROOT / "pcc/runtime/include/py_runtime.h").read_text()
    assert len(METRICS) == 12
    for name, (metric, suffix) in METRICS.items():
        assert definitions[name] == ("define_global_i64", 0)
        assert name in FREESTANDING_GC_I64_GLOBALS
        assert name not in FREESTANDING_GC_I32_GLOBALS
        assert re.search(rf"PCC_GC_COUNTER_GRAPH_WAIT_{suffix}\s*=\s*{metric}\b", header)
    assert FREESTANDING_GC_CROSS_OBJECT_SIGNATURES[RECORD] == (("c_int64",) * 3, "c_void")
    assert FREESTANDING_GC_CROSS_OBJECT_SIGNATURES["pcc_py_gc_minor_graph_lock_for"] == (
        ("c_int64",), "c_void",
    )
    record = _function(SOURCE, RECORD)
    assert ast.literal_eval(record.decorator_list[0].args[0]) == RECORD


def test_graph_wait_signature_literals_preserve_the_bootstrap_entry_limit():
    module = ast.parse((ROOT / "pcc/frontends/python/codegen/runtime_abi.py").read_text())
    chunks = [node for node in module.body if isinstance(node, ast.FunctionDef)
              and node.name.startswith(("_cross_object_signatures_part_",
                                        "_runtime_signatures_part_"))]
    assert chunks
    entries = {}
    for chunk in chunks:
        dictionaries = [node for node in ast.walk(chunk) if isinstance(node, ast.Dict)]
        assert len(dictionaries) == 1
        keys = [ast.literal_eval(key) for key in dictionaries[0].keys]
        assert len(keys) <= 50, (chunk.name, len(keys))
        for key in keys:
            entries.setdefault(key, []).append(chunk.name)
    # Existing aliases (for example py_decref) predate this mechanism. Each
    # new graph-wait export must be present exactly once in a bounded chunk.
    for name in (RECORD, ACQUIRE):
        assert entries[name] == ["_cross_object_signatures_part_12"]
    assert entries["pcc_py_gc_minor_graph_lock_for"] == [
        "_runtime_signatures_part_3", "_cross_object_signatures_part_12",
    ]


@pytest.mark.parametrize("site", range(4), ids=SITES)
def test_graph_wait_high_bits_zero_duration_and_class_isolation(site):
    memory, namespace = _counter_functions()
    names = [f"pcc_gc_metric_graph_wait_{SITES[site]}_{field}" for field in FIELDS]
    initial = [(1 << 31) - 1, (1 << 40) + 3, (1 << 32) + 7]
    for name, value in zip(names, initial):
        memory.set(name, value)
    writes = []

    def add(operation, name, offset, delta, order):
        writes.append((operation, name, offset, delta, order))
        return memory.rmw_i64(operation, name, offset, delta, order)

    namespace["atomic_rmw_i64"] = add
    elapsed = (1 << 33) + 9
    namespace[RECORD](site, 10, 10 + elapsed)
    namespace[RECORD](site, 10, 10)
    assert writes == [
        ("add", names[0], 0, 1, "relaxed"),
        ("add", names[1], 0, elapsed, "relaxed"),
        ("add", names[0], 0, 1, "relaxed"),
        ("add", names[1], 0, 0, "relaxed"),
    ]
    assert _values(namespace, site) == [initial[0] + 2, initial[1] + elapsed, elapsed]
    for other in set(range(4)) - {site}:
        assert _values(namespace, other) == [0, 0, 0]


@pytest.mark.parametrize("site", range(4), ids=SITES)
def test_graph_wait_invalid_clock_still_counts_contention_without_time(site):
    _, namespace = _counter_functions()
    for start, end in ((0, 100), (-1, 100), (20, 19), (1, -1)):
        namespace[RECORD](site, start, end)
    assert _values(namespace, site) == [4, 0, 0]
    namespace[RECORD](site, 1, 1)
    assert _values(namespace, site) == [5, 0, 0]


def test_graph_wait_unknown_classes_are_other_and_real_reset_clears_high_words():
    memory, namespace = _counter_functions()
    for site in (-1, 4, 1 << 32):
        namespace[RECORD](site, 10, 17)
    assert _values(namespace, 0) == [3, 21, 7]
    assert all(_values(namespace, site) == [0, 0, 0] for site in range(1, 4))
    for name, (metric, _) in METRICS.items():
        memory.set(name, (1 << 44) + metric)
    for name, (metric, _) in METRICS.items():
        assert namespace["pcc_gc_telemetry"](metric) == (1 << 44) + metric, name
    namespace["pcc_gc_telemetry_reset"]()
    assert all(memory.get(name) == 0 for name in METRICS)
    for site in range(4):
        namespace[RECORD](site, 1, 1)
        assert _values(namespace, site) == [1, 0, 0]


@pytest.mark.parametrize("site", range(4), ids=SITES)
@pytest.mark.parametrize("competitor", [50, 150])
def test_graph_wait_maximum_retries_without_overwriting_a_larger_value(site, competitor):
    memory, namespace = _counter_functions()
    original = namespace["atomic_cas_i64"]
    name = f"pcc_gc_metric_graph_wait_{SITES[site]}_max_us"
    attempts = []

    def contend_once(slot, offset, expected, replacement, success, failure):
        assert slot == name and replacement == 100
        attempts.append(expected)
        if len(attempts) == 1:
            memory.set(name, competitor)
        return original(slot, offset, expected, replacement, success, failure)

    namespace["atomic_cas_i64"] = contend_once
    namespace[RECORD](site, 10, 110)
    assert _values(namespace, site) == [1, 100, max(100, competitor)]
    assert attempts == ([0, 50] if competitor == 50 else [0])


def test_graph_wait_read_and_reset_are_individually_atomic_i64_slots():
    for path, operation in (
        (RUNTIME / "py_gc_telemetry.py", "atomic_load_i64"),
        (RUNTIME / "py_gc_backend.py", "atomic_store_i64"),
    ):
        observed = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            for argument in node.args:
                if (isinstance(argument, ast.Call) and isinstance(argument.func, ast.Name)
                        and argument.func.id == "global_addr"
                        and argument.args[0].value in METRICS):
                    assert isinstance(node.func, ast.Name) and node.func.id == operation
                    assert ast.literal_eval(node.args[-1]) == "relaxed"
                    observed.add(argument.args[0].value)
        assert observed == set(METRICS)
    # The recorder must stay leaf-only while graph/no-park ownership is live.
    record_body = ast.Module(body=_function(SOURCE, RECORD).body, type_ignores=[])
    calls = {node.func.id for node in ast.walk(record_body)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert calls <= {"global_addr", "atomic_rmw_i64", "atomic_load_i64", "atomic_cas_i64", "i64"}
    header = (ROOT / "pcc/runtime/include/py_runtime.h").read_text()
    contract = header.split("PCC_GC_COUNTER_GRAPH_WAIT_OTHER_COUNT", 1)[0].rsplit("/*", 1)[-1]
    assert "not a joint snapshot" in contract
    assert "collectors and mutators are quiescent" in contract
    assert "not pure spin" in contract


def test_classified_callsites_name_the_outer_acquisition_operation():
    expected = {
        ("py_gc_backend.py", "pcc_gc_pointer_register"): [2],
        ("py_gc_backend.py", "pcc_gc_note_object_allocated_sized"): [2] * 5,
        # A full staging ring registers its batch for the allocation.
        ("freestanding_gc_alloc_stage.py", "pcc_gc_alloc_stage_note"): [2],
        ("freestanding_gc_zpage_allocation.py", "pcc_gc_backend4_try_zpage_alloc"): [2],
        ("py_obj.py", "pcc_gc_publish_initialized"): [2],
        ("py_obj.py", "pcc_gc_store_ptr"): [3],
        ("py_obj.py", "pcc_gc_store_field_ptr"): [3],
        ("py_obj.py", "pcc_gc_store_field_ptr_if_empty"): [3],
    }
    actual = {}
    for path in RUNTIME.glob("*.py"):
        for function in ast.parse(path.read_text()).body:
            if not isinstance(function, ast.FunctionDef):
                continue
            calls = [node for node in ast.walk(function)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                     and node.func.id == "pcc_py_gc_minor_graph_lock_for"]
            if calls:
                assert all(len(call.args) == 1 and not call.keywords for call in calls)
                actual[path.name, function.name] = [ast.literal_eval(call.args[0]) for call in calls]
    assert actual == expected


class _TimedKernel:
    def __init__(self, allocations=(), *, outer_depth=0, release_on_poll=1):
        self.ns, self.memory, self.events = kernel_model(allocations)
        _install(self.ns, KERNEL, ACQUIRE)
        self.counter_memory, self.counters = _counter_functions()
        self.memory[TLS, 0] = REGISTERED + outer_depth
        self.now = 100
        self.clock_reads = []
        self.clock_states = []
        self.records = []
        self.polls = 0
        self.timeline = []
        compare = self.ns["atomic_cas_i32"]

        def cas(*args):
            observed = compare(*args)
            self.now += 7
            self.timeline.append(("cas", observed))
            return observed

        def clock():
            self.clock_reads.append(self.now)
            self.clock_states.append(self.memory[TLS, 0])
            self.timeline.append(("clock", self.now, self.memory.get((WORD, 0), 0)))
            return self.now

        def poll():
            self.polls += 1
            self.timeline.append(("poll", self.memory[TLS, 0]))
            self.now += 100
            if self.polls == release_on_poll:
                self.memory[WORD, 0] = 0
                self.memory["pcc_thread_stop_requested", 0] = 0

        allocate = self.ns["malloc"]

        def malloc(size):
            self.now += 11
            return allocate(size)

        queue = self.ns["_graph_wait_acquire"]

        def acquire_queue(word):
            self.now += 200
            return queue(word)

        def record(site, start, end):
            assert self.memory[WORD, 0] == 1
            assert self.memory[TLS, 0] & GRAPH_MASK == GRAPH_UNIT
            assert self.memory[TLS, 0] & NO_PARK_MASK == outer_depth + 1
            self.records.append((site, start, end))
            self.counters[RECORD](site, start, end)

        self.ns.update(atomic_cas_i32=cas, pcc_platform_monotonic_us=clock,
                       pcc_thread_safepoint=poll, malloc=malloc,
                       _graph_wait_acquire=acquire_queue,
                       pcc_gc_record_graph_lock_wait=record)

    def acquire(self, site):
        self.ns[ACQUIRE](WORD, site)

    def release(self):
        return self.ns["pcc_thread_graph_lock_release"](WORD)


@pytest.mark.parametrize("site", range(4), ids=SITES)
def test_fast_and_recursive_acquisitions_do_not_read_clock_or_record(site):
    model = _TimedKernel()

    def forbidden(*args):
        pytest.fail("fast or recursive acquisition entered timing/queue path")

    model.ns.update(pcc_platform_monotonic_us=forbidden,
                    pcc_gc_record_graph_lock_wait=forbidden,
                    _graph_wait_acquire=forbidden, malloc=forbidden,
                    global_load_ptr=forbidden)
    model.acquire(site)
    model.acquire((site + 1) % 4)
    assert model.memory[TLS, 0] == REGISTERED + 2 * GRAPH_UNIT + 1
    assert model.timeline == [("cas", 0)]
    assert model.release() == 0 and model.memory[WORD, 0] == 1
    assert model.release() == 0 and model.memory[WORD, 0] == 0
    assert model.memory[TLS, 0] == REGISTERED
    assert all(_values(model.counters, index) == [0, 0, 0] for index in range(4))


@pytest.mark.parametrize("site", range(4), ids=SITES)
@pytest.mark.parametrize("outer_depth,tripwire", [(0, False), (1, False), (0, True)])
def test_queue_wait_includes_pending_stop_and_records_once_after_ownership(site, outer_depth, tripwire):
    model = _TimedKernel((10001, 20001), outer_depth=outer_depth)
    model.memory[WORD, 0] = model.memory["pcc_thread_stop_requested", 0] = 1
    model.acquire(site)
    assert model.clock_reads == [107, 436]
    assert model.clock_states == [REGISTERED + outer_depth, REGISTERED + QUEUED + outer_depth + 1]
    assert model.timeline[:3] == [("cas", 1), ("clock", 107, 1), ("poll", REGISTERED + outer_depth)]
    assert model.timeline[-1] == ("clock", 436, 1)
    assert model.records == [(site, 107, 436)]
    assert _values(model.counters, site) == [1, 329, 329]
    node = model.memory["pcc_tls_graph_wait_node_py", 0]
    context = model.memory[node, 16]
    assert node % 128 == context % 128 == 0
    assert model.memory[context, 16] == node
    assert model.memory[TLS, 0] == REGISTERED + QUEUED + GRAPH_UNIT + outer_depth + 1
    model.acquire((site + 1) % 4)
    assert model.memory[TLS, 0] & GRAPH_MASK == 2 * GRAPH_UNIT
    assert model.records == [(site, 107, 436)] and model.clock_reads == [107, 436]
    assert model.release() == 0 and model.memory[WORD, 0] == 1
    if tripwire:
        model.memory[TLS, 0] |= TRIPWIRE
    assert model.release() == int(tripwire)
    assert model.memory[WORD, 0] == model.memory[context, 16] == model.memory[node, 16] == 0
    assert model.memory[TLS, 0] == REGISTERED + outer_depth + int(tripwire)
    if tripwire:
        model.ns["pcc_thread_no_park_exit"]()
    assert model.memory[TLS, 0] == REGISTERED + outer_depth
    assert model.records == [(site, 107, 436)]


@pytest.mark.parametrize("allocations,allocation_time", [((), 11), ((10001,), 22)])
@pytest.mark.parametrize("site", range(4), ids=SITES)
def test_node_or_context_oom_fallback_retries_are_one_contended_wait(allocations, allocation_time, site):
    model = _TimedKernel(allocations, release_on_poll=3)
    model.memory[WORD, 0] = model.memory["pcc_thread_stop_requested", 0] = 1
    model.acquire(site)
    elapsed = 300 + 200 + allocation_time + 3 * 7
    assert model.polls == 3
    assert model.clock_reads == [107, 107 + elapsed]
    assert model.clock_states == [REGISTERED, REGISTERED]
    assert model.records == [(site, 107, 107 + elapsed)]
    assert _values(model.counters, site) == [1, elapsed, elapsed]
    assert model.memory[TLS, 0] == REGISTERED + GRAPH_UNIT + 1
    assert model.release() == 0
    assert model.memory[WORD, 0] == 0 and model.memory[TLS, 0] == REGISTERED


def test_legacy_and_typed_wrappers_pass_other_or_the_requested_site_directly():
    calls = []
    namespace = {"i64": int, "global_addr": lambda name: name,
                 ACQUIRE: lambda *args: calls.append(args)}
    _install(namespace, SOURCE, "pcc_py_gc_minor_graph_lock", "pcc_py_gc_minor_graph_lock_for")
    namespace["pcc_py_gc_minor_graph_lock"]()
    namespace["pcc_py_gc_minor_graph_lock_for"](3)
    _install(namespace, KERNEL, "pcc_thread_graph_lock_acquire")
    namespace["pcc_thread_graph_lock_acquire"](WORD)
    assert calls == [("g_pcc_py_gc_minor_graph_lock", 0), ("g_pcc_py_gc_minor_graph_lock", 3), (WORD, 0)]


def test_typed_acquisition_preserves_container_lock_order_without_reading_clock():
    kernel = Kernel()
    _install(kernel.ns, KERNEL, ACQUIRE)

    def forbidden(*args):
        pytest.fail("lock-order rejection or uncontended acquire read the clock")

    kernel.ns["pcc_platform_monotonic_us"] = forbidden
    kernel.ns[RECORD] = forbidden
    kernel.lock(0x10000)
    before = kernel.state
    with pytest.raises(Abort):
        kernel.ns[ACQUIRE](("g_pcc_py_gc_minor_graph_lock", 0), 3)
    assert kernel.state == before == REGISTERED | HELD | 1
    kernel.unlock(0x10000)
    kernel.ns[ACQUIRE](("g_pcc_py_gc_minor_graph_lock", 0), 3)
    assert kernel.state == REGISTERED | GRAPH_UNIT | 1
    kernel.lock(0x10000)
    assert kernel.state == REGISTERED | GRAPH_UNIT | HELD | 2
    kernel.unlock(0x10000)
    assert kernel.state == REGISTERED | GRAPH_UNIT | 1


def _native_record_source():
    return ("from pcc import i64\nfrom pcc.extern import c_abi_export\n"
            "from pcc.unsafe import global_addr, atomic_rmw_i64, atomic_load_i64, atomic_cas_i64\n"
            "__pcc_freestanding__ = True\n"
            + ast.unparse(_function(SOURCE, RECORD)) + "\n")


def _native_probe_source():
    telemetry = _function(RUNTIME / "py_gc_telemetry.py", "pcc_gc_telemetry")
    branches = [statement for statement in telemetry.body
                if isinstance(statement, ast.If) and isinstance(statement.test, ast.Compare)
                and isinstance(statement.test.left, ast.Name) and statement.test.left.id == "metric"
                and len(statement.test.comparators) == 1
                and isinstance(statement.test.comparators[0], ast.Constant)
                and statement.test.comparators[0].value in range(146, 158)]
    assert len(branches) == 12
    telemetry.body = branches + [ast.Return(value=ast.Constant(value=-1))]
    reset = _function(RUNTIME / "py_gc_backend.py", "pcc_gc_telemetry_reset")
    reset.body = [statement for statement in reset.body
                  if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
                  and isinstance(statement.value.func, ast.Name)
                  and statement.value.func.id == "atomic_store_i64"
                  and isinstance(statement.value.args[0], ast.Call)
                  and statement.value.args[0].args[0].value in METRICS]
    assert len(reset.body) == 12
    module = ast.fix_missing_locations(ast.Module(body=[telemetry, reset], type_ignores=[]))
    lines = ["@c_abi_export('graph_wait_probe')", "def graph_wait_probe() -> i64:",
             "    pcc_gc_telemetry_reset()"]
    for site, name in enumerate(SITES):
        initial = [(1 << 31) - 1, (1 << 40) + 3, (1 << 32) + 7]
        for field, value in zip(FIELDS, initial):
            lines.append(f'    store_i64(global_addr("pcc_gc_metric_graph_wait_{name}_{field}"), 0, {value})')
        lines += [f"    record({site}, 10, 8589934611)", f"    record({site}, 10, 10)",
                  f"    record({site}, 0, 100)", f"    record({site}, -1, 100)",
                  f"    record({site}, 20, 19)"]
    lines += ["    record(-1, 10, 17)", "    record(4, 10, 17)", "    record(4294967296, 10, 17)"]
    for site in range(4):
        expected = [(1 << 31) + 4 + (3 if site == 0 else 0),
                    (1 << 40) + 3 + (1 << 33) + 9 + (21 if site == 0 else 0),
                    (1 << 33) + 9]
        for field, value in enumerate(expected):
            metric = 146 + site * 3 + field
            lines += [f"    if pcc_gc_telemetry({metric}) != {value}:", f"        return {metric - 145}"]
    lines += ["    pcc_gc_telemetry_reset()", "    metric: i64 = 146",
              "    while metric <= 157:", "        if pcc_gc_telemetry(metric) != 0:",
              "            return 13", "        metric += 1"]
    for site in range(4):
        lines += [f"    record({site}, 10, 10)",
                  f"    if pcc_gc_telemetry({146 + site * 3}) != 1:", "        return 14",
                  f"    if pcc_gc_telemetry({147 + site * 3}) != 0:", "        return 15",
                  f"    if pcc_gc_telemetry({148 + site * 3}) != 0:", "        return 16"]
    lines += ["    return 0"]
    return ("from pcc import i64\n"
            "from pcc.extern import c_abi_export, c_int64, c_void, extern\n"
            "from pcc.unsafe import global_addr, store_i64, atomic_load_i64, atomic_store_i64\n"
            "__pcc_freestanding__ = True\n"
            f'record = extern("{RECORD}", (c_int64, c_int64, c_int64), c_void)\n'
            + ast.unparse(module) + "\n" + "\n".join(lines) + "\n")


def _linux_x86_64():
    return platform.system() == "Linux" and platform.machine().lower() in ("x86_64", "amd64")


@pytest.mark.pcc_gate(probe=_linux_x86_64)
def test_owned_graph_wait_abi_state_reset_and_complete_owner_objects(tmp_path, monkeypatch):
    """Keep complete-source compiles serial within this one xdist item."""
    if not _linux_x86_64():
        pytest.fail("this owned ELF requires x86-64 Linux")
    from pcc.backend.elf_x86_64 import (
        SHN_UNDEF,
        STB_GLOBAL,
        link_static_executable,
        parse_relocatable,
    )
    from pcc.backend.x86_64_asm_driver import assemble_file
    from pcc.frontends.python.pipeline import compile_python
    from pcc.tools.ir_to_obj import emit_object

    monkeypatch.setenv("PCC_IR_TO_OBJ_EMITTER", "pcc")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    target = "x86_64-unknown-linux-gnu"
    record = tmp_path / "graph_wait_record.py"
    record.write_text(_native_record_source())
    probe = tmp_path / "graph_wait_probe.py"
    probe.write_text(_native_probe_source())

    def compile_object(name, source):
        ir = tmp_path / (name + ".ll")
        compile_python(str(source), str(ir), emit_llvm_only=True,
                       python_library=True, backend="self", target_triple=target)
        return parse_relocatable(emit_object(ir.read_text(), target_triple=target))

    def imports(obj):
        return {symbol.name for symbol in obj.symbols if symbol.name and symbol.section_index == SHN_UNDEF}

    def exports(obj):
        return {symbol.name for symbol in obj.symbols
                if symbol.binding == STB_GLOBAL and symbol.section_index != SHN_UNDEF}

    with patch.object(subprocess, "Popen", side_effect=AssertionError("external build process")):
        high = compile_object("complete_high_substrate", SOURCE)
        previous_poll_policy = os.environ.get("PCC_IMPLICIT_THREAD_SAFEPOINTS")
        with monkeypatch.context() as kernel_settings:
            # The kernel owns explicit polls. Match owned_runtime_build and
            # the Makefile without changing the policy for other owners.
            kernel_settings.setenv("PCC_IMPLICIT_THREAD_SAFEPOINTS", "0")
            kernel = compile_object("complete_thread_kernel", KERNEL)
        assert os.environ.get("PCC_IMPLICIT_THREAD_SAFEPOINTS") == previous_poll_policy
        kernel_ir = (tmp_path / "complete_thread_kernel.ll").read_text()
        acquire_bodies = re.findall(
            rf'^define\b[^\n]*@"?{ACQUIRE}"?\([^\n]*\).*?^}}',
            kernel_ir, flags=re.MULTILINE | re.DOTALL,
        )
        assert len(acquire_bodies) == 1
        acquire_ir = acquire_bodies[0]
        assert "pcc_thread_stop_requested" not in acquire_ir
        assert "thread.safepoint." not in acquire_ir
        assert "pcc_thread_safepoint_gate" not in acquire_ir
        assert len(re.findall(
            r'\bcall\s+void\s+(?:\(\)\s+)?@"?pcc_thread_safepoint"?\(\)', acquire_ir,
        )) == 2
        assert {RECORD, "pcc_py_gc_minor_graph_lock", "pcc_py_gc_minor_graph_lock_for"} <= exports(high)
        assert {ACQUIRE, "pcc_thread_graph_lock_acquire", "pcc_thread_graph_lock_release"} <= exports(kernel)
        assert set(METRICS) | {ACQUIRE} <= imports(high)
        assert {RECORD, "pcc_platform_monotonic_us"} <= imports(kernel)
        assert "pcc_thread_graph_lock_acquire" not in imports(high)
        assert not any(name.startswith(("Py", "pcc_py_call", "pcc_python_"))
                       for name in imports(high) | imports(kernel))
        state = compile_object("state", RUNTIME / "freestanding_gc_state.py")
        symbols = {symbol.name: symbol for symbol in state.symbols}
        assert all(symbols[name].size == 8 for name in METRICS)
        helper = compile_object("record", record)
        assert imports(helper) == set(METRICS)
        caller = compile_object("probe", probe)
        assert imports(caller) == set(METRICS) | {RECORD}
        startup = (".intel_syntax noprefix\n.text\n.globl _start\n_start:\n"
                   " call graph_wait_probe\n mov edi, eax\n mov eax, 60\n syscall\n")
        image = link_static_executable([state, helper, caller, assemble_file(startup)])
    executable = tmp_path / "graph_wait_probe"
    executable.write_bytes(image)
    executable.chmod(0o755)
    result = subprocess.run([str(executable)], capture_output=True, timeout=10, env={"PATH": ""})
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b""), result


def _native_threaded_source():
    # Reuse the entire original oracle, including its real FIFO queue witness,
    # pending-stop admission, late registration, 1,200 reuse rounds and depth
    # checks. Only the already-proven queued acquisitions get distinct sites.
    original_acquire = """static void *queued_once(void *argument) {
    struct Worker *worker = (struct Worker *)argument;
    pcc_py_gc_minor_graph_lock();"""
    classified_acquire = """static void *queued_once(void *argument) {
    struct Worker *worker = (struct Worker *)argument;
    pcc_py_gc_minor_graph_lock_for(worker->id + 1);"""
    assert QUEUED_SOURCE.count(original_acquire) == 1
    source = QUEUED_SOURCE.replace(original_acquire, classified_acquire, 1)
    original_print = '    printf("QUEUED_GRAPH_OK gc=%d fifo=012 reuse=%lld\\n", backend, (long long)counter);'
    checks = """    /* All workers are joined: read individually atomic slots quiescently.
       Internal calls may also be classified, so require lower bounds rather
       than an exact count or a zero OTHER bucket. A valid zero wait is legal. */
    for (int site = 1; site <= 3; site++) {
        int metric = PCC_GC_COUNTER_GRAPH_WAIT_BARRIER_COUNT + (site - 1) * 3;
        int64_t count = pcc_gc_telemetry(metric);
        int64_t sum_us = pcc_gc_telemetry(metric + 1);
        int64_t max_us = pcc_gc_telemetry(metric + 2);
        if (count < 1 || max_us < 0 || sum_us < max_us) return 50 + site;
    }
"""
    assert source.count(original_print) == 1
    source = source.replace(original_print, checks + original_print, 1)
    # Fail closed if this adapter ever stops preserving the original harness.
    assert source.replace(classified_acquire, original_acquire, 1).replace(checks, "", 1) == QUEUED_SOURCE
    return source


@pytest.mark.integration
def test_graph_wait_native_five_gc_handoff_stop_registration_and_reuse(
    tmp_path, threaded_pcc_runtime_archive,
):
    from tests.owned_c_execution import compile_owned_c_with_runtime
    from tests.python.process_timeout import run_process_group_timeout

    source = tmp_path / "graph_wait_threaded.c"
    source.write_text(_native_threaded_source())
    binary = tmp_path / "graph_wait_threaded"
    compile_owned_c_with_runtime(source, binary, runtime_archive=threaded_pcc_runtime_archive)
    receipt = {
        "base_harness_sha256": hashlib.sha256(QUEUED_SOURCE.encode()).hexdigest(),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "runtime_sha256": hashlib.sha256(threaded_pcc_runtime_archive.read_bytes()).hexdigest(),
        "runs": [],
    }
    for backend in range(5):
        result = run_process_group_timeout(
            [str(binary)], timeout=45,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_THREADS="1", PCC_REFCOUNT_KIND="atomic"),
        )
        (tmp_path / f"gc{backend}.stdout").write_text(result.stdout)
        (tmp_path / f"gc{backend}.stderr").write_text(result.stderr)
        receipt["runs"].append({
            "backend": backend, "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr,
        })
        (tmp_path / "graph-wait-threaded.json").write_text(json.dumps(receipt, indent=2) + "\n")
        assert (result.returncode, result.stdout, result.stderr) == (
            0, f"QUEUED_GRAPH_OK gc={backend} fifo=012 reuse=1200\n", "",
        ), backend
