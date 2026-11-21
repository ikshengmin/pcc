"""Verify the exclusive-phase snapshot of a diagnostic collection-window build.

Run `python -m pcc.diagnostics.gc_pause_report FILE` on either the native
handler's JSON or the gateway's line-oriented shutdown output. Sample once
after workers are quiescent. Ordinary runtime counters 119--121 alone carry
no phase information and cannot be converted into a phase report.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Sequence
from pathlib import Path

# Slots 4..17 accumulate each exclusive phase over the whole process.
# Slots 18..31 describe one event: the first event attaining the maximum.
# Where the world is released for a callback, the release-to-reacquire
# interval is kept separate; it is not necessarily mutator stop time.
PHASES = (
    "runtime_other", "retired_sweep_drain", "mark_other", "registered_roots",
    "prepare_heap_for_mark", "capture_sort_root_words", "pin_owner_lookup",
    "sweep_other", "candidate_recheck", "weak_targets", "select_and_call_finalizers",
    "clear_reclaim", "world_released_until_reacquire", "gray_drain",
)
HEADER = 4
SLOTS = HEADER + 2 * len(PHASES)


def phase_report(counters: Sequence[int], vector: Sequence[int]) -> dict:
    """Return the two complete vectors, rejecting mismatched/inconsistent reads."""
    if len(counters) != 3 or len(vector) != SLOTS:
        raise ValueError("expected 3 public counters and 32 phase slots")
    if any(type(x) is not int or x < 0 for seq in (counters, vector) for x in seq):
        raise ValueError("collection counters must be nonnegative integers")
    count, total, maximum = counters
    if list(vector[:3]) != list(counters) or vector[3] != 0:
        raise ValueError("phase header does not match counters 119–121 or reserved slot")
    if (not count and (total or maximum)) or (
            count and (maximum == 0 or not maximum + count - 1 <= total <= count * maximum)):
        raise ValueError("impossible count, total and maximum for nonempty windows")
    aggregate, longest = vector[HEADER:HEADER + len(PHASES)], vector[HEADER + len(PHASES):]
    if (sum(aggregate) != total or sum(longest) != maximum
            or any(event > all_events for event, all_events in zip(longest, aggregate))):
        raise ValueError("phase times do not conserve the process total and same-event maximum")
    return {
        "windows": count, "total_us": total, "maximum_us": maximum,
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
        if words[0] in ("gc-pause-stw", "issue4-stw"):
            if counters is not None or len(words) != 4:
                raise ValueError("malformed or duplicate public counter row")
            counters = [int(word) for word in words[1:]]
        elif words[0] in ("gc-pause-slot", "pause-slot"):
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
            counters = [data["gc"][str(i)] for i in (119, 120, 121)]
            vector = data["pause_vector"]
        except (KeyError, TypeError) as exc:
            raise ValueError("missing phase vector or public GC counters") from exc
    else:
        counters, vector = parse_native_counters(text.splitlines())
    return phase_report(counters, vector)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    try:
        report = read_measurement(args.file)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"{args.file}: {exc}\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
