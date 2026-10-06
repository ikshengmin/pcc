"""Resource admission executes bounded real children before any bootstrap run."""

import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

import pytest

from pcc.frontends.python import pipeline_frontend_workers as workers
from pcc.frontends.python import worker_process_pool as pool
from pcc.frontends.python import worker_resource_plan as policy


MIB = 1024 ** 2


def task(report, *, estimate=0, inputs=(1,), execution_class="host:test"):
    return {"report_path": str(report), "estimate_bytes": estimate,
            "inputs": list(inputs), "class": execution_class, "restartable": True}


def command(code):
    return shlex.join([sys.executable, "-B", "-c", code])


def child_code(marker, allocated=4 * MIB, delay=0.05, *, write_pid=False):
    marker_value = "str(os.getpid())" if write_pid else "'started'"
    return (
        "import os,time;from pathlib import Path;"
        "from pcc.frontends.python.worker_resource_plan import publish_worker_resource as report;"
        f"payload=bytearray({allocated});Path({str(marker)!r}).write_text({marker_value});"
        f"report('allocated');time.sleep({delay});report('complete')"
    )


def test_oversized_bytes_refuse_before_any_real_child_starts(tmp_path, monkeypatch):
    monkeypatch.delenv(policy.TREE_STATE_ENV, raising=False)
    marker = tmp_path / "must-not-launch"
    tasks = [task(tmp_path / "rss", estimate=300 * MIB)]
    owner = workers._coordinator_rss_bytes()
    budget = owner + policy.RSS_HEADROOM_BYTES + 299 * MIB
    with pytest.raises(policy.WorkerMemoryError, match="estimated_minimum_budget_bytes"):
        pool.run_resource_worker_processes(
            [command(child_code(marker))], tasks, 4, budget, observations=[],
        )
    assert not marker.exists()
    assert pool._HOST_WORKERS == {}


def test_uncalibrated_task_retires_then_admits_multiple_real_children(tmp_path, monkeypatch):
    monkeypatch.delenv(policy.TREE_STATE_ENV, raising=False)
    markers = [tmp_path / ("worker" + str(index)) for index in range(4)]
    tasks = [task(tmp_path / ("rss" + str(index)), inputs=(4 - index,))
             for index in range(4)]
    trace = tmp_path / "admission.tsv"
    observations = []
    pool.run_resource_worker_processes(
        [command(child_code(marker, delay=0.15)) for marker in markers], tasks,
        3, workers._coordinator_rss_bytes() + policy.RSS_HEADROOM_BYTES + 768 * MIB,
        observations=observations, trace_path=str(trace),
    )
    rows = [line.split("\t") for line in trace.read_text().splitlines()]
    assert rows[0][0] == "calibrate"
    assert rows[1][0] == "retire"
    events = [row[0] for row in rows[2:]]
    assert events[:3] == ["start", "start", "start"]
    assert all(marker.read_text() == "started" for marker in markers)
    assert len(observations) == 4
    assert all(sample[2] > 0 for sample in observations)
    assert pool._HOST_WORKERS == {}


def test_live_peak_growth_backs_off_until_worker_retires(tmp_path, monkeypatch):
    monkeypatch.delenv(policy.TREE_STATE_ENV, raising=False)
    first, second = tmp_path / "first", tmp_path / "second"
    # The first input is deliberately underestimated; its real 48 MiB
    # allocation raises the reservation above the spare worker capacity.
    tasks = [task(tmp_path / "rss0", estimate=8 * MIB, inputs=(2,)),
             task(tmp_path / "rss1", estimate=160 * MIB, inputs=(1,))]
    trace = tmp_path / "trace"
    original_start = pool._start_resource_worker

    def wait_for_report(specs, index):
        pid = original_start(specs, index)
        if index == 0:
            deadline = time.monotonic() + 3
            while not Path(tasks[index]["report_path"]).exists():
                assert time.monotonic() < deadline
                time.sleep(0.005)
        return pid

    monkeypatch.setattr(pool, "_start_resource_worker", wait_for_report)
    budget = workers._coordinator_rss_bytes() + policy.RSS_HEADROOM_BYTES + 250 * MIB
    pool.run_resource_worker_processes(
        [command(child_code(first, allocated=48 * MIB, delay=0.25)),
         command(child_code(second))], tasks, 2, budget,
        observations=[], trace_path=str(trace),
    )
    rows = [line.split("\t") for line in trace.read_text().splitlines()]
    first_retired = next(index for index, row in enumerate(rows) if row[:2] == ["retire", "0"])
    second_started = next(index for index, row in enumerate(rows) if row[:2] == ["start", "1"])
    assert any(row[0] == "backoff" for row in rows)
    assert first_retired < second_started
    assert pool._HOST_WORKERS == {}


