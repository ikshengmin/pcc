"""A direct call to a same-module function tests its NULL result for errors.

A compiled function returns NULL exactly when it raises, as the Python/C API
does. The caller used to root the result, ask py_err_occurred() (a thread-
local read behind a polling call) and reload, clear and unroot it. It now
branches on the NULL result: no root, no call. Exceptions still propagate,
are caught, carry their frames and run cleanup exactly as before.
"""
from __future__ import annotations

import os
import re
import subprocess

from pcc.frontends.python.pipeline import compile_python


PROGRAM = r'''
import gc

freed = 0


class Item:
    def __init__(self, value):
        self.value = value

    def __del__(self):
        global freed
        freed += 1


def pick(item, other):
    return item


def fail(item, depth):
    if depth == 0:
        raise ValueError("depth reached")
    return fail(item, depth - 1)


def deep(n):
    return deep(n + 1)


def main():
    total = 0
    for index in range(5000):
        item = pick(Item(index), [index])
        total += item.value
        try:
            fail(Item(index), index % 5)
        except ValueError as error:
            total += len(str(error))
    try:
        deep(0)
    except RecursionError:
        total += 1
    item = None
    gc.collect()
    print(total, freed)


main()
'''


def _body(text: str, name: str) -> str:
    found = re.search(r"define [^\n]*@" + re.escape(name) + r"\(.*?\n\}", text, re.S)
    assert found is not None, name
    return found.group(0)


def test_same_module_call_branches_on_null_result(tmp_path):
    source = tmp_path / "null_result.py"
    source.write_text(PROGRAM, encoding="utf-8")
    output = tmp_path / "null_result.ll"
    compile_python(str(source), str(output), emit_llvm_only=True, libpython_mode="off")
    main = _body(output.read_text(encoding="utf-8"), "user_null_result_main")
    call = re.search(r"(%pick_ret[\w.]*) = call ptr \(ptr, ptr\) @user_null_result_pick\(", main)
    assert call is not None
    after = main[call.end():].split("\n", 2)[1]
    # The very next instruction tests the result itself.
    assert re.search(r"icmp eq ptr " + re.escape(call.group(1)) + r", null", after), after
    assert "call.ret.root" not in main[call.start():call.start() + 600]


def test_null_result_errors_propagate_on_five_collectors(tmp_path, pcc_runtime_archive):
    source = tmp_path / "null_result.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "null_result"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        # CPython prints the same line.
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, EXPECTED, ""), backend


EXPECTED = "12562501 10000\n"
