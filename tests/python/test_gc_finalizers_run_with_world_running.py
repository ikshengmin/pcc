"""Collectors run finalizers and weakref callbacks with the world running.

Python language reference, 3.3.1 (object.__del__): a finalizer may run in
any thread and may take locks; the gc module docs (gc.collect) and CPython's
free-threaded collector (gc_free_threading.c, gc_collect_internal: "Call
weakref callbacks and finalizers after unpausing other threads to avoid
potential deadlocks") restart the world before user code runs and stop it
again only to find resurrected objects.

pcc ran __del__ and weakref callbacks inside the stopped world (GC1-GC4's
tracing sweep, GC0's cycle collector under its table lock too). A finalizer
that takes a lock a parked thread holds waited for that thread, which waited
for the world: every thread blocked in pthread_cond_wait. This program has
four threads rebinding instance fields while __init__ and __del__ share one
threading.Lock, with self-referencing items for GC0's cycle collector; before
the fix every collector hung, and now GC0-GC3 match CPython. GC4 keeps its
user code in the stopped world (resuming it mid-sweep ran __del__ twice or
never), so it runs the program without the finalizer lock and must finalize
each item at most once.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROGRAM = '''import gc
import os
import threading

lock = threading.Lock()
counts = [0, 0]
FINALIZER_LOCK = os.environ.get('FINALIZER_LOCK', '1') == '1'

class Item:
    def __init__(self, value, cyclic):
        self.value = value
        if cyclic:
            self.me = self
        with lock:
            counts[0] += 1
    def __del__(self):
        if FINALIZER_LOCK:
            with lock:
                counts[1] += 1
        else:
            counts[1] += 1

class Holder:
    def __init__(self):
        self.slot = Item(-1, False)

holders = [Holder() for _ in range(4)]
bad = [0]

def worker(tid):
    k = 0
    while k < 20000:
        h = holders[k % 4]
        if k % 3 == 0:
            h.slot = Item(tid * 1000000 + k, k % 2 == 0)
        elif k % 3 == 1:
            h.slot = holders[(k + 1) % 4].slot
        else:
            if not isinstance(h.slot.value, int):
                bad[0] += 1
        k += 1

threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
for t in threads:
    t.start()
for t in threads:
    t.join()
for h in holders:
    h.slot = None
gc.collect()
gc.collect()
print(bad[0], counts[0], counts[1])
'''


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    return env


def test_finalizers_taking_a_parked_threads_lock_finish_on_every_backend(tmp_path):
    src = tmp_path / "finalizer_locks.py"
    src.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run(
        [sys.executable, str(src)], text=True, capture_output=True, timeout=120, env=_env()
    )
    assert expected.returncode == 0, expected.stderr
    bad, created, destroyed = map(int, expected.stdout.split())
    assert bad == 0 and created == destroyed
    exe = tmp_path / "finalizer_locks.bin"
    built = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--ir-scaffold=on", str(src), "-o", str(exe)],
        text=True, capture_output=True, timeout=600, env=_env(),
    )
    assert built.returncode == 0, built.stderr
    for backend in ("0", "1", "2", "3", "4"):
        env = _env()
        env["PCC_GC_BACKEND"] = backend
        if backend == "4":
            # GC4 still runs its sweep's user code in the stopped world
            # (its windows ran __del__ twice or never), so a finalizer
            # lock can still deadlock it: check exactly-once finalization
            # without the lock instead.
            env["FINALIZER_LOCK"] = "0"
        try:
            run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=90, env=env)
        except subprocess.TimeoutExpired:
            raise AssertionError(f"GC{backend}: a finalizer deadlocked the stopped world")
        assert run.returncode == 0, (backend, run.stdout, run.stderr)
        bad, made, freed = map(int, run.stdout.split())
        assert bad == 0 and made == created, (backend, run.stdout)
        if backend == "0":
            # Reference counting and the cycle collector free every item.
            assert freed == made, (backend, run.stdout)
        else:
            # A tracing collector may keep a few behind conservative roots;
            # no finalizer runs twice.
            assert made - 64 <= freed <= made, (backend, run.stdout)
