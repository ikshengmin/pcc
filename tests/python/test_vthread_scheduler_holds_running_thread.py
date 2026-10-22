"""The virtual-thread scheduler keeps the thread it runs where it is.

_run_ready_step keeps the ready thread and its continuation as raw pointers
while it resumes the thread, and reads them after the resume returns. The
resume runs compiled code, whose application gates let GC4 relocate objects:
when the running thread itself moved, the step read its old copy, found it
still RUNNING and completed the thread with None, so the root task of the
gateway benchmark (one hundred requests a round, each joining a scope of two
children) returned None in its second round. The step now holds a counted
lease on the thread and on its continuation until it is done with them.

The program is the gateway benchmark with its structured-concurrency scope
inlined; it must finish every round on all five collectors.
"""
import json
import os
from pathlib import Path
import subprocess

SCOPE_LIB = r'''import pcc.virtual_thread as virtual_thread
from pcc.extern import c_int64, extern
_idle_sleep_ns = extern('pcc_platform_sleep_ns', (c_int64,), c_int64)

class TaskScopeError(RuntimeError):
    pass

class TaskScope:

    def __init__(self, name: str='scope') -> None:
        self.name = name
        self.children = []
        self.joined = False
        self.closed = False
        self.failure = None
        self.close_reason = ''

    @virtual_thread.continuation_factory
    def fork(self, thread):
        if self.closed or self.joined:
            return virtual_thread.continuation(_reject_scope_fork, self, thread)
        self.children.append(thread)
        return virtual_thread.completed(thread)

    def join(self) -> None:
        if self.joined:
            failure = self.failure
            if failure is not None:
                raise failure
            return
        self.joined = True
        failed_at = -1
        index = 0
        while index < len(self.children):
            try:
                virtual_thread.join(self.children[index])
            except Exception as error:
                self.failure = error
                failed_at = index
                break
            index += 1
        if failed_at >= 0:
            self._retire(failed_at + 1)
            failure = self.failure
            raise failure

    def result(self, thread):
        if not self.joined:
            raise TaskScopeError('scope ' + self.name + ': result read before join')
        return virtual_thread.result(thread)

    def outcome(self, thread) -> int:
        return virtual_thread.outcome(thread)

    @virtual_thread.continuation_factory
    def close(self, reason: str='scope closed'):
        if self.closed:
            return virtual_thread.completed(None)
        if self.joined:
            self.closed = True
            self.close_reason = reason
            return virtual_thread.completed(None)
        return virtual_thread.continuation(_close_scope_children, self, reason)

    def _close_unjoined(self, reason: str) -> None:
        if self.closed:
            return
        self.closed = True
        self.close_reason = reason
        if not self.joined:
            self.joined = True
            self._retire(0)

    def _retire(self, start: int) -> None:
        index = start
        while index < len(self.children):
            virtual_thread.cancel(self.children[index])
            index += 1
        index = start
        while index < len(self.children):
            self._drain_child(self.children[index])
            index += 1

    def _retire_child(self, child) -> None:
        virtual_thread.cancel(child)
        self._drain_child(child)

    def _drain_child(self, child) -> None:
        try:
            virtual_thread.join(child)
        except Exception:
            pass

def _reject_scope_fork(scope: 'TaskScope', thread):
    scope._retire_child(thread)
    if scope.closed:
        raise TaskScopeError('scope ' + scope.name + ' is closed')
    if scope.joined:
        raise TaskScopeError('scope ' + scope.name + ' already joined')
    scope.children.append(thread)
    return thread

def _close_scope_children(scope: 'TaskScope', reason: str):
    scope._close_unjoined(reason)

def run_until_complete(thread):
    if virtual_thread.current() is not None:
        raise TaskScopeError('run_until_complete requires an outer driver')
    while virtual_thread.outcome(thread) == virtual_thread.OUTCOME_PENDING:
        steps = virtual_thread.run(1, 4096)
        if steps < 0:
            raise TaskScopeError('virtual-thread scheduler failed')
        if steps == 0 and virtual_thread.outcome(thread) == virtual_thread.OUTCOME_PENDING:
            if _idle_sleep_ns(1000000) < 0:
                raise TaskScopeError('outer scheduler wait failed')
    failure = virtual_thread.exception(thread)
    if failure is not None:
        raise failure
    if virtual_thread.outcome(thread) != virtual_thread.OUTCOME_RETURNED:
        raise TaskScopeError('root task was cancelled')
    return virtual_thread.result(thread)
__all__ = ['TaskScope', 'TaskScopeError', 'run_until_complete']
'''

