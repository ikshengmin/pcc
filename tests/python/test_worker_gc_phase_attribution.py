"""Cold actual-worker GC diagnostic contract (no performance claim)."""
import ast
import builtins
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcc.frontends.python import pipeline_memory as memory
from pcc.frontends.python import pipeline_frontend_worker_execution as execution


def _native(monkeypatch):
    monkeypatch.setattr(memory, 'sys', SimpleNamespace(implementation=SimpleNamespace(name='pcc'), stderr=None))
    monkeypatch.setattr(memory, '_worker_gc_emit', lambda line: None)


def test_worker_gc_phase_opt_in_and_host_unavailable(monkeypatch):
    # Explicit host opt-in reports unavailable, never reads unrelated host GC.
    seen = []
    monkeypatch.setattr(memory, '_worker_gc_emit', seen.append)
    monkeypatch.setattr(memory, '_worker_gc_metric', lambda metric: (_ for _ in ()).throw(AssertionError('host getter')))
    timer = memory.WorkerGcPhaseTiming()
    timer.begin(7, 'ast')
    timer.complete(7, 'ast')
    assert len(seen) == 1 and 'index=7 phase=ast gc_available=0' in seen[0]
    assert 'gc_allocations_delta=' not in seen[0]


def test_worker_gc_phase_monotonic_width_and_gauges(monkeypatch):
    _native(monkeypatch)
    seen, reads = [], []
    values = {metric: 100 for metric in memory._WORKER_GC_COUNTER_IDS}
    def read(metric):
        reads.append(metric)
        return values[metric]
    monkeypatch.setattr(memory, '_worker_gc_metric', read)
    monkeypatch.setattr(memory, '_worker_gc_emit', seen.append)
    timer = memory.WorkerGcPhaseTiming()
    timer.begin(9, 'frontend_generate')
    for metric in values:
        values[metric] = 107
    values[6] = 20  # debt is an end gauge, can decrease
    timer.complete(9, 'frontend_generate')
    assert reads == list(memory._WORKER_GC_COUNTER_IDS) * 2
    assert 'gc_scope=process gc_snapshot=non_atomic' in seen[-1]
    assert 'gc_snapshot_overhead=inclusive' in seen[-1]
    assert 'gc_wide_delta_ok=1 gc_event_delta_ok=1' in seen[-1]
    assert 'gc_allocations_delta=7' in seen[-1]
    assert 'gc_stw_sum_us_delta=7' in seen[-1]
    assert 'gc_debt_bytes_end=20' in seen[-1] and 'gc_debt_bytes_delta=' not in seen[-1]
    assert 'gc_max_stw_us_since_reset_end=107' in seen[-1]
    timer.begin(10, 'infer')
    values[2] = 1  # event32 decrease invalidates event group
    values[120] = 108
    timer.complete(10, 'infer')
    assert 'gc_wide_delta_ok=1 gc_event_delta_ok=0' in seen[-1]
    assert 'gc_read_barriers_delta=' not in seen[-1]
    assert 'gc_allocations_delta=0' in seen[-1]
    assert 'gc_stw_sum_us_delta=1' in seen[-1]
    timer.begin(11, 'ast')
    values[5] = -1  # invalid wide value, events still valid
    timer.complete(11, 'ast')
    assert 'gc_wide_delta_ok=0 gc_event_delta_ok=1' in seen[-1]
    assert 'gc_stw_sum_us_delta=' not in seen[-1]
    values[5] = 100
    values[0] = 2147483646
    timer.begin(12, 'ast')
    values[0] = 2147483651  # actual allocation sharded i64, not event32
    values[3] = 2147483648  # not a valid signed i32 reading
    timer.complete(12, 'ast')
    assert 'gc_wide_delta_ok=1 gc_event_delta_ok=0' in seen[-1]
    assert 'gc_allocations_delta=5' in seen[-1]
    assert 'gc_safepoints_delta=' not in seen[-1]


def test_worker_gc_phase_observer_failure_is_nonfatal(monkeypatch):
    _native(monkeypatch)
    seen = []
    monkeypatch.setattr(memory, '_worker_gc_emit', seen.append)
    monkeypatch.setattr(memory, '_worker_gc_metric', lambda metric: 10)
    timer = memory.WorkerGcPhaseTiming()
    timer.begin(3, 'ast')
    timer.complete(4, 'ast')  # mismatched owner cannot produce a delta
    assert 'gc_available=0' in seen[-1]
    assert 'gc_allocations_delta' not in seen[-1]
    timer.begin(3, 'unknown')
    timer.complete(3, 'unknown')
    assert len(seen) == 1
    def fail(metric):
        raise RuntimeError('diagnostic unavailable')
    monkeypatch.setattr(memory, '_worker_gc_metric', fail)
    timer.begin(5, 'infer')
    timer.complete(5, 'infer')
    assert 'gc_available=0' in seen[-1]
    attempts = []
    def fail_sink(line):
        attempts.append(line)
        raise RuntimeError('sink failed')
    monkeypatch.setattr(memory, '_worker_gc_emit', fail_sink)
    timer.begin(6, 'ast')
    timer.complete(6, 'ast')  # writer failure cannot affect worker outcome
    assert len(attempts) == 1


