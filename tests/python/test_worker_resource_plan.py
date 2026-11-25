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
import weakref

import pytest

from pcc.frontends.python import pipeline_frontend_workers as workers
from pcc.frontends.python import worker_process_pool as pool
from pcc.frontends.python import worker_resource_plan as policy
from tests.python.owned_regression_support import explicit_owned_runtime
from tests.python.process_timeout import run_process_group_timeout, run_process_tree_timeout
from tests.python.process_liveness import require_process_running
from tests.python.resource_worker_fixture import (
    live_forecast_pressure_plan,
    start_resource_worker_and_wait,
)


MIB = 1024 ** 2
# These tests deliberately create memory pressure in real child processes.
# Keep their fixtures from overlapping while retaining the default six workers.
pytestmark = pytest.mark.xdist_group(name="worker_resource_fixtures")


def task(report, *, estimate=0, inputs=(1,), execution_class="host:test"):
    return {"report_path": str(report), "estimate_bytes": estimate,
            "inputs": list(inputs), "class": execution_class, "restartable": True}


def command(code):
    return shlex.join([sys.executable, "-B", "-c", code])


def _spec_lifetime_fixture(tmp_path, monkeypatch, count=2):
    """Observe real payload lifetime without retaining spawn's borrowed values."""
    class TrackedArgv(list):
        pass

    class TrackedEnvironmentEntry(str):
        pass

    state = {"refs": [], "specs": None, "starts": [], "retired": [], "stopped": []}
    commands = ["worker-" + str(index) for index in range(count)]
    tasks = [task(tmp_path / ("rss-" + str(index)), estimate=16 * MIB)
             for index in range(count)]

    def command_spec(command):
        argv = TrackedArgv([command])
        entry = TrackedEnvironmentEntry("PCC_SPEC_LIFETIME=" + command)
        state["refs"].append((weakref.ref(argv), weakref.ref(entry)))
        return argv, [entry]

    def start(specs, index):
        state["specs"] = specs
        assert specs[index][0] == [commands[index]]
        token_prefix = policy.RESOURCE_TOKEN_ENV + "="
        tokens = [entry[len(token_prefix):] for entry in specs[index][1]
                  if entry.startswith(token_prefix)]
        assert len(tokens) == 1
        pid = 100 + len(state["starts"])
        state["starts"].append((index, pid, tokens[0]))
        return pid

    monkeypatch.delenv(policy.TREE_STATE_ENV, raising=False)
    monkeypatch.setattr(pool, "_command_spec", command_spec)
    monkeypatch.setattr(pool, "_start_resource_worker", start)
    monkeypatch.setattr(pool, "_poll_resource_worker", lambda pid: 0)
    monkeypatch.setattr(pool, "_retire_resource_worker", state["retired"].append)
    monkeypatch.setattr(pool, "_stop_resource_worker", state["stopped"].append)
    monkeypatch.setattr(workers, "_coordinator_rss_bytes", lambda: MIB)
    monkeypatch.setattr(policy, "read_tree_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(policy, "read_worker_resource",
                        lambda path, pid, token: ("complete", MIB, MIB))
    return state, commands, tasks


@pytest.mark.parametrize("order", ((0, 1), (1, 0)))
def test_completed_worker_releases_spec_before_next_admission(tmp_path, monkeypatch, order):
    state, commands, tasks = _spec_lifetime_fixture(tmp_path, monkeypatch)
    original_commands = list(commands)
    original_tasks = [dict(item, inputs=list(item["inputs"])) for item in tasks]
    monkeypatch.setattr(policy, "resource_task_order", lambda *args, **kwargs: (list(order), []))
    boundaries = []

    def coordinator_rss():
        if state["retired"]:
            # Check before another launch can overwrite argv/vector locals.
            retired = order[0]
            assert state["specs"][retired] == ([], [])
            assert all(ref() is None for ref in state["refs"][retired])
            pending = order[1]
            assert all(ref() is not None for ref in state["refs"][pending])
            boundaries.append(retired)
        return MIB

    monkeypatch.setattr(workers, "_coordinator_rss_bytes", coordinator_rss)
    observations = []
    pool.run_resource_worker_processes(commands, tasks, 1, 512 * MIB, observations=observations)
    assert boundaries == [order[0]]
    assert [item[0] for item in state["starts"]] == list(order)
    assert state["specs"] == [([], []), ([], [])]
    assert all(ref() is None for refs in state["refs"] for ref in refs)
    assert commands == original_commands and tasks == original_tasks
    assert len(observations) == 2
    assert state["stopped"] == []


@pytest.mark.parametrize("failure", ("spawn", "exit", "missing", "live", "handoff", "observation"))
def test_unverified_worker_keeps_spec(tmp_path, monkeypatch, failure):
    from pcc.frontends.python import pipeline_indexed_handoff as handoff

    state, commands, tasks = _spec_lifetime_fixture(tmp_path, monkeypatch, count=1)

    def fail(*args):
        raise RuntimeError("completion failed")

    error = RuntimeError
    message = "completion failed"
    if failure == "spawn":
        start = pool._start_resource_worker

        def failed_start(specs, index):
            start(specs, index)
            return -1

        monkeypatch.setattr(pool, "_start_resource_worker", failed_start)
        error, message = subprocess.CalledProcessError, "127"
    elif failure == "exit":
        monkeypatch.setattr(pool, "_poll_resource_worker", lambda pid: 7)
        error, message = subprocess.CalledProcessError, "7"
    elif failure in ("missing", "live"):
        report = None if failure == "missing" else ("allocated", MIB, MIB)
        monkeypatch.setattr(policy, "read_worker_resource", lambda *args: report)
        error, message = policy.WorkerMemoryError, "did not publish final peak RSS"
    elif failure == "handoff":
        tasks[0].update(handoff_request="request", input_ready=True, handoff_seal_sha256="seal")
        monkeypatch.setattr(handoff, "reset_handoff_output", lambda item: None)
        monkeypatch.setattr(handoff, "retire_handoff_task", fail)
    else:
        monkeypatch.setattr(policy, "completed_task_observation", fail)

    with pytest.raises(error, match=message):
        pool.run_resource_worker_processes(commands, tasks, 1, 512 * MIB, observations=[])
    assert state["specs"][0][0] == commands
    assert all(ref() is not None for ref in state["refs"][0])


def test_retired_spec_preserves_completed_handoff_token(tmp_path, monkeypatch):
    from pcc.frontends.python import pipeline_indexed_handoff as handoff

    state, commands, tasks = _spec_lifetime_fixture(tmp_path, monkeypatch)
    tasks[0]["handoff_slot"] = 0
    tasks[1].update(depends_on=0, handoff_slot=0, handoff_request="request")
    prepared = []
    retired = []

    def prepare(item, token):
        assert state["specs"][0] == ([], [])
        assert all(ref() is None for ref in state["refs"][0])
        assert token == state["starts"][0][2]
        prepared.append(token)
        item.update(input_ready=True, handoff_seal_sha256="seal")

    monkeypatch.setattr(handoff, "prepare_handoff_task", prepare)
    monkeypatch.setattr(handoff, "reset_handoff_output", lambda item: None)
    monkeypatch.setattr(handoff, "retire_handoff_task", lambda item, token: retired.append(token))
    pool.run_resource_worker_processes(commands, tasks, 1, 512 * MIB, observations=[])
    assert prepared == [state["starts"][0][2]]
    assert retired == [state["starts"][1][2]]
    assert all(ref() is None for refs in state["refs"] for ref in refs)


def test_cancelled_worker_keeps_spec_until_verified_retry(tmp_path, monkeypatch):
    state, commands, tasks = _spec_lifetime_fixture(tmp_path, monkeypatch)
    start = pool._start_resource_worker
    polls = []

    def retry_start(specs, index):
        if len(state["starts"]) == 2:
            assert index == 1 and state["stopped"] == [101]
            assert specs[0] == ([], [])
            assert all(ref() is None for ref in state["refs"][0])
            assert all(ref() is not None for ref in state["refs"][1])
        return start(specs, index)

    def poll(pid):
        polls.append(pid)
        assert len(polls) <= 12
        assert len(state["starts"]) <= 3
        if state["stopped"] or pid == 102:
            return 0
        return pool._WORKER_RUNNING

    def report(path, pid, token):
        assert token == next(item[2] for item in state["starts"] if item[1] == pid)
        peak = 200 * MIB if len(state["starts"]) >= 2 and pid != 102 else MIB
        phase = "complete" if state["stopped"] or pid == 102 else "allocated"
        return phase, peak, peak

    monkeypatch.setattr(pool, "_start_resource_worker", retry_start)
    monkeypatch.setattr(pool, "_poll_resource_worker", poll)
    monkeypatch.setattr(policy, "read_worker_resource", report)
    pool.run_resource_worker_processes(commands, tasks, 2, 512 * MIB, observations=[])
    assert [item[0] for item in state["starts"]] == [0, 1, 1]
    assert state["starts"][1][2] != state["starts"][2][2]
    assert state["stopped"] == [101]
    assert tasks[1]["retry_calibration"] is False
    assert state["specs"] == [([], []), ([], [])]
    assert all(ref() is None for refs in state["refs"] for ref in refs)


def child_code(marker, allocated=4 * MIB, delay=0.05, *, write_pid=False):
    marker_value = "str(os.getpid())" if write_pid else "'started'"
    return (
        "import os,time;from pathlib import Path;"
        "from pcc.frontends.python.worker_resource_plan import publish_worker_resource as report;"
        f"payload=bytearray({allocated});Path({str(marker)!r}).write_text({marker_value});"
        f"report('allocated');time.sleep({delay});report('complete')"
    )


@pytest.mark.parametrize("failure", ("timeout", "predicate"))
def test_startup_wait_failure_stops_and_reaps_unreturned_child(tmp_path, monkeypatch, failure):
    monkeypatch.delenv(policy.TREE_STATE_ENV, raising=False)
    original_start = pool._start_resource_worker
    children = []

    def start(specs, index):
        pid = original_start(specs, index)
        children.append(pool._HOST_WORKERS[pid])
        return pid

    def ready():
        if failure == "predicate":
            raise RuntimeError("startup predicate failed")
        return False

    def wait_for_report(specs, index):
        return start_resource_worker_and_wait(start, specs, index, ready, timeout=0)

    monkeypatch.setattr(pool, "_start_resource_worker", wait_for_report)
    error = AssertionError if failure == "timeout" else RuntimeError
    message = "worker startup report timed out" if failure == "timeout" else "startup predicate failed"
    with pytest.raises(error, match=message):
        pool.run_resource_worker_processes(
            [command("import time;time.sleep(30)")],
            [task(tmp_path / "rss", estimate=8 * MIB)], 1,
            workers._coordinator_rss_bytes() + policy.RSS_HEADROOM_BYTES + 64 * MIB,
            observations=[],
        )
    assert len(children) == 1
    assert children[0].returncode is not None
    assert pool._HOST_WORKERS == {}
    if os.name == "posix":
        with pytest.raises(ChildProcessError):
            os.waitpid(children[0].pid, os.WNOHANG)


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