PROGRAM = r'''"""Matched two-fetch/JSON workload for pcc and pcc1 native artifacts."""

import json
import sys
import time

import pcc.virtual_thread as vt
from scope_lib import TaskScope, run_until_complete


def fetch_profile(delay_ms: int):
    vt.sleep_current(delay_ms)
    return {"name": "Ada", "plan": "pro"}


def fetch_notifications(delay_ms: int):
    vt.sleep_current(delay_ms)
    return ["Your report is ready"]


def request(delay_ms: int):
    started = time.perf_counter()
    scope = TaskScope("request")
    try:
        profile = scope.fork(vt.spawn(fetch_profile, delay_ms))
        notifications = scope.fork(vt.spawn(fetch_notifications, delay_ms))
        scope.join()
        data = {"profile": scope.result(profile),
                "notifications": scope.result(notifications)}
    finally:
        scope.close()
    body = json.dumps(data, sort_keys=True).encode()
    expected = b'{"notifications": ["Your report is ready"], "profile": {"name": "Ada", "plan": "pro"}}'
    if body != expected:
        raise RuntimeError("benchmark response mismatch: " + body.decode())
    return (time.perf_counter() - started) * 1000.0


def batch(concurrency: int, delay_ms: int):
    scope = TaskScope("batch")
    try:
        index = 0
        while index < concurrency:
            scope.fork(vt.spawn(request, delay_ms))
            index += 1
        scope.join()
        samples = []
        for child in scope.children:
            samples.append(scope.result(child))
        return samples
    finally:
        scope.close()


def benchmark():
    concurrency = int(sys.argv[1])
    delay_ms = int(sys.argv[2])
    rounds = int(sys.argv[3])
    warmup = batch(concurrency, delay_ms)
    second_warmup = batch(concurrency, delay_ms)
    warmup_requests = len(warmup) + len(second_warmup)
    samples = []
    started = time.perf_counter()
    index = 0
    while index < rounds:
        completed = batch(concurrency, delay_ms)
        samples.extend(completed)
        index += 1
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return {"concurrency": concurrency, "delay_ms": delay_ms,
            "rounds": rounds, "requests": len(samples),
            "warmup_requests": warmup_requests,
            "elapsed_ms": elapsed_ms, "latencies_ms": samples}


def main():
    thread = vt.spawn(benchmark)
    result = run_until_complete(thread)
    # Profilers need the request phase, without spending their samples on
    # formatting the final latency array. Normal comparison runs retain it.
    if len(sys.argv) > 4 and sys.argv[4] == "--summary":
        samples = result.pop("latencies_ms")
        result["latency_min_ms"] = min(samples)
        result["latency_max_ms"] = max(samples)
    print(json.dumps(result, sort_keys=True))


main()
'''


def test_scheduler_step_keeps_the_running_thread_on_five_collectors(
    tmp_path: Path, pcc_runtime_archive
):
    from pcc.frontends.python.pipeline import compile_python_multi

    scope = tmp_path / "scope_lib.py"
    program = tmp_path / "program.py"
    scope.write_text(SCOPE_LIB, encoding="utf-8")
    program.write_text(PROGRAM, encoding="utf-8")
    binary = tmp_path / "program"
    compile_python_multi([str(scope), str(program)], str(binary),
                         module_names=["scope_lib", "program"], entry_module="program",
                         ir_scaffold_mode="on", libpython_mode="off",
                         runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(binary), "100", "0", "3", "--summary"],
                             env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
                             capture_output=True, text=True, timeout=120)
        assert ran.returncode == 0, f"GC{backend}: " + ran.stderr[-2000:]
        summary = json.loads(ran.stdout.strip().splitlines()[-1])
        assert summary["requests"] == 300, f"GC{backend}: {summary}"
        assert summary["warmup_requests"] == 200, f"GC{backend}: {summary}"
