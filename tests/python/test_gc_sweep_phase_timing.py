"""Sweep telemetry contracts over production bodies and an owned ELF component.

Host tests bridge the actual sweep/candidate/recheck functions to the actual
counter helper, telemetry dispatch and reset, using exact-width memory and
controlled clocks/object providers. These are local control-flow and accounting
proofs, not complete GC, PEP 442, concurrency or timing-accuracy acceptance.

The ELF lane uses host pcc and its owned self backend, assembler and linker.
It compiles the complete strict owner and separately executes the exported
five-argument recording ABI with real state and telemetry/reset slices. It
does not build native pcc1 or link/run the complete GC runtime.
"""
from __future__ import annotations

import ast
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
from tests.python.runtime_body_model import load_functions
from tests.python.test_freestanding_gc_tracing_sweep_collector import (
    _CandidateModel,
    _has_sweep_candidate,
)
from tests.python.test_gc_phase_counter_widths import (
    ROOT,
    RUNTIME,
    _definitions,
    _function,
    _host_functions,
)
from tests.python.test_gc_tracing_sweep_verdict import (
    CANDIDATE,
    FINALIZED,
    WHITE,
    _SweepBoundary,
)


SOURCE = RUNTIME / "freestanding_gc_tracing_sweep_collector.py"
PHASES = ("candidate", "pass0", "pass1", "pass2", "recheck")
FIELDS = ("calls", "nodes", "work", "sum_us")
WORK_SUFFIXES = ("FOUND", "FINALIZER_RUNS", "CLEARED", "RECLAIMED", "CANDIDATES_CLEARED")
METRICS = {
    f"pcc_gc_metric_sweep_{phase}_{field}": (
        126 + phase_id * 4 + field_id,
        f"{phase.upper()}_{WORK_SUFFIXES[phase_id] if field == 'work' else field.upper()}",
    )
    for phase_id, phase in enumerate(PHASES)
    for field_id, field in enumerate(FIELDS)
}


def _counter_functions():
    memory, namespace = _host_functions()
    load_functions(SOURCE, ("_record_sweep_phase",), namespace)
    return memory, namespace


def _values(namespace, phase):
    return [namespace["pcc_gc_telemetry"](126 + 4 * phase + field) for field in range(4)]


def _bridge(function):
    """Keep object-provider memory separate from the exact-width metric cells."""
    memory, namespace = _counter_functions()
    observations = []

    def record(*args):
        observations.append(args)
        namespace["_record_sweep_phase"](*args)

    function.__globals__["_record_sweep_phase"] = record
    return memory, namespace, observations


class _Clock:
    def __init__(self):
        self.now = 100
        self.reads = []

    def read(self):
        self.reads.append(self.now)
        return self.now

    def wrap(self, function, duration):
        def call(*args):
            self.now += duration
            return function(*args)
        return call


def _timed_boundary(**kwargs):
    boundary = _SweepBoundary(**kwargs)
    memory, namespace, observations = _bridge(boundary.sweep)
    clock = _Clock()
    providers = boundary.sweep.__globals__
    providers["pcc_platform_monotonic_us"] = clock.read
    for name, duration in (
        ("pcc_gc_object_node_is_active", 2),
        ("pcc_gc_seed_roots", 17),
        ("pcc_gc_drain_all_gray_unlocked", 23),
        ("py_user_del_dispatch", 11),
        ("pcc_gc_tracing_clear_unpinned", 13),
        ("pcc_gc_tracing_finalize_unpinned", 19),
    ):
        providers[name] = clock.wrap(providers[name], duration)
    return boundary, memory, namespace, observations, clock


def test_sweep_metrics_have_distinct_wide_public_storage_and_provider():
    definitions = _definitions()
    header = (ROOT / "pcc/runtime/include/py_runtime.h").read_text()
    assert len(METRICS) == 20
    for name, (metric, suffix) in METRICS.items():
        assert definitions[name] == ("define_global_i64", 0)
        assert name in FREESTANDING_GC_I64_GLOBALS
        assert name not in FREESTANDING_GC_I32_GLOBALS
        assert re.search(rf"PCC_GC_COUNTER_SWEEP_{suffix}\s*=\s*{metric}\b", header)
    assert FREESTANDING_GC_CROSS_OBJECT_SIGNATURES["pcc_gc_record_sweep_phase"] == (
        ("c_int64",) * 5, "c_void",
    )
    record = _function(SOURCE, "_record_sweep_phase")
    assert ast.literal_eval(record.decorator_list[0].args[0]) == "pcc_gc_record_sweep_phase"