def test_failure_retires_running_peers_and_does_not_launch_tail(tmp_path, monkeypatch):
    monkeypatch.delenv(policy.TREE_STATE_ENV, raising=False)
    slow, tail = tmp_path / "slow", tmp_path / "tail"
    overlap = tmp_path / "overlap"
    owner_rss = workers._coordinator_rss_bytes()
    # A fork/exec child can inherit its parent's prior high-water reading.
    # Reserve that observed startup envelope so this cleanup test actually
    # overlaps peers even when collected with the larger compiler suites.
    startup_reservation = policy.peak_reservation(owner_rss + 32 * MIB)
    tasks = [task(tmp_path / ("rss" + str(index)), estimate=startup_reservation,
                  inputs=(3 - index,)) for index in range(3)]
    started = time.monotonic()
    with pytest.raises(subprocess.CalledProcessError) as failure:
        pool.run_resource_worker_processes(
            [command(child_code(slow, delay=20, write_pid=True)),
             command("import os,time;from pathlib import Path;"
                     + "deadline=time.monotonic()+3\n"
                     + "while not Path(" + repr(str(slow)) + ").exists() and time.monotonic()<deadline: time.sleep(0.01)\n"
                     + "peer=int(Path(" + repr(str(slow)) + ").read_text());os.kill(peer,0)\n"
                     + "Path(" + repr(str(overlap)) + ").write_text(str(peer)+' '+str(os.getpid()))\n"
                     + "raise SystemExit(7)"),
             command(child_code(tail))], tasks, 2,
            owner_rss + policy.RSS_HEADROOM_BYTES + 2 * startup_reservation + 32 * MIB,
            observations=[],
        )
    assert failure.value.returncode == 7
    assert time.monotonic() - started < 4
    assert slow.exists() and not tail.exists()
    assert overlap.exists()
    for pid in (int(value) for value in overlap.read_text().split()):
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    assert pool._HOST_WORKERS == {}


def test_overlapping_growth_cancels_and_requeues_one_peer_with_same_cap(tmp_path, monkeypatch):
    monkeypatch.delenv(policy.TREE_STATE_ENV, raising=False)
    ready = [tmp_path / "ready0", tmp_path / "ready1"]
    counts = [tmp_path / "starts0", tmp_path / "starts1"]
    commands = []
    for index in range(2):
        code = (
            "import os,time;from pathlib import Path;"
            "from pcc.frontends.python.worker_resource_plan import publish_worker_resource as report;"
            + "Path(" + repr(str(ready[index])) + ").write_text(str(os.getpid()));"
            + "count=Path(" + repr(str(counts[index])) + ");"
            + "count.write_text(count.read_text()+'x' if count.exists() else 'x');"
            + "deadline=time.monotonic()+3\n"
            + "while not Path(" + repr(str(ready[1 - index])) + ").exists() and time.monotonic()<deadline: time.sleep(0.01)\n"
            + "payload=bytearray(48*1024**2);report('allocated');time.sleep(0.3);report('complete')"
        )
        commands.append(command(code))
    tasks = [task(tmp_path / ("rss" + str(index)), estimate=8 * MIB,
                  inputs=(2 - index,)) for index in range(2)]
    trace = tmp_path / "overlap.tsv"
    budget = workers._coordinator_rss_bytes() + policy.RSS_HEADROOM_BYTES + 330 * MIB
    pool.run_resource_worker_processes(commands, tasks, 2, budget,
                                      observations=[], trace_path=str(trace))
    rows = [line.split("\t") for line in trace.read_text().splitlines()]
    cancelled = [row for row in rows if row[0] == "cancel"]
    assert len(cancelled) == 1
    assert cancelled[0][1] == "1"
    first_retired = next(index for index, row in enumerate(rows) if row[:2] == ["retire", "0"])
    second_starts = [index for index, row in enumerate(rows) if row[:2] == ["start", "1"]]
    assert len(second_starts) == 2 and first_retired < second_starts[1]
    assert counts[0].read_text() == "x" and counts[1].read_text() == "xx"
    original_start = rows[second_starts[0]]
    assert float(cancelled[0][6]) > float(original_start[6])
    with pytest.raises(ProcessLookupError):
        os.kill(int(cancelled[0][2]), 0)
    assert pool._HOST_WORKERS == {}


def test_tree_accounting_includes_wrappers_and_child_descendants(tmp_path):
    state = tmp_path / "tree.tsv"
    state.write_text(policy.TREE_STATE_SCHEMA + "\n" + str(time.monotonic())
                     + "\n1000\n10\t1\t50\n11\t10\t100\n12\t11\t200\n13\t12\t30\n")
    assert policy.read_tree_state(str(state), 1000, 11, [12]) == (50, {12: 230})
    assert policy.read_tree_state(str(state), 999, 11, [12]) is None
    state.write_text(state.read_text().replace(policy.TREE_STATE_SCHEMA, "unknown"))
    assert policy.read_tree_state(str(state), 1000, 11, [12]) is None


