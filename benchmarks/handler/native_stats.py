"""The benchmark program compiled by pcc: the handler and the primitives.

    native_stats CONCURRENCY DELAY_MS ROUNDS --summary WORKERS

is the handler benchmark: a request forks two virtual threads and encodes
JSON. Its CPython asyncio counterpart is asyncio_stats.py. Derived from
pcc-gateway's benchmark_native.py, with a carrier pool for more than one
worker. It prints one JSON object: requests, started_at/finished_at
(seconds), latency bounds, and "gc" with the collection, sweep and
graph-lock-wait counters by PCC_GC_COUNTER index.

    native_stats prim NAME BATCH MILLISECONDS WORKERS

runs one primitive of primitives.py and prints its JSON with the same
"gc" counters. Both live in one program so that a change to the compiler
emits one program, not two.
"""

import json
import sys
import time

import pcc.virtual_thread as vt
from pcc_gateway.structured import TaskScope, run_until_complete
from pcc.extern import extern, c_int64

from primitives import primitive_benchmark

_idle_sleep_ns = extern("pcc_platform_sleep_ns", (c_int64,), c_int64)
_telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)


def fetch_profile(delay_ms: int):
    vt.sleep_current(delay_ms)
    return {"name": "Ada", "plan": "pro"}


def fetch_notifications(delay_ms: int):
    vt.sleep_current(delay_ms)
    return ["Your report is ready"]


def request(delay_ms: int):
    started = time.perf_counter()
    scope = TaskScope("request")
    try:
        profile = scope.fork(vt.spawn(fetch_profile, delay_ms))
        notifications = scope.fork(vt.spawn(fetch_notifications, delay_ms))
        scope.join()
        data = {"profile": scope.result(profile),
                "notifications": scope.result(notifications)}
    finally:
        scope.close()
    body = json.dumps(data, sort_keys=True).encode()
    expected = b'{"notifications": ["Your report is ready"], "profile": {"name": "Ada", "plan": "pro"}}'
    if body != expected:
        raise RuntimeError("benchmark response mismatch: " + body.decode())
    return (time.perf_counter() - started) * 1000.0


def batch(concurrency: int, delay_ms: int):
    scope = TaskScope("batch")
    try:
        index = 0
        while index < concurrency:
            scope.fork(vt.spawn(request, delay_ms))
            index += 1
        scope.join()
        samples = []
        for child in scope.children:
            samples.append(scope.result(child))
        return samples
    finally:
        scope.close()


def benchmark():
    concurrency = int(sys.argv[1])
    delay_ms = int(sys.argv[2])
    rounds = int(sys.argv[3])
    warmup = batch(concurrency, delay_ms)
    second_warmup = batch(concurrency, delay_ms)
    warmup_requests = len(warmup) + len(second_warmup)
    samples = []
    started = time.perf_counter()
    index = 0
    while index < rounds:
        completed = batch(concurrency, delay_ms)
        samples.extend(completed)
        index += 1
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return {"concurrency": concurrency, "delay_ms": delay_ms,
            "rounds": rounds, "requests": len(samples),
            "warmup_requests": warmup_requests,
            "elapsed_ms": elapsed_ms, "latencies_ms": samples,
            "started_at": started, "finished_at": started + elapsed_ms / 1000.0}


def main():
    primitive = sys.argv[1] == "prim"
    if primitive:
        thread = vt.spawn(primitive_benchmark, sys.argv[2], int(sys.argv[3]),
                          int(sys.argv[4]), int(sys.argv[5]))
    else:
        thread = vt.spawn(benchmark)
    workers = int(sys.argv[5])
    if workers == 1:
        result = run_until_complete(thread)
    else:
        if vt.carrier_pool_start(workers) != workers:
            raise RuntimeError("carrier pool start failed")
        try:
            while vt.outcome(thread) == vt.OUTCOME_PENDING:
                if _idle_sleep_ns(1000000) < 0:
                    raise RuntimeError("outer wait failed")
            failure = vt.exception(thread)
            if failure is not None:
                raise failure
            if vt.outcome(thread) != vt.OUTCOME_RETURNED:
                raise RuntimeError("root task cancelled")
            result = vt.result(thread)
        finally:
            if vt.carrier_pool_stop() != workers:
                raise RuntimeError("carrier pool stop failed")
    # Profilers need the request phase, without spending their samples on
    # formatting the final latency array. Normal comparison runs retain it.
    if not primitive and sys.argv[4] == "--summary":
        samples = result.pop("latencies_ms")
        result["latency_min_ms"] = min(samples)
        result["latency_max_ms"] = max(samples)
    stats = {}
    for metric in (0, 3, 5, 7, 8, 9, 32, 33, 11, 13, 14, 18, 28) + tuple(range(119, 158)):
        stats[str(metric)] = _telemetry(metric)
    result["gc"] = stats
    print(json.dumps(result, sort_keys=True))


main()
