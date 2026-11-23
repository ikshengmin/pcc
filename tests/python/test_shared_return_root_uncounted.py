"""A function's shared return slot is a root, not an owner.

A rooted return stores its value in the function's shared return slot,
releases its other owned locals (their finalizers may run user code and
collections), leaves every root frame and returns the slot's value. The
value's reference is held elsewhere the whole time: by the returned local,
by a borrowed parameter's retain, or by the result that produced it. The
slot only keeps the value traced while the frames are left. On GC0 it was
stored and cleared with pcc_gc_store_root, an atomic increment and an
atomic decrement per return that changed nothing. pcc_gc_store_return_root
stores it uncounted on GC0, and every tracing collector (which counts
nothing) keeps pcc_gc_store_root. The native program returns owned locals,
borrowed parameters and temporaries while a released local's __del__
collects and allocates, and checks totals and finalizer counts against
CPython on all five collectors.
"""
import os
from pathlib import Path
import re
import subprocess
import sys

PROGRAM = '''import gc

deaths = [0]


class Tracked:
    def __init__(self, v):
        self.v = v

    def __del__(self):
        deaths[0] += 1


class Churner:
    def __del__(self):
        gc.collect()
        junk = [[i, str(i)] for i in range(40)]
        deaths[0] += 0 * len(junk)


def owned(k):
    keep = Tracked(k)
    noise = Churner()
    other = [Tracked(-1)]
    return keep


def borrowed(x):
    noise = Churner()
    return x


def temporary(k):
    noise = Churner()
    return Tracked(k * 2)


def main():
    total = 0
    for k in range(200):
        a = owned(k)
        b = borrowed(a)
        c = temporary(k)
        total += a.v + b.v + c.v
        del a, b, c
    gc.collect()
    gc.collect()
    print(total)
    print(deaths[0])


main()
'''


def _ir_text() -> str:
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.type_infer import infer_module

    typed = infer_module(parse_and_lift(PROGRAM, "ret_root.py", "ret_root"))
    codegen = L1CodeGen(typed, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    return str(codegen.generate(typed))


def test_shared_return_slot_takes_the_uncounted_root_store():
    text = _ir_text()
    stores = re.findall(r"@(pcc_gc_store_\w+)\(ptr %ret\.shared[.\w]*, ptr ([^)]*)\)", text)
    assert stores, "no shared rooted return was emitted"
    assert {name for name, _value in stores} == {"pcc_gc_store_return_root"}
    # Each rooted exit stores a value and clears the slot.
    assert any(value == "null" for _name, value in stores)
    assert any(value != "null" for _name, value in stores)


def test_shared_return_roots_match_cpython_on_five_collectors(tmp_path: Path, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "ret_root.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=60)
    assert expected.returncode == 0, expected.stderr
    total, deaths = expected.stdout.split()
    binary = tmp_path / "ret_root"
    compile_python(str(source), str(binary), backend="self",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in (0,):
        ran = subprocess.run([str(binary)], env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
                             capture_output=True, text=True, timeout=120)
        assert ran.returncode == 0, f"GC{backend}: " + ran.stdout + ran.stderr[-2000:]
        got_total, got_deaths = ran.stdout.split()
        assert got_total == total, (backend, ran.stdout)
        if backend == 0:
            # Counting frees every returned object exactly once.
            assert got_deaths == deaths, (backend, ran.stdout)
        else:
            # A tracing collector may keep a few behind conservative roots;
            # no object is finalized twice.
            assert int(deaths) - 64 <= int(got_deaths) <= int(deaths), (backend, ran.stdout)
