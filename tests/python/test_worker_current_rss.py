"""One guard snapshot authorizes live continuation, never future RSS safety."""

import os
from types import SimpleNamespace

import pytest

from pcc.frontends.python import pipeline_frontend_workers as workers
from pcc.frontends.python import worker_process_pool as pool
from pcc.frontends.python import worker_resource_plan as policy


MIB = 1024 * 1024


def _snapshot(path, *, stamp=100.0, budget=1000, owner=11, owner_rss=100,
              worker=12, worker_parent=11, worker_rss=200, descendant=True):
    rows = [f"10\t0\t50", f"{owner}\t10\t{owner_rss}"]
    if worker_rss is not None:
        rows.append(f"{worker}\t{worker_parent}\t{worker_rss}")
    if descendant:
        rows.append(f"13\t{worker}\t30")
    path.write_text(policy.TREE_STATE_SCHEMA + "\n" + str(stamp) + "\n"
                    + str(budget) + "\n" + "\n".join(rows) + "\n")


def test_current_owner_is_an_opt_in_from_the_same_single_read(tmp_path, monkeypatch):
    state = tmp_path / "tree"
    _snapshot(state)
    monkeypatch.setattr(policy, "time", SimpleNamespace(monotonic=lambda: 100.5))
    assert policy.read_tree_state(str(state), 1000, 11, [12]) == (50, {12: 230})
    opened = []
    actual_open = open

    def counted_open(*args, **kwargs):
        opened.append(args[0])
        return actual_open(*args, **kwargs)

    monkeypatch.setattr(policy, "open", counted_open, raising=False)
    assert policy.read_tree_state(str(state), 1000, 11, [12], include_owner=True) == (
        50, {11: 100, 12: 230},
    )
    assert opened == [str(state)]


@pytest.mark.parametrize("change", [
    {"worker_rss": None}, {"worker_rss": 0}, {"worker_parent": 10},
    {"worker": 11}, {"owner_rss": 0}, {"owner": 14},
    {"stamp": 97.0}, {"stamp": 101.0}, {"budget": 999},
])
def test_continuation_requires_fresh_positive_owned_roots(tmp_path, monkeypatch, change):
    state = tmp_path / "tree"
    _snapshot(state, **change)
    monkeypatch.setattr(policy, "time", SimpleNamespace(monotonic=lambda: 100.5))
    assert policy.read_tree_state(str(state), 1000, 11, [12], include_owner=True) is None


def test_missing_worker_stays_outside_and_cannot_authorize_continuation(tmp_path, monkeypatch):
    state = tmp_path / "tree"
    _snapshot(state, worker_rss=None)
    monkeypatch.setattr(policy, "time", SimpleNamespace(monotonic=lambda: 100.5))
    # The old projection already charges this orphan to outside RSS. The
    # opt-in path additionally requires the active root itself to be measured.
    assert policy.read_tree_state(str(state), 1000, 11, [12]) == (80, {12: 0})
    assert policy.read_tree_state(str(state), 1000, 11, [12], include_owner=True) is None
    assert policy.read_tree_state(str(state), 1000, 11, [11], include_owner=True) is None