@pytest.mark.parametrize("phase", range(5), ids=PHASES)
def test_sweep_counter_high_bits_zero_duration_and_atomic_aggregation(phase):
    memory, namespace = _counter_functions()
    names = [f"pcc_gc_metric_sweep_{PHASES[phase]}_{field}" for field in FIELDS]
    initial = [(1 << 31) - 1, (1 << 40) + 3, (1 << 41) + 5, (1 << 42) + 7]
    for name, value in zip(names, initial):
        memory.set(name, value)
    writes = []

    def record_write(operation, name, offset, delta, order):
        writes.append((operation, name, offset, delta, order))
        return memory.rmw_i64(operation, name, offset, delta, order)

    namespace["atomic_rmw_i64"] = record_write
    nodes, work, elapsed = (1 << 32) + 9, (1 << 33) + 7, (1 << 32) + 3
    namespace["_record_sweep_phase"](phase, 10, 10 + elapsed, nodes, work)
    assert writes == [("add", name, 0, delta, "relaxed")
                      for name, delta in zip(names, (1, nodes, work, elapsed))]
    assert _values(namespace, phase) == [initial[0] + 1, initial[1] + nodes,
                                        initial[2] + work, initial[3] + elapsed]
    namespace["_record_sweep_phase"](phase, 10, 10, 0, 0)
    assert _values(namespace, phase) == [initial[0] + 2, initial[1] + nodes,
                                        initial[2] + work, initial[3] + elapsed + 1]
    for other in set(range(5)) - {phase}:
        assert _values(namespace, other) == [0, 0, 0, 0]


@pytest.mark.parametrize("phase", range(5), ids=PHASES)
def test_sweep_invalid_clock_keeps_work_and_invalid_arguments_keep_all_slots(phase):
    memory, namespace = _counter_functions()
    record = namespace["_record_sweep_phase"]
    for start, end in ((0, 100), (-1, 100), (20, 19), (1, -1)):
        record(phase, start, end, 7, 3)
    assert _values(namespace, phase) == [4, 28, 12, 0]
    before = {name: memory.get(name) for name in METRICS}
    for invalid_phase in (-1, 5, 1 << 32):
        record(invalid_phase, 10, 20, 7, 3)
    record(phase, 10, 20, -1, 3)
    record(phase, 10, 20, 7, -1)
    assert {name: memory.get(name) for name in METRICS} == before


def test_sweep_real_telemetry_mapping_and_reset_clear_every_high_word():
    memory, namespace = _counter_functions()
    for name, (metric, _) in METRICS.items():
        memory.set(name, (1 << 44) + metric)
    for name, (metric, _) in METRICS.items():
        assert namespace["pcc_gc_telemetry"](metric) == (1 << 44) + metric, name
    namespace["pcc_gc_telemetry_reset"]()
    assert all(namespace["pcc_gc_telemetry"](metric) == 0 for metric, _ in METRICS.values())
    for phase in range(5):
        namespace["_record_sweep_phase"](phase, 10, 10, 2, 3)
        assert _values(namespace, phase) == [1, 2, 3, 1]


def test_sweep_telemetry_and_reset_use_atomic_i64_slots():
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


def test_candidate_counts_inactive_nodes_but_not_cache_or_lock_wait():
    model = _CandidateModel([0, CANDIDATE, 0, CANDIDATE])
    model.nodes[1]["active"] = 0
    candidate = _has_sweep_candidate(model)
    _, namespace, observations = _bridge(candidate)
    clock = _Clock()
    providers = candidate.__globals__
    providers["pcc_platform_monotonic_us"] = clock.read
    for name, duration in (("pcc_py_gc_minor_graph_lock", 1000),
                           ("pcc_py_gc_minor_graph_unlock", 2000),
                           ("pcc_gc_object_node_is_active", 3)):
        providers[name] = clock.wrap(providers[name], duration)
    assert candidate() == candidate() == 1
    model.nodes[3]["flags"] = 0
    assert candidate() == 0
    reads = list(clock.reads)
    assert candidate() == 0
    assert clock.reads == reads  # The cached no-candidate path never reads time.
    assert model.locked == 0
    assert _values(namespace, 0) == [3, 12, 2, 36]
    assert [(phase, end - start, nodes, work) for phase, start, end, nodes, work
            in observations] == [(0, 12, 4, 1), (0, 12, 4, 1), (0, 12, 4, 0)]


