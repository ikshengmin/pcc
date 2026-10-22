"""Augmented assignment mutates in place exactly where CPython does.

``py_obj_inplace_op`` fell back to the binary operator for every value that
was not a user instance, and codegen sent only ``+ - * / // %`` to it, so
``lst += [2]`` on a typed or dynamic local, an attribute or a subscript,
``lst *= 2``, ``d |= {...}`` and ``holder["s"] |= {...}`` built new objects
while aliases kept the old ones, and ``|= &= ^= <<= >>= **= @=`` never
reached a user ``__i*__`` method. The program below compares every route
with CPython on all five collectors.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.python.pcc1_gate import repo_root


PROGRAM = '''class Box:
    pass


class Acc:
    def __init__(self):
        self.log = []

    def __ior__(self, other):
        self.log.append(("ior", other))
        return self

    def __iand__(self, other):
        self.log.append(("iand", other))
        return self

    def __ixor__(self, other):
        self.log.append(("ixor", other))
        return self

    def __ilshift__(self, other):
        self.log.append(("ilshift", other))
        return self

    def __irshift__(self, other):
        self.log.append(("irshift", other))
        return self

    def __ipow__(self, other):
        self.log.append(("ipow", other))
        return self

    def __imatmul__(self, other):
        self.log.append(("imatmul", other))
        return self


class Prepend:
    def __radd__(self, other):
        return ["radd"] + other


class Items:
    def __iter__(self):
        return iter([7, 8])


def anything(value):
    return value


def main():
    a = [1]
    b = a
    a += [2]
    print("typed list", b, a is b)
    dyn = anything([1])
    alias = dyn
    dyn += (2, 3)
    print("dyn list from tuple", alias, dyn is alias)
    d = {"k": [1]}
    inner = d["k"]
    d["k"] += [2]
    print("subscript list", inner)
    box = Box()
    box.items = [1]
    kept = box.items
    box.items += "ab"
    print("attr list from str", kept)
    m = [1, 2]
    copy = m
    m *= 2
    print("list imul", copy)
    z = [3]
    zero = z
    z *= 0
    print("list imul zero", zero)
    self_extend = [1, 2]
    self_extend += self_extend
    print("self extend", self_extend)
    holder = {"s": {1}}
    shared = holder["s"]
    holder["s"] |= {2}
    holder["s"] -= {1}
    print("subscript set", sorted(shared))
    s = {1, 2, 3}
    t = s
    s &= {2, 3}
    s ^= {3, 4}
    print("set iand ixor", sorted(t))
    dd = {"a": 1}
    view = dd
    dd |= {"b": 2}
    dd |= [("c", 3)]
    print("dict ior", sorted(view.items()))
    acc = Acc()
    original = acc
    acc |= 1
    acc &= 2
    acc ^= 3
    acc <<= 4
    acc >>= 5
    acc **= 6
    acc @= 7
    print("user iops", original.log, acc is original)
    r = [1]
    r += Prepend()
    print("reflected radd", r)
    it = [0]
    it += Items()
    print("iterable extend", it)
    n = 5
    n += 2
    word = "ab"
    word += "c"
    tup = (1,)
    tup_alias = tup
    tup += (2,)
    print("immutables", n, word, tup, tup_alias)
    try:
        bad = {1}
        bad |= [2]
    except TypeError:
        print("set ior list TypeError")
    try:
        lst = [1]
        lst *= "x"
    except TypeError:
        print("list imul str TypeError")


main()
'''


@pytest.mark.integration
def test_augmented_assignment_mutates_like_cpython(tmp_path: Path) -> None:
    source = tmp_path / "inplace_aliasing.py"
    source.write_text(PROGRAM, encoding="utf-8")
    oracle = subprocess.run([sys.executable, str(source)], text=True,
                            capture_output=True, check=True, timeout=30)
    executable = tmp_path / "inplace_aliasing"
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    env.pop("PCC_WITH_THREADS", None)
    build = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--python-libpython=off",
         str(source), "-o", str(executable)],
        cwd=repo_root(), text=True, capture_output=True, timeout=900, env=env,
    )
    assert build.returncode == 0, build.stderr[-4000:]
    for backend in ("0", "1", "2", "3", "4"):
        run = subprocess.run([str(executable)], text=True, capture_output=True,
                             timeout=120, env={**env, "PCC_GC_BACKEND": backend})
        assert run.returncode == 0, (backend, run.stderr[-2000:])
        assert run.stdout == oracle.stdout, (backend, run.stdout)
