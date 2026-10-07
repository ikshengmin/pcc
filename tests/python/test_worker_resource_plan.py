"""Resource admission executes bounded real children before any bootstrap run."""

import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys
import time

import pytest

from pcc.frontends.python import pipeline_frontend_workers as workers
from pcc.frontends.python import worker_process_pool as pool
from pcc.frontends.python import worker_resource_plan as policy
from tests.python.owned_regression_support import explicit_owned_runtime
from tests.python.process_timeout import run_process_group_timeout


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


@pytest.mark.pcc_gate(probe=lambda: os.name == "posix")
def test_progressive_retry_drains_peers_then_restores_concurrency(tmp_path, monkeypatch):
    monkeypatch.delenv(policy.TREE_STATE_ENV, raising=False)
    child = tmp_path / "progressive.py"
    child.write_text("""
import os
from pathlib import Path
import sys
import time
from pcc.frontends.python.worker_resource_plan import (
    RESOURCE_REPORT_ENV, RESOURCE_TOKEN_ENV, publish_worker_resource,
)

root = Path(sys.argv[1])
index = int(sys.argv[2])
growth = int(sys.argv[3])
deadline = time.monotonic() + 6

def wait_for(predicate):
    while not predicate():
        assert time.monotonic() < deadline, (index, "coordination timed out")
        time.sleep(0.005)

def exists(name):
    return (root / name).exists()

def seen(event, number):
    trace = root / "progressive.tsv"
    return trace.exists() and any(
        line.split("\\t")[:2] == [event, str(number)]
        for line in trace.read_text().splitlines()
    )

count = root / ("attempts" + str(index))
attempt = int(count.read_text()) + 1 if count.exists() else 1
count.write_text(str(attempt))
record = str(os.getpid()) + "\\t" + os.environ[RESOURCE_TOKEN_ENV]
(root / ("started." + str(index) + "." + str(attempt))).write_text(record)

def overlap(name, peers):
    records = [root / ("started." + str(peer) + ".1") for peer in peers]
    wait_for(lambda: all(path.exists() for path in records))
    pids = [int(path.read_text().split("\\t")[0]) for path in records]
    for pid in pids:
        os.kill(pid, 0)
    (root / name).write_text(" ".join(str(pid) for pid in pids))

payloads = [bytearray(4 * 1024 ** 2)]
if index == 0:
    wait_for(lambda: exists("first-phase"))
    overlap("initial-overlap0", [0, 1])
    wait_for(lambda: exists("initial-overlap1"))
    payloads.append(bytearray(growth))
    publish_worker_resource("leader-grown")
    wait_for(lambda: seen("cancel", 1))
    time.sleep(0.15)
elif index == 1 and attempt == 1:
    publish_worker_resource("first-phase")
    (root / "first-phase").touch()
    overlap("initial-overlap1", [0, 1])
    wait_for(lambda: False)
elif index == 1:
    publish_worker_resource("retry-first-phase")
    time.sleep(0.08)
    payloads.append(bytearray(growth))
    publish_worker_resource("retry-second-phase")
    (root / "second-phase").write_text(str(time.monotonic()))
    time.sleep(0.2)
else:
    # Old admission starts task 2 beside the draining leader, then admits
    # the retry beside task 2. Keep task 2's report until the leader retires
    # so only the retry's later growth can cause the second cancellation.
    wait_for(lambda: seen("retire", 0))
    publish_worker_resource("tail-small")
    overlap("restored-overlap" + str(index), [2, 3])
    wait_for(lambda: exists("restored-overlap" + str(5 - index)))
    time.sleep(0.05)

publish_worker_resource("complete")
(root / ("complete." + str(index) + "." + str(attempt))).write_text(
    Path(os.environ[RESOURCE_REPORT_ENV]).read_text()
)
""")
    owner = workers._coordinator_rss_bytes()
    startup_peak = max(owner, workers._worker_peak_rss_bytes())
    growth = startup_peak + 128 * MIB
    available = 2 * policy.peak_reservation(startup_peak + 32 * MIB) + 32 * MIB
    budget = owner + policy.RSS_HEADROOM_BYTES + available
    (tmp_path / "configured-budget").write_text(str(budget))
    tasks = [task(tmp_path / ("rss" + str(index)), estimate=8 * MIB,
                  inputs=(4 - index,), execution_class="host:progressive:" + str(index))
             for index in range(4)]
    commands = [shlex.join([sys.executable, "-B", str(child), str(tmp_path),
                           str(index), str(growth)]) for index in range(4)]
    trace = tmp_path / "progressive.tsv"
    observations = []
    reaped = set()
    original_retire = pool._retire_resource_worker
    original_killpg = os.killpg

    def record_retire(pid):
        original_retire(pid)
        reaped.add(pid)

    def reject_signal_after_reap(pid, number):
        assert pid not in reaped
        return original_killpg(pid, number)

    monkeypatch.setattr(pool, "_retire_resource_worker", record_retire)
    monkeypatch.setattr(os, "killpg", reject_signal_after_reap)
    pool.run_resource_worker_processes(commands, tasks, 2, budget,
                                      observations=observations, trace_path=str(trace))
    rows = [line.split("\t") for line in trace.read_text().splitlines()]
    cancelled = [row for row in rows if row[0] == "cancel"]
    assert len(cancelled) == 1 and cancelled[0][1] == "1"
    retry_starts = [row for row in rows if row[:2] == ["start", "1"]]
    assert len(retry_starts) == 2
    events = {(row[0], row[1]): row for row in rows if row[0] in ("start", "retire")}
    assert float(events["retire", "0"][6]) < float(retry_starts[1][6])
    assert int(retry_starts[1][3]) == int(retry_starts[1][4])
    assert float(events["retire", "1"][6]) < float(events["start", "2"][6])
    assert float(events["start", "3"][6]) < float(events["retire", "2"][6])
    assert float(cancelled[0][6]) > float(retry_starts[0][6])
    assert float(retry_starts[1][6]) < float((tmp_path / "second-phase").read_text())
    assert [int((tmp_path / ("attempts" + str(index))).read_text())
            for index in range(4)] == [1, 2, 1, 1]
    first_attempt = (tmp_path / "started.1.1").read_text().split("\t")
    retry_attempt = (tmp_path / "started.1.2").read_text().split("\t")
    assert first_attempt[1] != retry_attempt[1]
    assert cancelled[0][2] == first_attempt[0]
    assert not (tmp_path / "complete.1.1").exists()
    completed_report = policy.read_worker_resource(
        str(tmp_path / "complete.1.2"), int(retry_attempt[0]), retry_attempt[1],
    )
    assert completed_report is not None and completed_report[0] == "complete"
    assert completed_report[2] > int(cancelled[0][5]) > 0
    assert tasks[1]["incomplete_peak_bytes"] == int(cancelled[0][5])
    assert tasks[1]["estimate_bytes"] == 8 * MIB
    assert tasks[1]["retry_calibration"] is False
    assert len(observations) == 4
    assert [sample[2] for sample in observations if sample[0] == tasks[1]["class"]] == [completed_report[2]]
    for name, peers in (("initial-overlap", (0, 1)), ("restored-overlap", (2, 3))):
        expected = [(tmp_path / ("started." + str(index) + ".1")).read_text().split("\t")[0]
                    for index in peers]
        for index in peers:
            assert (tmp_path / (name + str(index))).read_text().split() == expected
    for record in tmp_path.glob("started.*"):
        pid = int(record.read_text().split("\t")[0])
        assert pid in reaped
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    assert (tmp_path / "configured-budget").read_text() == str(budget)
    assert pool._HOST_WORKERS == {}


