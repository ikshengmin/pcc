"""Run generic managed extern arguments through a supplied native compiler."""
import os
from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.integration

SOURCE = '''import gc
from pcc.extern import extern, c_obj, c_int64
read_int = extern("py_index_i64_checked", (c_obj,), c_int64)
get = extern("py_tuple_get", (c_obj, c_int64), c_obj)
positive = extern("py_obj_pos", (c_obj,), c_obj)
freed = 0
triggered = 0
class Payload:
    def __init__(self, value):
        self.value = value
    def __del__(self):
        global freed
        freed += 1
class Trigger:
    def __del__(self):
        global triggered
        triggered += 1
        gc.collect()
class Alias:
    def __pos__(self):
        gc.collect()
        return self

def later():
    gc.collect()
    return 0

def fail():
    gc.collect()
    raise ValueError("second argument failed")

def main():
    assert read_int(37) == 37
    assert read_int(True) == 1
    result = get((Payload(47), Trigger()), later())
    gc.collect()
    assert triggered == 1
    assert freed == 0
    assert result.value == 47
    result = None
    gc.collect()
    assert freed == 1
    try:
        get((Payload(88),), fail())
    except ValueError:
        pass
    else:
        raise AssertionError("argument error disappeared")
    gc.collect()
    assert freed == 2
    original = Alias()
    result = positive(original)
    assert result is original
    original = None
    gc.collect()
    assert positive(result) is result
    print("extern-c-obj-ok")
main()
'''


def test_native_c_obj_boxing_argument_gc_error_and_owned_alias_result(tmp_path):
    compiler = os.environ.get("PCC_FILE_LOCKING_COMPILER", "")
    runtime = os.environ.get("PCC_RUNTIME_ARCHIVE", "")
    assert compiler and Path(compiler).is_file(), "set PCC_FILE_LOCKING_COMPILER"
    assert runtime and Path(runtime).is_file(), "set matching PCC_RUNTIME_ARCHIVE"
    source = tmp_path / "extern_objects.py"
    source.write_text(SOURCE)
    executable = tmp_path / ("extern_objects.exe" if os.name == "nt" else "extern_objects")
    compiled = subprocess.run([compiler, "--backend", "self", "--python-libpython", "off", str(source), "-o", str(executable)], capture_output=True, text=True, timeout=180)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    for gc in range(5):
        result = subprocess.run([str(executable)], env=dict(os.environ, PCC_GC_BACKEND=str(gc), PATH="/nonexistent"), capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, (gc, result.stdout, result.stderr)
        assert result.stdout == "extern-c-obj-ok\n"
