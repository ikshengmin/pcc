"""Byte reservations shared by host and native compiler worker schedulers.

A reservation is an estimate, never an RSS limit. The enclosing process-tree
watchdog remains the enforcement owner. Recognized host tasks have empirical
size priors; unsupported execution classes still calibrate exclusively. Completed
tasks supply measured envelopes for later, no-larger inputs in the same class. Estimates may rise while a worker is alive, but never
fall until that process and its descendants have retired.
"""

import os
import time


TREE_STATE_ENV = "PCC_WORKER_TREE_STATE_PATH"
# v1 timestamps use the same-machine CPython monotonic clock (Python >=3.13):
# Darwin mach_absolute_time / CLOCK_UPTIME_RAW, Linux CLOCK_MONOTONIC, and
# Windows QueryPerformanceCounter. The native platform clock must share both
# its epoch and suspend behavior. Wall time and process-relative clocks are
# not compatible; not_before is in this same domain. State is run-local.
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



def task_startup_prior_bytes(task) -> int:
    """Return a soft host forecast, never an explicit or measured lower bound.

    The coefficients use completed cc5 samples; 137 completions are held out.
    They are not upper bounds: both runs censor their largest unfinished work.
    Export bytes were constant within each sampled reader, so their multiplier
    is a provisional scaling assumption. Native workers do not use this model.
    Live growth, the tree watchdog and bounded exclusive retries remain owners
    of memory safety. Completed modeled samples can only raise this forecast's
    scale; unsupported workers retain the legacy absolute-envelope maximum.
    """
    model = str(task.get("startup_prior_model", "") or "")
    if not model:
        return 0
    mib = 1024 * 1024
    if model == "host-indexed-backend-v1":
        if not task.get("input_ready", False):
            return 0
        packed_bytes = task.get("prior_input_bytes", 0)
        if type(packed_bytes) is not int or packed_bytes <= 0:
            raise WorkerMemoryError("indexed worker prior needs verified packed bytes")
        return peak_reservation(64 * mib + 7 * packed_bytes)
    inputs = task["inputs"]
    if len(inputs) != 6 or any(type(value) is not int or value < 0 for value in inputs):
        raise WorkerMemoryError("host worker prior needs nonnegative source/AST/export bytes")
    source_bytes, ast_bytes, export_bytes = inputs[0], inputs[2], inputs[4]
    if model == "host-export-v1":
        working_bytes = 160 * mib + 160 * source_bytes
    elif model == "host-summary-v1":
        working_bytes = 192 * mib + 5 * (ast_bytes + export_bytes)
    elif model == "host-codegen-v1":
        if inputs[5] > 1:
            # Bounded host batches reserve the SUM of singleton forecasts.
            # Keep every member's fixed/export cost and margin; do not assume
            # that shared imports or sequential cleanup reduce peak demand.
            # 224 * 5 is divisible by 4, so the sum is exact for integer bytes.
            return (inputs[5] * peak_reservation(128 * mib + 43 * export_bytes)
                    + 280 * (source_bytes + ast_bytes))
        working_bytes = 128 * mib + 224 * (source_bytes + ast_bytes) + 43 * export_bytes
    elif model == "host-indexed-frontend-v1":
        working_bytes = 128 * mib + 80 * (source_bytes + ast_bytes) + 11 * export_bytes
    else:
        raise WorkerMemoryError("unknown worker startup prior model: " + model)
    return peak_reservation(working_bytes)


def completed_task_observation(task, peak):
    """Bind a verified completion to its original model, never a live sample."""
    sample = (task["class"], list(task["inputs"]), peak)
    model = str(task.get("startup_prior_model", "") or "")
    if not model:
        return sample
    prior = task_startup_prior_bytes(task)
    if prior <= 0:
        raise WorkerMemoryError("completed modeled worker has no startup prior")
    return sample + ((model, prior),)


