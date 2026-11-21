"""Exclusive GC phase time: separately conserve the process sum and real maximum."""
from __future__ import annotations

import json

import pytest

from pcc.diagnostics.gc_pause_report import (
    parse_native_counters, phase_report, read_measurement,
)

# Linux GC3, 4-carrier native handler: 10,000 responses, 200 warmups.
# This is one quiescent snapshot, not 14 independently observed maxima.
NATIVE = [
    5, 96435, 24670, 0,
    78, 0, 38118, 331, 0, 877, 22901, 124, 0, 5, 8078, 25923, 0, 0,
    10, 0, 10910, 61, 0, 128, 6342, 12, 0, 1, 1693, 5513, 0, 0,
]
COUNTERS = (5, 96435, 24670)


def dump(values=NATIVE, counters=COUNTERS):
    return "\n".join([
        "server ready", "gc-pause-stw %d %d %d" % counters,
        *[f"gc-pause-slot {i} {x}" for i, x in enumerate(values)],
    ])


def test_native_sum_and_actual_longest():
    report = phase_report(*parse_native_counters(dump().splitlines()))
    assert report["windows"] == 5
    assert report["total_us"] == sum(report["aggregate_us"].values()) == 96435
    assert report["maximum_us"] == sum(report["longest_window_us"].values()) == 24670
    assert report["longest_window_us"]["pin_owner_lookup"] == 6342
    assert report["aggregate_us"]["pin_owner_lookup"] == 22901


def test_native_phase_rows_can_arrive_in_any_order():
    assert parse_native_counters(reversed(dump().splitlines())) == (list(COUNTERS), NATIVE)


@pytest.mark.parametrize("bad", [
    dump().replace("gc-pause-slot 5 0\n", ""),
    dump() + "\ngc-pause-slot 5 0",
    dump() + "\ngc-pause-slot 32 0",
    dump() + "\ngc-pause-stw 5 96435 24670",
    dump().replace("gc-pause-slot 5 0", "gc-pause-slot 5"),
    "gc-pause-stw 20 3000 123",  # Totals alone cannot imply a breakdown.
])
def test_refuses_malformed_or_mixed_measurement(bad):
    with pytest.raises(ValueError):
        parse_native_counters(bad.splitlines())


@pytest.mark.parametrize("offset,value", [
    (0, 4), (1, 96436), (2, 24671), (3, 14),
    (6, 38117), (20, 10909), (4, -1), (4, True),
])
def test_refuses_partial_or_out_of_sync_snapshot(offset, value):
    vector = NATIVE.copy()
    vector[offset] = value
    with pytest.raises(ValueError):
        phase_report(COUNTERS, vector)


def test_rejects_apparent_longest_larger_than_total_in_one_phase():
    vector = NATIVE.copy()
    # Sums still match, but a particular phase at the claimed maximum exceeds
    # that phase's sum across *all* events.
    vector[18] += 90
    vector[20] -= 90
    with pytest.raises(ValueError):
        phase_report(COUNTERS, vector)


def test_zero_events_is_a_valid_empty_measurement():
    report = phase_report([0, 0, 0], [0] * 32)
    assert report["windows"] == 0
    assert not any(report["longest_window_us"].values())


def test_reads_native_json_or_diagnostic_stream(tmp_path):
    j = tmp_path / "native.json"
    j.write_text(json.dumps({"gc": dict(zip(("119","120","121"), COUNTERS)), "pause_vector": NATIVE}))
    t = tmp_path / "server.out"
    t.write_text(dump())
    assert read_measurement(j) == read_measurement(t)


@pytest.mark.parametrize(("label", "one_off"), [
    ("gc-pause-stw", "issue4-stw"), ("gc-pause-slot", "pause-slot"),
])
def test_no_one_off_native_log_labels(label, one_off):
    with pytest.raises(ValueError, match="incomplete phase snapshot"):
        parse_native_counters(dump().replace(label, one_off).splitlines())


@pytest.mark.parametrize(("count", "total", "maximum"), [(0, 9, 0), (2, 5, 5), (2, 4, 1)])
def test_rejects_unattainable_counts_even_when_both_vectors_sum(count, total, maximum):
    vector = [0] * 32
    vector[:3] = [count, total, maximum]
    vector[4] = total
    vector[18] = maximum
    with pytest.raises(ValueError):
        phase_report(vector[:3], vector)
