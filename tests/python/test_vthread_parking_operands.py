"""An operand evaluated before a park keeps its value across the park.

A parking call inside a larger expression suspends its resumable function
mid-statement. The resume edge enters after the park, where no SSA value
computed before it exists, so ``items.append(fetch(1))`` and seven other
shapes below failed the IR verifier, and ``seen.add(fetch(1))`` added to a
stale receiver: the set stayed empty. Each statement now evaluates the
operands Python evaluates before the park, and the parking operand, into
generator frame roots in evaluation order, then lowers the rest from them.

The spilled roots are released when the statement ends; the token's
finalizer shows it, on the normal and on the exception path. Augmented
assignment to an attribute, a subscript or a global reads the target before
the park, as Python does; a parking subscript in an assignment target, a
star operand before the park, ``if``/``while`` tests and a ``for`` iterable
are covered too.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.python.pcc1_gate import repo_root


PROGRAM = '''import gc
import pcc.virtual_thread as vt

G = [1]
events = []


class Token:
    def __init__(self, value):
        self.value = value

    def __del__(self):
        events.append("drop" + str(self.value))


class Box:
    def __init__(self):
        self.items = []

    def put(self, value):
        self.items.append(value)
        return len(self.items)


def fetch(n: int) -> int:
    vt.yield_now()
    return n * 10


def fail() -> int:
    vt.yield_now()
    raise ValueError("fetch failed")


def plain(a, b):
    return a + b


def keep(token, value):
    return token.value + value


counter = 1


class Tally:
    def __init__(self):
        self.total = 1
        self.items = [0, 0]


def targets():
    global counter
    out = []
    tally = Tally()
    tally.total += fetch(2)
    out.append(tally.total)
    tally.items[1] += fetch(2)
    out.append(tally.items)
    counter += fetch(2)
    out.append(counter)
    table = {}
    table[fetch(1)] = "v"
    out.append(table)
    xs = [1, 2]
    out.append([*xs, fetch(1)])
    a = 3
    if plain(a, fetch(1)) > 5:
        out.append("big")
    n = 0
    while plain(1, fetch(n)) < 25:
        n = n + 1
    out.append(n)
    for item in plain([a], [fetch(1)]):
        out.append(item)
    return out


def shapes():
    out = []
    totals = G
    totals.append(fetch(1))
    out.append(list(totals))
    items = [5]
    items.append(fetch(1))
    out.append(items)
    a = 3
    out.append(plain(a, fetch(1)))
    box = Box()
    box.put(7)
    out.append(box.put(fetch(1)))
    out.append([a, fetch(1)])
    k = "k"
    out.append({k: fetch(1)})
    xs = [0, 1, 2]
    out.append(xs[fetch(0) + 1])
    out.append(plain(a, plain(a, fetch(1))))
    seen = set()
    seen.add(fetch(1))
    out.append(sorted(seen))
    count = 1
    count += fetch(2)
    out.append(count)
    out.append(len(totals) + fetch(5))
    return out


def finalizers():
    keep(Token(1), fetch(1))
    gc.collect()
    after_normal = list(events)
    try:
        keep(Token(2), fail())
    except ValueError:
        pass
    gc.collect()
    return [after_normal, list(events)]


def main():
    first = vt.spawn(shapes)
    second = vt.spawn(finalizers)
    third = vt.spawn(targets)
    vt.run(1, 256)
    for value in vt.result(first):
        print(value)
    print(vt.result(second))
    print(vt.result(third))


main()
'''

EXPECTED = (
    "[1, 10]\n"
    "[5, 10]\n"
    "13\n"
    "2\n"
    "[3, 10]\n"
    "{'k': 10}\n"
    "1\n"
    "16\n"
    "[10]\n"
    "21\n"
    "52\n"
    "[['drop1'], ['drop1', 'drop2']]\n"
    "[21, [0, 20], 21, {10: 'v'}, [1, 2, 10], 'big', 3, 3, 10]\n"
)


@pytest.mark.integration
def test_operands_before_a_park_keep_their_values(tmp_path: Path) -> None:
    source = tmp_path / "parking_operands.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "parking_operands"
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
        assert run.stdout == EXPECTED, (backend, run.stdout)