def test_retry_calibration_barrier_preserves_known_minimum():
    retry = task("unused-retry", estimate=8 * MIB)
    retry["retry_calibration"] = True
    retry["incomplete_peak_bytes"] = 384 * MIB
    other = task("unused-other", estimate=8 * MIB)
    available = 512 * MIB
    # Even a smaller fitting task ahead of the retry cannot prolong the
    # draining wave. Only completed observations may authorize a shared wave.
    assert policy.choose_task([0, 1], [other, retry], [], [8 * MIB], 2, available) == (-1, 0, False)
    index, demand, exclusive = policy.choose_task([0, 1], [other, retry], [], [], 2, available)
    assert index == 1 and exclusive
    assert demand == policy.peak_reservation(384 * MIB) > available
    with pytest.raises(policy.WorkerMemoryError, match="estimated_minimum_budget_bytes"):
        policy.require_task_fits(index, demand, available, 768 * MIB)


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


RESOURCE_NATIVE_DRIVER = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "native" / "worker_resource_admission.py"
)
RESOURCE_PORTABLE_CASES = (
    "oversized", "calibration", "growth", "failure", "spawn_failure",
)
RESOURCE_LINUX_HANDLE_CASES = ("handles",)
RESOURCE_DRIVER_CASES = RESOURCE_PORTABLE_CASES + RESOURCE_LINUX_HANDLE_CASES


