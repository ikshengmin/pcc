"""list.extend from a list or tuple appends in graph transactions of 1024 items.

The locked extend read the target length and the source length once, grew,
then stored one item per transaction at the stale length + i. A concurrent
clear (or append) in between left NULL holes below the new length and lost
or overwrote items. Each transaction now rereads the target length, its room
and the source under the lock: an extend of up to 1024 items is atomic and
none ever leaves a hole.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python


SEMANTICS = r'''
def main():
    a = [1, 2]
    a.extend(a)
    print(a)
    a.extend(())
    a.extend([])
    big = list(range(2600))
    b = ["head"]
    b.extend(big)
    b.extend(tuple(range(1500)))
    print(len(b), b[0], b[1], b[2600], b[2601], b[-1], sum(b[1:]))
    c = list(range(1100))
    c.extend(c)
    print(len(c), c[1099], c[1100], c[-1], c == list(range(1100)) * 2)


main()
'''


@pytest.mark.integration
def test_extend_matches_cpython_on_five_collectors(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "extend_semantics.py"
    source.write_text(SEMANTICS, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=120)
    assert expected.returncode == 0, expected.stderr
    executable = tmp_path / "extend_semantics"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected.stdout, ""), backend


CONCURRENT = r'''
import pcc.virtual_thread as vt

shared = []


def extender(seed):
    step = 0
    while step < 1500:
        token = seed * 100000 + step + 1
        if step % 2 == 0:
            shared.extend((token, -token))
        else:
            shared.extend([token, -token])
        if step % 40 == 0:
            vt.yield_now()
        step += 1
    return seed


def clearer(seed):
    step = 0
    while step < 120:
        if step % 3 == 0:
            shared.extend(shared)
        else:
            shared.clear()
        vt.yield_now()
        step += 1
    return seed


def main():
    threads = [vt.spawn(extender, seed) for seed in range(4)]
    threads += [vt.spawn(clearer, seed) for seed in range(2)]
    vt.run(4, 10000000)
    print([vt.result(thread) for thread in threads])
    paired = True
    index = 0
    while index < len(shared):
        value = shared[index]
        if not isinstance(value, int) or value <= 0 or index + 1 >= len(shared) or shared[index + 1] != -value:
            paired = False
        index += 2
    print(paired, len(shared) % 2)


main()
'''


@pytest.mark.integration
def test_concurrent_extend_and_clear_keep_pairs_on_five_collectors(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "extend_concurrent.py"
    source.write_text(CONCURRENT, encoding="utf-8")
    executable = tmp_path / "extend_concurrent"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        for attempt in range(3):
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                                 env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
            assert (ran.returncode, ran.stdout, ran.stderr) == (
                0, "[0, 1, 2, 3, 0, 1]\nTrue 0\n", ""), (backend, attempt)
