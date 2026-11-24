"""Full collection of the fused collector (GC0) in bounded stops.

A full collection no longer stops the world for the whole heap. A cycle
gives the old tracked containers nodes, subtracts their internal references,
marks what outside referrers reach and sweeps its view, a bounded part of
each in every stop, with the program running in between. The unreached
objects become young again and a young collection decides among them.

The programs set the work budget of a stop to zero, so each stop does the
smallest unit of its phase and even a small heap takes hundreds of stops.
They read the cycle's counters through ``pcc.extern``, so their expected
output is stated here rather than taken from CPython.

The last program lets a collector thread do the passes with the world
running (the concurrent feature of the former GC2) while four threads
allocate, and checks the same outcome.
"""
from __future__ import annotations

import os
import subprocess
import textwrap

import pytest

PRELUDE = textwrap.dedent("""
    import gc
    import weakref
    from pcc.extern import extern, c_int64

    # 0: completed cycles, 1: stops, 2: candidates handed over, 3: phase.
    full_counter = extern("pcc_gc_backend0_full_counter", (c_int64,), c_int64)
    set_slice_us = extern("pcc_gc_backend0_full_set_slice_us", (c_int64,), c_int64)

    finalized = []
    saved = []
    called = []
    created = [0]


    class Node:
        def __init__(self, name):
            self.name = name
            self.peer = None
            self.keep = False
            self.serial = created[0]
            created[0] += 1

        def __del__(self):
            finalized.append(self.serial)
            if self.keep:
                self.keep = False
                saved.append(self)


    class Junk:
        def __init__(self):
            self.peer = None


    def pair(name):
        left = Node(name)
        right = Node(name)
        left.peer = right
        right.peer = left
        return left


    def churn(count):
        # Young cyclic garbage: its tracking paces the cycle's stops.
        index = 0
        while index < count:
            left = Junk()
            right = Junk()
            left.peer = right
            right.peer = left
            index += 1


    def start_cycle():
        rounds = 0
        while full_counter(3) == 0 and rounds < 4000:
            churn(500)
            rounds += 1
        return full_counter(3) != 0


    def run_cycle():
        before = full_counter(0)
        rounds = 0
        while full_counter(0) == before and rounds < 8000:
            churn(500)
            rounds += 1
        return full_counter(0) - before


    def note(reference):
        called.append("callback")
    """).lstrip()


RECLAIMS = PRELUDE + textwrap.dedent("""

    def main():
        set_slice_us(0)
        old = [pair(index) for index in range(3000)]
        gc.collect()
        index = 1
        while index < 3000:
            old[index] = None
            index += 2
        print("old garbage waits", len(finalized))
        stops = full_counter(1)
        print("cycles completed", run_cycle())
        print("in many stops", full_counter(1) - stops > 100)
        print("old garbage reclaimed", len(finalized), len(set(finalized)))
        alive = 0
        index = 0
        while index < 3000:
            node = old[index]
            if node.peer.peer is node and node.name == index and node.peer.name == index:
                if node.serial not in finalized and node.peer.serial not in finalized:
                    alive += 1
            index += 2
        print("live pairs intact", alive)
        print("candidates handed over", full_counter(2) >= 3000, full_counter(3))


    main()
    """)

RECLAIMS_OUTPUT = textwrap.dedent("""
    old garbage waits 0
    cycles completed 1
    in many stops True
    old garbage reclaimed 3000 3000
    live pairs intact 1500
    candidates handed over True 0
    """).lstrip()


MUTATES = PRELUDE + textwrap.dedent("""

    def main():
        set_slice_us(0)
        old = [pair(index) for index in range(2000)]
        extra = [pair(10000 + index) for index in range(500)]
        gc.collect()
        print("cycle running", start_cycle())
        seen = []
        dropped = 0
        broken = 0
        step = 0
        while full_counter(3) != 0 and step < 200000:
            phase = full_counter(3)
            if phase not in seen:
                seen.append(phase)
            # Live edges move between objects the cycle has and has not
            # scanned: two old pairs exchange their partners.
            first = old[step % 2000]
            second = old[(step + 997) % 2000]
            if first is not None and second is not None and first is not second:
                first_peer = first.peer
                second_peer = second.peer
                first.peer = second_peer
                second_peer.peer = first
                second.peer = first_peer
                first_peer.peer = second
            # An old pair with nodes dies by reference count.
            if step % 7 == 0 and step // 7 < 500:
                victim = extra[step // 7]
                victim.peer.peer = None
                extra[step // 7] = None
                victim = None
                broken += 1
            # An old pair becomes cyclic garbage while the cycle runs.
            if step % 11 == 0:
                index = 1000 + (step // 11) % 1000
                if old[index] is not None:
                    old[index] = None
                    dropped += 1
            first = None
            second = None
            first_peer = None
            second_peer = None
            churn(40)
            step += 1
        print("phases seen", sorted(seen))
        print("mutated", broken, dropped > 100, step > 1000)
        # Garbage made during the cycle waits at most for the next one,
        # which starts once the tracked population has grown by a quarter.
        grown = [pair(20000 + index) for index in range(3000)]
        print("next cycle", run_cycle())
        live = 0
        intact = 0
        index = 0
        dead = set(finalized)
        while index < 2000:
            node = old[index]
            if node is not None:
                live += 2
                if node.peer.peer is node and node.serial not in dead and node.peer.serial not in dead:
                    intact += 2
            index += 1
        print("live intact", live == intact, live == 2 * (2000 - dropped))
        print("each finalized once", len(dead) == len(finalized))
        print("all accounted", len(finalized) + live + 2 * len(grown) == created[0])


    main()
    """)

