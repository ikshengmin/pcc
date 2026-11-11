"""CPython asyncio.TaskGroup counterpart of native_stats.py.

    python asyncio_stats.py CONCURRENCY DELAY_MS ROUNDS --summary

runs the same request shape on asyncio and prints the same JSON fields
(without the GC counters), so scripts/pcc_perf.py can report every pcc
collector against it.
"""

import asyncio
import os
import json
import sys
import time


async def fetch_profile(delay_ms: int):
    await asyncio.sleep(delay_ms / 1000)
    return {"name": "Ada", "plan": "pro"}


async def fetch_notifications(delay_ms: int):
    await asyncio.sleep(delay_ms / 1000)
    return ["Your report is ready"]


async def request(delay_ms: int):
    started = time.perf_counter()
    async with asyncio.TaskGroup() as scope:
        profile = scope.create_task(fetch_profile(delay_ms))
        notifications = scope.create_task(fetch_notifications(delay_ms))
    data = {"profile": profile.result(),
            "notifications": notifications.result()}
    body = json.dumps(data, sort_keys=True).encode()
    expected = b'{"notifications": ["Your report is ready"], "profile": {"name": "Ada", "plan": "pro"}}'
    if body != expected:
        raise RuntimeError("benchmark response mismatch")
    return (time.perf_counter() - started) * 1000.0


async def batch(concurrency: int, delay_ms: int):
    async with asyncio.TaskGroup() as scope:
        children = [scope.create_task(request(delay_ms))
                    for _ in range(concurrency)]
    return [child.result() for child in children]


async def benchmark():
    concurrency, delay_ms, rounds = map(int, sys.argv[1:4])
    warmup = await batch(concurrency, delay_ms)
    second_warmup = await batch(concurrency, delay_ms)
    warmup_requests = len(warmup) + len(second_warmup)
    samples = []
    if len(sys.argv) > 5:
        barrier = sys.argv[5]
        ready = sys.argv[6]
        with open(ready, "w") as marker:
            marker.write("ready")
        while not os.path.exists(barrier):
            time.sleep(0.001)
    started = time.perf_counter()
    for _ in range(rounds):
        samples.extend(await batch(concurrency, delay_ms))
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return {"concurrency": concurrency, "delay_ms": delay_ms,
            "rounds": rounds, "requests": len(samples),
            "warmup_requests": warmup_requests,
            "elapsed_ms": elapsed_ms, "latencies_ms": samples,
            "started_at": started, "finished_at": started + elapsed_ms / 1000.0}


if __name__ == "__main__":
    result = asyncio.run(benchmark())
    if len(sys.argv) > 4 and sys.argv[4] == "--summary":
        samples = result.pop("latencies_ms")
        result["latency_min_ms"] = min(samples)
        result["latency_max_ms"] = max(samples)
    print(json.dumps(result, sort_keys=True))