def _resource_driver_environment(owner, collector):
    environment = dict(os.environ)
    environment.pop("LC_ALL", None)
    # Match the existing host policy tests' local owner/worker accounting.
    # The enclosing process-tree watchdog and its cap remain unchanged.
    for key in (policy.TREE_STATE_ENV, policy.RESOURCE_REPORT_ENV,
                policy.RESOURCE_TOKEN_ENV):
        environment.pop(key, None)
    environment.update({
        "PCC_TEST_RESOURCE_OWNER": owner,
        "PCC_GC_BACKEND": str(collector),
        "PCC_TEST_SETPGID_NR": "109" if platform.machine().lower() in ("x86_64", "amd64") else "154",
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PCC_TEST_NO_NATIVE_PROVISIONING": "1",
        "PCC_NO_AUTO_PCC1": "1",
    })
    if owner == "pcc":
        environment.update({
            "PATH": "", "PCC_HOST_PYTHON": "/nonexistent/host-python",
            "PCC_HOST_PCC": "/nonexistent/host-pcc",
            "PCC_RUNTIME_CC": "/nonexistent/runtime-compiler",
        })
    return environment


def _execute_resource_driver(prefix, case, directory, owner, collector):
    directory.mkdir()
    environment = _resource_driver_environment(owner, collector)
    command = [*prefix, case, str(directory)]
    result = run_process_group_timeout(command, env=environment, timeout=20)
    (directory / "driver.stdout").write_text(result.stdout)
    (directory / "driver.stderr").write_text(result.stderr)
    record = {
        "case": case,
        "boundary": "linux_owned_setpgid_handle_lifecycle" if case == "handles" else "shared_admission_policy",
        "owner": owner, "requested_collector": collector,
        "qualification_scope": "resource_component",
        "pcc1_stage2_worker_dispatch_proved": False,
        "child_launch_contract": "emitted_driver_self_spawn" if owner == "pcc" else "cpython_reference",
        "command": command, "returncode": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr,
        "policy_accounting_scope": "local_driver_and_workers",
        "outer_tree_cap_bytes": environment.get("PCC_WORKER_TREE_BUDGET_BYTES"),
        "artifacts": {},
    }
    # Retain every PID, attempt token, overlap witness and admission event.
    # No normalization substitutes for the assertions executed by the driver.
    for path in sorted(directory.iterdir()):
        if path.is_file():
            record["artifacts"][path.name] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
    receipt_path = directory / "execution.json"
    receipt_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    assert result.returncode == 0, record
    assert result.stdout == "RESOURCE_ACCEPTANCE_OK " + case + "\n", record
    assert result.stderr == "", record
    assert (directory / "complete").read_text() == owner + "\t" + str(collector)
    if case in RESOURCE_PORTABLE_CASES:
        budget = (directory / "budget.tsv").read_text().strip().split("\t")
        assert int(budget[0]) > 0 and int(budget[1]) > 0
        assert budget[4] == "measured-lower-bound-not-a-sufficient-budget"
        assert (directory / "configured-budget").read_text() == budget[0]
    # Actual child owner/collector readings must match the selected execution.
    # The native readings come from the owned pcc_gc_backend ABI, not merely
    # from echoing the environment selector.
    starts = sorted(directory.glob("started.*"))
    for path in starts:
        fields = path.read_text().split("\t")
        assert fields[1:3] == [owner, str(collector)], (path, fields)
        with pytest.raises(ProcessLookupError):
            os.kill(int(fields[0]), 0)
    if case in ("oversized", "spawn_failure"):
        assert starts == []
    elif case == "calibration":
        assert len(starts) == 4
    elif case == "growth":
        assert len(starts) == 3
    else:
        assert len(starts) == 2
    return record


