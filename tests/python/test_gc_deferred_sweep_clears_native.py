"""The collector thread clears and destroys a settled sweep's plain garbage.

Specification: docs/project-intent.md (five-GC lifetime equality); Python
language reference 3.1 (objects are never destroyed while reachable) and
the threading and weakref library documentation. A GC1-GC3 automatic sweep
that ran no finalizer or weak callback leaves its eligible candidates
(instances, lists, dicts, tuples, bytearrays, idle Locks and Events)
uncleared to the collector thread, which clears each right before its
teardown (pcc_gc_concurrent_sweep_slice) while the mutators run.

The program builds cycles of those kinds from four threads, keeps a rotating
set of survivors whose contents it checks, and lets a few early rounds carry
weak callbacks so those sweeps take the clearing path in the stopped world. The
printed results must equal CPython's on every collector. On GC1-GC3 the
stopped sweep's PASS1 must also have cleared fewer objects than PASS2 took
(pcc_gc_telemetry 136 and 140): the deferral without clearing ran.
"""
from __future__ import annotations

import os
import subprocess
import sys

PROGRAM = '''import threading
import weakref

try:
    from pcc.extern import c_int64, extern
    telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)
except ImportError:
    telemetry = None


class Node:
    def __init__(self, value):
        self.value = value
        self.peer = None
        self.items = [value, str(value), (value, value + 1)]
        self.table = {"value": value, "half": value // 2}


callbacks = [0]
callback_lock = threading.Lock()


def note(ref):
    with callback_lock:
        callbacks[0] += 1


def churn(tid, rounds, kept):
    total = 0
    k = 0
    refs = []
    while k < rounds:
        value = tid * 1000000 + k
        a = Node(value)
        b = Node(value + 1)
        a.peer = b
        b.peer = a
        lock = threading.Lock()
        event = threading.Event()
        buffer = bytearray(b"x" * (k % 48))
        holder = {"a": a, "buffer": buffer, "pair": [a, b], "lock": lock, "event": event}
        a.holder = holder
        with lock:
            total += len(holder["pair"]) + len(buffer)
        if not event.is_set():
            total += 1
        if tid == 0 and k < 3000 and k % 997 == 0:
            # A weak callback: the early collections that see one clear in
            # the stopped world; the later ones are settled.
            refs.append(weakref.ref(b, note))
        if k % 64 == 0:
            kept[k % 8] = a
        k += 1
    checked = 0
    for node in kept:
        if node is not None:
            if node.peer.peer is not node or node.items[1] != str(node.value):
                return -1
            if node.table["half"] != node.value // 2 or node.holder["a"] is not node:
                return -1
            checked += 1
    return total * 100 + checked


results = [0, 0, 0, 0]
survivors = [[None] * 8 for _ in range(4)]


def worker(tid):
    results[tid] = churn(tid, 30000, survivors[tid])


threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
for t in threads:
    t.start()
for t in threads:
    t.join()
print(results)
print(sum(1 for group in survivors for node in group if node is not None))
if telemetry is not None:
    import os
    if os.environ.get("PCC_GC_BACKEND", "0") in ("1", "2", "3"):
        print("deferred-uncleared", int(telemetry(136) < telemetry(140)))
    else:
        print("deferred-uncleared", 1)
else:
    print("deferred-uncleared", 1)
'''


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    return env


def test_settled_sweeps_leave_plain_garbage_uncleared_to_the_collector_thread(tmp_path):
    src = tmp_path / "deferred_sweep.py"
    src.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run(
        [sys.executable, str(src)], text=True, capture_output=True, timeout=300, env=_env()
    )
    assert expected.returncode == 0, expected.stderr
    assert "-1" not in expected.stdout.splitlines()[0]
    exe = tmp_path / "deferred_sweep.bin"
    built = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--ir-scaffold=on", str(src), "-o", str(exe)],
        text=True, capture_output=True, timeout=900, env=_env(),
    )
    assert built.returncode == 0, built.stderr
    for backend in ("0", "1", "2", "3", "4"):
        env = _env()
        env["PCC_GC_BACKEND"] = backend
        run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=180, env=env)
        assert run.returncode == 0, (backend, run.stdout, run.stderr)
        assert run.stdout == expected.stdout, (backend, run.stdout, expected.stdout)