def _run_mock_pool(tmp_path, monkeypatch, *, budget, owner_peak, owner_current,
                   worker_peak, worker_current, outside=32 * MIB, peer=False,
                   with_state=True):
    """Run the real policy/report parsers; replace only time and processes."""
    owner_pid = os.getpid()
    tree = tmp_path / "tree"
    trace = tmp_path / "events"
    live, all_workers, stopped, reaped, observations = {}, {}, [], [], []
    tasks = [{"class": "current-rss-first", "inputs": [2], "estimate_bytes": 0,
              "report_path": str(tmp_path / "rss0"), "restartable": True,
              "calibrate_before_peers": True}]
    if peer:
        tasks.append({"class": "current-rss-peer", "inputs": [1],
                      "estimate_bytes": 64 * MIB, "report_path": str(tmp_path / "rss1"),
                      "restartable": True})

    class Clock:
        now = 100.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds
            write_tree()

    clock = Clock()

    def write_tree():
        rows = [f"{owner_pid + 500}\t0\t{outside}",
                f"{owner_pid}\t{owner_pid + 500}\t{owner_current}"]
        for pid, item in live.items():
            current = worker_current if item["index"] == 0 else 16 * MIB
            rows.append(f"{pid}\t{owner_pid}\t{current}")
        tree.write_text(policy.TREE_STATE_SCHEMA + "\n" + str(clock.now) + "\n"
                        + str(budget) + "\n" + "\n".join(rows) + "\n")

    def report(item, phase):
        peak = worker_peak if item["index"] == 0 else 16 * MIB
        # CPython's resource publisher uses its high-water reading in both
        # report fields. Actual current RSS comes only from the guard file.
        with open(tasks[item["index"]]["report_path"], "w") as stream:
            stream.write(policy.RESOURCE_REPORT_SCHEMA + "\n" + str(item["pid"])
                         + "\n" + phase + "\n" + str(peak) + "\n" + str(peak)
                         + "\n" + item["token"] + "\n")

    def start(specs, index):
        pid = owner_pid + 100 + index
        token = next(value.split("=", 1)[1] for value in specs[index][1]
                     if value.startswith(policy.RESOURCE_TOKEN_ENV + "="))
        assert len(token) == 64
        item = {"pid": pid, "index": index, "polls": 0, "token": token}
        live[pid] = all_workers[pid] = item
        report(item, "assemble-complete")
        write_tree()
        return pid

    def poll(pid):
        item = all_workers[pid]
        item["polls"] += 1
        if item["polls"] < 3:
            return pool._WORKER_RUNNING
        report(item, "complete")
        return 0

    def retire(pid):
        reaped.append(pid)
        live.pop(pid)
        write_tree()

    def stop(pid):
        stopped.append(pid)
        live.pop(pid)

    monkeypatch.setattr(workers, "_coordinator_rss_bytes", lambda: owner_peak)
    monkeypatch.setattr(policy, "time", clock)
    monkeypatch.setattr(pool, "time", clock)
    monkeypatch.setattr(pool, "_start_resource_worker", start)
    monkeypatch.setattr(pool, "_poll_resource_worker", poll)
    monkeypatch.setattr(pool, "_retire_resource_worker", retire)
    monkeypatch.setattr(pool, "_stop_resource_worker", stop)
    monkeypatch.setenv("PCC_PY_FRONTEND_WORKER_TIMING", "0")
    if with_state:
        monkeypatch.setenv(policy.TREE_STATE_ENV, str(tree))
    else:
        monkeypatch.delenv(policy.TREE_STATE_ENV, raising=False)
    write_tree()
    failure = None
    try:
        pool.run_resource_worker_processes(
            ["unused-command" for _ in tasks], tasks, 2, budget,
            observations=observations, trace_path=str(trace),
        )
    except policy.WorkerMemoryError as error:
        failure = str(error)
    events = [line.split("\t") for line in trace.read_text().splitlines()] if trace.exists() else []
    assert not live
    return failure, observations, events, stopped, reaped, tasks