def estimated_task_bytes(task, observations) -> int:
    """Return a soft estimate or zero for an uncalibrated task.

    Samples are local to this invocation and execution class. They cannot be
    reused across machines, compilers, collectors, options, or export readers.
    Modeled samples raise the entire size predictor by the greatest measured
    padded-peak/original-prior ratio. A large task's absolute peak is not copied
    to every smaller task. This remains extrapolation, not an RSS upper bound.
    Legacy three-field samples retain their old covering-envelope maximum.
    """
    prior = task_startup_prior_bytes(task)
    model = str(task.get("startup_prior_model", "") or "")
    estimate = max(0, int(task["estimate_bytes"]), prior)
    incomplete_peak = int(task.get("incomplete_peak_bytes", 0))
    if incomplete_peak > 0:
        # A cancelled attempt establishes only a lower bound. It must never
        # become a completed-task sample that authorizes concurrent retries.
        estimate = max(estimate, peak_reservation(incomplete_peak))
    for sample in observations:
        if len(sample) not in (3, 4):
            raise WorkerMemoryError("worker observation shape differs")
        key, inputs, peak = sample[:3]
        if key != task["class"]:
            continue
        if model and len(sample) == 4:
            fitted = sample[3]
            if (type(fitted) is not tuple or len(fitted) != 2
                    or fitted[0] != model or type(fitted[1]) is not int
                    or fitted[1] <= 0):
                raise WorkerMemoryError("worker observation prior binding differs")
            # Arbitrary-precision integer ceil avoids float precision loss,
            # division by zero, and fixed-width intermediate overflow.
            scaled = (prior * peak_reservation(peak) + fitted[1] - 1) // fitted[1]
            estimate = max(estimate, scaled)
        elif input_envelope_covers(inputs, task["inputs"]):
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


def resource_task_order(tasks):
    """Schedule modeled host work largest-first; retain legacy native cohorts.

    Source/AST bytes vary per singleton; full-export bytes and module count
    do not. Split each power-of-two size band at its arithmetic midpoint,
    limiting a cohort to a size ratio below 1.5 instead of 2. The largest
    input in each half-band calibrates first, with the existing componentwise
    coverage and maximum-peak estimator unchanged. Task indices never move.
    Other phases, mixed classes and dependency-bearing tasks keep their old
    priority and readiness rules.
    """
    pending = sorted(range(len(tasks)), key=lambda index: (
        -sum(tasks[index]["inputs"]), index,
    ))
    if not tasks:
        return pending, []
    if any(task.get("startup_prior_model", "") for task in tasks):
        # Host priors replace the small-cohort drain. Indices and dependency
        # edges stay intact; lazy backend inputs are re-ranked when ready.
        return sorted(pending, key=lambda index: (
            -task_startup_prior_bytes(tasks[index]),
            -sum(tasks[index]["inputs"]), index,
        )), []
    execution_class = tasks[0]["class"]
    bands = []
    for task in tasks:
        inputs = task["inputs"]
        if (task.get("diagnostic_phase", "") != "codegen"
                or task["class"] != execution_class
                or len(task.get("diagnostic_indices", [])) != 1
                or len(inputs) != 6 or inputs[5] != 1
                or inputs[0] != inputs[1] or inputs[2] != inputs[3]
                or inputs[4] != tasks[0]["inputs"][4]
                or any(value < 0 for value in inputs)
                or task.get("depends_on", -1) >= 0
                or task.get("input_path", "")):
            return pending, []
        size = inputs[0] + inputs[2]
        size_total = size
        band = 0
        upper = 1
        while size > 0:
            size //= 2
            upper *= 2
            band += 1
        if size == 0 and size_total > 0:
            # [2**k, 2**(k+1)) splits at 3*2**(k-1). Keep the
            # original quotient loop and skip this split if it ends in NaN.
            band = 2 * band + (1 if 4 * size_total >= 3 * upper else 0)
        bands.append(band)
    pending = sorted(pending, key=lambda index: (
        bands[index], -sum(tasks[index]["inputs"]), index,
    ))
    return pending, bands


def ready_resource_cohort(ready, pending, active_indices, bands):
    """Drain the smallest unfinished cohort, including its live workers."""
    if not bands:
        return ready
    current = min(bands[index] for index in pending + active_indices)
    return [index for index in ready if bands[index] == current]


