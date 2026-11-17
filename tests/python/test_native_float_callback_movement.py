"""Counted cleanup order, GC4 success/error callback movement, and guarded deferred finalization."""
from __future__ import annotations

import ast
import hashlib
import json
import os

import pytest

from tests.python.owned_regression_support import explicit_owned_runtime
from tests.python.process_timeout import run_process_group_timeout


PROGRAM = '''import gc
from threading import Thread
import sys
run_mode = sys.argv[1]
from pcc.extern import extern, c_int64
backend = extern("pcc_gc_backend", (), c_int64)
metric = extern("pcc_gc_telemetry", (c_int64,), c_int64)
select_moving = extern("pcc_gc_select_relocation_set", (c_int64,), c_int64)
step = extern("pcc_gc_step", (c_int64,), c_int64)
stop_world = extern("pcc_stop_the_world", (), c_int64)
resume_world = extern("pcc_resume_world", (), c_int64)
stopped_at_gates = extern("pcc_thread_all_stopped_at_gates", (), c_int64)
active = extern("pcc_test_float_explicit_active", (), c_int64)
protocol = extern("pcc_test_float_countfree_protocol", (), c_int64)
evidence = []
counts = [0, 0]
completed = []
worker_errors = []
producer_phase = ["start"]
guards = []
events = []
converter = float

def move_live_objects(phase):
    producer_phase[0] = phase
    guards.append((phase, active()))
    # Keep this phase's small, initialized lists alive without an address
    # lease. Reusing an old ballast container enqueues fresh young children
    # and can leave the collector at its remembered-root gate, before copy.
    ballast = []
    for index in range(96):
        ballast.append([index, index + 1000])
    pending_before = metric(42)
    assert 0 <= pending_before <= 4096
    pending = pending_before
    drain_steps = 0
    # GC4 drains at most eight remembered entries per step. Spend only that
    # batch's budget here, so preparation does not request evacuation. A
    # non-converging queue is a failed precondition, never a retry for moves.
    for index in range((pending_before + 7) // 8):
        if pending == 0:
            break
        budget = min(pending, 8)
        step(budget)
        remaining = metric(42)
        assert 0 <= remaining < pending, phase + ":drain-progress"
        pending = remaining
        drain_steps += 1
    assert pending == 0, phase + ":pending-drained"
    # gc.disable() pauses automatic collection, not the CMS thread. Match
    # production moving steps: hold the world and require every other thread
    # at a relocation-safe gate. A runtime poll is not such a gate; fail that
    # precondition rather than retrying until this movement witness passes.
    stop_status = stop_world()
    assert stop_status == 0, phase + ":stop"
    try:
        gate_status = stopped_at_gates()
        if gate_status != 1:
            print("movement-gate-failed", phase, gate_status)
        assert gate_status == 1, phase + ":application-gates"
        before = metric(15)
        selected = select_moving(4096)
        processed = step(4096)
        moved = metric(15) - before
    finally:
        resume_status = resume_world()
        assert resume_status == 0, phase + ":resume"
    # The roots and payload must survive the measured relocation window.
    assert len(ballast) == 96, phase + ":ballast-length"
    for index in range(96):
        assert ballast[index] == [index, index + 1000], phase + ":ballast-content"
    evidence.append((phase, selected, processed, moved,
                     pending_before, pending, drain_steps))

class Nested:
    def __float__(self):
        events.append("nested")
        return 7.25

class Number:
    def __float__(self):
        events.append("callback-enter")
        if backend() == 4 and run_mode == "positive":
            move_live_objects("callback")
        assert converter(Nested()) == 7.25
        events.append("callback-exit")
        return 3.5

class InvalidReturn:
    def __del__(self):
        # GC0: synchronous invalid-result drop retains the canonical caller's
        # receiver across cleanup. GC4: count-free release defers this callback
        # to a guarded collection; real error-path movement is tested inside
        # BadNumber.__float__, not in this non-evacuating finalizer window.
        counts[0] += 1
        events.append("cleanup-enter")
        if backend() == 4:
            assert active() == 1
            move_live_objects("deferred-cleanup")
        else:
            assert active() == 0
        assert converter(Nested()) == 7.25
        events.append("cleanup-exit")

class BadNumber:
    def __float__(self):
        if backend() == 4 and run_mode == "positive":
            events.append("invalid-result-callback-enter")
            move_live_objects("invalid-result-callback")
            assert converter(Nested()) == 7.25
            assert counts[1] == 0
            events.append("invalid-result-callback-exit")
        return InvalidReturn()
    def __del__(self):
        counts[1] += 1
        events.append("receiver-finalized")

def conversions():
    if run_mode != "deferred":
        assert converter(Number()) == 3.5
    caught = False
    try:
        converter(BadNumber())
    except TypeError:
        caught = True
    assert caught
    completed.append(1)

def producer():
    try:
        conversions()
    except BaseException as error:
        worker_errors.append(producer_phase[0] + ":" + type(error).__name__ + ":" + str(error))

def reraiser():
    raise

def main():
    which = backend()
    assert (which == 0 and run_mode == "synchronous") or (which == 4 and (run_mode == "positive" or run_mode == "deferred"))
    if which == 4:
        assert protocol() == 3
    gc.disable()
    if run_mode == "deferred":
        # This producer never requests movement. Join waits for actual thread
        # teardown before main alone runs the guarded collection window.
        thread = Thread(target=producer)
        thread.start()
        thread.join()
    else:
        conversions()
    if worker_errors:
        print("worker-errors", worker_errors)
        print("worker-evidence", evidence)
    assert worker_errors == []
    assert completed == [1]
    if run_mode != "positive":
        try:
            raise ValueError("outer-marker")
        except ValueError:
            gc.collect()
            try:
                reraiser()
            except ValueError as error:
                assert str(error) == "outer-marker"
            else:
                assert False
        assert counts == [1, 1]
        before = len(events)
        gc.collect()
        assert counts == [1, 1]
        assert len(events) == before
    else:
        # Count-free invalid-result destruction is not synchronous. This
        # mode proves real error-producing callback movement, not finalization.
        assert counts == [0, 0]
    print("protocol", [which, protocol()])
    print("mode", run_mode)
    print("completed", completed)
    print("events", events)
    print("counts", counts)
    print("guards", guards)
    for row in evidence:
        print(row[0], row[1], row[2], row[3], row[4], row[5], row[6])
    if run_mode == "synchronous":
        assert events == ["callback-enter", "nested", "callback-exit", "cleanup-enter", "nested", "cleanup-exit", "receiver-finalized"]
        assert len(evidence) == 0
    elif run_mode == "positive":
        assert events == ["callback-enter", "nested", "callback-exit", "invalid-result-callback-enter", "nested", "invalid-result-callback-exit"]
        assert len(evidence) == 2
        assert evidence[0][0] == "callback"
        assert evidence[1][0] == "invalid-result-callback"
        assert guards == [("callback", 0), ("invalid-result-callback", 0)]
        for row in evidence:
            assert row[1] > 0 and row[3] > 0 and row[5] == 0
    else:
        assert events.count("cleanup-enter") == 1
        assert events.count("cleanup-exit") == 1
        assert events.count("receiver-finalized") == 1
        assert events.count("nested") == 1
        assert events.index("cleanup-enter") < events.index("nested") < events.index("cleanup-exit")
        assert len(evidence) == 1
        assert evidence[0][0] == "deferred-cleanup"
        assert evidence[0][1] > 0 and evidence[0][3] == 0 and evidence[0][5] == 0
        assert guards == [("deferred-cleanup", 1)]
    print("FLOAT_CALLBACK_SPLIT_OK", which, run_mode)
main()
'''



