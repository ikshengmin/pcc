"""Frozen-work handoff scheduling experiment, never a compiler benchmark.

Read a small JSON fixture or an existing Stage1 artifact. Both policies keep
identical task durations, reservations, worker width, and outstanding slots.
No process, network request, compiler, or CI action is started. Live growth,
calibration order, CPU contention, retry decisions, and launch/I/O costs are
NOT reconstructed: the result cannot establish a production speedup.
"""

import argparse
from collections import Counter
import heapq
import json
import math
from pathlib import Path
import re
import zipfile


def _positive_int(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError(label + " must be a positive integer")
    return value


def validate_model(model):
    available = _positive_int(model["available_bytes"], "available_bytes")
    width = _positive_int(model["width"], "width")
    slot_limit = _positive_int(model["slot_limit"], "slot_limit")
    tasks = {}
    for row in model["tasks"]:
        identity = row["id"]
        if type(identity) is not int or identity < 0 or identity in tasks:
            raise ValueError("task ids must be unique nonnegative integers")
        duration = row["duration_s"]
        if (type(duration) not in (int, float) or not math.isfinite(duration)
                or duration <= 0):
            raise ValueError("task durations must be finite and positive")
        reservation = _positive_int(row["reservation_bytes"], "reservation_bytes")
        if reservation > available:
            raise ValueError("frozen task reservation exceeds available bytes")
        if type(row["slot"]) is not int or row["slot"] < 0:
            raise ValueError("invalid slot owner")
        if type(row["depends_on"]) is not int:
            raise ValueError("invalid dependency")
        tasks[identity] = dict(row)
    slots = {}
    for identity, row in tasks.items():
        slots.setdefault(row["slot"], []).append(identity)
    for owner, members in slots.items():
        if len(members) != 2 or owner not in members:
            raise ValueError("each handoff needs exactly one producer and consumer")
        consumer = next(identity for identity in members if identity != owner)
        if (tasks[owner]["depends_on"] != -1
                or tasks[consumer]["depends_on"] != owner):
            raise ValueError("handoff dependency must name its own producer")
    return available, width, slot_limit, tasks


def replay(model, policy):
    if policy not in ("largest", "drain"):
        raise ValueError("policy must be largest or drain")
    available, width, slot_limit, tasks = validate_model(model)
    pending = set(tasks)
    completed, slots = set(), set()
    running = []
    clock = 0.0
    occupancy = Counter()
    trace = []
    maximum_reserved = maximum_slots = 0
    while pending or running:
        # Retire all simultaneous completions before making a new decision.
        while running and running[0][0] <= clock:
            _end, identity = heapq.heappop(running)
            completed.add(identity)
            if tasks[identity]["depends_on"] >= 0:
                slots.remove(tasks[identity]["slot"])
        reserved = sum(tasks[identity]["reservation_bytes"] for _, identity in running)
        ready = []
        for identity in pending:
            row = tasks[identity]
            if row["depends_on"] >= 0:
                if row["depends_on"] not in completed:
                    continue
            elif len(slots) >= slot_limit:
                continue
            ready.append(identity)
        ready.sort(key=lambda identity: (
            (tasks[identity]["depends_on"] < 0) if policy == "drain" else False,
            -tasks[identity]["reservation_bytes"], identity,
        ))
        selected = next((identity for identity in ready
                         if tasks[identity]["reservation_bytes"] <= available - reserved), None)
        if selected is not None and len(running) < width:
            row = tasks[selected]
            pending.remove(selected)
            if row["depends_on"] < 0:
                slots.add(row["slot"])
            end = clock + row["duration_s"]
            heapq.heappush(running, (end, selected))
            reserved += row["reservation_bytes"]
            maximum_reserved = max(maximum_reserved, reserved)
            maximum_slots = max(maximum_slots, len(slots))
            trace.append({"task": selected, "start_s": clock, "end_s": end,
                          "reserved_bytes": reserved, "slots": len(slots)})
            continue
        if not running:
            if pending:
                raise ValueError("frozen handoff graph cannot make progress")
            break
        following = running[0][0]
        occupancy[len(running)] += following - clock
        clock = following
    assert not slots and len(completed) == len(tasks)
    return {"policy": policy, "model_wall_s": clock,
            "occupancy_s": dict(sorted(occupancy.items())),
            "max_reserved_bytes": maximum_reserved,
            "max_outstanding_slots": maximum_slots,
            "completed_tasks": len(completed), "trace": trace}


_FIELD = re.compile(
    r"\b(event|task|pid|phase|indices|mapping_offset|reservation_bytes|available_bytes|"
    r"peak_bytes|monotonic_s)=(\[[^\]]*\]|'[^']*'|[^ ]+)"
)


def model_from_log(text, *, width, slot_limit, reservation):
    """Extract successful FE/BE pairs; disclose, do not replay, failed attempts.

    The host split implementation pairs adjacent FE/BE task IDs. Require that
    topology and singleton source indices rather than guessing arbitrary DAGs.
    The most frequently observed available capacity is used for BOTH policies.
    Its measured range is disclosed; this is not a live tree-RSS reconstruction.
    """
    if reservation not in ("admission", "completed-peak"):
        raise ValueError("unknown frozen reservation model")
    opened, completed = {}, {}
    cancelled = []
    for line in text.splitlines():
        if not line.startswith("pcc frontend admission "):
            continue
        row = dict(_FIELD.findall(line))
        phase = row.get("phase", "").strip("'")
        if phase not in ("indexed-frontend", "indexed-backend"):
            continue
        if int(row.get("mapping_offset", "0")):
            continue
        identity, pid = int(row["task"]), int(row["pid"])
        source = json.loads(row["indices"])
        if len(source) != 1 or type(source[0]) is not int:
            raise ValueError("only singleton indexed tasks are supported")
        key = identity, pid
        event, timestamp = row["event"], float(row["monotonic_s"])
        if event in ("start", "calibrate"):
            if key in opened:
                raise ValueError("duplicate start")
            opened[key] = (row, phase, source[0], timestamp)
        elif event in ("retire", "cancel"):
            if key not in opened:
                raise ValueError("terminal event has no matching start")
            start, start_phase, start_source, when = opened.pop(key)
            if (phase, source[0]) != (start_phase, start_source):
                raise ValueError("attempt identity changed")
            duration = timestamp - when
            if event == "cancel":
                cancelled.append(duration)
                continue
            if identity in completed:
                raise ValueError("task completed more than once")
            demand = int(start["reservation_bytes"])
            if reservation == "completed-peak":
                peak = int(row["peak_bytes"])
                if peak <= 0:
                    raise ValueError("completed peak is unavailable")
                demand = max(demand, (peak * 5 + 3) // 4 + 134217728)
            completed[identity] = {
                "id": identity, "duration_s": duration,
                "reservation_bytes": demand, "source": source[0], "phase": phase,
                "available": int(start["available_bytes"]),
                "start": when, "end": timestamp,
            }
    if opened or not completed:
        raise ValueError("log is incomplete or contains no indexed completions")
    tasks = []
    for identity, row in sorted(completed.items()):
        producer = row["phase"] == "indexed-frontend"
        owner = identity if producer else identity - 1
        peer = identity + 1 if producer else identity - 1
        if (peer not in completed or row["source"] != completed[peer]["source"]
                or row["phase"] == completed[peer]["phase"]):
            raise ValueError("observed task IDs do not form host split pairs")
        tasks.append({"id": identity, "duration_s": row["duration_s"],
                      "reservation_bytes": row["reservation_bytes"],
                      "slot": owner, "depends_on": -1 if producer else owner})
    capacities = Counter(row["available"] for row in completed.values())
    common_available = min(capacities, key=lambda value: (-capacities[value], value))
    model = {"available_bytes": common_available,
             "width": width, "slot_limit": slot_limit, "tasks": tasks}
    validate_model(model)
    evidence = {
        "reservation_model": reservation,
        "observed_available_min_bytes": min(capacities),
        "observed_available_max_bytes": max(capacities),
        "common_available_observations": capacities[common_available],
        "observed_successful_attempt_span_s": (
            max(row["end"] for row in completed.values())
            - min(row["start"] for row in completed.values())
        ),
        "cancelled_attempts_omitted": len(cancelled),
        "cancelled_worker_seconds_omitted": sum(cancelled),
    }
    return model, evidence


def compare(model):
    rows = [replay(model, policy) for policy in ("largest", "drain")]
    for row in rows:
        row.pop("trace")
    return {"available_bytes": model["available_bytes"], "width": model["width"],
            "slot_limit": model["slot_limit"], "policies": rows,
            "model_delta_s": rows[0]["model_wall_s"] - rows[1]["model_wall_s"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fixture", type=Path)
    source.add_argument("--artifact", type=Path)
    parser.add_argument("--log-member", default="worker-prior-gate/stage1.log")
    parser.add_argument("--width", type=int, default=3)
    parser.add_argument("--slots", type=int, default=3)
    args = parser.parse_args(argv)
    result = {"warning": "Frozen-work model only; not observed or predicted CI speedup."}
    if args.fixture:
        result["fixture"] = compare(json.loads(args.fixture.read_text()))
    else:
        with zipfile.ZipFile(args.artifact) as archive:
            report = json.loads(archive.read("worker-prior-gate/stage1.json"))
            log = archive.read(args.log_member).decode("utf-8")
        result["measured_source_commit"] = report["source"]["commit"]
        result["measured_compile_wall_s"] = report["metrics"]["compile_wall_s"]
        for reservation in ("admission", "completed-peak"):
            model, evidence = model_from_log(
                log, width=args.width, slot_limit=args.slots, reservation=reservation,
            )
            result[reservation] = {**compare(model), **evidence}
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
