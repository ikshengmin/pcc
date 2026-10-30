"""Shared dicts, lists and sets stay intact under concurrent carriers on GC0.

GC0 fast paths for dict set/get and rehash, set rehash and list insert, pop,
remove, clear, reverse and extend read and rewrite container tables without
the graph lock. They are correct only while one thread can touch the heap;
with four carriers a rehash freed a table that another carrier was reading
(a segfault in pcc_allocator_raw_magazine_put). The threaded runtime takes
the locked transactions there, as list append and get already do.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python


PROGRAM = r'''
import pcc.virtual_thread as vt

shared_dict = {}
pool = list(range(4000))
shared_list = []
scratch = []
shared_set = set()


def dict_writer(seed):
    step = 0
    while step < 3000:
        key = (seed * 7919 + step * 31) % 256
        if step % 3 == 0:
            shared_dict.pop(key, None)
        elif step % 5 == 0:
            if key in shared_dict:
                try:
                    del shared_dict[key]
                except KeyError:
                    pass
        else:
            shared_dict[key] = key * 3
        if step % 50 == 0:
            vt.yield_now()
        step += 1
    return seed


def dict_reader(seed):
    bad = 0
    step = 0
    while step < 600:
        kind = (seed + step) % 4
        if kind == 0:
            for key, value in shared_dict.items():
                if value != key * 3:
                    bad += 1
        elif kind == 1:
            for key in shared_dict.keys():
                if key < 0 or key >= 256:
                    bad += 1
        elif kind == 2:
            for value in shared_dict.values():
                if value % 3 != 0:
                    bad += 1
        else:
            probe = shared_dict.get(step % 256)
            if probe is not None and probe != (step % 256) * 3:
                bad += 1
        if step % 20 == 0:
            vt.yield_now()
        step += 1
    return bad


def popper(seed):
    total = 0
    count = 0
    while count < 900:
        total += pool.pop()
        count += 1
        if count % 40 == 0:
            vt.yield_now()
    return total


def reverser(seed):
    step = 0
    while step < 300:
        pool.reverse()
        if step % 10 == 0:
            vt.yield_now()
        step += 1
    return seed


def list_worker(seed):
    step = 0
    while step < 1000:
        token = seed * 100000 + step
        if step % 2 == 0:
            shared_list.insert(step % 7, token)
        else:
            shared_list.append(token)
        if step % 50 == 0:
            vt.yield_now()
        step += 1
    step = 0
    while step < 1000:
        shared_list.remove(seed * 100000 + step)
        if step % 50 == 0:
            vt.yield_now()
        step += 1
    return seed


def extender(seed):
    step = 0
    while step < 600:
        scratch.extend([seed, step])
        if step % 50 == 0:
            scratch.clear()
            vt.yield_now()
        step += 1
    return seed


def set_worker(seed):
    step = 0
    while step < 600:
        shared_set.add(seed * 1000 + step)
        if step % 50 == 0:
            vt.yield_now()
        step += 1
    step = 0
    while step < 600:
        shared_set.discard(seed * 1000 + step)
        step += 1
    return seed


def main():
    groups = []
    groups.append([vt.spawn(dict_writer, seed) for seed in range(4)])
    groups.append([vt.spawn(dict_reader, seed) for seed in range(4)])
    groups.append([vt.spawn(popper, seed) for seed in range(4)])
    groups.append([vt.spawn(reverser, 0)])
    groups.append([vt.spawn(list_worker, seed) for seed in range(4)])
    groups.append([vt.spawn(extender, seed) for seed in range(2)])
    groups.append([vt.spawn(set_worker, seed) for seed in range(4)])
    vt.run(4, 1000000)
    results = [[vt.result(thread) for thread in group] for group in groups]
    print(results[0], results[1], results[3], results[4], results[5], results[6])
    final = sorted(shared_dict.items())
    print(all(value == key * 3 for key, value in final), len(final) <= 256)
    print(sum(results[2]) + sum(pool) == sum(range(4000)), len(pool))
    print(shared_list, len(shared_set))
    print(all(isinstance(item, int) for item in scratch))


main()
'''

EXPECTED = (
    "[0, 1, 2, 3] [0, 0, 0, 0] [0] [0, 1, 2, 3] [0, 1] [0, 1, 2, 3]\n"
    "True True\n"
    "True 400\n"
    "[] 0\n"
    "True\n"
)


@pytest.mark.integration
def test_shared_containers_survive_four_carriers_on_five_collectors(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "shared_containers.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "shared_containers"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        for attempt in range(3):
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=180,
                                 env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
            assert (ran.returncode, ran.stdout, ran.stderr) == (0, EXPECTED, ""), (backend, attempt)
