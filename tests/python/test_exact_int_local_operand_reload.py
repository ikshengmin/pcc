"""A plain local left operand is reloaded from its own slot, not re-rooted.

``total += pair[1]`` evaluated ``total``, copied it into a temporary root
(unpin, pin, store_root), lowered the subscript, reloaded the root three
times, cleared it and pinned the value again: about ten runtime calls per
statement around an operand its own frame slot already roots. A function
local's slot is a registered root that a moving collector updates, and only a
walrus can rebind a local inside an expression, so the operand is now loaded
again after the right operand (exact_int_lowering
``_exact_int_lhs_reloads_from_local``). Python's evaluation order stays: the
left operand is still read (and an unbound one raises) before the right one
runs.

The IR test pins the shape; the run tests pin the semantics against CPython
on every collector: tagged and big left operands across a right operand that
allocates and collects, a walrus rebinding the left operand inside the right
one, comparisons, and an unbound left operand whose right operand must not
run.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


LOOP = (
    "def main() -> None:\n"
    "    pairs = [(1, 2), (3, 4)]\n"
    "    total = 0\n"
    "    i = 0\n"
    "    while i < 1000:\n"
    "        pair = pairs[i % 2]\n"
    "        total += pair[1]\n"
    "        i += 1\n"
    "    print(total)\n"
    "\n"
    "main()\n"
)

SEMANTICS = (
    "import gc\n"
    "\n"
    "def churn(n: int) -> int:\n"
    "    junk = [[k, k + 1, str(k)] for k in range(3000)]\n"
    "    gc.collect()\n"
    "    return n + len(junk) - 3000\n"
    "\n"
    "def big() -> int:\n"
    "    total = 10 ** 30\n"
    "    total += churn(7)\n"
    "    total -= churn(2)\n"
    "    total *= churn(3)\n"
    "    return total\n"
    "\n"
    "def tagged() -> int:\n"
    "    total = 5\n"
    "    k = 0\n"
    "    while k < 20:\n"
    "        total += churn(k)\n"
    "        k += 1\n"
    "    return total\n"
    "\n"
    "def walrus() -> int:\n"
    "    total = 100\n"
    "    total += (total := 7) + churn(0)\n"
    "    other = 3\n"
    "    result = other + [other := 40][0]\n"
    "    return total * 1000 + result * 10 + other\n"
    "\n"
    "def compare() -> int:\n"
    "    limit = 10 ** 25\n"
    "    hits = 0\n"
    "    if limit > churn(5):\n"
    "        hits += 1\n"
    "    small = 4\n"
    "    if small < churn(9):\n"
    "        hits += 10\n"
    "    if small == churn(4):\n"
    "        hits += 100\n"
    "    return hits\n"
    "\n"
    "calls = []\n"
    "\n"
    "def noted() -> int:\n"
    "    calls.append(1)\n"
    "    return 1\n"
    "\n"
    "def unbound(flag: int) -> str:\n"
    "    if flag > 0:\n"
    "        value = 1\n"
    "    try:\n"
    "        value += noted()\n"
    "    except UnboundLocalError:\n"
    "        return 'unbound ' + str(len(calls))\n"
    "    return 'bound ' + str(value) + ' ' + str(len(calls))\n"
    "\n"
    "print(big())\n"
    "print(tagged())\n"
    "print(walrus())\n"
    "print(compare())\n"
    "print(unbound(0))\n"
    "print(unbound(1))\n"
)


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    return env


def _compile(tmp_path: Path, name: str, source: str, *, emit_llvm: bool):
    src = tmp_path / f"{name}.py"
    src.write_text(source, encoding="utf-8")
    out = tmp_path / (f"{name}.ll" if emit_llvm else f"{name}.bin")
    cmd = ["uv", "run", "pcc", "--backend", "self", "--ir-scaffold=on"]
    cmd += [f"--emit-llvm={out}", str(src)] if emit_llvm else [str(src), "-o", str(out)]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=420, env=_env())
    assert proc.returncode == 0, proc.stderr
    return src, out


def _function_body(ll: str, name: str) -> str:
    m = re.search(
        r"define [^\n]*@user_[a-z0-9_]*" + name + r"\([^\n]*\{\n(.*?)\n\}", ll, re.S
    )
    assert m, f"{name} not found"
    return m.group(1)


def test_local_left_operand_takes_no_temporary_root(tmp_path):
    _, ll_path = _compile(tmp_path, "loop", LOOP, emit_llvm=True)
    body = _function_body(ll_path.read_text(encoding="utf-8"), "main")
    # The subscript right operand used to park ``total`` in a temporary
    # root named for the exact-int left operand.
    assert "exact.int.lhs.rooted" not in body
    assert "exact.int.lhs.root.ptr" not in body
    # No root slot of this function is a temporary for ``total``: the
    # only frame entries are the function's own locals and owned roots.
    assert "@pcc_gc_frame_enter_lifo" not in body


def test_reloaded_local_operands_match_cpython_on_every_backend(tmp_path):
    src, exe = _compile(tmp_path, "semantics", SEMANTICS, emit_llvm=False)
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
