"""An int attribute read releases a receiver the expression itself produced.

``items[0].value`` and ``make().value`` read an ``int`` field through the
exact-int object boundary, which keeps bignum fields intact. Its receiver --
a fresh tuple element or call result -- was never released there, so each
read leaked that object and its ``__del__`` never ran on any collector.
"""

from __future__ import annotations

import os
import re
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python


PROGRAM = r'''
import gc

freed = 0


class Item:
    def __init__(self, value: int):
        self.value = value

    def __del__(self):
        global freed
        freed += 1


def make(value: int) -> Item:
    return Item(value)


def main():
    items = (Item(41), Item(42))
    total = 0
    for _ in range(8):
        if items[0].value == 41:
            total += items[1].value
        total += make(7).value
    items = None
    gc.collect()
    print(total, freed)


main()
'''


def test_receivers_take_the_exact_int_attribute_boundary(tmp_path):
    source = tmp_path / "exact_int_attr_receiver.py"
    source.write_text(PROGRAM, encoding="utf-8")
    output = tmp_path / "exact_int_attr_receiver.ll"
    compile_python(str(source), str(output), emit_llvm_only=True, libpython_mode="off")
    main = re.search(r"define [^\n]*@user_exact_int_attr_receiver_main\(.*?\n\}",
                     output.read_text(encoding="utf-8"), re.S)
    assert main is not None
    assert "exact.int.attr.obj" in main.group(0)


@pytest.mark.integration
def test_fresh_receivers_finalize_on_five_collectors(tmp_path, pcc_runtime_archive):
    source = tmp_path / "exact_int_attr_receiver.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "exact_int_attr_receiver"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60,
                             env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert ran.returncode == 0, f"GC{backend}: " + ran.stdout + ran.stderr
        # CPython prints "392 10": both tuple items and all eight made items.
        assert ran.stdout == "392 10\n", f"GC{backend}: " + ran.stdout
