"""CPython counterpart of primitives.py (scripts/pcc_perf.py ``prim``).

    python primitives_asyncio.py NAME BATCH MILLISECONDS

runs the same primitive_ops.py loops in CPython, and the asyncio
counterparts of the virtual-thread primitives (a task group for a scope, a
task for a spawned thread, ``asyncio.sleep(0)`` for a yield), on one
thread, and prints the same JSON fields.
"""

import asyncio
import json
import sys
import time

import primitive_ops


async def child(delay_ms: int):
    await asyncio.sleep(delay_ms / 1000)
    return 1


async def fetch_profile(delay_ms: int):
    await asyncio.sleep(delay_ms / 1000)
    return {"name": "Ada", "plan": "pro"}


async def fetch_notifications(delay_ms: int):
    await asyncio.sleep(delay_ms / 1000)
    return ["Your report is ready"]


async def op_sleep(count: int) -> int:
    index = 0
    while index < count:
        await asyncio.sleep(0)
        index += 1
    return index


async def op_scope(count: int) -> int:
    index = 0
    while index < count:
        async with asyncio.TaskGroup():
            pass
        index += 1
    return index


async def op_spawn(count: int) -> int:
    total = 0
    index = 0
    while index < count:
        async with asyncio.TaskGroup() as scope:
            handle = scope.create_task(child(0))
        total += handle.result()
        index += 1
    return total


async def op_request(count: int) -> int:
    expected = b'{"notifications": ["Your report is ready"], "profile": {"name": "Ada", "plan": "pro"}}'
    total = 0
    index = 0
    while index < count:
        async with asyncio.TaskGroup() as scope:
            profile = scope.create_task(fetch_profile(0))
            notifications = scope.create_task(fetch_notifications(0))
        data = {"profile": profile.result(),
                "notifications": notifications.result()}
        body = json.dumps(data, sort_keys=True).encode()
        if body != expected:
            raise RuntimeError("primitive response mismatch: " + body.decode())
        total += 1
        index += 1
    return total


TASK_OPS = {
    "sleep": op_sleep,
    "scope": op_scope,
    "spawn": op_spawn,
    "request": op_request,
}


async def run_batch(name: str, count: int) -> int:
    if name in TASK_OPS:
        return await TASK_OPS[name](count)
    return primitive_ops.OPS[name](count)


async def benchmark():
    name = sys.argv[1]
    batch = int(sys.argv[2])
    milliseconds = int(sys.argv[3])
    await run_batch(name, batch)
    started = time.perf_counter()
    deadline = started + milliseconds / 1000.0
    operations = 0
    checksum = 0
    while True:
        checksum += await run_batch(name, batch)
        operations += batch
        if time.perf_counter() >= deadline:
            break
    return {"name": name, "workers": 1, "batch": batch,
            "operations": operations, "checksum": checksum,
            "seconds": time.perf_counter() - started}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(benchmark()), sort_keys=True))
