"""Class instances are owned temporaries even in a C-ABI-exporting module.

A module that imports pcc.unsafe and defines a @c_abi_export helper takes the
raw-scaffold ownership rules, which treat most call results as borrowed. A
class instantiation still allocates a fresh instance: classified as borrowed,
every temporary instance leaked and its __del__ never ran on any collector.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python


PROGRAM = r'''
import gc
from pcc.extern import c_abi_export, c_obj
from pcc.unsafe import ptr_to_int


@c_abi_export("export_module_address")
def address(value: c_obj) -> int:
    return ptr_to_int(value)


finished = 0


class Tracked:
    def __init__(self, holder):
        self.holder = holder

    def __del__(self):
        global finished
        finished += 1


def consume(value):
    return value is not None


def main():
    for index in range(16):
        holder = [str(index)]
        try:
            holder[0].encode(errors=Tracked(holder))
        except TypeError:
            pass
        consume(Tracked(holder))
    gc.collect()
    print(finished)


main()
'''


@pytest.mark.integration
def test_temporary_instances_finalize_on_five_collectors(tmp_path, pcc_runtime_archive):
    source = tmp_path / "export_module_finalizers.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "export_module_finalizers"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60,
                             env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert ran.returncode == 0, f"GC{backend}: " + ran.stdout + ran.stderr
        assert ran.stdout == "32\n", f"GC{backend}: " + ran.stdout