def test_stale_state_prevents_launch(tmp_path, monkeypatch):
    state = tmp_path / "tree.tsv"
    marker = tmp_path / "must-not-run"
    budget = 512 * MIB
    state.write_text(policy.TREE_STATE_SCHEMA + "\n0\n" + str(budget)
                     + "\n" + str(os.getpid()) + "\t1\t100\n")
    monkeypatch.setenv(policy.TREE_STATE_ENV, str(state))
    with pytest.raises(policy.WorkerMemoryError, match="missing, stale, or incompatible"):
        pool.run_resource_worker_processes(
            [command(child_code(marker))], [task(tmp_path / "rss")], 2, budget,
            observations=[],
        )
    assert not marker.exists()


def test_worker_class_and_input_envelope_do_not_reuse_wrong_peak():
    item = task("unused", inputs=(5, 3), execution_class="host:codegen:full")
    observations = [("native:codegen:full", [9, 9], 100),
                    ("host:codegen:full", [4, 9], 200)]
    assert policy.estimated_task_bytes(item, observations) == 0
    observations.append(("host:codegen:full", [9, 9], 300))
    assert policy.estimated_task_bytes(item, observations) == policy.peak_reservation(300)


def test_successful_child_completes_between_live_report_and_poll(tmp_path, monkeypatch):
    monkeypatch.delenv(policy.TREE_STATE_ENV, raising=False)
    go = tmp_path / "go"
    report_path = tmp_path / "rss"
    original_read = policy.read_worker_resource
    original_poll = pool._poll_resource_worker
    interleaved = []

    def stale_read(path, pid, token=""):
        report = original_read(path, pid, token)
        if report is not None and report[0] == "allocated" and not interleaved:
            interleaved.append(report)
            go.touch()
        return report

    def completed_poll(pid):
        if interleaved:
            deadline = time.monotonic() + 3
            while True:
                result = original_poll(pid)
                if result != pool._WORKER_RUNNING:
                    return result
                assert time.monotonic() < deadline
                time.sleep(0.005)
        return original_poll(pid)

    monkeypatch.setattr(policy, "read_worker_resource", stale_read)
    monkeypatch.setattr(pool, "_poll_resource_worker", completed_poll)
    code = (
        "import time;from pathlib import Path;"
        "from pcc.frontends.python.worker_resource_plan import publish_worker_resource as report;"
        "report('allocated');deadline=time.monotonic()+3\n"
        "while not Path(" + repr(str(go)) + ").exists() and time.monotonic()<deadline: time.sleep(0.005)\n"
        "report('complete')"
    )
    observations = []
    pool.run_resource_worker_processes([command(code)], [task(report_path)], 1,
                                      workers._coordinator_rss_bytes() + 512 * MIB,
                                      observations=observations)
    assert interleaved and interleaved[0][0] == "allocated"
    assert len(observations) == 1
    assert pool._HOST_WORKERS == {}


def test_resource_report_rejects_another_attempt_or_pid(tmp_path):
    report = tmp_path / "rss"
    report.write_text(policy.RESOURCE_REPORT_SCHEMA + "\n42\ncomplete\n100\n120\ncorrect-token\n")
    assert policy.read_worker_resource(str(report), 42, "correct-token") == ("complete", 100, 120)
    assert policy.read_worker_resource(str(report), 42, "stale-token") is None
    assert policy.read_worker_resource(str(report), 43, "correct-token") is None


def test_native_handle_inventory_never_polls_or_signals_an_unowned_pid(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(pool, "sys", SimpleNamespace(implementation=SimpleNamespace(name="pcc")))
    monkeypatch.setattr(pool, "_NATIVE_LIVE_WORKERS", set())
    monkeypatch.setattr(pool, "_NATIVE_COMPLETED_WORKERS", {})
    actions = []
    monkeypatch.setattr(pool, "_native_worker_start", lambda _specs, _index: 42)
    monkeypatch.setattr(pool, "_native_worker_poll", lambda pid: actions.append(("wait", pid)) or 0)
    monkeypatch.setattr(pool, "_native_worker_stop", lambda pid: actions.append(("stop", pid)) or 0)
    assert pool._start_resource_worker([], 0) == 42
    assert pool._poll_resource_worker(42) == 0
    assert pool._poll_resource_worker(42) == 0
    pool._retire_resource_worker(42)
    pool._stop_resource_worker(42)
    pool._stop_resource_worker(999)
    with pytest.raises(ValueError, match="unowned"):
        pool._poll_resource_worker(42)
    assert actions == [("wait", 42)]


def test_host_chunk_count_is_independent_of_admitted_concurrency():
    counts = [workers.codegen_chunk_count(454, jobs, ["python", "-m", "pcc"],
                                         native_predicate=lambda _: False)
              for jobs in (1, 2, 4, 10)]
    assert counts == [454, 454, 454, 454]
