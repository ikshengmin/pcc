"""A virtual thread runs on one carrier at a time and keeps its own writes.

Each of twelve virtual threads counts in its own list and yields every third
step, on four carriers, so threads move between carriers thousands of times.
No two threads share data, yet the scheduler used to lose counts and finish
threads with None (about every other run):

* a wakeup queued a thread while its carrier was still leaving it (after it
  parked or yielded, before its resume returned), so a second carrier ran it
  concurrently from its previous continuation;
* a thread could be queued twice, and popping the stale node cleared the
  mark of the carrier running it;
* after queueing the thread at the end of a step, the step still read its
  state and completed it with None when it found it RUNNING -- another
  carrier's run.

A thread now carries a running mark from its dequeue until the step's last
action (_step_finished), wakeups in that window only make it READY, and the
step queues it then. Every count and every result must be exact on all five
collectors.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.python.pcc1_gate import repo_root

THREADS = 12
STEPS = 4000


def _program() -> str:
    lines = ["import pcc.virtual_thread as vt", ""]
    lines += [f"c{index} = [0]" for index in range(THREADS)]
    lines.append("")
    for index in range(THREADS):
        lines += [
            f"def w{index}():",
            f"    for step in range({STEPS}):",
            f"        c{index}[0] = c{index}[0] + 1",
            "        if step % 3 == 0:",
            "            vt.yield_now()",
            f"    return {index}",
            "",
        ]
    lines += ["def main():", "    workers = []"]
    lines += [f"    workers.append(vt.spawn(w{index}))" for index in range(THREADS)]
    lines += [
        "    vt.run(4, 10000000)",
        "    print([" + ", ".join(f"c{index}[0]" for index in range(THREADS)) + "])",
        "    print([vt.result(worker) for worker in workers])",
        "",
        "",
        "main()",
        "",
    ]
    return "\n".join(lines)


EXPECTED = (
    "[" + ", ".join([str(STEPS)] * THREADS) + "]\n"
    + "[" + ", ".join(str(index) for index in range(THREADS)) + "]\n"
)


def _environment(backend=None):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env.pop("PCC_WITH_THREADS", None)
    if backend is not None:
        env["PCC_GC_BACKEND"] = backend
    return env


@pytest.mark.integration
def test_migrating_virtual_threads_keep_their_writes_and_results(tmp_path: Path) -> None:
    source = tmp_path / "vthread_migration.py"
    source.write_text(_program(), encoding="utf-8")
    executable = tmp_path / "vthread_migration"
    build = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
         str(source), "-o", str(executable)],
        cwd=repo_root(), text=True, capture_output=True, timeout=900, env=_environment(),
    )
    assert build.returncode == 0, build.stderr
    # About every other run of the old scheduler lost a count or a result.
    for backend in ("0", "1", "2", "3", "4"):
        for attempt in range(4):
            run = subprocess.run([str(executable)], text=True, capture_output=True,
                                 timeout=300, env=_environment(backend))
            assert run.returncode == 0, (backend, attempt, run.stderr[-2000:])
            assert run.stdout == EXPECTED, (backend, attempt, run.stdout)