def test_empty_candidate_walk_is_counted_once_and_finish_invalidates_cache():
    model = _CandidateModel([])
    candidate = _has_sweep_candidate(model)
    _, namespace, _ = _bridge(candidate)
    clock = _Clock()
    candidate.__globals__["pcc_platform_monotonic_us"] = clock.read
    assert candidate() == candidate() == 0
    assert _values(namespace, 0) == [1, 0, 0, 1]
    assert len(clock.reads) == 2
    model.words["pcc_gc_tracing_finish_commits"] += 1
    assert candidate() == 0
    assert _values(namespace, 0) == [2, 0, 0, 2]


def test_actual_sweep_counts_local_inactive_nodes_and_phase_work_once():
    boundary, _, namespace, observations, _ = _timed_boundary()
    boundary.object("inactive")
    boundary.nodes[0]["active"] = False
    dead = boundary.object("dead")
    dead["edges"] = [dead]
    assert boundary.sweep(1) == 1
    assert [row[0] for row in observations] == [4, 1, 4, 2, 3]
    assert _values(namespace, 0) == [0, 0, 0, 0]
    assert _values(namespace, 1) == [1, 2, 1, 15]
    assert _values(namespace, 2) == [1, 2, 1, 17]
    assert _values(namespace, 3) == [1, 2, 1, 23]
    assert _values(namespace, 4) == [2, 4, 0, 88]
    assert boundary.events == ["seed", "drain", "del:dead", "seed", "drain",
                               "weakref:dead", "clear:dead", "free:dead"]


def test_pass0_work_is_actual_user_finalizer_delta_not_dispatch_attempts():
    boundary, _, namespace, _, _ = _timed_boundary(held_epoch=7, finish_epoch=7)
    boundary.object("new")
    old = boundary.object("already_finalized")
    old[12] |= FINALIZED
    boundary.object("class", is_class=True)
    boundary.object("extension", cext=True)
    boundary.object("pinned", pinned=True)
    excluded = boundary.object("excluded")
    excluded[12] |= 16384
    boundary.object("inactive")
    boundary.nodes[-1]["active"] = False
    dispatched = []
    providers = boundary.sweep.__globals__
    original = providers["py_user_del_dispatch"]

    def dispatch(obj):
        dispatched.append(obj["name"])
        original(obj)

    providers["py_user_del_dispatch"] = dispatch
    boundary.sweep(20)
    assert dispatched == ["new", "already_finalized", "class"]
    assert _values(namespace, 1)[:3] == [1, 7, 1]
    assert boundary.global_cells["pcc_gc_user_finalizer_runs"][0] == 1


@pytest.mark.parametrize("count_free", [False, True])
def test_recheck_counts_local_walks_and_candidate_removal_including_shell(count_free):
    boundary, _, namespace, observations, _ = _timed_boundary(backend=4, count_free=count_free)
    root = boundary.object("root", candidate=False)
    child = boundary.object("child")
    shell = boundary.object("shell")
    inactive = boundary.object("inactive")
    boundary.nodes[-1]["active"] = False
    root["edges"] = [child]
    boundary.roots = [root]
    boundary.forward(shell, child)
    boundary.sweep.__globals__["pcc_gc_tracing_recheck_reachability_after_finalizers"]()
    visits = 8 if count_free else 4
    # Root seeding and gray draining include their own graph walks. They add
    # time, not extra ObjectNode visits to this function's local walk count.
    assert _values(namespace, 4) == [1, visits, 2, 40 + visits * 2]
    assert len(observations) == 1
    assert child[12] & CANDIDATE == shell[12] & CANDIDATE == 0
    assert shell[12] & WHITE  # Shell removal is not a resurrection count.
    assert inactive[12] & CANDIDATE


def test_finalizer_resurrection_is_rechecked_before_clear_work():
    boundary, _, namespace, observations, _ = _timed_boundary(held_epoch=7, finish_epoch=7)
    child = boundary.object("child")
    child["edges"] = [child]
    boundary.callbacks["child"] = lambda obj: boundary.roots.append(obj)
    assert boundary.sweep(1) == 0
    assert [row[0] for row in observations] == [1, 4, 2, 3]
    assert _values(namespace, 1)[:3] == [1, 1, 1]
    assert _values(namespace, 4)[:3] == [1, 1, 1]
    assert _values(namespace, 2)[:3] == [1, 1, 0]
    assert _values(namespace, 3)[:3] == [1, 1, 0]
    assert child["edges"] == [child] and child["weakref_live"]
    assert boundary.events == ["del:child", "seed", "drain"]


