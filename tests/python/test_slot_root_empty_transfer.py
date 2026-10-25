"""Empty owner transfers keep cleanup callbacks and the original exception."""
import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python


@pytest.mark.parametrize("backend", range(5))
def test_empty_transfer_does_not_consume_a_reentrant_replacement(backend):
    source = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_obj.py"
    node = next(n for n in ast.parse(source.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == "pcc_gc_store_root_take")
    node.decorator_list = []
    memory = {100: 200}
    released = []

    def decref(value):
        released.append(value)
        memory[100] = 300  # The old owner's finalizer repopulates the root.

    def checked_store(slot, value):
        old = memory[slot]
        memory[slot] = value
        decref(old)

    namespace = {
        "ptr_is_null": lambda value: value == 0,
        "_gc_backend_fast": lambda: backend,
        "load_i32": lambda pointer, offset: 0,
        "global_addr": lambda name: name,
        "null": lambda: 0,
        "load_ptr": lambda pointer, offset: memory[pointer],
        "store_ptr": lambda pointer, offset, value: memory.__setitem__(pointer, value),
        "is_tagged_int": lambda value: value & 1,
        "py_decref": decref,
        "pcc_gc_store_root": checked_store,
        "pcc_gc_load_ptr": lambda owner, slot: memory[slot],
    }
    exec(compile(ast.Module([node], type_ignores=[]), str(source), "exec"), namespace)
    namespace["pcc_gc_store_root_take"](100, 0)
    assert released == [200] and memory[100] == 300


PROGRAM = '''import gc

events = []
error = ValueError("marker")
class Temporary:
    def __del__(self):
        events.append("released")

def callback(value, fail):
    if fail:
        raise error
    return value

def invoke(target, fail):
    return target(Temporary(), fail)

def main():
    global error
    try:
        invoke(callback, True)
    except ValueError as caught:
        assert caught is error
    # CPython tracebacks keep callback arguments alive until this owner drops.
    error = None
    gc.collect()
    assert events == ["released"]
    value = invoke(callback, False)
    gc.collect()
    assert events == ["released"]
    del value
    gc.collect()
    assert events == ["released", "released"]
    print("EMPTY_TRANSFER_OK")
main()
'''


def test_native_cleanup_keeps_exception_identity_and_owned_result(tmp_path, pcc_runtime_archive):
    source = tmp_path / "empty_transfer.py"
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=15)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, "EMPTY_TRANSFER_OK\n", "")
    output = tmp_path / "empty_transfer"
    compile_python(str(source), str(output), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for gc in range(5):
        result = subprocess.run([str(output)], capture_output=True, text=True, timeout=20,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc)))
        assert (result.returncode, result.stdout, result.stderr) == (0, reference.stdout, ""), gc
