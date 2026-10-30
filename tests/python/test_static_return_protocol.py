"""Returning an immortal static or a tagged immediate takes no owner protocol.

``return None`` retained the singleton, stored it through a root slot, entered
a LIFO frame for it, left the function's frames, reloaded it and cleared the
slot: six runtime calls on every such return. None/True/False, static literals
and tagged ints never move and their counts never change, so the return now
passes the value straight through. Owned locals are still released first.
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


def nothing(item, items):
    return None


def bare(flag):
    if flag:
        return
    return None


def truth():
    return True


def small():
    return 7


def text():
    return "fixed"


def make_then_none(n):
    owned = [Item(n)]
    return None


def main():
    total = 0
    items = []
    for index in range(20000):
        item = Item(index)
        if nothing(item, items) is None:
            total += 1
        if bare(index % 2) is None:
            total += 1
        if truth() is True:
            total += 1
        total += small()
        if text() == "fixed":
            total += 1
        if make_then_none(index) is None:
            total += 1
    gc.collect()
    print(total, freed)


main()
'''


def _body(text: str, name: str) -> str:
    found = re.search(r"define [^\n]*@" + re.escape(name) + r"\(.*?\n\}", text, re.S)
    assert found is not None, name
    return found.group(0)


def test_static_returns_skip_retain_and_return_roots(tmp_path):
    source = tmp_path / "static_return.py"
    source.write_text(PROGRAM, encoding="utf-8")
    output = tmp_path / "static_return.ll"
    compile_python(str(source), str(output), emit_llvm_only=True, libpython_mode="off")
    text = output.read_text(encoding="utf-8")
    for name in ("nothing", "bare", "truth", "small", "text", "make_then_none"):
        body = _body(text, "user_static_return_" + name)
        # No return retain, no return root slot, no LIFO frame for it.
        assert "%ret.retain" not in body, name
        assert not re.search(r"@pcc_gc_store_root\(ptr %ret\.", body), name
        assert not re.search(r"@pcc_gc_frame_enter_lifo_mapped\(ptr [^,]+, ptr %ret\.", body), name
    # The owned local is still released before the shared exit returns.
    assert "@pcc_gc_store_root(" in _body(text, "user_static_return_make_then_none")


def test_static_returns_run_on_five_collectors(tmp_path, pcc_runtime_archive):
    source = tmp_path / "static_return.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "static_return"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        # CPython prints the same line: 20000 * (5 + 7), and every item but
        # the one the loop variable still holds is freed.
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, "240000 39999\n", ""), backend
