"""str.join reads a list's items under the graph lock and copies what it sized.

A non-str item raises CPython's TypeError; join used to return NULL as its
value without an exception, which printed as <null>.

join read a list's items without the lock (a replaced item could be released
while it was being measured) and sized the result before copying: an item
replaced by a longer str in between was copied past the allocation. The
items are now measured under the lock, the result is allocated outside it,
and the copy runs in one transaction after measuring again; a list that
changed size in between is joined again.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python


SEMANTICS = r'''
def main():
    parts = ["notifications", ": ", "[", "\"ready\"", "]"]
    print("".join(parts), "-".join(("a", "b", "c")), "x".join([]), ",".join(["one"]))
    words = []
    index = 0
    while index < 300:
        words.append("w" + str(index))
        index += 1
    joined = ", ".join(words)
    print(len(joined), joined[:20], joined[-12:])
    print("|".join(["é", "日本", "a"]), len("|".join(["é", "日本", "a"])))
    for bad in (["a", 1], [None], ["x", b"y"], ("a", 2)):
        try:
            "".join(bad)
            print("joined")
        except TypeError as error:
            print("TypeError", error)


main()
'''


@pytest.mark.integration
def test_join_matches_cpython_on_five_collectors(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "join_semantics.py"
    source.write_text(SEMANTICS, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=120)
    assert expected.returncode == 0, expected.stderr
    executable = tmp_path / "join_semantics"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected.stdout, ""), backend


CONCURRENT = r'''
import pcc.virtual_thread as vt

shared = ["ab"] * 50


def mutator(seed):
    step = 0
    while step < 4000:
        shared[(seed * 13 + step) % 50] = "x" * ((seed + step) % 9)
        if step % 100 == 0:
            vt.yield_now()
        step += 1
    return seed


def joiner(seed):
    valid = 0
    step = 0
    while step < 1500:
        text = "-".join(shared)
        pieces = text.split("-")
        if len(pieces) == 50 and text.replace("x", "").replace("a", "").replace("b", "").replace("-", "") == "":
            valid += 1
        if step % 50 == 0:
            vt.yield_now()
        step += 1
    return valid


def main():
    threads = [vt.spawn(mutator, seed) for seed in range(3)]
    threads += [vt.spawn(joiner, seed) for seed in range(3)]
    vt.run(4, 100000000)
    print([vt.result(thread) for thread in threads])


main()
'''


@pytest.mark.integration
def test_join_of_a_list_mutated_on_other_carriers(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "join_concurrent.py"
    source.write_text(CONCURRENT, encoding="utf-8")
    executable = tmp_path / "join_concurrent"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        for attempt in range(3):
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                                 env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
            assert (ran.returncode, ran.stdout, ran.stderr) == (
                0, "[0, 1, 2, 1500, 1500, 1500]\n", ""), (backend, attempt)