def test_worker_gc_phase_exact_callsite_no_runtime_changes():
    tree = ast.parse(Path(execution.__file__).read_text())
    worker = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run_codegen_worker')
    imports = [node for node in ast.walk(worker) if isinstance(node, ast.ImportFrom) and node.module == 'pcc.frontends.python.pipeline_memory']
    assert len(imports) == 1
    flags = [node for node in ast.walk(worker) if isinstance(node, ast.If) and 'PCC_PROFILE_WORKER_GC' in ast.unparse(node.test)]
    assert len(flags) == 1 and 'worker_timing' in ast.unparse(flags[0].test)
    assert imports[0] in list(ast.walk(flags[0]))
    calls = [node for node in ast.walk(worker) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'worker_gc_timing']
    assert len(calls) == 6
    phases = [(node.func.attr, node.args[1].value) for node in calls]
    assert sorted(phases) == sorted((op, phase) for phase in ('ast', 'infer', 'frontend_generate') for op in ('begin', 'complete'))
    # No diagnostic collect/reset/trim/log API; actual existing host-batch collect unchanged.
    source = Path(memory.__file__).read_text()
    diagnostic = source[source.index('class WorkerGcPhaseTiming'):]
    assert 'gc.collect(' not in diagnostic and 'pcc_gc_telemetry_reset' not in diagnostic and 'PCC_LOG' not in diagnostic


def test_worker_gc_phase_flag_off_never_imports(monkeypatch):
    tree = ast.parse(Path(execution.__file__).read_text())
    worker = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run_codegen_worker')
    gate = next(node for node in ast.walk(worker) if isinstance(node, ast.Try)
                and any(isinstance(child, ast.If) and 'PCC_PROFILE_WORKER_GC' in ast.unparse(child.test)
                        for child in node.body))
    code = compile(ast.Module(body=[gate], type_ignores=[]), '<worker-gc-gate>', 'exec')
    original_import = builtins.__import__
    attempts = []
    def observe_import(name, *args, **kwargs):
        if name == 'pcc.frontends.python.pipeline_memory':
            attempts.append(name)
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', observe_import)
    for timing, raw in ((False, '1'), (True, ''), (True, '0'), (False, '')):
        state = {'worker_timing': timing, 'worker_gc_timing': None,
                 'os': SimpleNamespace(environ={'PCC_PROFILE_WORKER_GC': raw})}
        exec(code, state)
        assert state['worker_gc_timing'] is None
    assert attempts == []
    state = {'worker_timing': True, 'worker_gc_timing': None,
             'os': SimpleNamespace(environ={'PCC_PROFILE_WORKER_GC': '1'})}
    exec(code, state)
    assert type(state['worker_gc_timing']) is memory.WorkerGcPhaseTiming
    assert attempts == ['pcc.frontends.python.pipeline_memory']


def test_worker_gc_phase_cancellation_propagates(monkeypatch):
    _native(monkeypatch)
    timer = memory.WorkerGcPhaseTiming()
    def interrupted(metric):
        raise KeyboardInterrupt()
    monkeypatch.setattr(memory, '_worker_gc_metric', interrupted)
    with pytest.raises(KeyboardInterrupt):
        timer.begin(1, 'ast')
    monkeypatch.setattr(memory, '_worker_gc_metric', lambda metric: 10)
    timer.begin(2, 'infer')
    def cancelled(metric):
        raise SystemExit(42)
    monkeypatch.setattr(memory, '_worker_gc_metric', cancelled)
    with pytest.raises(SystemExit, match='42'):
        timer.complete(2, 'infer')
    monkeypatch.setattr(memory, '_worker_gc_metric', lambda metric: 10)
    timer.begin(3, 'frontend_generate')
    def cancelled_sink(line):
        raise KeyboardInterrupt()
    monkeypatch.setattr(memory, '_worker_gc_emit', cancelled_sink)
    with pytest.raises(KeyboardInterrupt):
        timer.complete(3, 'frontend_generate')
    assert timer.active_index == -1 and timer.start_values == []


def test_worker_gc_phase_gate_cancellation_propagates(monkeypatch):
    tree = ast.parse(Path(execution.__file__).read_text())
    worker = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run_codegen_worker')
    gate = next(node for node in ast.walk(worker) if isinstance(node, ast.Try)
                and any(isinstance(child, ast.If) and 'PCC_PROFILE_WORKER_GC' in ast.unparse(child.test)
                        for child in node.body))
    code = compile(ast.Module(body=[gate], type_ignores=[]), '<worker-gc-gate>', 'exec')
    original_import = builtins.__import__
    def interrupt_import(name, *args, **kwargs):
        if name == 'pcc.frontends.python.pipeline_memory':
            raise KeyboardInterrupt()
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', interrupt_import)
    state = {'worker_timing': True, 'worker_gc_timing': None,
             'os': SimpleNamespace(environ={'PCC_PROFILE_WORKER_GC': '1'})}
    with pytest.raises(KeyboardInterrupt):
        exec(code, state)
