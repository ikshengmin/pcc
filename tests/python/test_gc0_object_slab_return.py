"""Empty object slabs go back to the system after a full collection.

An object slab is 64 KiB of cells of one size class. It used to stay
resident for the life of the process once any object had lived in it, so a
peak left the process at its peak size. A full collection now ends with a
trim: a slab whose every cell is free keeps its mapping and its span and
gives its pages back, and its size class carves it again when it next
needs cells.

Nothing is moved, so a slab with a live object stays. The allocator keeps
such slabs few instead: every slab has its own free cells, and a size class
takes a cell from a slab that is more than half full before it takes one
from a sparse slab. The third program leaves a heap of sparse slabs, churns
short-lived objects of which a few stay, and then frees the first
survivors: the stragglers sit in the dense slabs, so the sparse ones empty
and return.

The programs read the allocator's counters through ``pcc.extern``, so their
expected output is stated here.
"""
from __future__ import annotations

import os
import subprocess
import textwrap

import pytest

PRELUDE = textwrap.dedent("""
    import gc
    from pcc.extern import extern, c_int64

    # 0: object slabs given back, 1: carved again, 2: bytes given back now.
    slab_counter = extern("pcc_allocator_object_slab_counter", (c_int64,), c_int64)
    resident = extern("pcc_os_current_rss_bytes", (), c_int64)


    class Node:
        def __init__(self, value):
            self.value = value
            self.peer = None


    def build(count, base):
        return [Node(base + index) for index in range(count)]


    def total(nodes):
        result = 0
        for node in nodes:
            result += node.value
        return result
    """).lstrip()


PEAK = PRELUDE + textwrap.dedent("""

    def main():
        peak = build(200000, 0)
        keep = peak[::1000]
        print("peak", total(peak), len(keep))
        at_peak = resident()
        peak = None
        gc.collect()
        given_back = slab_counter(0)
        print("slabs given back", given_back > 100)
        print("bytes given back", slab_counter(2) == (slab_counter(0) - slab_counter(1)) * 65536)
        print("resident size fell", at_peak - resident() > given_back * 65536 // 2)
        print("kept", total(keep))
        # The size classes carve the given-back slabs again: no new mapping.
        again = build(200000, 1000000)
        print("slabs carved again", slab_counter(1) > 100)
        print("again", total(again), total(keep))
        again = None
        keep = None
        gc.collect()
        print("given back again", slab_counter(0) > given_back + 100)
        print("bytes given back", slab_counter(2) == (slab_counter(0) - slab_counter(1)) * 65536)


    main()
    """)

PEAK_OUTPUT = textwrap.dedent("""
    peak 19999900000 200
    slabs given back True
    bytes given back True
    resident size fell True
    kept 19900000
    slabs carved again True
    again 219999900000 19900000
    given back again True
    bytes given back True
    """).lstrip()


ROUNDS = PRELUDE + textwrap.dedent("""

    def shapes(count, base):
        # Several size classes at once: instances, lists, tuples, dicts
        # and strings.
        out = []
        index = 0
        while index < count:
            value = base + index
            out.append((Node(value), [value, value + 1, value + 2], (value, str(value)),
                        {"value": value, "text": "v" + str(value)}))
            index += 1
        return out


    def check(items, base):
        good = 0
        index = 0
        for node, numbers, pair, table in items:
            value = base + index
            if (node.value == value and numbers == [value, value + 1, value + 2]
                    and pair == (value, str(value)) and table["value"] == value
                    and table["text"] == "v" + str(value)):
                good += 1
            index += 1
        return good


    def main():
        survivors = []
        round_number = 0
        while round_number < 5:
            base = round_number * 1000000
            items = shapes(30000, base)
            print("round", round_number, check(items, base))
            survivors.append((base, items[::997]))
            items = None
            gc.collect()
            round_number += 1
        intact = 0
        for base, kept in survivors:
            index = 0
            for node, numbers, pair, table in kept:
                value = base + index * 997
                if node.value == value and numbers[2] == value + 2 and pair[1] == str(value) and table["value"] == value:
                    intact += 1
                index += 1
        print("survivors intact", intact)
        print("slabs given back and carved again", slab_counter(0) > 50, slab_counter(1) > 50)
        print("bytes given back", slab_counter(2) == (slab_counter(0) - slab_counter(1)) * 65536)


    main()
    """)

ROUNDS_OUTPUT = textwrap.dedent("""
    round 0 30000
    round 1 30000
    round 2 30000
    round 3 30000
    round 4 30000
    survivors intact 155
    slabs given back and carved again True True
    bytes given back True
    """).lstrip()


SPARSE = PRELUDE + textwrap.dedent("""

    def main():
        first = build(200000, 0)
        survivors = first[::10]
        first = None
        gc.collect()
        # One object in ten is left in every slab: none is empty.
        print("sparse heap returns nothing", slab_counter(0) < 3)
        # Short-lived objects, of which one in two hundred stays.
        stragglers = []
        round_number = 0
        while round_number < 100:
            batch = build(20000, 1000000)
            stragglers.extend(batch[::200])
            batch = None
            round_number += 1
        gc.collect()
        print("stragglers", len(stragglers), total(survivors))
        survivors = None
        gc.collect()
        print("sparse slabs emptied and returned", slab_counter(0) > 150)
        print("stragglers intact", total(stragglers))
        print("bytes given back", slab_counter(2) == (slab_counter(0) - slab_counter(1)) * 65536)


    main()
    """)

SPARSE_OUTPUT = textwrap.dedent("""
    sparse heap returns nothing True
    stragglers 10000 1999900000
    sparse slabs emptied and returned True
    stragglers intact 10099000000
    bytes given back True
    """).lstrip()


@pytest.mark.parametrize(
    ("name", "program", "expected"),
    [("peak", PEAK, PEAK_OUTPUT), ("rounds", ROUNDS, ROUNDS_OUTPUT), ("sparse", SPARSE, SPARSE_OUTPUT)],
    ids=["peak", "rounds", "sparse"],
)
def test_empty_object_slabs_return_to_the_system(tmp_path, name, program, expected):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / (name + ".py")
    executable = tmp_path / (name + ".out")
    source.write_text(program, encoding="utf-8")
    compile_python(str(source), str(executable), ir_scaffold_mode="on", backend="self")
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=300,
                            env=dict(os.environ))
    assert (result.returncode, result.stdout) == (0, expected), result.stderr


def test_one_live_cell_per_slab_keeps_identity_across_page_return_and_reuse(
    tmp_path, pcc_runtime_archive,
):
    import mmap
    from pathlib import Path
    from tests.owned_runtime_c_fixture import link_c_harness_bounded

    source = Path(__file__).with_name("fixtures") / "gc_sparse_slab.c"
    binary = tmp_path / "sparse_slab"
    linked = link_c_harness_bounded(source, binary, runtime_archive=pcc_runtime_archive)
    assert linked.returncode == 0, linked.stdout + linked.stderr
    for layout in ([], ["crossing"]):
        run = subprocess.run([binary, str(mmap.PAGESIZE), *layout], capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, (layout, run.returncode, run.stdout, run.stderr)