MUTATES_OUTPUT = textwrap.dedent("""
    cycle running True
    phases seen [1, 2, 3, 4, 5]
    mutated 500 True True
    next cycle 1
    live intact True True
    each finalized once True
    all accounted True
    """).lstrip()


CONTROLS = PRELUDE + textwrap.dedent("""

    def main():
        set_slice_us(0)
        old = [pair(index) for index in range(2000)]
        gc.collect()
        index = 1
        while index < 2000:
            old[index] = None
            index += 2
        # A snapshot needs every tracked object as a node of its own view:
        # the running cycle gives up its nodes.
        print("cycle running", start_cycle())
        found = 0
        for obj in gc.get_objects():
            if obj is old[0]:
                found += 1
        obj = None
        print("snapshot", found, full_counter(3), len(finalized))
        # With automatic collection off, the cycle releases its view and no
        # candidate reaches a collection.
        print("cycle running", start_cycle())
        set_slice_us(1000)
        gc.disable()
        rounds = 0
        while full_counter(3) != 0 and rounds < 4000:
            churn(500)
            rounds += 1
        print("disabled", full_counter(3), len(finalized), gc.isenabled())
        gc.enable()
        set_slice_us(0)
        # An explicit collection during a cycle is complete on its own.
        print("cycle running", start_cycle())
        print("explicit", gc.collect() >= 2000, full_counter(3), len(finalized))
        completed = full_counter(0)
        print("none completed yet", completed)
        print("cycles completed", run_cycle())
        print("live pair", old[0].peer.peer is old[0], len(finalized))


    main()
    """)

CONTROLS_OUTPUT = textwrap.dedent("""
    cycle running True
    snapshot 1 0 0
    cycle running True
    disabled 0 0 False
    cycle running True
    explicit True 0 2000
    none completed yet 0
    cycles completed 1
    live pair True 2000
    """).lstrip()


FINALIZES = PRELUDE + textwrap.dedent("""

    def main():
        set_slice_us(0)
        old = [pair(index) for index in range(1500)]
        reference = weakref.ref(old[1])
        notified = weakref.ref(old[3], note)
        old[5].keep = True
        phoenix_serial = old[5].serial
        gc.collect()
        old[1] = None
        old[3] = None
        old[5] = None
        print("before", reference() is None, notified() is None, len(finalized))
        print("cycles completed", run_cycle())
        print("weak references cleared", reference() is None, notified() is None, called)
        print("finalized", len(finalized), len(set(finalized)))
        phoenix = saved[0]
        print("resurrected", len(saved), phoenix.serial == phoenix_serial,
              phoenix.peer.peer is phoenix, phoenix.peer.name)
        phoenix = None
        del saved[:]
        gc.collect()
        print("finalized once", len(finalized), len(set(finalized)))


    main()
    """)

FINALIZES_OUTPUT = textwrap.dedent("""
    before False False 0
    cycles completed 1
    weak references cleared True True ['callback']
    finalized 6 6
    resurrected 1 True True 5
    finalized once 6 6
    """).lstrip()


CONCURRENT = PRELUDE + textwrap.dedent("""
    import threading

    set_concurrent = extern("pcc_gc_backend0_full_set_concurrent", (c_int64,), c_int64)


    def spin(limit):
        rounds = 0
        while full_counter(0) == 0 and rounds < limit:
            churn(500)
            rounds += 1


    def main():
        set_slice_us(0)
        print("concurrent before", set_concurrent(1))
        old = [pair(index) for index in range(3000)]
        gc.collect()
        index = 1
        while index < 3000:
            old[index] = None
            index += 2
        print("old garbage waits", len(finalized))
        threads = [threading.Thread(target=spin, args=(8000,)) for _ in range(3)]
        for thread in threads:
            thread.start()
        spin(8000)
        for thread in threads:
            thread.join()
        print("cycle completed", full_counter(0) >= 1)
        print("collector thread worked", full_counter(4) > 0)
        # A mutator runs the young collection that decides among the
        # candidates, at its next automatic-collection request.
        rounds = 0
        while len(finalized) < 3000 and rounds < 200:
            churn(500)
            rounds += 1
        print("old garbage reclaimed", len(finalized), len(set(finalized)))
        alive = 0
        index = 0
        dead = set(finalized)
        while index < 3000:
            node = old[index]
            if node.peer.peer is node and node.name == index and node.peer.name == index:
                if node.serial not in dead and node.peer.serial not in dead:
                    alive += 1
            index += 2
        print("live pairs intact", alive)


    main()
    """)

CONCURRENT_OUTPUT = textwrap.dedent("""
    concurrent before 0
    old garbage waits 0
    cycle completed True
    collector thread worked True
    old garbage reclaimed 3000 3000
    live pairs intact 1500
    """).lstrip()


@pytest.mark.parametrize(
    ("name", "program", "expected"),
    [
        ("reclaims", RECLAIMS, RECLAIMS_OUTPUT),
        ("mutates", MUTATES, MUTATES_OUTPUT),
        ("controls", CONTROLS, CONTROLS_OUTPUT),
        ("finalizes", FINALIZES, FINALIZES_OUTPUT),
        ("concurrent", CONCURRENT, CONCURRENT_OUTPUT),
    ],
    ids=["reclaims", "mutates", "controls", "finalizes", "concurrent"],
)
def test_full_collection_in_bounded_stops(tmp_path, name, program, expected):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / (name + ".py")
    executable = tmp_path / (name + ".out")
    source.write_text(program, encoding="utf-8")
    compile_python(str(source), str(executable), ir_scaffold_mode="on", backend="self")
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=300,
                            env=dict(os.environ, PCC_GC_BACKEND="0"))
    assert (result.returncode, result.stdout) == (0, expected), result.stderr
