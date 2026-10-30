"""A direct call pins only the arguments that a root does not already cover.

A direct user call passes object arguments as raw pointers. The callee stores
them in its borrowed parameter frame before its first application gate, and a
moving collector relocates only while every mutator is parked at such a gate:
runtime polls never relocate. The collector marks and rewrites that frame, and
the caller's own root slots, like any other root.

An argument read from a local or parameter root of an ordinary activation stays
alive through that slot, and nothing can rebind the slot while the callee runs.
It needs no pin of its own, unless evaluating a later argument may reach an
application gate: those arguments are pinned just before that evaluation.
Owned temporaries, globals and resumable (generator) bodies keep their pins.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from pcc.frontends.python.codegen.layer1 import L1CodeGen
from tests.python.pcc1_gate import repo_root
from tests.python.test_shared_call_binding import _emit, _function


SHAPES = '''
def leaf(a, b, c):
    return c

def make():
    return [0]

def mark(count, value):
    return value

G = [1]

def stable(a, b):
    local = [a, b]
    leaf(a, local, b)
    return leaf(local, b, a)

def later(a, b):
    return leaf(a, b, mark(make(), b))

def rebound(a, b):
    return leaf(a, (a := b), a)

def global_arg(a):
    return leaf(G, a, a)

def generator(a, b):
    yield leaf(a, b, a)
'''


def _leaf_calls(monkeypatch):
    call_user = L1CodeGen._call_user
    calls: dict[str, list] = {}

    def observe(host, function, args_ir, *args, **kwargs):
        if function.name == "user_binding_leaf":
            caller = host.current_function.name
            pinned = tuple(kwargs.get("pinned_arg_temps", ()))
            calls.setdefault(caller, []).append(
                ([str(value) for value in args_ir],
                 [(str(value), owned) for value, owned in pinned])
            )
        return call_user(host, function, args_ir, *args, **kwargs)

    monkeypatch.setattr(L1CodeGen, "_call_user", observe)
    return calls, _emit(SHAPES)


def test_direct_call_pins_only_unrooted_arguments(monkeypatch):
    calls, text = _leaf_calls(monkeypatch)

    # Parameters and an owned local, with no later argument that can reach a
    # gate: every argument is covered by a root of this activation.
    assert [pinned for _, pinned in calls["user_binding_stable"]] == [[], []]

    # ``mark(make(), b)`` may reach a gate after ``a`` and ``b`` were read:
    # they are pinned before it runs, then the owned result is pinned too.
    (arguments, pinned), = calls["user_binding_later"]
    assert pinned[:2] == [(arguments[0], False), (arguments[1], False)]
    assert [owned for _, owned in pinned] == [False, False, True]
    body = _function(text, "user_binding_later")
    make_call = re.search(r"\bcall [^\n]*@user_binding_make\(", body)
    assert make_call is not None, body
    for reference in arguments[:2]:
        pin = re.search(r"@pcc_gc_pin\(ptr " + re.escape(reference) + r"\)", body)
        assert pin is not None and pin.start() < make_call.start(), body

    # The assignment expression rebinds ``a`` after its first read.
    (arguments, pinned), = calls["user_binding_rebound"]
    assert pinned[0] == (arguments[0], False)
    assert arguments[2] not in [value for value, _ in pinned]

    # A module global can be rebound by the callee: it keeps its pin.
    (arguments, pinned), = calls["user_binding_global_arg"]
    assert pinned == [(arguments[0], False)]

    # A resumable body keeps the existing argument protocol.
    (arguments, pinned), = calls["user_binding_generator__gen_resume"]
    assert [value for value, _ in pinned] == arguments


PROGRAM = '''import gc
from pcc.extern import extern, c_int64

pcc_gc_telemetry = extern("pcc_gc_telemetry", (c_int64,), c_int64)

events = []


class Token:
    def __init__(self, label):
        self.label = label
        self.items = [label, label, label]

    def __del__(self):
        events.append(self.label)


def churn(n):
    # Allocation debt and loop gates: GC1-4 step here, GC4 relocates.
    keep = []
    i = 0
    while i < n:
        keep.append([i, str(i)])
        if len(keep) > 64:
            keep = []
        i = i + 1
    return n


def leaf(a, b, c):
    churn(3000)
    return a.label + b.label + c.label + str(len(a.items) + len(b.items) + len(c.items))


def mark(count, value):
    return value


def stable(a, b, c, sep):
    local = Token(sep)
    first = leaf(a, b, local)
    return first + sep + leaf(local, c, a)


def later(a, b):
    return leaf(a, b, mark(churn(3000), b))


def rebound(a, b):
    return leaf(a, (a := b), a)


def run():
    x = Token("x")
    y = Token("y")
    z = Token("z")
    print(stable(x, y, z, "/"))
    print(later(x, y))
    print(rebound(x, z))
    print(x.items, y.items, z.items)


run()
gc.collect()
print(sorted(events))
print(pcc_gc_telemetry(15) > 0)
'''

EXPECTED = (
    "xy/9//zx9\n"
    "xyy9\n"
    "xzz9\n"
    "['x', 'x', 'x'] ['y', 'y', 'y'] ['z', 'z', 'z']\n"
    "['/', 'x', 'y', 'z']\n"
)


@pytest.mark.integration
def test_unpinned_arguments_survive_collection_and_relocation(tmp_path: Path) -> None:
    source = tmp_path / "stable_arguments.py"
    source.write_text(PROGRAM, encoding="utf-8")
    executable = tmp_path / "stable_arguments"
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
        run = subprocess.run(
            [str(executable)], text=True, capture_output=True, timeout=120,
            env={**env, "PCC_GC_BACKEND": backend, "PCC_GC_DEBT_THRESHOLD": "4096"},
        )
        assert run.returncode == 0, (backend, run.stderr[-2000:])
        # Only the relocating backend forwards objects; it must have moved
        # some while the arguments above were held by callee frames.
        relocated = "True\n" if backend == "4" else "False\n"
        assert run.stdout == EXPECTED + relocated, (backend, run.stdout)
