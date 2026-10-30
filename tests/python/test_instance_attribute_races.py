"""Instances shared by four carriers keep every attribute and stay valid.

Concurrent rebinding of a declared field, deleting and setting it, and
setattr of distinct dynamic attributes on one instance from eight tasks.
The instance __dict__ is created on first use; two threads that both found
it missing each stored their own dict, so one thread's attributes were lost
and the dict it was inserting into was freed under it (lost attributes and
SIGSEGV on every collector). The dict is now installed only while the slot
is still empty (pcc_gc_store_field_ptr_if_empty).
"""
from __future__ import annotations

import os
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python


PROGRAM = r'''
import pcc.virtual_thread as vt
from pcc.extern import extern, c_int64

pause = extern("pcc_platform_sleep_ns", (c_int64,), c_int64)

ROUNDS = 400
GROUPS = 8


class Payload:
    def __init__(self, group, index):
        self.group = group
        self.index = index
        self.items = [group, index]

    def check(self):
        return self.items[0] == self.group and self.items[1] == self.index


class Shared:
    def __init__(self):
        self.value = Payload(-1, -1)
        self.count = 0


class Open:
    pass


def writer(shared, group):
    index = 0
    while index < ROUNDS:
        shared.value = Payload(group, index)
        if index % 16 == 0:
            vt.yield_now()
        index += 1
    return ROUNDS


def reader(shared, rounds):
    bad = 0
    index = 0
    while index < rounds:
        current = shared.value
        if not current.check():
            bad += 1
        if index % 16 == 0:
            vt.yield_now()
        index += 1
    return bad


def deleter(shared, rounds):
    seen = 0
    index = 0
    while index < rounds:
        try:
            del shared.count
            seen += 1
        except AttributeError:
            pass
        shared.count = index
        if hasattr(shared, "count"):
            seen += 1
        if index % 16 == 0:
            vt.yield_now()
        index += 1
    return seen


def setter(target, group):
    index = 0
    while index < 50:
        setattr(target, "a" + str(group) + "_" + str(index), group * 1000 + index)
        if index % 8 == 0:
            vt.yield_now()
        index += 1
    return 50


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


def main():
    shared = Shared()
    tasks = []
    group = 0
    while group < GROUPS:
        tasks.append(vt.spawn(writer, shared, group))
        tasks.append(vt.spawn(reader, shared, ROUNDS))
        group += 1
    results = run(tasks)
    bad = 0
    for result in results:
        if result != ROUNDS:
            bad += result
    print("fields", bad, shared.value.check())

    tasks = []
    group = 0
    while group < 4:
        tasks.append(vt.spawn(deleter, shared, ROUNDS))
        group += 1
    run(tasks)
    print("delete", hasattr(shared, "count"))

    misses = 0
    attempt = 0
    while attempt < 20:
        target = Open()
        tasks = []
        group = 0
        while group < GROUPS:
            tasks.append(vt.spawn(setter, target, group))
            group += 1
        run(tasks)
        group = 0
        while group < GROUPS:
            index = 0
            while index < 50:
                if getattr(target, "a" + str(group) + "_" + str(index), None) != group * 1000 + index:
                    misses += 1
                index += 1
            group += 1
        attempt += 1
    print("dict", misses)


main()
'''

EXPECTED = "fields 0 True\ndelete True\ndict 0\n"


@pytest.mark.integration
def test_shared_instance_attributes_under_four_carriers(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "instance_races.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "instance_races"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        for run in range(2):
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                                 env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
            assert (ran.returncode, ran.stdout, ran.stderr) == (0, EXPECTED, ""), (backend, run)