@pytest.mark.pcc_gate(probe=lambda: os.name == "posix")
@pytest.mark.parametrize("case", RESOURCE_DRIVER_CASES)
def test_resource_acceptance_driver_matches_cpython(case, tmp_path):
    """The same real-child contract is retained as an executable host oracle."""
    _execute_resource_driver(
        [sys.executable, "-B", str(RESOURCE_NATIVE_DRIVER)],
        case, tmp_path / case, "cpython", 0,
    )


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=lambda: sys.platform.startswith("linux") and platform.machine().lower() in (
    "x86_64", "amd64", "aarch64", "arm64",
))
def test_resource_acceptance_driver_native_five_collectors(
    tmp_path, explicit_owned_runtime, python_program_compiler, request, capfd,
):
    """Compile once, then execute real native policy/handles on all five GCs.

    Requires an explicitly selected source-matched runtime; the maintained
    fixture forbids provisioning a runtime or a compiler. The Linux-only
    moved-group no-signal witness is recorded separately from shared policy.
    """
    binary = tmp_path / "resource-admission.out"
    receipt = {
        "status": "COMPILING",
        "compiler_parameter": request.node.callspec.params["python_program_compiler"],
        "compiler_module": python_program_compiler.__module__,
        "backend": "self", "libpython": "off", "ir_scaffold": "on",
        "qualification_scope": "native_resource_component",
        "pcc1_stage2_worker_dispatch_proved": False,
        "native_child_launch_contract": "same_PCC_emitted_ELF_self_spawn",
        "source": str(RESOURCE_NATIVE_DRIVER),
        "source_sha256": hashlib.sha256(RESOURCE_NATIVE_DRIVER.read_bytes()).hexdigest(),
        "runtime_archive": str(explicit_owned_runtime),
        "runtime_sha256": hashlib.sha256(explicit_owned_runtime.read_bytes()).hexdigest(),
        "executions": [],
    }
    receipt_path = tmp_path / "native-resource-acceptance.json"

    def save():
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")

    save()
    try:
        python_program_compiler(
            str(RESOURCE_NATIVE_DRIVER), str(binary), backend="self",
            libpython_mode="off", ir_scaffold_mode="on",
            runtime_archive=str(explicit_owned_runtime),
        )
    except Exception as error:
        receipt["status"] = "COMPILE_FAILED"
        receipt["error"] = type(error).__name__ + ": " + str(error)
        save()
        raise
    finally:
        captured = capfd.readouterr()
        (tmp_path / "compile.stdout").write_text(captured.out)
        (tmp_path / "compile.stderr").write_text(captured.err)
    assert binary.is_file()
    magic = binary.read_bytes()[:4]
    assert magic == b"\x7fELF", "native resource driver must be an ELF executable"
    receipt["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
    receipt["status"] = "RUNNING"
    save()
    for collector in range(5):
        for case in RESOURCE_DRIVER_CASES:
            try:
                record = _execute_resource_driver(
                    [str(binary)], case,
                    tmp_path / ("gc" + str(collector) + "-" + case),
                    "pcc", collector,
                )
            except BaseException as error:
                receipt["status"] = "NATIVE_EXECUTION_FAILED"
                receipt["failed_case"] = case
                receipt["failed_collector"] = collector
                receipt["error"] = type(error).__name__ + ": " + str(error)
                save()
                raise
            receipt["executions"].append(record)
            save()
    assert len(receipt["executions"]) == 5 * len(RESOURCE_DRIVER_CASES)
    receipt["status"] = "PASS"
    save()
