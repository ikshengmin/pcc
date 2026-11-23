"""Generational cycle collection on the fused collector (GC0).

A container tracked in flag mode is young: it carries the young bit and is
in its thread's young log. An automatic collection examines the young
containers only (old objects count as external referrers through the
reference counts), a full one every tracked container; both promote what
they examined. The program checks the observable contract against CPython:
young cyclic garbage is reclaimed without an explicit collection, a young
object held by an old container survives, and a cycle through an old object
is reclaimed by a full collection.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

PROGRAM = textwrap.dedent("""
    import gc

    finalized = []


    class Node:
        def __init__(self, name):
            self.name = name
            self.peer = None

        def __del__(self):
            finalized.append(self.name)


    def pair(name):
        left = Node(name)
        right = Node(name)
        left.peer = right
        right.peer = left
        return left


    def main():
        old = [pair(index) for index in range(3000)]
        gc.collect()
        print("old alive", len(finalized))
        # Young cyclic garbage, no explicit collection: automatic young
        # collections reclaim nearly all of it.
        index = 0
        while index < 60000:
            pair(index)
            index += 1
        print("young reclaimed automatically", len(finalized) > 100000)
        # A young cycle held only by an old container survives collections.
        old[0].peer.peer = None
        old[0].peer = pair("kept")
        index = 0
        while index < 20000:
            pair(index)
            index += 1
        print("kept by old", "kept" not in finalized, old[0].peer.name)
        # A cycle through old objects needs a full collection.
        before = len(finalized)
        old[1] = None
        gc.collect()
        print("old cycle reclaimed", len(finalized) - before >= 2, finalized.count(1) >= 2)
        del finalized[:]
        old = None
        gc.collect()
        print("all old reclaimed", len(finalized) >= 5996, gc.isenabled())


    main()
    """).lstrip()


def test_generational_collection_matches_cpython_on_the_fused_collector(tmp_path):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "generations.py"
    executable = tmp_path / "generations.out"
    source.write_text(PROGRAM, encoding="utf-8")
    compile_python(str(source), str(executable), ir_scaffold_mode="on", backend="self")
    reference = subprocess.run([sys.executable, str(source)], capture_output=True,
                               text=True, timeout=60)
    assert reference.returncode == 0, reference.stderr
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                            env=dict(os.environ, PCC_GC_BACKEND="0"))
    assert (result.returncode, result.stdout) == (0, reference.stdout), result.stderr