def test_uncalibrated_task_retires_then_admits_multiple_real_children(tmp_path):
    # A long-lived pytest worker can retain compiler heaps exceeding this
    # scenario's worker space. Forked children inherit that RSS high-water
    # mark even after exec. Run the unchanged admission contract from a fresh
    # coordinator, not by raising its budget or replacing resource readings.
    environment = _resource_driver_environment("cpython", 3)
    result = run_process_group_timeout(
        [sys.executable, "-B", "-c",
         "from pathlib import Path; import sys; "
         "from tests.python.test_worker_resource_plan import _uncalibrated_children_scenario; "
         "_uncalibrated_children_scenario(Path(sys.argv[1]))", str(tmp_path)],
        cwd=Path(__file__).resolve().parents[2], env=environment, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _uncalibrated_children_scenario(tmp_path):
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
    if sys.platform.startswith("linux"):
        # On Linux, forking from a resident pytest process can give the first
        # child's real high-water report a reservation above all 250 MiB.
        # That is a valid solo refusal, not the intended live peer backoff.
        # Keep pytest under its outer tree cap; supervise the fresh coordinator
        # and its workers (which create their own process groups) together.
        result = run_process_tree_timeout(
            [sys.executable, "-B", "-c",
             "from pathlib import Path; import sys; "
             "from tests.python.test_worker_resource_plan import _live_peak_growth_scenario; "
             "_live_peak_growth_scenario(Path(sys.argv[1]))", str(tmp_path)],
            cwd=Path(__file__).resolve().parents[2], evidence=tmp_path / "coordinator",
            timeout=10,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        _live_peak_growth_scenario(tmp_path)
    assert pool._HOST_WORKERS == {}


def _live_peak_growth_scenario(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    # The first input is deliberately underestimated; its real 48 MiB
    # allocation raises the reservation above the spare worker capacity.
    tasks = [task(tmp_path / "rss0", estimate=8 * MIB, inputs=(2,)),
             task(tmp_path / "rss1", estimate=160 * MIB, inputs=(1,))]
    trace = tmp_path / "trace"
    original_start = pool._start_resource_worker

    def wait_for_report(specs, index):
        return start_resource_worker_and_wait(
            original_start, specs, index,
            lambda: index != 0 or Path(tasks[index]["report_path"]).exists(), timeout=3,
        )

    observations = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(pool, "_start_resource_worker", wait_for_report)
        budget = workers._coordinator_rss_bytes() + policy.RSS_HEADROOM_BYTES + 250 * MIB
        pool.run_resource_worker_processes(
            [command(child_code(first, allocated=48 * MIB, delay=0.25)),
             command(child_code(second))], tasks, 2, budget,
            observations=observations, trace_path=str(trace),
        )
    rows = [line.split("\t") for line in trace.read_text().splitlines()]
    assert [row[:2] for row in rows if row[0] != "backoff"] == [
        ["start", "0"], ["retire", "0"], ["start", "1"], ["retire", "1"],
    ]
    first_retired = next(index for index, row in enumerate(rows) if row[:2] == ["retire", "0"])
    # The first worker alone still fits, but its grown reservation plus the
    # pending second task no longer does. Both must then finish under the cap.
    assert any(row[0] == "backoff"
               and tasks[0]["estimate_bytes"] < int(row[3]) <= int(row[4])
               < int(row[3]) + tasks[1]["estimate_bytes"] for row in rows[:first_retired])
    assert [sample[1] for sample in observations] == [[2], [1]]
    assert observations[0][2] >= 48 * MIB
    assert first.read_text() == second.read_text() == "started"
    assert pool._HOST_WORKERS == {}


@pytest.mark.parametrize("measured_limit", (False, True))
def test_live_forecast_pressure_uses_measured_cold_tail_reservations(measured_limit):
    owner = 46 * MIB
    peaks = [49324032, 49496064]
    # The clean, bytecode-free publisher run exceeded the old 512 MiB plan.
    assert sum(policy.peak_reservation(peak) for peak in peaks) > policy.available_worker_bytes(
        512 * MIB, owner,
    )
    plan = live_forecast_pressure_plan(owner, peaks, measured_limit=measured_limit)
    assert plan["budget"] <= 640 * MIB
    assert plan["target_peak"] < plan["available"] < policy.peak_reservation(plan["target_peak"])
    capacity = max(plan["tail_reservations"]) if measured_limit else sum(plan["tail_reservations"])
    assert capacity < plan["available"]
    if measured_limit:
        assert plan["limit_payload"] == plan["available"]


@pytest.mark.pcc_gate(probe=lambda: os.name == "posix")
@pytest.mark.parametrize("case", (
    "forecast", "calibration", "measured-limit", "no-state", "stale-state",
))
def test_live_forecast_keeps_exclusive_worker_under_real_guard(tmp_path, case):
    """A real allocation may exceed its padded forecast without reaching the cap."""
    source = Path(workers.__file__).resolve().parents[3]
    driver = tmp_path / "live_forecast.py"
    driver.write_text('''
import json
import os
from pathlib import Path
import shlex
import sys
import time
from pcc.frontends.python import pipeline_frontend_workers as workers
from pcc.frontends.python import worker_process_pool as pool
from pcc.frontends.python import worker_resource_plan as policy
from tests.python.resource_worker_fixture import start_resource_worker_and_wait

root = Path(sys.argv[1])
case = sys.argv[2]
phase = sys.argv[3]
mib = 1024 ** 2
budget = int(os.environ["PCC_WORKER_TREE_BUDGET_BYTES"])
trace = root / "admission.tsv"

def child_command(index, allocation, target_peak=0):
    code = (
        "import os,time\\nfrom pathlib import Path\\n"
        "from pcc.frontends.python.worker_resource_plan import publish_worker_resource as report\\n"
    )
    if target_peak:
        code += (
            "from pcc.frontends.python.pipeline_frontend_workers import _worker_peak_rss_bytes as peak\\n"
            "payloads=[];allocated=0\\n"
            + "while peak()<" + str(target_peak) + ":\\n"
            + " payload=bytearray(1024**2)\\n"
            + " for offset in range(0,len(payload),4096): payload[offset]=1\\n"
            + " payloads.append(payload);allocated+=len(payload)\\n"
            + " assert allocated<=" + str(target_peak + mib) + "\\n"
            + "assert allocated>0\\n"
        )
    else:
        code += (
            "allocated=" + str(allocation) + ";payload=bytearray(allocated)\\n"
            + "for offset in range(0,len(payload),4096): payload[offset]=1\\n"
        )
    code += (
        "Path(" + repr(str(root / ("allocated" + str(index)))) + ").write_text(str(allocated))\\n"
        + "Path(" + repr(str(root / ("started" + str(index)))) + ").write_text(str(os.getpid()))\\n"
        + "report('allocated')\\n"
    )
    if index >= 2:
        peer = root / ("started" + str(5 - index))
        code += (
            "deadline=time.monotonic()+5\\n"
            + "while not Path(" + repr(str(peer)) + ").exists():\\n"
            + " assert time.monotonic()<deadline;time.sleep(0.005)\\n"
            + "os.kill(int(Path(" + repr(str(peer)) + ").read_text()),0);"
            + "Path(" + repr(str(root / ("overlap" + str(index)))) + ").touch();"
        )
    if case == "stale-state" and index == 0:
        # Hold completion until explicitly released. A fixed six-second sleep
        # races the two-second freshness window plus four-second host recovery.
        # The existing outer guard bounds a broken scheduler/cleanup path.
        code += (
            "Path(" + repr(str(root / "holding0")) + ").touch()\\n"
            + "while not Path(" + repr(str(root / "release0")) + ").exists():\\n"
            + " time.sleep(0.005)\\n"
        )
    else:
        code += "time.sleep(0.3);"
    code += (
        "report('complete');"
        + "Path(" + repr(str(root / ("completed" + str(index)))) + ").touch()"
    )
    return shlex.join([sys.executable, "-B", "-c", code])

if phase == "probe":
    probe_tasks = [
        {"class": "tail-probe", "inputs": [1], "estimate_bytes": 64 * mib,
         "report_path": str(root / ("rss" + str(index))), "restartable": True}
        for index in (2, 3)
    ]
    measured = []
    pool.run_resource_worker_processes(
        [child_command(index, 4 * mib) for index in (2, 3)], probe_tasks, 2, budget,
        observations=measured, trace_path=str(trace),
    )
    assert len(measured) == 2 and pool._HOST_WORKERS == {}
    for index in (2, 3):
        assert (root / ("completed" + str(index))).exists()
        assert (root / ("overlap" + str(index))).exists()
        try:
            os.kill(int((root / ("started" + str(index))).read_text()), 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("probe survived retirement")
    (root / "calibration.json").write_text(json.dumps({
        "owner_peak": workers._coordinator_rss_bytes(),
        "tail_peaks": [sample[2] for sample in measured], "observations": measured,
    }))
    print("LIVE_FORECAST_PROBE_OK")
    sys.exit(0)

plan = json.loads((root / "pressure-plan.json").read_text())
assert budget == plan["budget"]
if case == "no-state":
    os.environ.pop(policy.TREE_STATE_ENV)
commands = []
tasks = []
for index in range(4):
    commands.append(child_command(
        index, plan["limit_payload"] if index == 0 else 4 * mib,
        plan["target_peak"] if index == 0 and case != "measured-limit" else 0,
    ))
    tasks.append({"class": "live-forecast" if index < 2 else "fitting-tail", "inputs": [4 - index],
                  "estimate_bytes": 0 if case == "calibration" and index == 0 else 64 * mib,
                  "report_path": str(root / ("rss" + str(index))), "restartable": True})
    if index >= 2:
        tasks[index]["depends_on"] = 1
        tasks[index]["estimate_bytes"] = plan["tail_reservations"][index - 2]
if case == "calibration":
    tasks[1]["depends_on"] = 0

if case == "stale-state":
    # Freeze an unmodified real snapshot after promotion. The guard itself
    # keeps enforcing its original cap while the scheduler loses freshness.
    frozen = root / "frozen-tree.tsv"
    original_read = policy.read_tree_state
    original_event = pool._resource_event
    def read_state(path, tree_budget, owner_pid, active_pids, not_before=0.0, include_owner=False, diagnostic=None):
        return original_read(str(frozen) if frozen.exists() else path,
                             tree_budget, owner_pid, active_pids, not_before, include_owner,
                             diagnostic=diagnostic)
    def event(*args):
        original_event(*args)
        if args[1] == "exclusive" and not frozen.exists():
            frozen.write_bytes(Path(os.environ[policy.TREE_STATE_ENV]).read_bytes())
    policy.read_tree_state = read_state
    pool._resource_event = event

# Wait for the actual first allocation before returning its owned handle.
# This makes the live forecast cross before the independent tail can launch.
original_start = pool._start_resource_worker
def start(specs, index):
    return start_resource_worker_and_wait(
        original_start, specs, index,
        lambda: index != 0 or (Path(tasks[index]["report_path"]).exists()
                and (case != "stale-state" or (root / "holding0").exists())),
        timeout=5,
    )
pool._start_resource_worker = start
observations = []
error = None
try:
    pool.run_resource_worker_processes(commands, tasks, 2, budget,
                                      observations=observations, trace_path=str(trace))
except policy.WorkerMemoryError as exc:
    error = str(exc)
rows = [line.split("\\t") for line in trace.read_text().splitlines()]
record = {"case": case, "budget": budget, "error": error, "events": rows,
          "observations": observations, "owner_peak": workers._coordinator_rss_bytes(),
          "pressure_plan": plan}
(root / "outcome.json").write_text(json.dumps(record, indent=2) + "\\n")
assert pool._HOST_WORKERS == {}, record
for marker in root.glob("started*"):
    try:
        os.kill(int(marker.read_text()), 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("worker survived retirement")
if case in ("forecast", "calibration"):
    assert error is None, record
    assert all((root / ("completed" + str(index))).exists() for index in range(4)), record
    assert (root / "overlap2").exists() and (root / "overlap3").exists(), record
    assert len([row for row in rows if row[0] in ("start", "calibrate")]) == 4, record
    assert not any(row[0] == "cancel" for row in rows), record
    retired = next(index for index, row in enumerate(rows) if row[:2] == ["retire", "0"])
    tail = next(index for index, row in enumerate(rows)
                if row[1] == "1" and row[0] in ("start", "calibrate"))
    assert retired < tail, record
    assert rows[tail][0] == "calibrate" and int(rows[tail][3]) > int(rows[tail][4]), record
    first = rows[0]
    allocated = int((root / "allocated0").read_text())
    assert observations[0][2] > allocated, record
    assert policy.peak_reservation(observations[0][2]) > int(first[4]), record
    if case == "forecast":
        promoted = next(row for row in rows if row[0] == "exclusive")
        assert int(promoted[3]) == policy.peak_reservation(int(promoted[5])), record
        assert int(promoted[5]) <= int(promoted[4]) < int(promoted[3]), record
    else:
        assert first[0] == "calibrate", record
    tail_peaks = [sample[2] for sample in observations if sample[0] == "fitting-tail"]
    tail_available = min(int(row[4]) for row in rows
                         if row[0] == "start" and row[1] in ("2", "3"))
    assert len(tail_peaks) == 2, record
    assert sum(policy.peak_reservation(peak) for peak in tail_peaks) <= tail_available, record
elif case == "measured-limit":
    assert error and "live worker current subtree exceeds safe worker space" in error, record
    assert not (root / "completed0").exists() and not (root / "started1").exists(), record
elif case == "no-state":
    assert error and "worker memory budget cannot admit" in error, record
    assert not (root / "completed0").exists() and not (root / "started1").exists(), record
else:
    assert error and "missing, stale, or incompatible" in error, record
    assert any(row[0] == "exclusive" for row in rows), record
    assert (root / "holding0").exists() and not (root / "release0").exists(), record
    assert observations == [], record
    assert not (root / "completed0").exists() and not (root / "started1").exists(), record
    # Owned-child absence was checked above. Releasing only now cannot let a
    # leaked worker finish naturally and conceal a cleanup failure.
    (root / "release0").touch()
print("LIVE_FORECAST_OK " + case)
''')
    env = dict(os.environ)
    env.pop("LC_ALL", None)
    env["PYTHONPATH"] = str(source)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PCC_NO_AUTO_PCC1"] = "1"
    env["PCC_TEST_NO_NATIVE_PROVISIONING"] = "1"
    started = time.monotonic()
    guard_deadline, wait_deadline = started + 12, started + 15

    def run_guard(directory, budget, phase):
        # Calibration consumes the original timeout; it does not buy a second
        # twelve-second attempt. Both trees retain independently recorded caps.
        remaining = guard_deadline - time.monotonic()
        assert remaining > 0, "live forecast fixture exhausted its guard deadline"
        guard = directory / "guard.json"
        result = run_process_group_timeout([
            sys.executable, "-B", str(source / "scripts" / "run_process_tree_sample.py"),
            "--result", str(guard), "--samples", str(directory / "samples.tsv"),
            "--stdout", str(directory / "driver.stdout"), "--stderr", str(directory / "driver.stderr"),
            "--cwd", str(source), "--timeout", str(remaining), "--interval", "0.01",
            "--no-performance-lock", "--max-tree-rss-bytes", str(budget),
            "--", sys.executable, "-B", str(driver), str(directory), case, phase,
        ], env=env, timeout=wait_deadline - time.monotonic())
        assert result.returncode == 0, (result, (directory / "driver.stderr").read_text())
        receipt = json.loads(guard.read_text())
        assert receipt["status"] == "COMPLETE" and receipt["returncode"] == 0
        assert receipt["max_tree_rss_bytes"] == budget <= 640 * MIB
        assert 0 < receipt["peak_tree_rss_bytes"] < budget

    probe = tmp_path / "tail-probe"
    probe.mkdir()
    run_guard(probe, 640 * MIB, "probe")
    assert (probe / "driver.stdout").read_text() == "LIVE_FORECAST_PROBE_OK\n"
    measured = json.loads((probe / "calibration.json").read_text())
    plan = live_forecast_pressure_plan(
        measured["owner_peak"], measured["tail_peaks"], measured_limit=case == "measured-limit",
    )
    (tmp_path / "pressure-plan.json").write_text(json.dumps(plan))
    run_guard(tmp_path, plan["budget"], "scenario")
    assert (tmp_path / "driver.stdout").read_text() == "LIVE_FORECAST_OK " + case + "\n"


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
                     + "from tests.python.process_liveness import require_process_running;"
                     + "deadline=time.monotonic()+3\n"
                     + "while not Path(" + repr(str(slow)) + ").exists() and time.monotonic()<deadline: time.sleep(0.01)\n"
                     + "peer=int(Path(" + repr(str(slow)) + ").read_text());require_process_running(peer)\n"
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
            require_process_running(pid)
    assert pool._HOST_WORKERS == {}


def _exercise_overlap_trim(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "1")
    guard_cap = os.environ.get("PCC_WORKER_TREE_BUDGET_BYTES")
    _exercise_progressive_retry_pressure(tmp_path, monkeypatch, 0, restore_peers=False)
    assert os.environ.get("PCC_WORKER_TREE_BUDGET_BYTES") == guard_cap
    # The original two-worker witness must still prove one actual local
    # accounting trim, with the surviving reservation fitting the same cap.
    trims = [s for s in capsys.readouterr().err.splitlines() if s.startswith("pcc worker trim ")]
    assert len(trims) == 1
    r = {k: int(v) for k, v in (field.split("=") for field in trims[0].split()[3:])}
    assert (r["synchronized"], r["position"], r["count_before"], r["task"], r["previous_task"]) == (0, 0, 2, 1, 0)
    assert r["first_pid"] == r["pid"] and r["first_bytes"] == r["removed_bytes"]
    assert r["before_bytes"] > r["available_bytes"] >= r["after_bytes"]


def test_overlapping_growth_cancels_and_requeues_one_peer_with_same_cap(tmp_path, monkeypatch, capsys):
    _exercise_overlap_trim(tmp_path, monkeypatch, capsys)


def test_overlapping_growth_with_retired_parent_rss(tmp_path):
    # Replay the exact original node after a genuine retired allocation in
    # an isolated pytest coordinator: do not inflate the six-worker suite's
    # own permanent maxrss merely to establish this regression witness.
    root = Path(__file__).resolve().parents[2]
    launcher = (
        "import sys,pytest\n"
        "from pcc.frontends.python.pipeline_frontend_workers import _worker_peak_rss_bytes\n"
        "history=bytearray(256*1024**2)\n"
        "for offset in range(0,len(history),4096): history[offset]=1\n"
        "del history\n"
        "assert _worker_peak_rss_bytes()>=256*1024**2\n"
        "raise SystemExit(pytest.main(sys.argv[1:]))\n"
    )
    environment = dict(os.environ)
    for name in ("PYTEST_XDIST_WORKER", "PYTEST_XDIST_WORKER_COUNT", "PYTEST_XDIST_TESTRUNUID"):
        environment.pop(name, None)
    evidence = tmp_path / "retired-history"
    result = run_process_tree_timeout(
        [sys.executable, "-B", "-c", launcher, "-q", "-n0", "--override-ini=addopts=",
         "--basetemp", str(tmp_path / "nested-pytest"),
         "tests/python/test_worker_resource_plan.py::test_overlapping_growth_cancels_and_requeues_one_peer_with_same_cap"],
        cwd=root, env=environment, evidence=evidence, timeout=30,
    )
    assert result.returncode == 0 and "1 passed" in result.stdout, (result.stdout, result.stderr)
    traces = list((tmp_path / "nested-pytest").glob("test_overlapping_growth_cancel[0-9]*/progressive.tsv"))
    assert len(traces) == 1
    rows = [line.split("\t") for line in traces[0].read_text().splitlines()]
    assert [row[:2] for row in rows if row[0] == "cancel"] == [["cancel", "1"]]
    assert len([row for row in rows if row[:2] == ["start", "1"]]) == 2


def _progressive_retry_pressure_plan(available, leader_peak, peer_peak):
    leader = policy.peak_reservation(leader_peak)
    peer = policy.peak_reservation(peer_peak)
    assert leader + peer <= available, ("initial pair must fit", leader, peer, available)
    lower = max(leader, peer, available - peer)
    # Cross the cancellation threshold by a bounded margin, not half of all
    # spare capacity: historical parent RSS can make that spare space huge.
    target = lower + min(8 * MIB, (available - lower) // 2)
    target_peak = (target - policy.RSS_HEADROOM_BYTES) * 4 // 5
    target_charge = policy.peak_reservation(target_peak)
    assert max(leader, peer) < target_charge < available
    assert target_charge + peer > available
    # The final real allocation may cross the target by one chunk. Keep that
    # chunk inside half the remaining single-worker reservation headroom.
    chunk = min(8 * MIB, (available - target_charge) * 4 // 10)
    assert chunk >= 64 * 1024
    return {
        "available": available, "leader_peak": leader_peak,
        "peer_peak": peer_peak, "target_peak": target_peak,
        "chunk_bytes": chunk, "max_payload_bytes": target_peak,
    }


@pytest.mark.parametrize("parent_peak", [32 * MIB, 256 * MIB, 1024 * MIB])
def test_progressive_retry_pressure_uses_child_reports_not_parent_history(parent_peak):
    leader_peak, peer_peak = 24 * MIB, 20 * MIB
    available = 2 * policy.peak_reservation(parent_peak + 32 * MIB) + 32 * MIB
    plan = _progressive_retry_pressure_plan(available, leader_peak, peer_peak)
    grown = policy.peak_reservation(plan["target_peak"])
    peer = policy.peak_reservation(peer_peak)
    assert policy.peak_reservation(leader_peak) + peer <= available
    assert grown <= available < grown + peer
    assert grown - max(policy.peak_reservation(leader_peak), peer, available - peer) <= 8 * MIB
    assert policy.peak_reservation(plan["target_peak"] + plan["chunk_bytes"]) < available
    assert 0 < plan["chunk_bytes"] <= 8 * MIB < plan["max_payload_bytes"]
    if parent_peak >= 256 * MIB:
        # The old guessed allocation never forced cancellation in this shape:
        # available grows as 2.5 * parent history, guessed growth only as 1x.
        guessed_peak = leader_peak + parent_peak + 128 * MIB
        assert policy.peak_reservation(guessed_peak) + peer <= available


def _exercise_progressive_retry_pressure(tmp_path, monkeypatch, parent_history, *, restore_peers):
    task_count = 4 if restore_peers else 2
    outer_cap = os.environ.get("PCC_WORKER_TREE_BUDGET_BYTES")
    monkeypatch.delenv(policy.TREE_STATE_ENV, raising=False)
    # Exercise a genuine parent high-water mark, not a fabricated RSS reader.
    # Release it before starting fresh children; no second full-sized copy.
    # Bound the history too: its inherited peak sets the minimum real growth
    # needed below, alongside all six pytest workers inside the 1 GiB gate.
    history = bytearray(parent_history)
    for offset in range(0, len(history), 4096):
        history[offset] = 1
    del history
    assert workers._worker_peak_rss_bytes() >= parent_history
    child = tmp_path / "progressive.py"
    child.write_text("""
import json
import os
from pathlib import Path
import sys
import time
# Retain startup/import failures before the first resource or attempt marker.
# A distinct PID file preserves both the cancelled attempt and its retry.
for stream_name, descriptor in (("stdout", 1), ("stderr", 2)):
    stream_path = Path(sys.argv[1]) / (
        "child." + stream_name + "." + sys.argv[2] + "." + str(os.getpid())
    )
    with stream_path.open("wb") as stream:
        os.dup2(stream.fileno(), descriptor)
from pcc.frontends.python.worker_resource_plan import (
    RESOURCE_REPORT_ENV, RESOURCE_TOKEN_ENV, peak_reservation, publish_worker_resource,
)
from tests.python.process_liveness import require_process_running

root = Path(sys.argv[1])
index = int(sys.argv[2])
deadline = time.monotonic() + 6

def wait_for(predicate, phase):
    publish("waiting." + str(index) + "." + str(attempt), phase)
    while not predicate():
        assert time.monotonic() < deadline, (
            index, attempt, phase, "coordination timed out", rows()[-12:],
        )
        time.sleep(0.005)

def exists(name):
    return (root / name).exists()

def publish(name, value):
    marker = root / name
    temporary = root / (name + "." + str(os.getpid()) + ".tmp")
    temporary.write_text(value)
    temporary.replace(marker)

def rows():
    trace = root / "progressive.tsv"
    return [line.split("\\t") for line in trace.read_text().splitlines(keepends=True)
            if line.endswith("\\n")] if trace.exists() else []

def seen(event, number, pid=None):
    return any(row[:2] == [event, str(number)]
               and (pid is None or row[2] == str(pid)) for row in rows())

def draining():
    history = rows()
    for position, row in enumerate(history):
        if row[:2] == ["cancel", "1"]:
            return any(item[0] == "backoff" for item in history[position + 1:])
    return False

def acknowledged(phase):
    marker = root / ("observed." + str(os.getpid()) + "." + phase)
    return marker.exists() and marker.read_text() == os.environ[RESOURCE_TOKEN_ENV]

count = root / ("attempts" + str(index))
attempt = int(count.read_text()) + 1 if count.exists() else 1
count.write_text(str(attempt))
record = str(os.getpid()) + "\\t" + os.environ[RESOURCE_TOKEN_ENV]
publish("started." + str(index) + "." + str(attempt), record)

def overlap(name, peers):
    records = [root / ("started." + str(peer) + ".1") for peer in peers]
    wait_for(lambda: all(path.exists() for path in records), name + " peer starts")
    pids = [int(path.read_text().split("\\t")[0]) for path in records]
    for pid in pids:
        require_process_running(pid)
    publish(name, " ".join(str(pid) for pid in pids))

payloads = [bytearray(4 * 1024 ** 2)]

def grow_to_accepted_pressure():
    from pcc.frontends.python.pipeline_frontend_workers import (
        _coordinator_rss_bytes, _worker_peak_rss_bytes,
    )

    marker = root / ("pressure." + str(os.getpid()) + "." + os.environ[RESOURCE_TOKEN_ENV])
    wait_for(marker.exists, "accepted real-RSS pressure plan")
    plan = json.loads(marker.read_text())
    assert plan["pid"] == os.getpid() and plan["token"] == os.environ[RESOURCE_TOKEN_ENV]
    allocated = 0
    page_size = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
    measured = max(_coordinator_rss_bytes(), _worker_peak_rss_bytes())
    while measured < plan["target_peak"]:
        assert time.monotonic() < deadline, (
            index, attempt, "pressure allocation timed out", plan, measured,
        )
        remaining = plan["max_payload_bytes"] - allocated
        assert remaining > 0, ("bounded real allocation did not reach target", plan, measured)
        payload = bytearray(min(plan["chunk_bytes"], remaining))
        for offset in range(0, len(payload), page_size):
            payload[offset] = 1
        payloads.append(payload)
        allocated += len(payload)
        measured = max(_coordinator_rss_bytes(), _worker_peak_rss_bytes())
    assert 0 < allocated <= plan["max_payload_bytes"]
    assert peak_reservation(measured) <= plan["available"], (
        "grown worker must fit alone", plan, measured,
    )
    publish("growth." + str(index) + "." + str(attempt), json.dumps({
        "allocated": allocated, "measured_peak": measured, "plan": plan,
    }))

if index == 0:
    publish_worker_resource("leader-small")
    wait_for(lambda: exists("first-phase"), "peer first phase")
    overlap("initial-overlap0", [0, 1])
    wait_for(lambda: exists("initial-overlap1"), "peer initial overlap")
    grow_to_accepted_pressure()
    publish_worker_resource("leader-grown")
    wait_for(lambda: seen("cancel", 1), "real reservation cancellation")
    # Keep the leader alive until admission actually waits for it to drain.
    # A fixed sleep can expire before the coordinator runs under xdist.
    wait_for(lambda: draining() or seen("start", 2), "retry barrier drains leader")
    assert draining() and not seen("start", 2)
elif index == 1 and attempt == 1:
    publish_worker_resource("first-phase")
    publish("first-phase", record)
    overlap("initial-overlap1", [0, 1])
    wait_for(lambda: False, "first attempt must be cancelled")
elif index == 1:
    publish_worker_resource("retry-first-phase")
    wait_for(lambda: seen("start", index, os.getpid()), "retry start")
    wait_for(lambda: acknowledged("retry-first-phase"), "retry first-phase acceptance")
    grow_to_accepted_pressure()
    publish_worker_resource("retry-second-phase")
    publish("second-phase", str(time.monotonic()))
    wait_for(lambda: acknowledged("retry-second-phase"), "retry second-phase acceptance")
else:
    # Old admission starts task 2 beside the draining leader, then admits
    # the retry beside task 2. Keep task 2's report until the leader retires
    # so only the retry's later growth can cause the second cancellation.
    wait_for(lambda: seen("retire", 0), "leader retirement")
    publish_worker_resource("tail-small")
    overlap("restored-overlap" + str(index), [2, 3])
    wait_for(lambda: exists("restored-overlap" + str(5 - index)), "restored peer overlap")

publish_worker_resource("complete")
publish("complete." + str(index) + "." + str(attempt),
        Path(os.environ[RESOURCE_REPORT_ENV]).read_text())
""")
    owner = workers._coordinator_rss_bytes()
    startup_peak = max(owner, workers._worker_peak_rss_bytes())
    startup_budget = (owner + policy.RSS_HEADROOM_BYTES
                      + 2 * policy.peak_reservation(startup_peak + 32 * MIB) + 32 * MIB)
    # Calibrate the actual child envelope, including any inherited high-water
    # mark. Keep the pair's launch margin outside that measured reservation;
    # padding the parent again forced hundreds of unnecessary MiB of growth.
    startup_observations = []
    pool.run_resource_worker_processes(
        [command(child_code(tmp_path / "startup-probe"))],
        [task(tmp_path / "startup-probe.rss")], 1, startup_budget,
        observations=startup_observations, trace_path=str(tmp_path / "startup-probe.tsv"),
    )
    assert len(startup_observations) == 1 and startup_observations[0][2] > 0
    assert pool._HOST_WORKERS == {}
    available = 2 * policy.peak_reservation(startup_observations[0][2]) + 32 * MIB
    # The configured fixture budget is set exactly once from the real cold
    # observation; it never follows growing workers or retries. The outer
    # process-tree cap is not changed, and neither RSS reader is substituted.
    budget = workers._coordinator_rss_bytes() + policy.RSS_HEADROOM_BYTES + available
    (tmp_path / "configured-budget").write_text(str(budget))
    tasks = [task(tmp_path / ("rss" + str(index)), estimate=8 * MIB,
                  inputs=(task_count - index,), execution_class=("host:progressive:" + str(index)
                                                               if restore_peers else "host:test"))
             for index in range(task_count)]
    # Executing a temporary script puts its directory, not the checkout, at
    # sys.path[0]. CI installs dependencies with --no-install-project, so give
    # each real worker the tested source explicitly and preserve caller paths.
    pythonpath = str(Path(__file__).resolve().parents[2])
    inherited_pythonpath = os.environ.get("PYTHONPATH")
    if inherited_pythonpath:
        pythonpath += os.pathsep + inherited_pythonpath
    commands = [shlex.join(["PYTHONPATH=" + pythonpath, sys.executable, "-B",
                           str(child), str(tmp_path), str(index)]) for index in range(task_count)]
    trace = tmp_path / "progressive.tsv"
    observations = []
    reaped = set()
    original_retire = pool._retire_resource_worker
    original_killpg = getattr(os, "killpg", None)
    original_read = policy.read_worker_resource
    initial_reports = {}
    pressure_plans = {}

    def publish_pressure_plan(pid, token, leader_peak, peer_peak):
        available_now = policy.available_worker_bytes(budget, workers._coordinator_rss_bytes())
        plan = _progressive_retry_pressure_plan(available_now, leader_peak, peer_peak)
        plan.update(pid=pid, token=token)
        marker = tmp_path / ("pressure." + str(pid) + "." + token)
        temporary = marker.with_suffix(".tmp")
        temporary.write_text(json.dumps(plan))
        temporary.replace(marker)
        pressure_plans[pid, token] = plan

    def acknowledge_report(path, pid, token=""):
        report = original_read(path, pid, token)
        if report is not None:
            if report[0] in ("leader-small", "first-phase"):
                initial_reports.setdefault(report[0], (pid, token, report))
            if len(initial_reports) == 2:
                leader_pid, leader_token, leader = initial_reports["leader-small"]
                peer = initial_reports["first-phase"][2]
                if (leader_pid, leader_token) not in pressure_plans:
                    publish_pressure_plan(leader_pid, leader_token, leader[2], peer[2])
                if report[0] == "retry-first-phase" and (pid, token) not in pressure_plans:
                    publish_pressure_plan(pid, token, report[2], peer[2])
            if report[0] in ("leader-grown", "retry-second-phase"):
                available_now = policy.available_worker_bytes(budget, workers._coordinator_rss_bytes())
                grown = policy.peak_reservation(report[2])
                peer = policy.peak_reservation(initial_reports["first-phase"][2][2])
                assert grown <= available_now < grown + peer, (
                    "real pressure precondition", report, available_now, peer,
                )
        if report is not None and report[0] in ("retry-first-phase", "retry-second-phase"):
            # Observe the real reader result without substituting its RSS or
            # changing admission. Each phase stays live until the scheduler
            # has accepted this PID/token's report, not for an assumed delay.
            marker = tmp_path / ("observed." + str(pid) + "." + report[0])
            temporary = marker.with_suffix(".tmp")
            temporary.write_text(token)
            temporary.replace(marker)
        return report

    def record_retire(pid):
        original_retire(pid)
        reaped.add(pid)

    def reject_signal_after_reap(pid, number):
        assert pid not in reaped
        return original_killpg(pid, number)

    monkeypatch.setattr(pool, "_retire_resource_worker", record_retire)
    if original_killpg is not None:
        monkeypatch.setattr(os, "killpg", reject_signal_after_reap)
    monkeypatch.setattr(policy, "read_worker_resource", acknowledge_report)
    try:
        pool.run_resource_worker_processes(commands, tasks, 2, budget,
                                          observations=observations, trace_path=str(trace))
    except subprocess.CalledProcessError as error:
        child_output = "\n".join(
            path.name + ":\n" + path.read_text(encoding="utf-8", errors="replace")
            for stream_name in ("stdout", "stderr")
            for path in sorted(tmp_path.glob("child." + stream_name + ".*"))
        )
        raise AssertionError(
            str(error) + "\nFull child stdout/stderr (raw PID files retained):\n"
            + (child_output or "No child stdout/stderr was retained.")
        ) from error
    rows = [line.split("\t") for line in trace.read_text().splitlines()]
    cancelled = [row for row in rows if row[0] == "cancel"]
    assert len(cancelled) == 1 and cancelled[0][1] == "1"
    retry_starts = [row for row in rows if row[:2] == ["start", "1"]]
    assert len(retry_starts) == 2
    events = {(row[0], row[1]): row for row in rows if row[0] in ("start", "retire")}
    assert float(events["retire", "0"][6]) < float(retry_starts[1][6])
    assert any(row[0] == "backoff" and
               float(cancelled[0][6]) < float(row[6]) < float(events["retire", "0"][6])
               for row in rows)
    assert int(retry_starts[1][3]) == int(retry_starts[1][4])
    if restore_peers:
        assert float(events["retire", "1"][6]) < float(events["start", "2"][6])
        assert float(events["start", "3"][6]) < float(events["retire", "2"][6])
    else:
        assert set(events) == {("start", "0"), ("start", "1"), ("retire", "0"), ("retire", "1")}
    assert float(cancelled[0][6]) > float(retry_starts[0][6])
    assert float(retry_starts[1][6]) < float((tmp_path / "second-phase").read_text())
    assert [int((tmp_path / ("attempts" + str(index))).read_text())
            for index in range(task_count)] == ([1, 2, 1, 1] if restore_peers else [1, 2])
    first_attempt = (tmp_path / "started.1.1").read_text().split("\t")
    retry_attempt = (tmp_path / "started.1.2").read_text().split("\t")
    leader_attempt = (tmp_path / "started.0.1").read_text().split("\t")
    assert set(pressure_plans) == {
        (int(leader_attempt[0]), leader_attempt[1]),
        (int(retry_attempt[0]), retry_attempt[1]),
    }
    for index, attempt, record in ((0, 1, leader_attempt), (1, 2, retry_attempt)):
        growth = json.loads((tmp_path / f"growth.{index}.{attempt}").read_text())
        plan = pressure_plans[int(record[0]), record[1]]
        assert growth["plan"] == plan
        assert 0 < growth["allocated"] <= plan["max_payload_bytes"]
        assert growth["measured_peak"] >= plan["target_peak"]
        assert policy.peak_reservation(growth["measured_peak"]) <= plan["available"]
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
    assert len(observations) == task_count
    assert [sample[2] for sample in observations
            if sample[:2] == (tasks[1]["class"], tasks[1]["inputs"])] == [completed_report[2]]
    overlaps = [("initial-overlap", (0, 1))]
    if restore_peers:
        overlaps.append(("restored-overlap", (2, 3)))
    for name, peers in overlaps:
        expected = [(tmp_path / ("started." + str(index) + ".1")).read_text().split("\t")[0]
                    for index in peers]
        for index in peers:
            assert (tmp_path / (name + str(index))).read_text().split() == expected
    for record in tmp_path.glob("started.*"):
        pid = int(record.read_text().split("\t")[0])
        assert pid in reaped
        with pytest.raises(ProcessLookupError):
            require_process_running(pid)
    assert (tmp_path / "configured-budget").read_text() == str(budget)
    assert os.environ.get("PCC_WORKER_TREE_BUDGET_BYTES") == outer_cap
    assert pool._HOST_WORKERS == {}


@pytest.mark.pcc_gate(probe=lambda: os.name == "posix")
@pytest.mark.parametrize(
    "parent_history", [0, 128 * MIB], ids=["fresh-parent", "retired-parent-rss"],
)
def test_progressive_retry_drains_peers_then_restores_concurrency(tmp_path, monkeypatch, parent_history):
    _exercise_progressive_retry_pressure(tmp_path, monkeypatch, parent_history, restore_peers=True)


@pytest.mark.pcc_gate(probe=lambda: os.name == "posix")
def test_ready_unknown_head_drains_peer_before_exclusive_calibration(tmp_path, monkeypatch):
    # Use local real RSS accounting as the other lifecycle tests do. The
    # enclosing process-tree guard and its inherited cap remain unchanged.
    monkeypatch.delenv(policy.TREE_STATE_ENV, raising=False)
    child = tmp_path / "ready_unknown.py"
    child.write_text("""
import os
from pathlib import Path
import sys
import time
from pcc.frontends.python.worker_resource_plan import (
    RESOURCE_TOKEN_ENV, publish_worker_resource,
)

root = Path(sys.argv[1])
index = int(sys.argv[2])
deadline = time.monotonic() + 6

def wait_for(predicate):
    while not predicate():
        assert time.monotonic() < deadline, (index, "coordination timed out")
        time.sleep(0.005)

def rows():
    trace = root / "ready_unknown.tsv"
    return [line.split("\\t") for line in trace.read_text().splitlines()] if trace.exists() else []

def seen(event, number):
    return any(row[:2] == [event, str(number)] for row in rows())

def draining():
    history = rows()
    for position, row in enumerate(history):
        if row[:2] == ["retire", "0"]:
            return any(item[0] == "backoff" for item in history[position + 1:])
    return False

payload = bytearray(4 * 1024 ** 2)
publish_worker_resource("allocated")
record = str(os.getpid()) + "\\t" + os.environ[RESOURCE_TOKEN_ENV]
record += "\\t" + os.environ.get("PCC_WORKER_TREE_BUDGET_BYTES", "")
marker = root / ("started" + str(index))
temporary = marker.with_suffix(".tmp")
temporary.write_text(record)
temporary.replace(marker)
if index == 0:
    wait_for(lambda: (root / "started1").exists())
    peer = int((root / "started1").read_text().split("\\t")[0])
    os.kill(peer, 0)
    (root / "initial-overlap").write_text(str(os.getpid()) + "\\t" + str(peer))
elif index == 1:
    # The dependency makes unknown task 2 ready only after task 0 retires.
    # Keep this peer alive until real admission backs off, without a race
    # against a sleep. Old admission launches known task 3 and fails here.
    wait_for(lambda: draining() or seen("start", 3))
    assert draining() and not seen("start", 3)
elif index == 2:
    assert seen("retire", 1) and not seen("start", 3)
else:
    assert seen("retire", 2)
publish_worker_resource("complete")
""")
    owner = workers._coordinator_rss_bytes()
    startup_peak = max(owner, workers._worker_peak_rss_bytes())
    reservation = policy.peak_reservation(startup_peak + 32 * MIB)
    budget = owner + policy.RSS_HEADROOM_BYTES + 2 * reservation + 32 * MIB
    guard_cap = os.environ.get("PCC_WORKER_TREE_BUDGET_BYTES", "")
    tasks = [task(tmp_path / ("rss" + str(index)),
                  estimate=0 if index == 2 else reservation, inputs=(4 - index,),
                  execution_class="host:ready-unknown:" + str(index))
             for index in range(4)]
    for item in tasks:
        item["calibrate_before_peers"] = True
    tasks[2]["depends_on"] = tasks[3]["depends_on"] = 0
    # Executing a temporary script puts its directory, not the checkout, at
    # sys.path[0]. CI installs dependencies with --no-install-project, so give
    # each real worker the tested source explicitly and preserve caller paths.
    pythonpath = str(Path(__file__).resolve().parents[2])
    inherited_pythonpath = os.environ.get("PYTHONPATH")
    if inherited_pythonpath:
        pythonpath += os.pathsep + inherited_pythonpath
    commands = [shlex.join(["PYTHONPATH=" + pythonpath, sys.executable, "-B",
                           str(child), str(tmp_path), str(index)]) for index in range(4)]
    trace = tmp_path / "ready_unknown.tsv"
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
    assert [row[:2] for row in rows if row[0] != "backoff"] == [
        ["start", "0"], ["start", "1"], ["retire", "0"], ["retire", "1"],
        ["calibrate", "2"], ["retire", "2"], ["start", "3"], ["retire", "3"],
    ]
    events = {(row[0], row[1]): row for row in rows}
    assert any(row[0] == "backoff" and
               float(events["retire", "0"][6]) < float(row[6]) < float(events["retire", "1"][6])
               for row in rows)
    calibration = events["calibrate", "2"]
    assert 0 < int(calibration[3]) == int(calibration[4]) < budget
    records = [(tmp_path / ("started" + str(index))).read_text().split("\t")
               for index in range(4)]
    assert (tmp_path / "initial-overlap").read_text().split("\t") == [
        records[0][0], records[1][0],
    ]
    assert len(observations) == 4 and len({record[1] for record in records}) == 4
    for index, (pid_text, token, inherited_cap) in enumerate(records):
        pid = int(pid_text)
        report = policy.read_worker_resource(tasks[index]["report_path"], pid, token)
        assert report is not None and report[0] == "complete" and report[2] >= report[1] > 0
        assert [sample[2] for sample in observations if sample[0] == tasks[index]["class"]] == [report[2]]
        assert inherited_cap == guard_cap
        assert pid in reaped
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    assert tasks[2]["estimate_bytes"] == 0
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
        "boundary": (
            ("darwin_libsystem_setpgid_handle_lifecycle" if sys.platform == "darwin"
             else "linux_owned_setpgid_handle_lifecycle")
            if case == "handles" else "shared_admission_policy"
        ),
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
    expected_stderr = ""
    if case == "oversized":
        # The driver retains the actual refusal after catching it. Require
        # exactly the bounded primary diagnostic emitted before pool cleanup.
        refusal = (directory / "refusal").read_text()
        expected_stderr = (
            "pcc frontend primary resource error before cleanup: WorkerMemoryError="
            + repr(refusal[:2048])
            + (" [truncated]" if len(refusal) > 2048 else "") + "\n"
        )
    assert result.stderr == expected_stderr, record
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
            require_process_running(int(fields[0]))
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


@pytest.mark.parametrize("variant", (
    "recorded", "escaped", "bounded", "other-case-empty", "missing",
    "wrong-type", "wrong-message", "duplicate", "extra", "unescaped",
    "other-case-diagnostic", "missing-refusal",
))
def test_resource_driver_requires_exact_case_specific_stderr(tmp_path, monkeypatch, variant):
    prefix = "pcc frontend primary resource error before cleanup: WorkerMemoryError="
    refusal = "expected admission refusal"
    stderr = prefix + "'expected admission refusal'\n"
    case = "oversized"
    accepted = variant in ("recorded", "escaped", "bounded", "other-case-empty")
    if variant in ("escaped", "unescaped"):
        refusal = "one\nline"
        stderr = prefix + ("'one\\nline'\n" if variant == "escaped" else "'one\nline'\n")
    elif variant == "bounded":
        refusal = "x" * 2049
        stderr = prefix + "'" + "x" * 2048 + "' [truncated]\n"
    elif variant == "missing":
        stderr = ""
    elif variant == "wrong-type":
        stderr = stderr.replace("WorkerMemoryError=", "RuntimeError=")
    elif variant == "wrong-message":
        stderr = prefix + "'unrelated refusal'\n"
    elif variant == "duplicate":
        stderr *= 2
    elif variant == "extra":
        stderr += "unexpected diagnostic\n"
    elif variant in ("other-case-empty", "other-case-diagnostic"):
        case = "spawn_failure"
        if variant == "other-case-empty":
            stderr = ""

    directory = tmp_path / case
    calls = []

    def completed_driver(command, *, env, timeout):
        calls.append(command)
        assert command == ["synthetic-driver", case, str(directory)]
        assert timeout == 20 and env["PCC_TEST_RESOURCE_OWNER"] == "cpython"
        (directory / "complete").write_text("cpython\t0")
        (directory / "configured-budget").write_text("4096")
        (directory / "budget.tsv").write_text(
            "4096\t2048\t0\t0\tmeasured-lower-bound-not-a-sufficient-budget\n"
        )
        if variant != "missing-refusal":
            (directory / "refusal").write_text(refusal)
        return subprocess.CompletedProcess(
            command, 0, "RESOURCE_ACCEPTANCE_OK " + case + "\n", stderr,
        )

    monkeypatch.setitem(globals(), "run_process_group_timeout", completed_driver)
    if accepted:
        record = _execute_resource_driver(["synthetic-driver"], case, directory, "cpython", 0)
        assert record["stderr"] == stderr
    else:
        error = FileNotFoundError if variant == "missing-refusal" else AssertionError
        with pytest.raises(error):
            _execute_resource_driver(["synthetic-driver"], case, directory, "cpython", 0)
    assert calls == [["synthetic-driver", case, str(directory)]]
    record = json.loads((directory / "execution.json").read_text())
    assert record["returncode"] == 0 and record["stderr"] == stderr
    assert record["stdout"] == "RESOURCE_ACCEPTANCE_OK " + case + "\n"


@pytest.mark.pcc_gate(probe=lambda: os.name == "posix")
def test_resource_acceptance_growth_uses_child_not_retired_parent_peak(tmp_path):
    # Establish history in the driver itself: POSIX does not promise that an
    # exec will inherit pytest's high-water RSS on every supported platform.
    launcher = (
        "import runpy,sys\n"
        "history=bytearray(256*1024**2)\n"
        "for offset in range(0,len(history),4096): history[offset]=1\n"
        "del history\n"
        "sys.argv=sys.argv[1:]\n"
        "runpy.run_path(sys.argv[0],run_name='__main__')\n"
    )
    directory = tmp_path / "growth"
    _execute_resource_driver(
        [sys.executable, "-B", "-c", launcher, str(RESOURCE_NATIVE_DRIVER)],
        "growth", directory, "cpython", 0,
    )
    budget, owner, peak = [int(value) for value in
                           (directory / "budget.tsv").read_text().split("\t")[:3]]
    assert owner >= 256 * MIB
    measured = int((directory / "growth-probe" / "budget.tsv").read_text().split("\t")[2])
    # Real grown children fit one at a time, but never as a simultaneous pair.
    available = policy.available_worker_bytes(budget, owner)
    assert available == policy.peak_reservation(measured) + 32 * MIB
    assert policy.peak_reservation(peak) <= available < 2 * policy.peak_reservation(peak)


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
            ir_scaffold_mode="on",
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
    for collector in (0,):
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


def test_measured_limit_preserves_task_identity_and_accounting(tmp_path, monkeypatch):
    """Failure context survives temporary manifests without exposing launch data."""
    monkeypatch.setenv(policy.TREE_STATE_ENV, str(tmp_path / "tree"))
    pid = 12345
    owner = 64 * MIB
    outside = 32 * MIB
    peak = 300 * MIB
    current = 290 * MIB
    budget = 512 * MIB
    stopped = []
    monkeypatch.setattr(workers, "_coordinator_rss_bytes", lambda: owner)
    monkeypatch.setattr(pool, "_start_resource_worker", lambda specs, index: pid)
    monkeypatch.setattr(pool, "_poll_resource_worker", lambda pid: pool._WORKER_RUNNING)
    monkeypatch.setattr(pool, "_stop_resource_worker", stopped.append)
    monkeypatch.setattr(policy, "read_tree_state", lambda path, limit, owner_pid, active, after, include_owner=False, diagnostic=None:
                        (outside, {owner_pid: owner, **{child: current for child in active}}))
    monkeypatch.setattr(policy, "read_worker_resource", lambda path, child, token:
                        ("emit-object", current, peak))
    item = task(tmp_path / "rss")
    item["diagnostic_phase"] = "codegen"
    item["diagnostic_modules"] = ["package.first", "package.second"]
    with pytest.raises(policy.WorkerMemoryError) as failure:
        pool.run_resource_worker_processes(
            ["PRIVATE_TEST_VALUE=do-not-log /never-executed --secret-argument"],
            [item], 1, budget, observations=[],
        )
    message = str(failure.value)
    assert "live worker current subtree exceeds safe worker space" in message
    assert "phase=codegen modules=['package.first', 'package.second']" in message
    assert "worker_report_phase=emit-object" in message
    assert "observed_peak_so_far_bytes=" + str(peak) in message
    assert "available_worker_bytes=" + str(budget - owner - outside - policy.RSS_HEADROOM_BYTES) in message
    assert "owner_reservation_rss_bytes=" + str(owner) in message
    assert "current_owner_rss_bytes=" + str(owner) in message
    assert "outside_owner_rss_bytes=" + str(outside) in message
    assert "current_worker_subtree_rss_bytes=" + str(current) in message
    assert "budget=" + str(budget) in message
    assert "full-task peak is unknown; tree cap is unchanged" in message
    assert "do-not-log" not in message and "secret-argument" not in message
    assert stopped == [pid]


def test_resource_task_diagnostics_retain_only_assigned_modules(tmp_path, monkeypatch):
    from pcc.frontends.python import pipeline_stage1_checkpoint as checkpoint

    manifest_path = str(tmp_path / "worker.manifest")
    manifest = {
        "assigned_indices": [2, 0], "job_kind": "codegen", "ast_dir": "",
        "exports_path": "", "src_paths": ["one.py", "other.py", "three.py"],
        "module_names": ["pkg.one", "pkg.unassigned", "pkg.three"],
    }
    monkeypatch.setattr(workers, "read_worker_manifest", lambda path: manifest)
    monkeypatch.setattr(workers, "_artifact_size", lambda path: 123)
    monkeypatch.setattr(checkpoint, "file_sha256", lambda path: "a" * 64)
    items = workers.resource_tasks_for_commands([
        shlex.join([sys.executable, "--pcc-python-multi-codegen-worker", manifest_path]),
    ])
    assert items[0]["diagnostic_phase"] == "codegen"
    assert items[0]["diagnostic_modules"] == ["pkg.three", "pkg.one"]
    assert items[0]["estimate_bytes"] == 0
    roots = tmp_path / "roots"
    roots.write_text("pkg.one\npkg.three\n")
    items = workers.resource_tasks_for_commands([
        shlex.join([sys.executable, "--pcc-preload-delta-worker", "exports", str(roots), "output"]),
    ])
    assert items[0]["diagnostic_phase"] == "preload-delta"
    assert items[0]["diagnostic_modules"] == ["pkg.one", "pkg.three"]
    assert items[0]["estimate_bytes"] == 0


def test_macos_failure_workflow_preserves_narrow_guard_evidence():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/pcc1-package-parity.yml").read_text()
    macos = workflow.split("  pcc1-package-parity:\n", 1)[1]
    evidence = macos.split("      - name: Preserve macOS bootstrap evidence\n", 1)[1]
    evidence = evidence.split("      - name:", 1)[0]
    assert "if: always()" in evidence
    assert "uses: actions/upload-artifact@v6" in evidence
    paths = evidence.split("          path: |\n", 1)[1].splitlines()
    assert [path.strip() for path in paths if path.strip()] == [
        "build/bootstrap-pytest-self*/profile/stage*.result.json",
        "build/bootstrap-pytest-self*/profile/stage*.process.*/result.json",
        # Stage2/3 transports only the single collector's finite text evidence.
        *[
            f"build/bootstrap-pytest-self/profile/stage{stage}.process.{kind}"
            for stage in (2, 3)
            for kind in ("stdout", "stderr", "samples.tsv")
        ],
        "build/bootstrap/stage*.process.*/result.json",
        "build/bootstrap/stage*.process.*/samples.tsv",
        "build/bootstrap/stage*.process.*/target.stdout",
        "build/bootstrap/stage*.process.*/target.stderr",
        "build/bootstrap/stage*.process.*/result.json.worker-rss.tsv",
        "build/bootstrap/stage*.process.*/result.json.process-rss.jsonl",
        "build/bootstrap/stage*.json",
        # Size-prior gate receipts are text only; objects and binaries stay out.
        "build/worker-prior-gate/**/*.json",
        "build/worker-prior-gate/**/*.jsonl",
        "build/worker-prior-gate/**/*.log",
        "build/worker-prior-gate/**/*.stdout",
        "build/worker-prior-gate/**/*.stderr",
        "build/worker-prior-gate/**/*.tsv",
        "build/worker-prior-gate/**/*.xml",
    ]
    environment = macos.split("    env:\n", 1)[1].split("    steps:\n", 1)[0]
    assert 'PCC_BOOTSTRAP_AUTO_TREE_RSS_CEILING_BYTES: "4294967296"' in environment
    assert 'PCC_BOOTSTRAP_MIN_TREE_RSS_BYTES: "2147483648"' in environment
    assert 'PCC_BOOTSTRAP_HOST_MEMORY_RESERVE_BYTES: "536870912"' in environment
    assert "PCC_BOOTSTRAP_MAX_TREE_RSS_BYTES:" not in environment



def _diagnostic_publication(tmp_path, monkeypatch, *, enabled=True):
    report = tmp_path / "rss"
    token = "a" * 64
    calls = []
    monkeypatch.setenv(policy.RESOURCE_REPORT_ENV, str(report))
    monkeypatch.setenv(policy.RESOURCE_TOKEN_ENV, token)
    monkeypatch.setattr(workers, "_coordinator_rss_bytes", lambda: calls.append("current") or 120)
    monkeypatch.setattr(workers, "_worker_peak_rss_bytes", lambda: calls.append("peak") or 110)
    diagnostics = policy.worker_resource_diagnostics(str(tmp_path / "published.tsv")) if enabled else None
    return report, token, calls, diagnostics


def test_resource_diagnostics_disabled_preserves_publication_and_reader(tmp_path, monkeypatch):
    report, token, calls, _ = _diagnostic_publication(tmp_path, monkeypatch, enabled=False)

    def forbidden(*args):
        raise AssertionError("default path called diagnostics")

    monkeypatch.setattr(policy, "record_worker_resource_diagnostic", forbidden)
    policy.publish_worker_resource("complete")
    assert policy.read_worker_resource(str(report), os.getpid(), token) == ("complete", 120, 120)
    assert calls == ["current", "peak"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["rss"]


def test_resource_diagnostics_bind_actual_publication_and_single_accepted_read(tmp_path, monkeypatch):
    import ast
    from io import StringIO

    report, token, calls, published = _diagnostic_publication(tmp_path, monkeypatch)
    policy.publish_worker_resource("started", published)
    text = report.read_text()
    rows = [ast.literal_eval(line) for line in (tmp_path / "published.tsv").read_text().splitlines()]
    assert rows == [("published", (text, 120, 110, 120, 0))]
    assert calls == ["current", "peak"]
    reads = []

    class SameRead(StringIO):
        def read(self):
            reads.append("read")
            report.write_text("overwritten after this read")
            return super().read()

    stream = SameRead(text)
    monkeypatch.setattr(policy, "_open_worker_resource", lambda path: stream)
    accepted = policy.worker_resource_diagnostics(str(tmp_path / "accepted.tsv"))
    assert policy.read_worker_resource(str(report), os.getpid(), token, accepted) == ("started", 120, 120)
    assert stream.closed and reads == ["read"]
    assert ast.literal_eval((tmp_path / "accepted.tsv").read_text()) == (
        "accepted", (os.getpid(), token, "started", 120, 120, text))


def test_resource_diagnostics_rejected_reports_never_become_accepted(tmp_path):
    report = tmp_path / "rss"
    state = policy.worker_resource_diagnostics(str(tmp_path / "diagnostic.tsv"))
    valid = policy.RESOURCE_REPORT_SCHEMA + "\n123\ncomplete\n120\n130\n" + "a" * 64 + "\n"
    for text, pid, token in (
        (valid, 124, "a" * 64), (valid, 123, "b" * 64),
        (valid.replace("120\n130", "120\n119"), 123, "a" * 64),
        (valid.replace("120\n130", "0\n130"), 123, "a" * 64),
        (valid + "extra\n", 123, "a" * 64),
        (valid.replace("120\n130", "bad\n130"), 123, "a" * 64),
    ):
        report.write_text(text)
        assert policy.read_worker_resource(str(report), pid, token, state) is None
    assert state["records"] == 0 and not (tmp_path / "diagnostic.tsv").exists()


def test_resource_diagnostics_failed_replace_keeps_original_error_and_no_publication(tmp_path, monkeypatch):
    report, token, calls, state = _diagnostic_publication(tmp_path, monkeypatch)
    original = OSError("replace sentinel")

    def fail(source, destination):
        raise original

    monkeypatch.setattr(policy, "_replace_worker_resource", fail)
    with pytest.raises(OSError) as raised:
        policy.publish_worker_resource("complete", state)
    assert raised.value is original
    assert state["records"] == 0 and not Path(state["path"]).exists()
    assert not report.exists() and not Path(str(report) + ".tmp").exists()
    assert calls == ["current", "peak"]


def test_resource_diagnostics_write_and_close_failures_do_not_change_report_result(tmp_path, monkeypatch):
    import builtins

    report, token, calls, state = _diagnostic_publication(tmp_path, monkeypatch)
    real_open = builtins.open

    class BrokenClose:
        def __enter__(self):
            return self

        def write(self, text):
            return len(text)

        def __exit__(self, *args):
            raise OSError("diagnostic close sentinel")

    def controlled_open(path, *args, **kwargs):
        if str(path) == state["path"]:
            return BrokenClose()
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(policy, "open", controlled_open, raising=False)
    policy.publish_worker_resource("complete", state)
    assert state["incomplete"] == "write-error"
    assert policy.read_worker_resource(str(report), os.getpid(), token, state) == ("complete", 120, 120)
    assert calls == ["current", "peak"]
    # A missing directory fails opening, not only close; preserve acceptance.
    missing = policy.worker_resource_diagnostics(str(tmp_path / "absent" / "journal"))
    assert policy.read_worker_resource(str(report), os.getpid(), token, missing) == ("complete", 120, 120)
    assert missing["incomplete"] == "write-error"
    policy.finish_worker_resource_diagnostics(missing)  # Status I/O is best effort too.


def test_resource_diagnostics_bounds_are_explicit_and_never_grow_after_overflow(tmp_path):
    import ast

    state = policy.worker_resource_diagnostics(str(tmp_path / "journal"))
    for index in range(256):
        policy.record_worker_resource_diagnostic(state, "published", ("report", index, index, index))
    before = Path(state["path"]).read_bytes()
    policy.record_worker_resource_diagnostic(state, "published", ("report", 256, 256, 256))
    assert state["records"] == 256 and state["incomplete"] == "limit"
    assert Path(state["path"]).read_bytes() == before
    policy.finish_worker_resource_diagnostics(state)
    assert ast.literal_eval(Path(state["path"] + ".status").read_text()) == (256, len(before), "limit")
    large = policy.worker_resource_diagnostics(str(tmp_path / "large"))
    policy.record_worker_resource_diagnostic(large, "published", ("x" * 1024, 1, 1, 1))
    assert large["incomplete"] == "shape-limit" and large["records"] == 0
    assert not Path(large["path"]).exists()
    byte_bound = policy.worker_resource_diagnostics(str(tmp_path / "bytes"))
    byte_bound["bytes"] = 262144
    policy.record_worker_resource_diagnostic(byte_bound, "accepted", ())
    assert byte_bound["incomplete"] == "limit" and not Path(byte_bound["path"]).exists()


def test_resource_diagnostics_pool_retains_actual_observation_and_both_acceptances(tmp_path, monkeypatch):
    import ast

    actual_reader = policy.read_worker_resource
    state, commands, tasks = _spec_lifetime_fixture(tmp_path, monkeypatch, count=1)
    start = pool._start_resource_worker

    def publish_on_start(specs, index):
        pid = start(specs, index)
        token = state["starts"][-1][2]
        Path(tasks[index]["report_path"]).write_text(
            policy.RESOURCE_REPORT_SCHEMA + "\n" + str(pid) + "\ncomplete\n100\n120\n" + token + "\n")
        return pid

    monkeypatch.setattr(pool, "_start_resource_worker", publish_on_start)
    monkeypatch.setattr(policy, "read_worker_resource", actual_reader)
    diagnostics = policy.worker_resource_diagnostics(str(tmp_path / "driver.tsv"))
    history = []
    pool.run_resource_worker_processes(commands, tasks, 1, 512 * MIB,
                                      observations=history, diagnostics=diagnostics)
    records = [ast.literal_eval(line) for line in Path(diagnostics["path"]).read_text().splitlines()]
    assert [row[0] for row in records] == ["accepted", "accepted", "observation"]
    assert records[0][1] == records[1][1]
    assert records[2][1] == (0, state["starts"][0][1], state["starts"][0][2], history[0])
    assert history[0][2] == 120 and state["retired"] == [state["starts"][0][1]]


def _complete_diagnostic_chain(directory):
    directory.mkdir()
    driver = policy.worker_resource_diagnostics(str(directory / "resource-driver.tsv"))
    children = []
    starts, retires = [], []
    for index in range(3):
        pid, token = 123 + index, chr(97 + index) * 64
        (directory / ("started." + str(index) + ".1")).write_text(
            str(pid) + "\t" + token + "\tpcc\t1")
        child = policy.worker_resource_diagnostics(str(directory / (
            "resource-child-" + str(index) + "-" + token + ".tsv")))
        for phase, peak in (("started", 120), ("complete", 125)):
            text = (policy.RESOURCE_REPORT_SCHEMA + "\n" + str(pid) + "\n" + phase
                    + "\n120\n" + str(peak) + "\n" + token + "\n")
            policy.record_worker_resource_diagnostic(child, "published", (text, 120, peak, peak))
            policy.record_worker_resource_diagnostic(driver, "accepted", (pid, token, phase, 120, peak, text))
        sample = ("prior-component:" + str(index), [0, 0, 0, 0, 0, 1], 125,
                  ("host-export-v1", 343932928))
        policy.record_worker_resource_diagnostic(driver, "observation", (index, pid, token, sample))
        policy.finish_worker_resource_diagnostics(child)
        children.append(child)
        starts.append("start\t" + str(index) + "\t" + str(pid) + "\t1\t1\t0\t1\n")
        retires.append("retire\t" + str(index) + "\t" + str(pid) + "\t1\t0\t125\t2\n")
    policy.finish_worker_resource_diagnostics(driver)
    (directory / "admission.tsv").write_text("".join(starts + retires))
    return driver, children


def _rewrite_diagnostic_journal(path, records):
    payload = "".join(repr(row) + "\n" for row in records).encode("utf-8")
    path.write_bytes(payload)
    Path(str(path) + ".status").write_bytes((repr((len(records), len(payload), "")) + "\n").encode())


def test_resource_diagnostics_receipt_matches_exact_text_marks_ambiguity_and_partial_history(tmp_path):
    import ast
    from tests.python.test_worker_size_prior_processes import _fixture_resource_diagnostics

    directory = tmp_path / "width"
    driver, children = _complete_diagnostic_chain(directory)
    first = _fixture_resource_diagnostics(directory)
    assert first["complete"] and len(first["verified_observations"]) == 3
    assert first["accepted"][0]["state"] == "matched"
    assert first["accepted"][0]["raw_candidates"] == [(120, 120, 120)]
    child = children[0]
    text = ast.literal_eval(Path(child["path"]).read_text().splitlines()[0])[1][0]
    policy.record_worker_resource_diagnostic(child, "published", (text, 120, 119, 120))
    policy.finish_worker_resource_diagnostics(child)
    ambiguous = _fixture_resource_diagnostics(directory)
    assert not ambiguous["complete"] and ambiguous["accepted"][0]["state"] == "ambiguous"
    Path(child["path"] + ".status").unlink()
    partial = _fixture_resource_diagnostics(directory)
    assert not partial["complete"] and partial["accepted"][0]["state"] == "ambiguous"
    Path(child["path"]).write_bytes(b"broken journal\n")
    invalid = _fixture_resource_diagnostics(directory)
    assert not invalid["complete"] and invalid["accepted"][0]["state"] == "unmatched"
    Path(child["path"]).write_bytes(b"x" * 262145)
    limited = _fixture_resource_diagnostics(directory)
    assert not limited["complete"] and limited["journals"][Path(child["path"]).name]["truncated"]



def test_resource_diagnostics_native_fixture_keeps_exact_gate_and_width_only_activation():
    import ast

    root = Path(__file__).resolve().parents[2]
    source = (root / "tests/fixtures/native/worker_size_priors.py").read_text()
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    original_gate = ast.dump(ast.parse("samples == [report[2]]", mode="eval").body)
    assert sum(isinstance(node, ast.Assert) and ast.dump(node.test) == original_gate
               for node in ast.walk(functions["run_case"])) == 1
    main = functions["main"]
    factories = [node for node in ast.walk(main) if isinstance(node, ast.If)
                 and any(isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                         and child.func.attr == "worker_resource_diagnostics"
                         for statement in node.body for child in ast.walk(statement))]
    width_conditions = {ast.dump(ast.parse('sys.argv[1] == "width"', mode="eval").body),
                        ast.dump(ast.parse('sys.argv[3] == "width"', mode="eval").body)}
    assert sum(ast.dump(node.test) in width_conditions for node in factories) == 2
    assert 'timeout=20' in (root / "tests/python/test_worker_size_prior_processes.py").read_text()
    assert 'diagnostics=diagnostics' in ast.get_source_segment(source, functions["run_case"])
    assert 'policy.publish_worker_resource("complete", diagnostics)' in source


def test_resource_diagnostics_checks_shape_before_repr_and_bounds_encoded_bytes(tmp_path):
    calls = []

    class Hostile:
        def __repr__(self):
            calls.append("repr")
            raise AssertionError("must not serialize")

    for index, payload in enumerate(((Hostile(), 1, 1, 1), ("x" * 385, 1, 1, 1),
                                     ("report", 2**10000, 1, 1),
                                     ("report", True, 1, 1))):
        state = policy.worker_resource_diagnostics(str(tmp_path / str(index)))
        policy.record_worker_resource_diagnostic(state, "published", payload)
        assert state["incomplete"] == "shape-limit" and state["records"] == 0
        assert not Path(state["path"]).exists()
    assert calls == []
    state = policy.worker_resource_diagnostics(str(tmp_path / "unicode"))
    policy.record_worker_resource_diagnostic(state, "published", ("\U0001f600" * 384, 1, 1, 1))
    assert state["incomplete"] == "limit" and not Path(state["path"]).exists()


def test_resource_diagnostics_preserve_cancellation_and_prior_exception(tmp_path, monkeypatch):
    import builtins

    real_open = builtins.open
    report, token, calls, state = _diagnostic_publication(tmp_path, monkeypatch)
    for error in (KeyboardInterrupt(), SystemExit(37)):
        def interrupt_journal(path, *args, **kwargs):
            if str(path).startswith(state["path"]):
                raise error
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(policy, "open", interrupt_journal, raising=False)
        with pytest.raises(type(error)) as raised:
            policy.publish_worker_resource("complete", state)
        assert raised.value is error and report.exists()
        with pytest.raises(type(error)) as raised:
            policy.read_worker_resource(str(report), os.getpid(), token, state)
        assert raised.value is error
        with pytest.raises(type(error)) as raised:
            policy.finish_worker_resource_diagnostics(state)
        assert raised.value is error
        original = ValueError("original case assertion surrogate")
        with pytest.raises(ValueError) as raised:
            try:
                raise original
            finally:
                policy.finish_worker_resource_diagnostics(state, preserve_error=True)
        assert raised.value is original


def test_resource_diagnostics_receipt_rejects_invalid_observation_retirement_and_roles(tmp_path):
    import ast
    from tests.python.test_worker_size_prior_processes import _fixture_resource_diagnostics

    mutations = ("missing-observation", "null-observation", "wrong-pid", "wrong-token", "wrong-index",
                 "wrong-class", "wrong-inputs", "wrong-binding", "wrong-peak", "duplicate-observation",
                 "missing-complete", "late-acceptance", "retire-peak", "retire-pid", "duplicate-retire",
                 "driver-role", "child-role", "missing-marker", "missing-child", "partial-line",
                 "partial-admission", "partial-status")
    for mutation in mutations:
        directory = tmp_path / mutation
        driver, children = _complete_diagnostic_chain(directory)
        path = Path(driver["path"])
        records = [ast.literal_eval(line) for line in path.read_text().splitlines()]
        observation = list(records[2][1])
        sample = list(observation[3])
        if mutation == "missing-observation":
            del records[2]
        elif mutation == "null-observation":
            records[2] = ("observation", None)
        elif mutation == "wrong-pid":
            observation[1] = 999
        elif mutation == "wrong-token":
            observation[2] = "d" * 64
        elif mutation == "wrong-index":
            observation[0] = 1
        elif mutation == "wrong-class":
            sample[0] = "prior-component:1"
        elif mutation == "wrong-inputs":
            sample[1] = [True, 0, 0, 0, 0, 1]
        elif mutation == "wrong-binding":
            sample[3] = ("host-export-v1", False)
        elif mutation == "wrong-peak":
            sample[2] = 126
        elif mutation == "duplicate-observation":
            records.append(records[2])
        elif mutation == "missing-complete":
            del records[1]
        elif mutation == "late-acceptance":
            records.append(records[1])
        elif mutation == "driver-role":
            child_row = ast.literal_eval(Path(children[0]["path"]).read_text().splitlines()[0])
            records = [child_row]
        if mutation.startswith("wrong-"):
            observation[3] = tuple(sample)
            records[2] = ("observation", tuple(observation))
        _rewrite_diagnostic_journal(path, records)
        admission = directory / "admission.tsv"
        if mutation == "retire-peak":
            admission.write_text(admission.read_text().replace("\t125\t2\n", "\t126\t2\n", 1))
        elif mutation == "retire-pid":
            admission.write_text(admission.read_text().replace("retire\t0\t123", "retire\t0\t999"))
        elif mutation == "duplicate-retire":
            admission.write_text(admission.read_text() + admission.read_text().splitlines(keepends=True)[-1])
        elif mutation == "child-role":
            _rewrite_diagnostic_journal(Path(children[0]["path"]), [records[0]])
        elif mutation == "missing-marker":
            (directory / "started.0.1").unlink()
        elif mutation == "missing-child":
            Path(children[0]["path"]).unlink()
        elif mutation == "partial-line":
            path.write_bytes(path.read_bytes()[:-1])
        elif mutation == "partial-admission":
            admission.write_bytes(admission.read_bytes()[:-1])
        elif mutation == "partial-status":
            status_path = Path(str(path) + ".status")
            status_path.write_bytes(status_path.read_bytes()[:-1])
        result = _fixture_resource_diagnostics(directory)
        assert not result["complete"], mutation


def test_resource_diagnostics_binary_journal_enforces_physical_byte_bound(tmp_path, monkeypatch):
    import builtins

    real_open = builtins.open
    calls = []

    def observe_open(path, mode, **kwargs):
        calls.append((mode, kwargs))
        return real_open(path, mode, **kwargs)

    monkeypatch.setattr(policy, "open", observe_open, raising=False)
    state = policy.worker_resource_diagnostics(str(tmp_path / "physical.tsv"))
    payload = ("\x00" * 248 + "aaa", 1, 1, 1)
    expected = (repr(("published", payload)) + "\n").encode("utf-8")
    assert len(expected) == 1024  # Text-mode CRLF would have made this 1025.
    for index in range(256):
        policy.record_worker_resource_diagnostic(state, "published", payload)
    policy.record_worker_resource_diagnostic(state, "published", payload)
    policy.finish_worker_resource_diagnostics(state)
    actual = Path(state["path"]).read_bytes()
    assert actual == expected * 256 and len(actual) == 262144
    assert state["incomplete"] == "limit" and state["bytes"] == len(actual)
    assert calls == [("ab", {})] * 256 + [("wb", {})]
    assert b"\r\n" not in actual


def test_resource_diagnostics_complete_can_explain_original_peak_equality_failure(tmp_path):
    import ast
    from tests.python.test_worker_size_prior_processes import _fixture_resource_diagnostics

    directory = tmp_path / "decreased-final-report"
    driver, children = _complete_diagnostic_chain(directory)
    child_path = Path(children[0]["path"])
    publications = [ast.literal_eval(line) for line in child_path.read_text().splitlines()]
    old_text = publications[0][1][0]
    higher_text = old_text.replace("\n120\n120\n", "\n120\n130\n")
    publications[0] = ("published", (higher_text, 120, 130, 130))
    _rewrite_diagnostic_journal(child_path, publications)
    driver_path = Path(driver["path"])
    records = [ast.literal_eval(line) for line in driver_path.read_text().splitlines()]
    first = list(records[0][1])
    first[4], first[5] = 130, higher_text
    records[0] = ("accepted", tuple(first))
    observed = list(records[2][1])
    sample = list(observed[3])
    sample[2] = 130
    observed[3] = tuple(sample)
    records[2] = ("observation", tuple(observed))
    _rewrite_diagnostic_journal(driver_path, records)
    admission = directory / "admission.tsv"
    admission.write_text(admission.read_text().replace("retire\t0\t123\t1\t0\t125", "retire\t0\t123\t1\t0\t130"))
    result = _fixture_resource_diagnostics(directory)
    assert result["complete"]  # Complete evidence, NOT a passing fixture assertion.
    assert result["verified_observations"][0]["accepted_maximum"] == 130
    assert result["accepted"][1]["phase"] == "complete" and result["accepted"][1]["peak"] == 125


def test_trim_numeric_receipt_declines_opaque_and_does_not_propagate_writer_failures(monkeypatch):
    from io import StringIO

    class Opaque(int):
        def __str__(self):
            raise AssertionError("untrusted representation")
        def __add__(self, other):
            raise AssertionError("untrusted sum")
        def __lt__(self, other):
            raise AssertionError("untrusted comparison")
    class Broken:
        def write(self, data):
            raise OSError("extra receipt is unavailable")
    log = StringIO()
    monkeypatch.setattr(pool.sys, "stderr", log)
    args = [1, 2, 10, 3, 20, 0, 3, 10, 20, 30, [(0, 11, 15, False, 2)], True]
    monkeypatch.delenv("PCC_PY_FRONTEND_WORKER_TIMING", raising=False)
    pool._resource_trim_diagnostic(*args)
    assert log.getvalue() == ""
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "true")
    for pos, value in ((0, Opaque(1)), (3, -1), (8, Opaque(20)), (9, 10**1000),
                       (10, [(0, 11, Opaque(15), False, 2)]), (10, []), (11, 1),
                       (10, [(0, 11, 2**63-1, False, 2)])):
        trial = list(args)
        trial[pos] = value
        pool._resource_trim_diagnostic(*trial)
    assert log.getvalue() == ""
    pool._resource_trim_diagnostic(*args)
    assert log.getvalue() == ("pcc worker trim owner_pid=1 decision=2 first_pid=10 first_task=3 first_bytes=20 position=0 "
                              "task=3 pid=10 count_before=2 available_bytes=30 before_bytes=35 "
                              "removed_bytes=20 after_bytes=15 previous_task=0 previous_bytes=15 "
                              "synchronized=1\n")
    monkeypatch.setattr(pool.sys, "stderr", Broken())
    pool._resource_trim_diagnostic(*args)


@pytest.mark.parametrize("same", [False, True])
@pytest.mark.parametrize("synchronized", [False, True])
@pytest.mark.parametrize("timing", [False, True])
def test_trim_receipt_distinguishes_identical_lifo_actions_without_reordering(
    tmp_path, monkeypatch, capsys, same, synchronized, timing,
):
    state, commands, tasks = _spec_lifetime_fixture(tmp_path, monkeypatch, count=4)
    for i, (item, w) in enumerate(zip(tasks, (142, 230, 138, 135))):
        item.update(estimate_bytes=w*MIB, inputs=[4-i], **{"class": "class" + str(i)})
    if timing:
        monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "1")
    else:
        monkeypatch.delenv("PCC_PY_FRONTEND_WORKER_TIMING", raising=False)
    if synchronized:
        monkeypatch.setenv(policy.TREE_STATE_ENV, str(tmp_path / "state"))
    monkeypatch.delenv(policy.TREE_RECOVERY_ENV, raising=False)
    retired = []
    def index_for(pid):
        return next(i for i, p, _ in state["starts"] if p == pid)
    def read(path, pid, token):
        i = index_for(pid)
        assert token == next(t for _, p, t in state["starts"] if p == pid)
        peak = (140*MIB if (same or state["stopped"]) else 60*MIB) if i == 0 and len(state["starts"]) >= 3 else MIB
        return ("complete" if pid in retired else "live", peak, peak)
    def poll(pid):
        if len(state["stopped"]) < 2:
            return pool._WORKER_RUNNING
        retired.append(pid)
        return 0
    def snapshot(path, cap, owner_pid, pids, fresh_after, **kw):
        assert bool(path) == synchronized
        return (0, {p: MIB for p in [owner_pid, *pids]}) if synchronized else None
    monkeypatch.setattr(policy, "read_tree_state", snapshot)
    monkeypatch.setattr(policy, "read_worker_resource", read)
    monkeypatch.setattr(pool, "_poll_resource_worker", poll)
    budget = (1+128+512)*MIB
    trace = tmp_path / "admission.tsv"
    observed = []
    pool.run_resource_worker_processes(commands, tasks, 3, budget, observed, str(trace))
    assert [i for i, _, _ in state["starts"]] == [0,1,2,1,2,3]
    assert [index_for(p) for p in state["stopped"]] == [2,1]
    assert len(observed) == 4 and len(set(t for _, _, t in state["starts"])) == 6
    original = [r.split("\t") for r in trace.read_text().splitlines()]
    assert [int(r[1]) for r in original if r[0] == "cancel"] == [2,1]
    assert all(int(r[3]) == int(r[4]) == 512*MIB for r in original
               if r[0]=="start" and int(r[2]) in [p for _,p,_ in state["starts"]][3:5])
    err = capsys.readouterr().err.splitlines()
    log = [dict((k,int(v)) for k,v in (s.split("=") for s in line.split()[3:]))
           for line in err if line.startswith("pcc worker trim ")]
    if not timing:
        assert not log
        return
    assert [r["task"] for r in log] == [2,1]
    assert [r["previous_task"] for r in log] == [1,0]
    assert [r["count_before"] for r in log] == [3,2]
    assert [r["decision"] for r in log] == ([1,1] if same else [1,2])
    assert [r["position"] for r in log] == ([0,1] if same else [0,0])
    assert all(r["synchronized"] == int(synchronized) and r["owner_pid"] == os.getpid() for r in log)
    assert all(r["before_bytes"] == r["after_bytes"] + r["removed_bytes"] > r["available_bytes"]
               for r in log)
    assert (log[0]["after_bytes"] > log[0]["available_bytes"]) == same
    assert (log[0]["first_pid"] == log[1]["first_pid"]) == same
    assert (log[0]["after_bytes"] == log[1]["before_bytes"]) == same
    second = log[1]
    if same:
        # This ONE row, even if surrounding rows were missing, proves two
        # successful stops at that decision and the one-stop arithmetic.
        assert second["position"] == 1 and (second["first_task"], second["task"]) == (2, 1)
        assert second["before_bytes"] > second["available_bytes"]
        assert second["after_bytes"] + second["first_bytes"] <= second["available_bytes"]
    else:
        assert second["position"] == 0 and second["first_task"] == second["task"]


def test_trim_receipt_does_not_mask_single_worker_high_water_refusal(tmp_path, monkeypatch, capsys):
    # A prior high-water mark can put the surviving worker above the same
    # local limit after the original LIFO trim. Recording after_bytes must
    # not convert that into success, and no exclusive retry may start.
    state, commands, tasks = _spec_lifetime_fixture(tmp_path, monkeypatch, count=2)
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "1")
    for i, item in enumerate(tasks):
        item.update(estimate_bytes=8*MIB, inputs=[2-i])
    peak = 205131776
    demand = policy.peak_reservation(peak)
    assert demand == 390632448
    monkeypatch.setattr(policy, "read_worker_resource", lambda *args:
                        ("live", peak, peak) if len(state["starts"]) == 2 else None)
    monkeypatch.setattr(pool, "_poll_resource_worker", lambda pid: pool._WORKER_RUNNING)
    available = 330*MIB
    budget = MIB + policy.RSS_HEADROOM_BYTES + available
    with pytest.raises(policy.WorkerMemoryError, match="worker memory budget cannot admit task=0") as error:
        pool.run_resource_worker_processes(commands, tasks, 2, budget, observations=[])
    assert "worker_reservation_bytes=390632448 available_worker_bytes=346030080" in str(error.value)
    assert [i for i, _, _ in state["starts"]] == [0,1]
    lines = [s for s in capsys.readouterr().err.splitlines() if s.startswith("pcc worker trim ")]
    assert len(lines) == 1
    r = dict((k, int(v)) for k, v in (field.split("=") for field in lines[0].split()[3:]))
    assert (r["count_before"], r["position"], r["first_task"], r["task"]) == (2,0,1,1)
    assert r["available_bytes"] == available < r["after_bytes"] == demand