def assert_native_evidence(result, gc_backend, mode):
    assert result.returncode == 0 and result.stderr == '', (result.returncode,result.stdout,result.stderr)
    lines = result.stdout.splitlines()
    assert lines and lines[-1] == f'FLOAT_CALLBACK_SPLIT_OK {gc_backend} {mode}', lines
    def datum(name):
        hits=[line[len(name)+1:] for line in lines if line.startswith(name+' ')]
        assert len(hits)==1,(name,lines)
        return ast.literal_eval(hits[0])
    identity=datum('protocol');events=datum('events');counts=datum('counts');guards=datum('guards')
    assert isinstance(events,list) and identity[0]==gc_backend,identity
    assert datum('completed')==[1]
    assert [line for line in lines if line.startswith('mode ')]==['mode '+mode]
    if mode=='synchronous':
        assert gc_backend==0 and counts==[1,1] and guards==[],(identity,counts,guards)
        assert events==['callback-enter','nested','callback-exit','cleanup-enter','nested','cleanup-exit','receiver-finalized'],events
        assert len(lines)==7,lines
        return
    assert gc_backend==4 and identity==[4,3],identity
    phases=('callback','invalid-result-callback') if mode=='positive' else ('deferred-cleanup',)
    assert mode in ('positive','deferred')
    rows=[line.split() for line in lines if line.split() and line.split()[0] in phases]
    assert len(lines)==7+len(phases) and len(rows)==len(phases),lines
    assert guards==[(phase,0 if mode=='positive' else 1) for phase in phases],guards
    for phase,fields in zip(phases,rows):
        assert len(fields)==7 and fields[0]==phase,fields
        assert all(field.lstrip('-').isdigit() for field in fields[1:]),fields
        selected,processed,moved,pending_before,pending,drains=map(int,fields[1:])
        assert selected>0 and processed>=0,fields
        assert moved==0 if mode=='deferred' else moved>0,fields
        assert 0<=pending_before<=4096 and pending==0,fields
        assert 0<=drains<=(pending_before+7)//8,fields
    if mode=='positive':
        assert counts==[0,0],counts
        assert events==['callback-enter','nested','callback-exit','invalid-result-callback-enter','nested','invalid-result-callback-exit'],events
    else:
        assert counts==[1,1],counts
        assert len(events)==4 and all(events.count(event)==1 for event in ('cleanup-enter','nested','cleanup-exit','receiver-finalized')),events
        assert events.index('cleanup-enter')<events.index('nested')<events.index('cleanup-exit'),events

