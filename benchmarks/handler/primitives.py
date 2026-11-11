"""Primitive micro-benchmarks compiled by pcc (scripts/pcc_perf.py ``prim``).

A module of native_stats.py, which runs

    native_stats prim NAME BATCH MILLISECONDS WORKERS

as ``primitive_benchmark(NAME, BATCH, MILLISECONDS, WORKERS)``: primitive NAME in
WORKERS virtual threads, each repeating batches of BATCH operations until
MILLISECONDS have passed (one batch when it is 0). The result holds the
operations done, the seconds they took and the sum of the batches'
results. The pure primitives come from primitive_ops.py, which CPython runs
unchanged (primitives_asyncio.py); the virtual-thread primitives here have
asyncio counterparts there.
"""

import json
import sys
import time

import pcc.virtual_thread as vt
from pcc_gateway.structured import TaskScope

import primitive_ops


def primitive_child(delay_ms: int):
    vt.sleep_current(delay_ms)
    return 1


def primitive_profile(delay_ms: int):
    vt.sleep_current(delay_ms)
    return {"name": "Ada", "plan": "pro"}


def primitive_notifications(delay_ms: int):
    vt.sleep_current(delay_ms)
    return ["Your report is ready"]


def op_sleep(count: int) -> int:
    index = 0
    while index < count:
        vt.sleep_current(0)
        index += 1
    return index


def op_scope(count: int) -> int:
    index = 0
    while index < count:
        scope = TaskScope("primitive")
        try:
            scope.join()
        finally:
            scope.close()
        index += 1
    return index


def op_spawn(count: int) -> int:
    total = 0
    index = 0
    while index < count:
        scope = TaskScope("primitive")
        try:
            handle = scope.fork(vt.spawn(primitive_child, 0))
            scope.join()
            total += scope.result(handle)
        finally:
            scope.close()
        index += 1
    return total


def op_request(count: int) -> int:
    expected = b'{"notifications": ["Your report is ready"], "profile": {"name": "Ada", "plan": "pro"}}'
    total = 0
    index = 0
    while index < count:
        scope = TaskScope("request")
        try:
            profile = scope.fork(vt.spawn(primitive_profile, 0))
            notifications = scope.fork(vt.spawn(primitive_notifications, 0))
            scope.join()
            data = {"profile": scope.result(profile),
                    "notifications": scope.result(notifications)}
        finally:
            scope.close()
        body = json.dumps(data, sort_keys=True).encode()
        if body != expected:
            raise RuntimeError("primitive response mismatch: " + body.decode())
        total += 1
        index += 1
    return total


def run_batch(name: str, count: int) -> int:
    if name == "sleep":
        return op_sleep(count)
    if name == "scope":
        return op_scope(count)
    if name == "spawn":
        return op_spawn(count)
    if name == "request":
        return op_request(count)
    return primitive_ops.OPS[name](count)


def primitive_worker(name: str, batch: int, milliseconds: int):
    deadline = time.perf_counter() + milliseconds / 1000.0
    done = 0
    checksum = 0
    while True:
        checksum += run_batch(name, batch)
        done += batch
        if time.perf_counter() >= deadline:
            return [done, checksum]


def primitive_benchmark(name: str, batch: int, milliseconds: int, workers: int):
    # One untimed batch: first-use initialisation is not the primitive.
    run_batch(name, batch)
    started = time.perf_counter()
    scope = TaskScope("primitives")
    try:
        index = 0
        while index < workers:
            scope.fork(vt.spawn(primitive_worker, name, batch, milliseconds))
            index += 1
        scope.join()
        operations = 0
        checksum = 0
        for thread in scope.children:
            done = scope.result(thread)
            operations += done[0]
            checksum += done[1]
    finally:
        scope.close()
    return {"name": name, "workers": workers, "batch": batch,
            "operations": operations, "checksum": checksum,
            "seconds": time.perf_counter() - started}
