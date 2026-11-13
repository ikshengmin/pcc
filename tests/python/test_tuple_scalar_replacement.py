"""Function-local tuples that never escape live in hidden locals.

Specification: the Python language reference, 6.2.3 (parenthesized forms /
tuple displays: elements are evaluated left to right before the tuple
exists), 6.3.2 (subscriptions), 7.2 (assignment: an unpacking binds its
targets left to right) and 4.2.2 (UnboundLocalError). pcc's
tuple_scalar_replacement rewrites a local bound only to tuple literals of one
arity and read only through constant indexes or unpacking: no tuple is
allocated, and nothing a program can observe changes. The transform tests
pin which locals qualify; the run test compares every shape with CPython on
all five collectors, including the shapes that must stay tuples.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from pcc.frontends.python.py_lift import parse_and_lift


def _hidden_names(source: str) -> set[str]:
    from pcc.frontends.python.type_infer import infer_module

    module = infer_module(parse_and_lift(source, "probe.py", "probe"))
    names: set[str] = set()
    pending = list(module.body)
    while pending:
        node = pending.pop()
        ident = getattr(node, "ident", None)
        if isinstance(ident, str) and ident.startswith("__pcc_tuple_"):
            names.add(ident)
        for value in getattr(node, "__dict__", {}).values():
            if isinstance(value, (list, tuple)):
                pending.extend(item for item in value if hasattr(item, "__dict__"))
            elif hasattr(value, "__dict__"):
                pending.append(value)
    return names


def test_a_local_read_only_by_constant_index_is_replaced():
    names = _hidden_names(
        "def f(n: int) -> int:\n"
        "    total = 0\n"
        "    i = 0\n"
        "    while i < n:\n"
        "        pair = (i, 1)\n"
        "        total += pair[1] + pair[-2]\n"
        "        i += 1\n"
        "    return total\n"
    )
    assert names == {"__pcc_tuple_pair_0", "__pcc_tuple_pair_1"}


def test_escaping_or_ambiguous_locals_stay_tuples():
    for body in (
        "    t = (1, 2)\n    return t\n",                       # escapes
        "    t = (1, 2)\n    g = lambda: t[0]\n    return g()\n",  # captured
        "    t = (1, 2)\n    return t[2]\n",                    # IndexError
        "    t = (1, 2)\n    t[0] = 3\n    return 0\n",          # TypeError
        "    t = (1, 2)\n    del t\n    return 0\n",             # deleted
        "    if n:\n        t = (1, 2)\n    return t[0]\n",       # maybe unbound
        "    t = (1, 2)\n    t = (1, 2, 3)\n    return t[0]\n",   # two arities
        "    t = (1, 2)\n    return len(t)\n",                  # other use
        "    t = (1, 2)\n    for t in [(3, 4)]:\n        pass\n    return t[0]\n",
        "    t = (1, 'a')\n    t = ('b', 2)\n    return 0\n",     # mixed types
        "    t = (1, 2)\n    x = locals()\n    return t[0]\n",    # frame view
        "    t = (1, 2)\n    yield t[0]\n",                      # generator
    ):
        assert _hidden_names("def f(n):\n" + body) == set(), body


PROGRAM = (
    "def fib(n):\n"
    "    p = (0, 1)\n"
    "    i = 0\n"
    "    while i < n:\n"
    "        p = (p[1], p[0] + p[1])\n"
    "        i += 1\n"
    "    a, b = p\n"
    "    return a, b\n"
    "\n"
    "def boom(x):\n"
    "    if x > 2:\n"
    "        raise ValueError(x)\n"
    "    return x\n"
    "\n"
    "def keeps_old_elements_when_an_element_raises():\n"
    "    seen = []\n"
    "    t = (10, 20)\n"
    "    i = 0\n"
    "    while i < 5:\n"
    "        try:\n"
    "            t = (i, boom(i))\n"
    "        except ValueError:\n"
    "            seen.append((t[0], t[1]))\n"
    "        i += 1\n"
    "    return seen, t[0], t[1]\n"
    "\n"
    "def strings_and_objects():\n"
    "    out = []\n"
    "    for word in ['ab', 'cd', 'ef']:\n"
    "        item = (word, [word, word.upper()])\n"
    "        out.append(item[0] + item[1][1] + str(len(item[-1])))\n"
    "    return out\n"
    "\n"
    "def unpack_into_targets():\n"
    "    store = {}\n"
    "    row = ('k', 7)\n"
    "    key, store['v'] = row\n"
    "    return key, store\n"
    "\n"
    "def maybe_unbound(flag):\n"
    "    if flag:\n"
    "        t = (1, 2)\n"
    "    try:\n"
    "        return t[0]\n"
    "    except UnboundLocalError:\n"
    "        return 'unbound'\n"
    "\n"
    "def item_assignment():\n"
    "    t = (1, 2)\n"
    "    try:\n"
    "        t[0] = 3\n"
    "    except TypeError:\n"
    "        return 'immutable', t[0]\n"
    "    return 'mutated', t[0]\n"
    "\n"
    "def big_ints():\n"
    "    t = (2 ** 70, -1)\n"
    "    total = 0\n"
    "    i = 0\n"
    "    while i < 3:\n"
    "        t = (t[0] + 1, t[1] - 1)\n"
    "        total += t[0] + t[1]\n"
    "        i += 1\n"
    "    return total\n"
    "\n"
    "print(fib(30))\n"
    "print(keeps_old_elements_when_an_element_raises())\n"
    "print(strings_and_objects())\n"
    "print(unpack_into_targets())\n"
    "print(maybe_unbound(True), maybe_unbound(False))\n"
    "print(item_assignment())\n"
    "print(big_ints())\n"
)


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    return env


def test_replaced_tuples_match_cpython_on_every_backend(tmp_path):
    src = tmp_path / "tuples.py"
    src.write_text(PROGRAM, encoding="utf-8")
    exe = tmp_path / "tuples.bin"
    ll = tmp_path / "tuples.ll"
    for args in ([f"--emit-llvm={ll}", str(src)], [str(src), "-o", str(exe)]):
        built = subprocess.run(
            ["uv", "run", "pcc", "--backend", "self", "--ir-scaffold=on", *args],
            text=True, capture_output=True, timeout=600, env=_env(),
        )
        assert built.returncode == 0, built.stderr
    fib = re.search(r"define [^\n]*@user_[a-z0-9_]*fib\([^\n]*\{\n(.*?)\n\}",
                    ll.read_text(encoding="utf-8"), re.S).group(1)
    # Only the returned ``a, b`` builds a tuple; the loop's ``p`` does not.
    assert fib.count("@py_tuple_new(") == 1
    expected = subprocess.run(
        [sys.executable, str(src)], text=True, capture_output=True, timeout=120, env=_env()
    )
    assert expected.returncode == 0, expected.stderr
    for backend in ("0", "1", "2", "3", "4"):
        env = _env()
        env["PCC_GC_BACKEND"] = backend
        run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=120, env=env)
        assert run.returncode == 0, (backend, run.stderr)
        assert run.stdout == expected.stdout, (backend, run.stdout, expected.stdout)
