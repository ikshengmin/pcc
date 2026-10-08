"""Byte reservations shared by host and native compiler worker schedulers.

A reservation is an estimate, never an RSS limit. The enclosing process-tree
watchdog remains the enforcement owner. Uncalibrated tasks run exclusively;
completed tasks supply measured envelopes for later, no-larger inputs in the
same execution class. Estimates may rise while a worker is alive, but never
fall until that process and its descendants have retired.
"""

import os
import time


TREE_STATE_ENV = "PCC_WORKER_TREE_STATE_PATH"
TREE_STATE_SCHEMA = "pcc.worker-tree-rss.v1"
RESOURCE_REPORT_ENV = "PCC_WORKER_RESOURCE_REPORT"
RESOURCE_TOKEN_ENV = "PCC_WORKER_RESOURCE_TOKEN"
RESOURCE_REPORT_SCHEMA = "pcc.worker-resource.v2"
RSS_HEADROOM_BYTES = 134217728
STATE_MAX_AGE_SECONDS = 2.0


class WorkerMemoryError(ValueError):
    pass


def peak_reservation(peak_bytes: int) -> int:
    if peak_bytes <= 0:
        raise WorkerMemoryError("worker peak RSS is unavailable")
    # The margin is the existing measured-worker calibration contract.
    return (int(peak_bytes) * 5 + 3) // 4 + RSS_HEADROOM_BYTES


def input_envelope_covers(observed_inputs, pending_inputs) -> bool:
    if len(observed_inputs) != len(pending_inputs):
        return False
    for before, after in zip(observed_inputs, pending_inputs):
        if before < after or after < 0:
            return False
    return True


def estimated_task_bytes(task, observations) -> int:
    """Return a conservative estimate or zero for an uncalibrated task.

    Samples are local to this invocation and execution class. They cannot be
    reused across machines, compilers, collectors, options, or export readers.
    A size envelope is an explicit extrapolation, not an absolute bound.
    """
    estimate = max(0, int(task["estimate_bytes"]))
    incomplete_peak = int(task.get("incomplete_peak_bytes", 0))
    if incomplete_peak > 0:
        # A cancelled attempt establishes only a lower bound. It must never
        # become a completed-task sample that authorizes concurrent retries.
        estimate = max(estimate, peak_reservation(incomplete_peak))
    for key, inputs, peak in observations:
        if key == task["class"] and input_envelope_covers(inputs, task["inputs"]):
            estimate = max(estimate, peak_reservation(peak))
    return estimate


def minimum_task_bytes(task) -> int:
    """Retain explicit reservations and this task's observed lower bound.

    Completed peaks borrowed from other inputs and their safety margins are
    forecasts. They cannot prove that this unmeasured task exceeds the budget.
    An incomplete same-task peak is actual evidence and remains a floor.
    """
    return max(0, int(task["estimate_bytes"]),
               int(task.get("incomplete_peak_bytes", 0)))


def available_worker_bytes(tree_budget: int, owner_rss: int,
                           outside_owner_rss: int = 0) -> int:
    if tree_budget <= 0 or owner_rss <= 0 or outside_owner_rss < 0:
        raise WorkerMemoryError("positive tree budget and measured owner RSS are required")
    reserve = int(owner_rss) + int(outside_owner_rss) + RSS_HEADROOM_BYTES
    available = int(tree_budget) - reserve
    if available <= 0:
        raise WorkerMemoryError(
            "worker memory budget cannot admit one task: budget=" + str(tree_budget)
            + " owner=" + str(owner_rss) + " other_processes=" + str(outside_owner_rss)
            + " headroom=" + str(RSS_HEADROOM_BYTES)
            + " owner_reservation_bytes=" + str(reserve)
            + "; task demand is not yet known"
        )
    return available


def require_task_fits(task_index: int, demand: int, available: int,
                      tree_budget: int) -> None:
    if demand <= 0:
        raise WorkerMemoryError("worker byte reservation must be positive")
    if demand > available:
        raise WorkerMemoryError(
            "worker memory budget cannot admit task=" + str(task_index)
            + " worker_reservation_bytes=" + str(demand)
            + " available_worker_bytes=" + str(available)
            + " estimated_minimum_budget_bytes=" + str(tree_budget - available + demand)
            + " budget=" + str(tree_budget)
            + "; this reservation is not a guaranteed full-task peak"
        )


