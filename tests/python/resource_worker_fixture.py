"""Own a fixture child until its startup report is ready for the real pool."""

import time

from pcc.frontends.python import worker_process_pool as pool
from pcc.frontends.python import worker_resource_plan as policy


def live_forecast_pressure_plan(owner_peak, tail_peaks, *, measured_limit=False):
    """Fit the measured tail pair, then cross only the intended RSS boundary."""
    assert owner_peak > 0 and len(tail_peaks) == 2 and min(tail_peaks) > 0
    reservations = [policy.peak_reservation(peak) for peak in tail_peaks]
    capacity = max(reservations) if measured_limit else sum(reservations)
    # Apply the existing measured-peak padding to the owner as well as both
    # children. The owner's 25% margin covers launch/report bookkeeping.
    budget = policy.peak_reservation(owner_peak) + capacity
    assert budget <= 640 * 1024 ** 2, ("measured fixture exceeds original cap", budget)
    available = policy.available_worker_bytes(budget, owner_peak)
    crossing = (available - policy.RSS_HEADROOM_BYTES) * 4 // 5 + 1
    target = crossing + min(max(1, owner_peak // 4), (available - crossing) // 2)
    assert 0 < target < available < policy.peak_reservation(target)
    assert capacity < available
    return {
        "budget": budget, "owner_peak": owner_peak, "tail_peaks": list(tail_peaks),
        "tail_reservations": reservations, "available": available,
        "target_peak": target, "limit_payload": available,
    }


def start_resource_worker_and_wait(start, specs, index, ready, *, timeout):
    """Do not leak a child if a fixture fails before returning its handle."""
    pid = start(specs, index)
    try:
        deadline = time.monotonic() + timeout
        while not ready():
            assert time.monotonic() < deadline, "worker startup report timed out"
            time.sleep(0.005)
    except BaseException:
        # The admission loop cannot own this PID until this wrapper returns.
        # Stop and reap here, including on an interrupted or failed predicate.
        pool._stop_resource_worker(pid)
        raise
    return pid
