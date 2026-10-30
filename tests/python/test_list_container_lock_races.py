"""Shared lists stay consistent while four carriers mutate and read them.

On GC0 every list transaction takes the list's own container lock (striped
0/1 words, pcc_container_lock); the tracing and moving collectors keep the
graph lock. Either way one list operation is one transaction: concurrent
adds through append, extend, insert, `+=`, slice assignment and a tuple
extend lose and duplicate nothing; while writers only append, every copy,
slice, stepped slice, repeat, concatenation, sorted() and str.join is a
snapshot of one prefix of the final list (so are tuple() and bytes.join of
a list of bytes); concurrent removes and pops take
each element exactly once; a concurrent reverse only permutes. The C
program checks which lock pcc_list_lock takes on each collector.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python
from tests.owned_c_execution import compile_owned_c_with_runtime


PROGRAM = r'''
import gc
import pcc.virtual_thread as vt
from pcc.extern import extern, c_int64

pause = extern("pcc_platform_sleep_ns", (c_int64,), c_int64)

COUNT = 240
GROUPS = 8


def adder(shared, base):
    index = 0
    while index < COUNT:
        value = base + index
        op = index % 6
        if op == 0:
            shared.append(value)
        elif op == 1:
            shared.extend([value])
        elif op == 2:
            shared.insert(0, value)
        elif op == 3:
            shared += [value]
        elif op == 4:
            end = len(shared)
            shared[end:end] = [value]
        else:
            shared.extend((value,))
        if index % 16 == 0:
            vt.yield_now()
        index += 1
    return COUNT


def appender(shared, names, encoded, base):
    index = 0
    while index < COUNT:
        value = base + index
        if index % 3 == 0:
            shared.append(value)
        elif index % 3 == 1:
            shared.extend([value])
        else:
            shared += [value]
        names.append(str(value))
        encoded.append(str(value).encode())
        if index % 16 == 0:
            vt.yield_now()
        index += 1
    return COUNT


def reader(shared, names, encoded, rounds):
    snaps = []
    last = 0
    round_index = 0
    while round_index < rounds:
        size = len(shared)
        assert size >= last
        last = size
        assert -7 in shared
        assert not (-5 in shared)
        assert shared.count(-7) == 1
        assert shared.index(-7) == 0
        snaps.append(("copy", shared.copy()))
        snaps.append(("slice", shared[:]))
        snaps.append(("step", shared[::2]))
        snaps.append(("twice", shared * 2))
        snaps.append(("plus", shared + [-1]))
        snaps.append(("sorted", sorted(shared)))
        snaps.append(("join", ",".join(names)))
        snaps.append(("tuple", tuple(shared)))
        snaps.append(("bytes", b",".join(encoded)))
        if round_index % 4 == 0:
            vt.yield_now()
        round_index += 1
    return snaps


def remover(shared, base):
    index = 0
    while index < COUNT:
        shared.remove(base + index)
        if index % 16 == 0:
            vt.yield_now()
        index += 1
    return COUNT


def popper(stack):
    taken = []
    done = False
    while not done:
        try:
            taken.append(stack.pop())
        except IndexError:
            done = True
        if len(taken) % 16 == 0:
            vt.yield_now()
    return taken


def reverser(shared, rounds):
    index = 0
    while index < rounds:
        shared.reverse()
        if index % 4 == 0:
            vt.yield_now()
        index += 1
    return rounds


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


def universe():
    values = []
    group = 0
    while group < GROUPS:
        index = 0
        while index < COUNT:
            values.append(group * 100000 + index)
            index += 1
        group += 1
    return values


def check_prefix(kind, snap, final, final_names, final_encoded):
    if kind == "copy" or kind == "slice":
        return snap == final[:len(snap)]
    if kind == "step":
        return snap == final[0:2 * len(snap):2]
    if kind == "twice":
        half = len(snap) // 2
        return snap[half:] == snap[:half] and snap[:half] == final[:half]
    if kind == "plus":
        return snap[-1] == -1 and snap[:-1] == final[:len(snap) - 1]
    if kind == "sorted":
        return snap == sorted(final[:len(snap)])
    if kind == "tuple":
        return list(snap) == final[:len(snap)]
    if kind == "bytes":
        pieces = snap.decode().split(",")
        index = 0
        while index < len(pieces):
            if pieces[index] != final_encoded[index].decode():
                return False
            index += 1
        return True
    parts = snap.split(",")
    return parts == final_names[:len(parts)]


def main():
    expected = universe()
    # Adds through every list mutation that inserts one value.
    shared = []
    tasks = []
    group = 0
    while group < GROUPS:
        tasks.append(vt.spawn(adder, shared, group * 100000))
        group += 1
    run(tasks)
    print("adds", len(shared), sorted(shared) == expected)

    # Appends against snapshot readers.
    shared = [-7]
    names = ["seed"]
    encoded = [b"seed"]
    tasks = []
    group = 0
    while group < GROUPS:
        tasks.append(vt.spawn(appender, shared, names, encoded, group * 100000))
        if group % 2 == 0:
            tasks.append(vt.spawn(reader, shared, names, encoded, 24))
        group += 1
    results = run(tasks)
    consistent = True
    snapshots = 0
    for result in results:
        if result != COUNT:
            for kind, snap in result:
                snapshots += 1
                if not check_prefix(kind, snap, shared, names, encoded):
                    consistent = False
    print("snapshots", snapshots, consistent, sorted(shared[1:]) == expected, len(names))

    # Removes and pops take each element once.
    shared = list(expected)
    stack = list(expected)
    tasks = []
    group = 0
    while group < GROUPS:
        tasks.append(vt.spawn(remover, shared, group * 100000))
        if group % 2 == 0:
            tasks.append(vt.spawn(popper, stack))
        group += 1
    results = run(tasks)
    taken = []
    for result in results:
        if result != COUNT:
            taken.extend(result)
    print("removes", len(shared), len(stack), sorted(taken) == expected)

    # A concurrent reverse only permutes.
    shared = []
    tasks = [vt.spawn(reverser, shared, 400)]
    group = 0
    while group < GROUPS:
        tasks.append(vt.spawn(appender, shared, [], [], group * 100000))
        group += 1
    tasks.append(vt.spawn(reverser, shared, 400))
    run(tasks)
    gc.collect()
    print("reverse", len(shared), sorted(shared) == expected)


main()
'''

EXPECTED = (
    "adds 1920 True\n"
    "snapshots 864 True True 1921\n"
    "removes 0 0 True\n"
    "reverse 1920 True\n"
)


@pytest.mark.integration
def test_shared_lists_stay_consistent_under_four_carriers(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "list_races.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "list_races"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        for run in range(2):
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                                 env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
            assert (ran.returncode, ran.stdout, ran.stderr) == (0, EXPECTED, ""), (backend, run)


C_PROGRAM = r"""
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern void pcc_list_lock(PyObject *list);
extern void pcc_list_unlock(PyObject *list);
extern int64_t pcc_container_lock_held(void);
extern int64_t pcc_thread_graph_lock_depth(void);

