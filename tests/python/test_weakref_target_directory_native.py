"""Weakref invalidation visits only the dying target's refs (#16).

Specification: weakref.ref callback and clearing semantics (Python
Language Reference / ``weakref`` docs; PEP 205; PEP 442 resurrection) and
CPython's per-object weaklist (``Objects/weakrefobject.c``,
``PyObject_ClearWeakRefs``): a death examines only its own references.

The one collector sets ``PY_FLAG_WEAK_REFERENCED`` on a target when its
first weakref is created and keeps a target directory of the ordinary list.
The semantics program compares output with CPython. The scaling program
checks that an object's death does not cost work proportional to unrelated
live weakrefs, and that killing n weakly referenced cycles is linear; before
the fix both were quadratic (100k deaths with 16k live weakrefs took 20.8 s
against 0.04 s without them, local Mac).
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

SEMANTICS = textwrap.dedent("""
    import gc
    import threading
    import weakref

    events = []


    class Item:
        def __init__(self, name):
            self.name = name
            self.peer = None


    def callback(label):
        def run(ref):
            events.append(label + ":" + str(ref() is None))
        return run


    def same_target_order():
        target = Item("t")
        older = weakref.ref(target, callback("older"))
        silent = weakref.ref(target)
        newer = weakref.ref(target, callback("newer"))
        del target
        print("same target", events, older() is None, silent() is None, newer() is None)
        events.clear()


    def ref_dies_first():
        target = Item("t")
        ref = weakref.ref(target, callback("never"))
        del ref
        del target
        print("ref dies first", events)
        events.clear()


    keep = []


    def callback_creates_refs():
        first = Item("first")
        second = Item("second")
        keep.append(second)

        def make_more(ref):
            events.append("make")
            keep.append(weakref.ref(second, callback("second")))

        hold = weakref.ref(first, make_more)
        del first
        print("callback created", events, hold() is None)
        events.clear()
        keep.pop(0)  # drop second's strong reference
        print("second death", events, keep[0]() is None)
        events.clear()
        keep.clear()


    saved = []


    class Phoenix:
        def __init__(self, name):
            self.name = name

        def __del__(self):
            events.append("del-" + self.name)
            if not saved:
                saved.append(self)


    def resurrection_then_death():
        p = Phoenix("p")
        ref = weakref.ref(p)
        del p
        print("resurrected", events, len(saved), ref() is saved[0])
        events.clear()
        later = weakref.ref(saved[0], callback("later"))
        saved.clear()
        print("final death", events, ref() is None, later() is None)
        events.clear()


    def cycles_with_refs(n):
        refs = []
        for i in range(n):
            a = Item(str(i))
            b = Item(str(i))
            a.peer = b
            b.peer = a
            refs.append(weakref.ref(a, callback("cycle")))
            del a, b
        gc.collect()
        print("cycles", n, sum(1 for r in refs if r() is None), len(events))
        events.clear()


    def threads(workers, n):
        counts = [0] * workers

        def work(slot):
            local = []
            for i in range(n):
                obj = Item(str(i))
                ref = weakref.ref(obj, lambda r: local.append(1))
                if i % 3 == 0:
                    del ref
                del obj
            counts[slot] = len(local)

        pool = [threading.Thread(target=work, args=(k,)) for k in range(workers)]
        for t in pool:
            t.start()
        for t in pool:
            t.join()
        print("threads", counts)


    same_target_order()
    ref_dies_first()
    callback_creates_refs()
    resurrection_then_death()
    cycles_with_refs(3000)
    threads(4, 3000)
    print("WEAKREF_DIRECTORY_SEMANTICS_OK")
""").lstrip()


SCALING = textwrap.dedent("""
    import gc
    import time
    import weakref


    class Node:
        def __init__(self, i):
            self.i = i
            self.items = []


    def churn(m):
        total = 0
        for i in range(m):
            total += Node(i).i
        return total


    def deaths_with_live_weakrefs(k):
        anchors = [Node(i) for i in range(k)]
        refs = [weakref.ref(a) for a in anchors]
        best = None
        for _ in range(3):
            t0 = time.perf_counter()
            churn(100000)
            dt = time.perf_counter() - t0
            best = dt if best is None else min(best, dt)
        assert all(r() is not None for r in refs)
        return best


    def weak_cycles(n):
        best = None
        for _ in range(3):
            t0 = time.perf_counter()
            refs = []
            for i in range(n):
                d = {"i": i}
                o = Node(i)
                o.items.append(d)
                d["o"] = o
                refs.append(weakref.ref(o))
            del d, o
            gc.collect()
            dt = time.perf_counter() - t0
            assert all(r() is None for r in refs)
            best = dt if best is None else min(best, dt)
        return best


    base = deaths_with_live_weakrefs(0)
    loaded = deaths_with_live_weakrefs(16000)
    assert loaded <= 4 * base + 0.05, (base, loaded)
    small = weak_cycles(4000)
    large = weak_cycles(16000)
    assert large <= 8 * small + 0.05, (small, large)
    print("WEAKREF_DIRECTORY_SCALING_OK")
""").lstrip()


def _compile_and_compare(tmp_path, name, program):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / (name + ".py")
    executable = tmp_path / (name + ".out")
    source.write_text(program, encoding="utf-8")
    compile_python(str(source), str(executable), ir_scaffold_mode="on", backend="self")
    reference = subprocess.run([sys.executable, str(source)], capture_output=True,
                               text=True, timeout=120)
    assert reference.returncode == 0, reference.stderr
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                            env=dict(os.environ))
    assert (result.returncode, result.stdout) == (0, reference.stdout), result.stderr


def test_weakref_semantics_match_cpython_with_the_target_directory(tmp_path):
    _compile_and_compare(tmp_path, "weakref_directory_semantics", SEMANTICS)


def test_weakref_deaths_do_not_scale_with_unrelated_live_weakrefs(tmp_path):
    _compile_and_compare(tmp_path, "weakref_directory_scaling", SCALING)
