"""The sole collector clears generator weakrefs before closing a dead generator."""
import os
import subprocess
import sys

from pcc.frontends.python.pipeline import compile_python


PROGRAM = """\
import gc
import weakref

events = []
refs = []

def dead_generator():
    try:
        yield 42
    finally:
        events.append("close")

def observe(reference):
    assert reference() is None
    assert refs[0]() is None
    events.append("callback")

def exercise():
    gen = dead_generator()
    assert next(gen) == 42
    refs.append(weakref.ref(gen))
    refs.append(weakref.ref(gen, observe))
    assert refs[0]() is gen and refs[1]() is gen

exercise()
gc.collect()
assert refs[0]() is None and refs[1]() is None
print(events)
"""


def test_dead_generator_weakrefs_clear_before_close(tmp_path, pcc_runtime_archive):
    source = tmp_path / "generator_weakrefs.py"
    source.write_text(PROGRAM)
    binary = tmp_path / "generator_weakrefs"
    expected = (0, "['callback', 'close']\n", "")
    oracle = subprocess.run([sys.executable, str(source)], capture_output=True,
                            text=True, timeout=20)
    assert (oracle.returncode, oracle.stdout, oracle.stderr) == expected
    compile_python(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                   runtime_archive=str(pcc_runtime_archive))
    native = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                             env=dict(os.environ, PATH="/nonexistent"))
    assert (native.returncode, native.stdout, native.stderr) == expected