int main(int argc, char **argv) {
    if (argc != 2) return 1;
    if (pcc_gc_set_backend(atoi(argv[1])) != 0) return 2;
    PyObject *list = py_list_new(0);
    if (!list) return 3;
    pcc_list_lock(list);
    int64_t held = pcc_container_lock_held();
    int64_t depth = pcc_thread_graph_lock_depth();
    pcc_list_unlock(list);
    if (pcc_container_lock_held() != 0 || pcc_thread_graph_lock_depth() != 0) return 4;
    py_decref(list);
    printf("%lld %lld\n", (long long)held, (long long)depth);
    return 0;
}
"""


@pytest.mark.integration
def test_list_lock_is_the_container_lock_only_on_gc0_without_the_probe(tmp_path, pcc_runtime_archive):
    source = tmp_path / "list_lock_mode.c"
    source.write_text(C_PROGRAM, encoding="utf-8")
    binary = tmp_path / "list_lock_mode"
    compile_owned_c_with_runtime(source, binary, runtime_archive=Path(pcc_runtime_archive))
    cases = [(backend, "0", "1 0\n" if backend == 0 else "0 1\n") for backend in range(5)]
    # The provenance probe's checked lease path takes the graph lock inside
    # a transaction, so probed GC0 lists keep the graph lock.
    cases.append((0, "1", "0 1\n"))
    for backend, probe, expected in cases:
        ran = subprocess.run([str(binary), str(backend)], capture_output=True, text=True, timeout=60,
                             env=dict(os.environ, PATH="/nonexistent",
                                      PCC_GC_REFCOUNT_PROVENANCE_PROBE=probe))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected, ""), (backend, probe)