@pytest.mark.parametrize("exit_kind, visits, reclaimed", [
    ("pending_nonclass", 4, 0),
    ("uncleared_class", 4, 0),
    ("normal", 6, 1),
])
def test_pass2_all_three_returns_record_once_and_include_repeat_walks(exit_kind, visits, reclaimed):
    boundary, _, namespace, observations, _ = _timed_boundary(
        backend=4, count_free=True, held_epoch=7, finish_epoch=7,
    )
    if exit_kind == "normal":
        boundary.object("inactive")
        boundary.nodes[0]["active"] = False
        boundary.object("class", is_class=True)
    else:
        first = boundary.object("first", is_class=True)
        second = boundary.object("second", is_class=exit_kind == "uncleared_class")
        first["edges"] = [second]
        second[12] |= FINALIZED
        if exit_kind == "uncleared_class":
            second["edges"] = [first]
        else:
            second["layout"] = [first]
    assert boundary.sweep(1) == reclaimed
    assert [row[0] for row in observations] == [1, 2, 3]
    assert _values(namespace, 3)[:3] == [1, visits, reclaimed]
    assert sum(event.startswith("free:") for event in boundary.events) == reclaimed


def test_pass2_work_counts_typed_finalize_returns_not_physical_frees():
    boundary, _, namespace, _, _ = _timed_boundary(held_epoch=7, finish_epoch=7)
    obj = boundary.object("deferred_storage")
    obj[12] |= FINALIZED
    typed = []
    # A successful typed finalizer can defer physical release (for example a
    # page-owned object). The sweep's historical return still charges the call.
    boundary.sweep.__globals__["pcc_gc_tracing_finalize_unpinned"] = typed.append
    assert boundary.sweep(1) == 1
    assert typed == [obj] and boundary.nodes[0]["active"]
    assert _values(namespace, 3)[:3] == [1, 1, 1]
    assert not any(event.startswith("free:") for event in boundary.events)


@pytest.mark.parametrize("budget", [-1, 0])
def test_nonpositive_sweep_budget_does_not_start_or_record_any_phase(budget):
    boundary, _, namespace, observations, clock = _timed_boundary()
    boundary.object("candidate")
    assert boundary.sweep(budget) == 0
    assert observations == clock.reads == boundary.events == []
    assert all(_values(namespace, phase) == [0, 0, 0, 0] for phase in range(5))


def _native_record_source():
    return ("from pcc import i64\nfrom pcc.extern import c_abi_export\n"
            "from pcc.unsafe import global_addr, atomic_rmw_i64\n"
            "__pcc_freestanding__ = True\n"
            + ast.unparse(_function(SOURCE, "_record_sweep_phase")) + "\n")


def _native_probe_source():
    telemetry = _function(RUNTIME / "py_gc_telemetry.py", "pcc_gc_telemetry")
    branches = [statement for statement in telemetry.body
                if isinstance(statement, ast.If)
                and isinstance(statement.test, ast.Compare)
                and isinstance(statement.test.left, ast.Name)
                and statement.test.left.id == "metric"
                and len(statement.test.comparators) == 1
                and isinstance(statement.test.comparators[0], ast.Constant)
                and statement.test.comparators[0].value in range(126, 146)]
    assert len(branches) == 20
    telemetry.body = branches + [ast.Return(value=ast.Constant(value=-1))]
    reset = _function(RUNTIME / "py_gc_backend.py", "pcc_gc_telemetry_reset")
    reset.body = [statement for statement in reset.body
                  if isinstance(statement, ast.Expr)
                  and isinstance(statement.value, ast.Call)
                  and isinstance(statement.value.func, ast.Name)
                  and statement.value.func.id == "atomic_store_i64"
                  and isinstance(statement.value.args[0], ast.Call)
                  and statement.value.args[0].args[0].value in METRICS]
    assert len(reset.body) == 20
    module = ast.fix_missing_locations(ast.Module(body=[telemetry, reset], type_ignores=[]))
    lines = ["@c_abi_export('sweep_phase_probe')", "def sweep_phase_probe() -> i64:",
             "    pcc_gc_telemetry_reset()"]
    for phase, name in enumerate(PHASES):
        initial = [(1 << 31) - 1, (1 << 40) + 3, (1 << 41) + 5, (1 << 42) + 7]
        for field, value in zip(FIELDS, initial):
            lines.append(f'    store_i64(global_addr("pcc_gc_metric_sweep_{name}_{field}"), 0, {value})')
        lines += [f"    record({phase}, 10, 4294967309, 4294967305, 8589934599)",
                  f"    record({phase}, 10, 10, 0, 0)",
                  f"    record({phase}, 0, 100, 7, 3)",
                  f"    record({phase}, 20, 19, 7, 3)",
                  f"    record({phase}, -1, 100, 7, 3)",
                  f"    record({phase}, 10, 20, -1, 3)",
                  f"    record({phase}, 10, 20, 7, -1)"]
    lines += ["    record(-1, 10, 20, 7, 3)", "    record(5, 10, 20, 7, 3)"]
    expected = [(1 << 31) + 4, (1 << 40) + 3 + 4294967305 + 21,
                (1 << 41) + 5 + 8589934599 + 9, (1 << 42) + 7 + 4294967299 + 1]
    for phase in range(5):
        for field, value in enumerate(expected):
            metric = 126 + phase * 4 + field
            lines += [f"    if pcc_gc_telemetry({metric}) != {value}:",
                      f"        return {metric - 125}"]
    lines += ["    pcc_gc_telemetry_reset()", "    metric: i64 = 126",
              "    while metric <= 145:", "        if pcc_gc_telemetry(metric) != 0:",
              "            return 21", "        metric += 1", "    phase: i64 = 0",
              "    while phase < 5:", "        record(phase, 10, 10, 0, 0)",
              "        if pcc_gc_telemetry(126 + phase * 4) != 1:", "            return 22",
              "        if pcc_gc_telemetry(129 + phase * 4) != 1:", "            return 23",
              "        phase += 1", "    return 0"]
    return ("from pcc import i64\n"
            "from pcc.extern import c_abi_export, c_int64, c_void, extern\n"
            "from pcc.unsafe import global_addr, store_i64, atomic_load_i64, atomic_store_i64\n"
            "__pcc_freestanding__ = True\n"
            'record = extern("pcc_gc_record_sweep_phase", '
            '(c_int64, c_int64, c_int64, c_int64, c_int64), c_void)\n'
            + ast.unparse(module) + "\n" + "\n".join(lines) + "\n")