def choose_task(pending, tasks, observations, active_reservations,
                width: int, available: int, guarded_calibration: bool = False):
    """Pick a fitting task or reserve idle capacity for a calibration.

    Unknown demand is not represented by a fabricated fixed worker peak. The
    initial task reserves all available child memory. Its actual observed peak
    can admit multiple subsequent workers whose input envelope it covers.
    A cancelled attempt needs the same exclusive space for its bounded retry;
    stop admitting peers until the active workers have drained. Preserve any
    known minimum above that space so the caller refuses before launching.
    """
    if len(active_reservations) >= width:
        return -1, 0, False
    for index in pending:
        if tasks[index].get("retry_calibration", False):
            if active_reservations:
                return -1, 0, False
            return index, max(available, estimated_task_bytes(tasks[index], observations)), True
    remaining = available - sum(active_reservations)
    unknown = -1
    forecast_only = -1
    for index in pending:
        demand = estimated_task_bytes(tasks[index], observations)
        if demand == 0:
            if unknown < 0:
                unknown = index
        elif demand <= remaining:
            return index, demand, False
        elif (guarded_calibration and demand > available and forecast_only < 0
              and minimum_task_bytes(tasks[index]) <= available):
            forecast_only = index
    if not active_reservations and unknown >= 0 and available > 0:
        return unknown, available, True
    if not active_reservations and forecast_only >= 0 and available > 0:
        # Keep the complete forecast visible while reserving the entire idle
        # lane. Fresh guard accounting must supervise this calibration.
        return forecast_only, estimated_task_bytes(tasks[forecast_only], observations), True
    return -1, 0, False


def read_worker_resource(path: str, expected_pid: int, expected_token: str = ""):
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as stream:
            rows = stream.read().splitlines()
        if len(rows) != 6 or rows[0] != RESOURCE_REPORT_SCHEMA or rows[5] != expected_token:
            return None
        pid, current, peak = int(rows[1]), int(rows[3]), int(rows[4])
        if pid != expected_pid or current <= 0 or peak < current:
            return None
        return rows[2], current, peak
    except (OSError, ValueError):
        return None


def read_tree_state(path: str, tree_budget: int, owner_pid: int, active_pids,
                    not_before: float = 0.0):
    """Read the guard's synchronized accounting without shelling out.

    Return (outside-owner-and-worker bytes, worker-subtree bytes). Missing,
    stale, malformed and mismatched state is unavailable, never zero RSS.
    This optional observation transport does not provide the RSS guard.
    """
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as stream:
            lines = stream.read().splitlines()
        if len(lines) < 4 or lines[0] != TREE_STATE_SCHEMA:
            return None
        sampled_at = float(lines[1])
        age = time.monotonic() - sampled_at
        if (not (0 <= age <= STATE_MAX_AGE_SECONDS) or sampled_at < not_before
                or int(lines[2]) != tree_budget):
            return None
        rows = {}
        for line in lines[3:]:
            values = line.split("\t")
            if len(values) != 3:
                return None
            pid, parent, rss = int(values[0]), int(values[1]), int(values[2])
            if pid <= 0 or parent < 0 or rss < 0 or pid in rows:
                return None
            rows[pid] = (parent, rss)
        if owner_pid not in rows or rows[owner_pid][1] <= 0:
            return None
        children = {}
        for pid in active_pids:
            children[pid] = 0
        outside = 0
        for pid, row in rows.items():
            if pid == owner_pid:
                continue
            cursor = pid
            seen = set()
            worker = 0
            while cursor in rows and cursor not in seen:
                if cursor in children:
                    worker = cursor
                    break
                seen.add(cursor)
                cursor = rows[cursor][0]
            if worker:
                children[worker] += row[1]
            else:
                outside += row[1]
        return outside, children
    except (OSError, ValueError):
        return None


def publish_worker_resource(phase: str) -> None:
    """Publish self high-water RSS at existing worker phase boundaries."""
    path = str(os.environ.get(RESOURCE_REPORT_ENV, "") or "")
    if not path:
        return
    from pcc.frontends.python.pipeline_frontend_workers import (
        _coordinator_rss_bytes, _worker_peak_rss_bytes,
    )

    current = _coordinator_rss_bytes()
    peak = max(current, _worker_peak_rss_bytes())
    if current <= 0 or peak <= 0:
        raise WorkerMemoryError("worker RSS observation is unavailable")
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        stream.write(
            RESOURCE_REPORT_SCHEMA + "\n" + str(os.getpid()) + "\n" + phase
            + "\n" + str(current) + "\n" + str(peak)
            + "\n" + str(os.environ.get(RESOURCE_TOKEN_ENV, "") or "") + "\n"
        )
    # CPython's Windows reader can deny delete sharing while reading the old
    # snapshot. Keep that valid snapshot until atomic replacement succeeds;
    # never truncate it or treat a failed final publication as completion.
    # Bound contention below STATE_MAX_AGE_SECONDS. The independent tree RSS
    # guard and the reader's PID/token/completion checks remain authoritative.
    deadline = time.monotonic() + 1.0
    try:
        while True:
            try:
                os.replace(temporary, path)
                break
            except PermissionError as error:
                remaining = deadline - time.monotonic()
                if getattr(error, "winerror", 0) not in (5, 32) or remaining <= 0:
                    raise
                time.sleep(min(0.01, remaining))
    except BaseException:
        # Do not leave a failed attempt's unpublished snapshot for a retry.
        # Preserve the publication error if cleanup itself is unavailable.
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
