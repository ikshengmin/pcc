"""Deleting a module definition retires its hidden cache but preserves aliases."""
import os
import subprocess
import sys

from pcc.frontends.python.pipeline import compile_python


PROGRAM = '''import gc
import weakref

events = []
class Payload:
    def __del__(self):
        events.append("released")

def target(value=Payload()):
    return value

def obtain():
    return target

function_ref = weakref.ref(target)
default_ref = weakref.ref(target.__defaults__[0])

def main():
    global target
    alias = obtain()
    del target
    gc.collect()
    print(function_ref() is alias, alias() is default_ref())
    try:
        obtain()
    except NameError:
        print("unbound")
    del alias
    gc.collect()
    print(function_ref() is None, default_ref() is None, events)
main()
'''

EXPECTED = "True True\nunbound\nTrue True ['released']\n"


def test_deleted_module_function_releases_cache_without_invalidating_alias(tmp_path, pcc_runtime_archive):
    source = tmp_path / "function_lifetime.py"
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True,
                               text=True, timeout=15)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, EXPECTED, "")
    output = tmp_path / "function_lifetime"
    compile_python(str(source), str(output), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for gc in range(5):
        result = subprocess.run([str(output)], capture_output=True, text=True, timeout=20,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc)))
        assert (result.returncode, result.stdout, result.stderr) == (0, EXPECTED, ""), gc
