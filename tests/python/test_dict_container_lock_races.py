"""Shared dicts stay consistent while four carriers mutate and read them.

In container mode (every collector without the refcount provenance probe)
a str or tagged-int set of an ordinary dict holds only that dict's
container lock (py_dict._dict_set_gc0_locked). Every other transaction on a
dict's tables -- a rooted insert, replacement or delete, setdefault's and
update's slot protocol, items() materialization and a table replacement
(_rehash) -- holds the dict's container lock inside the graph lock, and a
class namespace's sets keep the graph lock outside it. The program below
runs those transactions against each other on one dict: concurrent adds
lose and duplicate nothing, readers copying a slot whose displaced value
is being released only ever see live values, growth keeps every entry,
and class attribute writes stay visible to lookups that copy the
namespace slot. A rooted operation whose optimistic probe keeps losing to
other writers retries until it commits (it used to drop the set after 16
attempts), and one that finds the table full grows it first.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python


PROGRAM = r'''
import gc
import pcc.virtual_thread as vt
from pcc.extern import extern, c_int64

pause = extern("pcc_platform_sleep_ns", (c_int64,), c_int64)

COUNT = 240
GROUPS = 8


class Box:
    def __init__(self, n):
        self.n = n


class Holder:
    attr = Box(-1)


def adder(shared, base):
    # str keys take the container-only set; tuple keys the rooted insert;
    # both grow the same table.
    index = 0
    while index < COUNT:
        value = base + index
        if index % 3 == 0:
            shared[(base, index)] = value
        else:
            shared["k" + str(value)] = value
        if index % 16 == 0:
            vt.yield_now()
        index += 1
    return COUNT


def replacer(shared, base):
    # Replacements of one hot key release the displaced box while readers
    # copy the same slot.
    index = 0
    while index < COUNT:
        shared["hot"] = Box(base + index)
        if index % 4 == 0:
            shared[("hot",)] = Box(base + index)
        if index % 16 == 0:
            vt.yield_now()
        index += 1
    return COUNT


def reader(shared, rounds):
    bad = 0
    index = 0
    while index < rounds:
        box = shared.get("hot")
        if box is not None and box.n < 0 and box.n != -5:
            bad += 1
        kept = shared.setdefault("hot", Box(-5))
        if kept.n < 0 and kept.n != -5:
            bad += 1
        pairs = list(shared.items())
        for key, value in pairs:
            if key == "hot" and value.n < 0 and value.n != -5:
                bad += 1
        copy = {}
        copy.update(shared)
        if len(copy) < 1:
            bad += 1
        vt.yield_now()
        index += 1
    return -1 - bad


def class_writer(base):
    index = 0
    while index < COUNT:
        Holder.attr = Box(base + index)
        if index % 16 == 0:
            vt.yield_now()
        index += 1
    return COUNT


def class_reader(rounds):
    bad = 0
    index = 0
    while index < rounds:
        if Holder.attr.n < -1:
            bad += 1
        if Holder().attr.n < -1:
            bad += 1
        vt.yield_now()
        index += 1
    return -1 - bad


def run(tasks):
    assert vt.carrier_pool_start(4) == 4
    results = []
    try:
        for task in tasks:
            while vt.outcome(task) == vt.OUTCOME_PENDING:
                pause(200000)
            failure = vt.exception(task)
            if failure is not None:
                raise failure
            results.append(vt.result(task))
    finally:
        assert vt.carrier_pool_stop() == 4
    return results


def expected_adds():
    keys = {}
    group = 0
    while group < GROUPS:
        base = group * 100000
        index = 0
        while index < COUNT:
            value = base + index
            if index % 3 == 0:
                keys[(base, index)] = value
            else:
                keys["k" + str(value)] = value
            index += 1
        group += 1
    return keys


def main():
    shared = {}
    tasks = []
    group = 0
    while group < GROUPS:
        tasks.append(vt.spawn(adder, shared, group * 100000))
        group += 1
    run(tasks)
    print("adds", len(shared), shared == expected_adds())

    # The same adds into tables already large enough: no growth, only the
    # rooted insert's optimistic probe racing the container-locked sets.
    shared = {}
    index = 0
    while index < 8192:
        shared["p" + str(index)] = index
        index += 1
    shared.clear()
    tasks = []
    group = 0
    while group < GROUPS:
        tasks.append(vt.spawn(adder, shared, group * 100000))
        group += 1
    run(tasks)
    print("presized adds", len(shared), shared == expected_adds())

    # Every value of this dict is a Box, as the replacers store.
    shared = {"seed": Box(0)}
    tasks = []
    group = 0
    while group < GROUPS:
        tasks.append(vt.spawn(replacer, shared, group * 100000))
        if group % 2 == 0:
            tasks.append(vt.spawn(reader, shared, 24))
        group += 1
    results = run(tasks)
    bad = 0
    for result in results:
        if result < 0:
            bad += -1 - result
    hot = shared["hot"].n
    print("replace", len(shared), bad, hot == -5 or hot % 100000 < COUNT)
    shared = None
    gc.collect()

    tasks = []
    group = 0
    while group < GROUPS:
        tasks.append(vt.spawn(class_writer, group * 100000))
        if group % 2 == 0:
            tasks.append(vt.spawn(class_reader, 24))
        group += 1
    results = run(tasks)
    bad = 0
    for result in results:
        if result < 0:
            bad += -1 - result
    final = Holder.attr.n
    print("class", bad, final >= 0 and final % 100000 < COUNT)
    Holder.attr = None
    gc.collect()
    print("done")


main()
'''

EXPECTED = (
    "adds 1920 True\n"
    "presized adds 1920 True\n"
    "replace 3 0 True\n"
    "class 0 True\n"
    "done\n"
)


@pytest.mark.integration
def test_shared_dicts_stay_consistent_under_four_carriers(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "dict_races.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "dict_races"
    compile_python(str(source), str(executable), backend="self",
                   ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        for run in range(2):
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                                 env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
            assert (ran.returncode, ran.stdout, ran.stderr) == (0, EXPECTED, ""), (backend, run)
