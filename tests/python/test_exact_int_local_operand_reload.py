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


# Python reference: expression evaluation order and lambda default values
# (Language Reference sections 6.16 and 6.14). These lambdas do not capture
# total: the default runs in the enclosing scope, and the body reads its arg.
LAMBDA_DEFAULTS = (
    "def lambda_default_add() -> None:\n"
    "    total = 10 ** 30\n"
    "    result = total + len([lambda value=(total := 7): value])\n"
    "    print(result, total)\n"
    "\n"
    "def lambda_default_compare() -> None:\n"
    "    total = 10 ** 30\n"
    "    result = total > len([lambda value=(total := 0): value])\n"
    "    print(result, total)\n"
    "\n"
    "lambda_default_add()\n"
    "lambda_default_compare()\n"
)
SEMANTICS += LAMBDA_DEFAULTS

# The right operand drops the left operand's source reference: a call
# rebinds the module global, a walrus rebinds the local. The left operand is
# still the old big integer (Language Reference 6.16), so it must outlive its
# source; GC0 frees an object at its last reference.
REBOUND_SOURCES = (
    "BIG = 0\n"
    "\n"
    "def bump_big() -> int:\n"
    "    global BIG\n"
    "    BIG = 5\n"
    "    return 1\n"
    "\n"
    "def global_rebound() -> None:\n"
    "    global BIG\n"
    "    BIG = 10 ** 30\n"
    "    added = BIG + bump_big()\n"
    "    BIG = 10 ** 30\n"
    "    greater = BIG > bump_big()\n"
    "    print(added, greater, BIG)\n"
    "\n"
    "def walrus_rebound() -> None:\n"
    "    total = 10 ** 30\n"
    "    added = total + len([(total := 7)])\n"
    "    limit = 10 ** 30\n"
    "    greater = limit > len([(limit := 0)])\n"
    "    print(added, total, greater, limit)\n"
    "\n"
    "global_rebound()\n"
    "walrus_rebound()\n"
)
SEMANTICS += REBOUND_SOURCES


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


def test_rebinding_scan_visits_lambda_defaults_and_comprehension_filters():
    from pcc.frontends.python.codegen.exact_int_lowering import (
        ExactIntLoweringMixin,
        _exact_int_expr_rebinds_name,
    )
    from pcc.frontends.python.py_lift import parse_and_lift

    host = ExactIntLoweringMixin()
    host.env = {"total": None}
    host._cpy_env_flags = {}
    for source, rebinds in (
        ("len([lambda value=(total := 7): value])", True),
        ("len([item for item in (1,) if (total := 7)])", True),
        ("pick(value=(total := 7))", True),
        ("len([lambda value=(other := 7): value])", False),
        ("len([lambda value=7: value])", False),
        ("len([lambda value: value])", False),
    ):
        module = parse_and_lift(
            "result = total + " + source + "\n", "rebinding.py", "rebinding"
        )
        expr = module.body[0].value
        assert _exact_int_expr_rebinds_name(expr.rhs, "total") is rebinds
        assert not _exact_int_expr_rebinds_name(expr.rhs, "untouched")
        assert host._exact_int_lhs_reloads_from_local(
            expr.lhs, expr.rhs, False
        ) is (not rebinds)


def test_lambda_default_rebinding_preserves_left_operand_roots(tmp_path):
    _, ll_path = _compile(tmp_path, "lambda_defaults", LAMBDA_DEFAULTS, emit_llvm=True)
    ll = ll_path.read_text(encoding="utf-8")
    addition = _function_body(ll, "lambda_default_add")
    comparison = _function_body(ll, "lambda_default_compare")
    assert "exact.int.lhs.rooted" in addition
    assert "exact.int.compare.lhs.rooted" in comparison
