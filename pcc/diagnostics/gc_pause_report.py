"""Verify the exclusive-phase snapshot of a diagnostic collection-window build.

Run `python scripts/pcc_perf.py pause report FILE` on either the native
handler's JSON or the gateway's line-oriented shutdown output. Sample once
after workers are quiescent. Ordinary runtime counters 119--121 alone carry
no phase information and cannot be converted into a phase report.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path

from pcc.diagnostics._gc_pause_layout import (
    AGGREGATE,
    INVALID,
    LONGEST,
    MAXIMUM_US,
    PHASES,
    PUBLIC_STW_COUNTERS,
    SLOTS,
    TOTAL_US,
    WINDOWS,
    ZERO_US_WINDOWS,
)


def phase_report(counters: Sequence[int], vector: Sequence[int]) -> dict:
    """Return the two complete vectors, rejecting mismatched/inconsistent reads."""
    if not isinstance(vector, Sequence) or len(counters) != len(PUBLIC_STW_COUNTERS) or len(vector) != SLOTS:
        raise ValueError(f"expected {len(PUBLIC_STW_COUNTERS)} public counters and {SLOTS} phase slots")
    if any(type(x) is not int or x < 0 for seq in (counters, vector) for x in seq):
        raise ValueError("collection counters must be nonnegative integers")
    count, total, maximum = vector[WINDOWS], vector[TOTAL_US], vector[MAXIMUM_US]
    zeroes = vector[ZERO_US_WINDOWS]
    if vector[INVALID] != 0:
        raise ValueError("phase snapshot contains an invalid clock or phase")
    nonzero = count - zeroes
    if (nonzero < 0 or (not nonzero and (total or maximum)) or
            (nonzero > 0 and (not maximum or not maximum + nonzero - 1 <= total <= nonzero * maximum))):
        raise ValueError("impossible count, total and maximum for measured windows")
    # Public telemetry retains its 1µs floor per event. The raw phase
    # ledger does not invent a duration when both clock samples agree.
    public_count, public_total, public_maximum = counters
    if (count != public_count or total + zeroes != public_total or
            (max(1, maximum) if count else 0) != public_maximum):
        raise ValueError("phase header does not match public counters 119–121 after the 1µs floor")
    aggregate, longest = vector[AGGREGATE:LONGEST], vector[LONGEST:ZERO_US_WINDOWS]
    if (sum(aggregate) != total or sum(longest) != maximum
            or any(event > all_events for event, all_events in zip(longest, aggregate))):
        raise ValueError("phase times do not conserve the process total and same-event maximum")
    return {
        "windows": count, "total_us": total, "maximum_us": maximum,
        "zero_us_windows": zeroes,
        "public_telemetry": {
            "windows": public_count, "total_us": public_total, "maximum_us": public_maximum,
        },
        "aggregate_us": dict(zip(PHASES, aggregate)),
        "longest_window_us": dict(zip(PHASES, longest)),
    }


def parse_native_counters(lines: Iterable[str]) -> tuple[list[int], list[int]]:
    """Recognize one snapshot amid unrelated server log lines; never fill gaps."""
    counters = None
    values = {}
    for line in lines:
        words = line.split()
        if not words:
            continue
        if words[0] == "gc-pause-stw":
            if counters is not None or len(words) != 4:
                raise ValueError("malformed or duplicate public counter row")
            counters = [int(word) for word in words[1:]]
        elif words[0] == "gc-pause-slot":
            if len(words) != 3:
                raise ValueError("malformed phase slot row")
            index, value = int(words[1]), int(words[2])
            if not 0 <= index < SLOTS or index in values:
                raise ValueError(f"invalid or duplicate phase slot {index}")
            values[index] = value
    if counters is None or len(values) != SLOTS:
        raise ValueError("incomplete phase snapshot; public counters alone are insufficient")
    return counters, [values[i] for i in range(SLOTS)]


def read_measurement(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if text.lstrip().startswith("{"):
        data = json.loads(text)
        try:
            counters = [data["gc"][str(i)] for i in PUBLIC_STW_COUNTERS]
            vector = data["pause_vector"]
        except (KeyError, TypeError) as exc:
            raise ValueError("missing phase vector or public GC counters") from exc
    else:
        counters, vector = parse_native_counters(text.splitlines())
    return phase_report(counters, vector)