def _record_float_execution(receipt, current):
    executions = receipt["executions"]
    expected = ((0, "synchronous"), (4, "positive"), (4, "deferred"))
    assert len(executions) < len(expected)
    assert (current["gc_backend"], current["execution_mode"]) == expected[len(executions)]
    executions.append(dict(current))
    if any(row["status"] != "PASS" for row in executions):
        receipt["status"] = "NATIVE_EXECUTION_FAILED"
    elif len(executions) == len(expected):
        receipt["status"] = "PASS"
    else:
        receipt["status"] = "RUNNING"


@pytest.mark.integration
@pytest.mark.parametrize("python_program_compiler", ("pcc0", "pcc1"), indirect=True)
def test_canonical_float_callback_movement_gc4(
    python_program_compiler, request, explicit_owned_runtime, tmp_path, capfd,
):
    # This is an instrumented native correctness witness, not a CPython
    # differential program or a claim that the protected receiver must move.
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    from pcc.frontends.python.pipeline_targets import host_target_triple
    helper_text = '/* Test-only read of the existing production guard. No state mutation. */\nextern int pcc_gc_explicit_collect_active;\nlong long pcc_test_float_explicit_active(void) {\n    return pcc_gc_explicit_collect_active;\n}\nextern int pcc_gc_backend4_no_refcounts;\nextern int pcc_gc_nonmoving_count_free;\nlong long pcc_test_float_countfree_protocol(void) {\n    return (pcc_gc_backend4_no_refcounts == 1) + 2 * (pcc_gc_nonmoving_count_free == 1);\n}\n'
    evaluator = CEvaluator(backend="self", target_triple=host_target_triple())
    units = evaluator.compile_translation_units(
        [TranslationUnit("float_guard.c", str(tmp_path / "float_guard.c"), helper_text)],
        use_system_cpp=False, use_compile_cache=False,
    )
    helper_object = tmp_path / "float_guard.o"
    evaluator.emit_compiled_units(units, emit_obj=str(helper_object))
    source = tmp_path / "float_callback_movement.py"
    binary = tmp_path / "float_callback_movement.out"
    source.write_text(PROGRAM, encoding="utf-8")
    mode = request.node.callspec.params["python_program_compiler"]
    if python_program_compiler.__module__ == "tests.pcc1_route":
        mode = "pcc1-routed"
    receipt = {
        "status": "RUNNING", "compiler_mode": mode, "executions": [],
        "backend": "self", "libpython": "off", "gc_backend": 4,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "runtime_archive": str(explicit_owned_runtime),
        "runtime_sha256": hashlib.sha256(explicit_owned_runtime.read_bytes()).hexdigest(),
    }
    receipt_path = tmp_path / "float-callback-movement.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    try:
        python_program_compiler(
            str(source), str(binary), backend="self",
            ir_scaffold_mode="on", runtime_archive=str(explicit_owned_runtime),
            object_inputs=(str(helper_object),),
        )
    except Exception as error:
        receipt.update(status="COMPILE_FAILED", error=type(error).__name__ + ": " + str(error))
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        raise
    finally:
        captured = capfd.readouterr()
        (tmp_path / "compiler.stdout").write_text(captured.out)
        (tmp_path / "compiler.stderr").write_text(captured.err)
    assert binary.is_file()
    receipt["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    failures = []
    for gc_backend, run_mode in ((0, "synchronous"), (4, "positive"), (4, "deferred")):
        environment = dict(
            os.environ, PCC_GC_BACKEND=str(gc_backend), PCC_GC_MINOR_ALLOC_MAX="4096",
            PCC_GC_REFCOUNT_PROVENANCE_PROBE="2", PATH="",
            PCC_HOST_PYTHON="/nonexistent/host-python", PCC_HOST_PCC="/nonexistent/host-pcc",
            PCC_TEST_NO_NATIVE_PROVISIONING="1", PCC_NO_AUTO_PCC1="1",
        )
        environment.pop("LC_ALL", None)
        result = run_process_group_timeout([str(binary), run_mode], env=environment, timeout=20)
        (tmp_path / f"gc{gc_backend}-{run_mode}.stdout").write_text(result.stdout)
        (tmp_path / f"gc{gc_backend}-{run_mode}.stderr").write_text(result.stderr)
        current = dict(gc_backend=gc_backend, execution_mode=run_mode, returncode=result.returncode,
                       stdout=result.stdout, stderr=result.stderr)
        try:
            assert_native_evidence(result, gc_backend, run_mode)
            success = True
        except (AssertionError, ValueError, SyntaxError) as error:
            success = False
            current["host_oracle_error"] = str(error)
        current["status"] = "PASS" if success else "NATIVE_EXECUTION_FAILED"
        (tmp_path / f"float-callback-gc{gc_backend}-{run_mode}.json").write_text(json.dumps(current, indent=2) + "\n")
        _record_float_execution(receipt, current)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        if not success:
            failures.append(current)
    assert not failures, failures