@pytest.mark.parametrize("owner_peak,owner_current,worker_current", [
    (192 * MIB, 64 * MIB, 400 * MIB),
    (192 * MIB, 192 * MIB, 250 * MIB),
])
def test_nonoverlapping_peaks_do_not_stop_safe_exclusive_work(
    tmp_path, monkeypatch, owner_peak, owner_current, worker_current,
):
    failure, observations, events, stopped, reaped, tasks = _run_mock_pool(
        tmp_path, monkeypatch, budget=640 * MIB, owner_peak=owner_peak,
        owner_current=owner_current, worker_peak=400 * MIB,
        worker_current=worker_current, peer=True,
    )
    assert failure is None and not stopped and len(reaped) == 2
    first_retire = next(row for row in events if row[:2] == ["retire", "0"])
    assert int(first_retire[3]) == policy.peak_reservation(400 * MIB) == 628 * MIB
    assert int(first_retire[5]) == observations[0][2] == 400 * MIB
    assert events[0][0] == "calibrate"
    peer_start = next(row for row in events if row[1] == "1" and row[0] in ("start", "calibrate"))
    assert events.index(first_retire) < events.index(peer_start)
    assert not any(row[0] == "cancel" for row in events)
    # The correction does not fit cancelled peaks or relax a later retry's
    # same-task lower bound. A new run with that actual floor still refuses.
    tasks[0]["incomplete_peak_bytes"] = 400 * MIB
    assert policy.minimum_task_bytes(tasks[0]) == 400 * MIB
    with pytest.raises(policy.WorkerMemoryError, match="cannot admit"):
        policy.require_task_fits(0, policy.minimum_task_bytes(tasks[0]), 288 * MIB, 640 * MIB)


@pytest.mark.parametrize("extra,accepted", [(0, True), (1, False), (2 * MIB, False)])
def test_simultaneous_limit_preserves_the_full_headroom(tmp_path, monkeypatch, extra, accepted):
    failure, observations, events, stopped, reaped, _tasks = _run_mock_pool(
        tmp_path, monkeypatch, budget=512 * MIB, owner_peak=64 * MIB,
        owner_current=64 * MIB, worker_peak=300 * MIB,
        worker_current=288 * MIB + extra,
    )
    if accepted:
        assert failure is None and len(reaped) == 1 and not stopped
        assert observations[0][2] == 300 * MIB
    else:
        assert "live worker current subtree exceeds safe worker space" in failure
        assert "current_owner_rss_bytes=" + str(64 * MIB) in failure
        assert "current_worker_subtree_rss_bytes=" + str(288 * MIB + extra) in failure
        assert "available_worker_bytes=" + str(288 * MIB) in failure
        assert len(stopped) == 1 and not reaped and observations == []
        assert not any(row[0] == "retire" for row in events)


def test_current_crossing_is_checked_even_when_padded_forecast_fits(tmp_path, monkeypatch):
    # A native current-owner getter may be below an earlier accepted guard
    # sample. This case would be missed by checking only forecast promotion.
    assert policy.peak_reservation(225 * MIB) < 640 * MIB - 64 * MIB - 32 * MIB - 128 * MIB
    failure, observations, _events, stopped, reaped, _tasks = _run_mock_pool(
        tmp_path, monkeypatch, budget=640 * MIB, owner_peak=64 * MIB,
        owner_current=256 * MIB, worker_peak=225 * MIB, worker_current=225 * MIB,
    )
    assert "live worker current subtree exceeds safe worker space" in failure
    assert "available_worker_bytes=" + str(224 * MIB) in failure
    assert len(stopped) == 1 and not observations and not reaped


def test_no_guard_does_not_receive_current_rss_relief(tmp_path, monkeypatch):
    failure, observations, _events, stopped, reaped, _tasks = _run_mock_pool(
        tmp_path, monkeypatch, budget=640 * MIB, owner_peak=192 * MIB,
        owner_current=64 * MIB, worker_peak=400 * MIB, worker_current=250 * MIB,
        with_state=False,
    )
    assert "exclusive calibration exceeded safe worker space" in failure
    assert len(stopped) == 1 and not observations and not reaped


def test_exhausted_historical_owner_reserve_still_blocks_admission(tmp_path, monkeypatch):
    failure, observations, events, stopped, reaped, _tasks = _run_mock_pool(
        tmp_path, monkeypatch, budget=640 * MIB, owner_peak=500 * MIB,
        owner_current=64 * MIB, worker_peak=100 * MIB, worker_current=100 * MIB,
    )
    assert "worker memory budget cannot admit one task" in failure
    assert not events and not stopped and not reaped and not observations
