"""Dynamic method calls run plain compiled methods through their vector entry.

`obj.name(args)` on a receiver the compiler cannot type loads the method
and calls `py_obj_call_method`. That built `(self,) + args` and ran the
generic call protocol (pins, frame, signature binding) before the tuple
adapter unpacked it again. A function whose formals are plain positional
parameters now carries a `{entry, arity}` descriptor in its vectorcall word;
at exactly that arity the runtime passes a registered root frame
`[inner captures, self, args...]` to the vector entry instead. Every other
shape -- defaults filled in, keyword-only or variadic formals, more than
eight positional values, roles, bound receivers -- keeps the tuple path.

The semantic program is compared with CPython under all five collectors;
the allocation probe shows the per-call `(self,) + args` tuple is gone.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from pcc.frontends.python.codegen.user_function_lowering import (
    NATIVE_VECTOR_ARGS_SLOT,
    NATIVE_VECTOR_CAPTURES_SLOT,
    NATIVE_VECTOR_MAX_ARGS,
    native_vector_arity,
)
from pcc.frontends.python.pipeline import compile_python
from pcc.frontends.python.py_ast import Arg


SEMANTIC_PROGRAM = '''import gc


class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def norm1(self):
        return abs(self.x) + abs(self.y)

    def add(self, other):
        return Point(self.x + other.x, self.y + other.y)

    def scale(self, k: int) -> int:
        return (self.x + self.y) * k

    def many(self, a, b, c, d, e, f, g):
        return [self.x, a, b, c, d, e, f, g]

    def too_many(self, a, b, c, d, e, f, g, h):
        return [a, h]

    def defaults(self, a, b=10):
        return a + b

    def kwonly(self, a, *, b=3):
        return a * b

    def star(self, *items):
        return len(items)

    def nothing(self, a):
        self.last = a

    def boom(self, a):
        raise ValueError("boom " + str(a))

    def collect_then(self, a, b):
        gc.collect()
        return [a, b, self.x]

    def gen(self, n):
        for i in range(n):
            yield i * self.x

    def pos_only(self, a, /, b):
        return a - b


def make_scaler(factor, offset):
    class Scaler:
        def apply(self, value):
            return value * factor + offset

    return Scaler()


def receivers(count):
    items = []
    i = 0
    while i < count:
        if i % 3 == 0:
            items.append(Point(i, -i))
        elif i % 3 == 1:
            items.append(make_scaler(i, 1))
        else:
            items.append(Point(-1, i))
        i += 1
    return items


def drive(items, rounds):
    total = 0
    acc = Point(0, 0)
    r = 0
    while r < rounds:
        for item in items:
            if isinstance(item, Point):
                total += item.norm1()
                acc = acc.add(item)
                total += item.scale(3)
                item.nothing(r)
                total += item.defaults(r)
                total += item.defaults(r, 1)
                total += item.kwonly(2)
                total += item.kwonly(2, b=5)
                total += item.star(1, 2, 3)
                total += item.pos_only(9, 4)
                total += sum(item.gen(4))
                total += len(item.many(1, 2, 3, 4, 5, 6, 7))
                total += item.too_many(1, 2, 3, 4, 5, 6, 7, 8)[1]
            else:
                total += item.apply(r)
        r += 1
    return total, acc.x, acc.y


def failures(point):
    out = []
    try:
        point.boom(7)
    except ValueError as exc:
        out.append(str(exc))
    for call in (lambda: point.norm1(1), lambda: point.add(), lambda: point.many(1)):
        try:
            call()
        except TypeError as exc:
            out.append(type(exc).__name__)
    return out


def main():
    items = receivers(12)
    print(drive(items, 300))
    point = items[0]
    kept = []
    i = 0
    while i < 200:
        kept.append(point.collect_then([i], str(i)) if i % 50 == 0 else point.add(Point(i, i)))
        i += 1
    print(len(kept), kept[0], kept[50], kept[199].x, kept[199].y)
    print(failures(point))
    print(point.last, list(point.gen(3)))


main()
'''


ALLOCATION_PROGRAM = '''from pcc.extern import c_int64, extern

allocation_count = extern("pcc_gc_metric_allocation_count", (), c_int64)


class Box:
    def __init__(self, value):
        self.value = value

    def mix(self, a, b):
        return self.value + a + b


def run(items, rounds):
    total = 0
    r = 0
    while r < rounds:
        for item in items:
            total += item.mix(r, 1)
        r += 1
    return total


def main():
    items = [Box(1), Box(2), Box(3), Box(4)]
    run(items, 10)
    before = allocation_count()
    total = run(items, 2500)
    after = allocation_count()
    print("TOTAL", total)
    print("PER_CALL", (after - before) / 10000.0)


main()
'''


def _formals(*kinds):
    return tuple(Arg(name=f"a{i}", annotation=None, default=None, kind=kind)
                 for i, kind in enumerate(kinds))


def test_vector_arity_admits_only_plain_positional_formals():
    assert (NATIVE_VECTOR_CAPTURES_SLOT, NATIVE_VECTOR_ARGS_SLOT) == (0, 1)
    assert native_vector_arity(()) == 0
    assert native_vector_arity(_formals("pos", "pos_only", "pos")) == 3
    assert native_vector_arity(_formals(*["pos"] * NATIVE_VECTOR_MAX_ARGS)) == NATIVE_VECTOR_MAX_ARGS
    assert native_vector_arity(_formals(*["pos"] * (NATIVE_VECTOR_MAX_ARGS + 1))) is None
    for kind in ("kw_only", "*args", "**kwargs"):
        assert native_vector_arity(_formals("pos", kind)) is None


def test_runtime_frame_layout_matches_codegen():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "pcc" / "runtime" / "py" / "py_func.py").read_text()
    assert f"_VECTOR_FRAME_CAPTURES_SLOT = {NATIVE_VECTOR_CAPTURES_SLOT}\n" in source
    assert f"_VECTOR_FRAME_ARGS_SLOT = {NATIVE_VECTOR_ARGS_SLOT}\n" in source
    assert f"_VECTOR_FRAME_MAX_ARGS = {NATIVE_VECTOR_MAX_ARGS}\n" in source
    slots = 1 + NATIVE_VECTOR_MAX_ARGS
    assert f"_VECTOR_FRAME_BYTES = {slots * 8}\n" in source
    assert f'define_global_i32("pcc_vector_call_frame_map", {slots})' in source


def _compile(tmp_path, name, program, archive):
    source = tmp_path / f"{name}.py"
    source.write_text(program)
    binary = tmp_path / name
    compile_python(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                   runtime_archive=str(archive))
    return source, binary


def _run(binary, backend):
    return subprocess.run([str(binary)], capture_output=True, text=True, timeout=120,
                          env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))


@pytest.mark.skipif(sys.platform != "darwin", reason="native Darwin execution")
def test_method_calls_match_cpython_on_every_collector(tmp_path, pcc_runtime_archive):
    source, binary = _compile(tmp_path, "method_vector_call", SEMANTIC_PROGRAM, pcc_runtime_archive)
    expected = subprocess.run([sys.executable, str(source)], capture_output=True, text=True,
                              timeout=120)
    assert expected.returncode == 0, expected.stderr
    for backend in range(5):
        run = _run(binary, backend)
        assert (run.returncode, run.stdout, run.stderr) == (0, expected.stdout, ""), backend


@pytest.mark.skipif(sys.platform != "darwin", reason="native Darwin execution")
def test_vector_method_call_allocates_no_argument_tuple(tmp_path, pcc_runtime_archive):
    _source, binary = _compile(tmp_path, "method_vector_allocations", ALLOCATION_PROGRAM,
                               pcc_runtime_archive)
    for backend in range(5):
        run = _run(binary, backend)
        assert run.returncode == 0, (backend, run.stderr)
        lines = dict(line.split(" ", 1) for line in run.stdout.splitlines())
        assert lines["TOTAL"] == str(sum(4 * r + 14 for r in range(2500))), backend
        # The caller's `(r, 1)` tuple remains; `(self, r, 1)` does not.
        assert float(lines["PER_CALL"]) < 1.5, (backend, run.stdout)