def _linux_x86_64():
    return platform.system() == "Linux" and platform.machine().lower() in ("x86_64", "amd64")


@pytest.mark.pcc_gate(probe=_linux_x86_64)
def test_owned_native_sweep_phase_abi_reset_and_complete_owner_closure(tmp_path, monkeypatch):
    """All complete-source compiles stay serial inside this one xdist item."""
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
    from tests.python import test_freestanding_gc_tracing_sweep_collector as owner

    monkeypatch.setenv("PCC_IR_TO_OBJ_EMITTER", "pcc")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    target = "x86_64-unknown-linux-gnu"
    record = tmp_path / "sweep_record.py"
    record.write_text(_native_record_source())
    probe = tmp_path / "sweep_probe.py"
    probe.write_text(_native_probe_source())

    def compile_object(name, source):
        ir = tmp_path / (name + ".ll")
        compile_python(str(source), str(ir), emit_llvm_only=True,
                       python_library=True, backend="self", target_triple=target)
        return parse_relocatable(emit_object(ir.read_text(), target_triple=target))

    with patch.object(subprocess, "Popen", side_effect=AssertionError("external build process")):
        complete = compile_object("complete_sweep", SOURCE)
        imports = {symbol.name for symbol in complete.symbols
                   if symbol.name and symbol.section_index == SHN_UNDEF}
        definitions = {symbol.name for symbol in complete.symbols
                       if symbol.binding == STB_GLOBAL and symbol.section_index != SHN_UNDEF}
        assert imports == owner.RAW_FUNCTION_IMPORTS | owner.RAW_GLOBAL_IMPORTS
        assert definitions == owner.OWNED_SYMBOLS
        state = compile_object("state", RUNTIME / "freestanding_gc_state.py")
        symbols = {symbol.name: symbol for symbol in state.symbols}
        assert all(symbols[name].size == 8 for name in METRICS)
        helper = compile_object("record", record)
        caller = compile_object("probe", probe)
        caller_imports = {symbol.name for symbol in caller.symbols
                          if symbol.section_index == SHN_UNDEF}
        assert "pcc_gc_record_sweep_phase" in caller_imports
        startup = (".intel_syntax noprefix\n.text\n.globl _start\n_start:\n"
                   " call sweep_phase_probe\n mov edi, eax\n mov eax, 60\n syscall\n")
        image = link_static_executable([state, helper, caller, assemble_file(startup)])
    executable = tmp_path / "sweep_phase_probe"
    executable.write_bytes(image)
    executable.chmod(0o755)
    result = subprocess.run([str(executable)], capture_output=True, timeout=10, env={"PATH": ""})
    assert (result.returncode, result.stdout, result.stderr) == (0, b"", b""), result