def choose_task(pending, tasks, observations, active_reservations,
                width: int, available: int, guarded_calibration: bool = False):
    """Pick a fitting task or reserve idle capacity for a calibration.

    Modeled host tasks use empirical byte priors, refreshed for each ready
    input. Unsupported shapes retain exclusive calibration; its measured peak
    can admit later workers whose full input envelope it covers.
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
    if any(tasks[index].get("startup_prior_model", "") for index in pending):
        # Re-evaluate after dependency/input preparation and after every live
        # observation. Select the largest fitting ready forecast, not a stale
        # initial ordering with zero-sized backend placeholders.
        pending = sorted(pending, key=lambda index: (
            -estimated_task_bytes(tasks[index], observations), index,
        ))
        if (pending and guarded_calibration and available > 0
                and tasks[pending[0]].get("startup_prior_model", "")
                and estimated_task_bytes(tasks[pending[0]], observations) > available):
            if active_reservations:
                return -1, 0, False
            index = pending[0]
            return index, estimated_task_bytes(tasks[index], observations), True
    # Split dependency heads must not wait behind an indefinitely refilled
    # known-work lane. Only ready, explicitly marked, genuinely unknown tasks
    # participate; retry calibration above keeps its original priority.
    calibration = -1
    for index in pending:
        if (tasks[index].get("calibrate_before_peers", False)
                and estimated_task_bytes(tasks[index], observations) == 0
                and (calibration < 0 or index < calibration)):
            calibration = index
    if calibration >= 0:
        if active_reservations or available <= 0:
            return -1, 0, False
        return calibration, available, True
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
                    not_before: float = 0.0, include_owner: bool = False,
                    diagnostic=None):
    """Read the guard's synchronized accounting without shelling out.

    Return (outside-owner-and-worker bytes, worker-subtree bytes). Missing,
    stale, malformed and mismatched state is unavailable, never zero RSS.
    This optional observation transport does not provide the RSS guard.
    With include_owner, the RSS map also contains the owner's current RSS;
    all active roots must be positively measured direct children. Every value
    comes from this one atomically published process-table sample, not from
    independently timed high-water marks or a second file read. Optional
    diagnostic output describes this read; -1 marks fields not yet parsed.
    """
    reason = "empty_path"
    sampled_at = checked_at = age = -1.0
    observed_budget = owner_parent = owner_rss = -1
    active_pid = active_parent = active_rss = -1
    row_number = line_count = 0
    try:
        if not path:
            return None
        reason = "read"
        with open(path, "r", encoding="utf-8") as stream:
            lines = stream.read().splitlines()
        line_count = len(lines)
        reason = "header"
        if len(lines) < 4 or lines[0] != TREE_STATE_SCHEMA:
            return None
        reason = "timestamp"
        sampled_at = float(lines[1])
        reason = "clock"
        checked_at = time.monotonic()
        age = checked_at - sampled_at
        reason = "age"
        if not (0 <= age <= STATE_MAX_AGE_SECONDS):
            return None
        reason = "before_barrier"
        if sampled_at < not_before:
            return None
        reason = "budget"
        observed_budget = int(lines[2])
        if observed_budget != tree_budget:
            return None
        rows = {}
        for line in lines[3:]:
            row_number += 1
            values = line.split("\t")
            reason = "row_fields"
            if len(values) != 3:
                return None
            reason = "row_integer"
            pid, parent, rss = int(values[0]), int(values[1]), int(values[2])
            reason = "row_identity"
            if pid <= 0 or parent < 0 or rss < 0 or pid in rows:
                return None
            rows[pid] = (parent, rss)
        reason = "owner_missing"
        if owner_pid not in rows:
            return None
        owner_parent, owner_rss = rows[owner_pid]
        reason = "owner_rss"
        if owner_rss <= 0:
            return None
        children = {}
        for pid in active_pids:
            if include_owner:
                active_pid = pid
                active_parent = active_rss = -1
                reason = "active_owner"
                if pid == owner_pid:
                    return None
                reason = "active_missing"
                if pid not in rows:
                    return None
                active_parent, active_rss = rows[pid]
                reason = "active_parent"
                if active_parent != owner_pid:
                    return None
                reason = "active_rss"
                if active_rss <= 0:
                    return None
            children[pid] = 0
        reason = "accounting"
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
        if include_owner:
            children[owner_pid] = rows[owner_pid][1]
        reason = "accepted"
        return outside, children
    except (OSError, ValueError):
        return None
    finally:
        if diagnostic is not None:
            try:
                diagnostic.clear()
                diagnostic.update({
                    "reason": reason, "sampled_at": sampled_at,
                    "checked_at": checked_at, "age": age, "not_before": not_before,
                    "expected_budget": tree_budget, "observed_budget": observed_budget,
                    "owner_pid": owner_pid, "owner_parent": owner_parent, "owner_rss": owner_rss,
                    "active_pid": active_pid, "active_parent": active_parent, "active_rss": active_rss,
                    "line_count": line_count, "row_number": row_number,
                })
            except Exception:
                pass  # Optional evidence must never change the reader outcome.


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
