"""A container literal roots its own fresh container without counting it.

List, tuple and dict literals allocate their container, then evaluate and
store the elements while the container sits in a temporary frame root. That
root used to pin and count the container (unpin of the slot's previous
value, pin, pcc_gc_store_root; then a reload, a clearing pcc_gc_store_root
and an unpin): two atomic count updates and four pin updates per literal
under GC0. The container is the literal's own fresh allocation: in container
mode (pcc_gc_list_container_mode), where no object a running thread names
moves, the literal's reference keeps it alive and a tracing mark reaches it
through the registered slot or the stopped stack, so the root is a plain
store (pcc_gc_temp_root_enter_fresh / pcc_gc_temp_root_leave_fresh); every
other mode keeps the counted, pinned root. Every clear of such a slot -- the
literal's own, an error cleanup's, a function exit's -- takes the matching
leave.

The run test builds literals whose elements allocate and collect, abandons a
literal half built when an element raises, and compares with CPython on all
five collectors.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


PROGRAM = (
    "import gc\n"
    "\n"
    "class Box:\n"
    "    def __init__(self, v):\n"
    "        self.v = v\n"
    "\n"
    "def churn(tag):\n"
    "    junk = [[k, str(k)] for k in range(1500)]\n"
    "    gc.collect()\n"
    "    return Box(tag + len(junk) - 1500)\n"
    "\n"
    "def build():\n"
    "    return (churn(1), churn(2), [churn(3), churn(4)],\n"
    "            {'k': churn(5), 'j': (churn(6),)})\n"
    "\n"
    "def run():\n"
    "    total = 0\n"
    "    i = 0\n"
    "    while i < 12:\n"
    "        t = build()\n"
    "        total += t[0].v + t[1].v + t[2][0].v + t[2][1].v\n"
    "        total += t[3]['k'].v + t[3]['j'][0].v\n"
    "        i += 1\n"
    "    return total\n"
    "\n"
    "def boom():\n"
    "    raise ValueError('half built')\n"
    "\n"
    "def abandoned():\n"
    "    count = 0\n"
    "    i = 0\n"
    "    while i < 20:\n"
    "        try:\n"
    "            pair = (churn(i), boom(), churn(i + 1))\n"
    "        except ValueError:\n"
    "            count += 1\n"
    "        try:\n"
    "            row = [churn(i), {'x': boom()}]\n"
    "        except ValueError:\n"
    "            count += 1\n"
    "        i += 1\n"
    "    return count\n"
    "\n"
    "def early(flag):\n"
    "    items = [Box(1), Box(2)]\n"
    "    if flag:\n"
    "        return len(items)\n"
    "    return (items[0].v, [items[1].v])\n"
    "\n"
    "print(run())\n"
    "print(abandoned())\n"
    "print(early(True), early(False))\n"
    "gc.collect()\n"
    "print('done')\n"
)

TUPLE_FUNCTION = (
    "def make(a: int, b: int):\n"
    "    return (a, b, [a, b], {'a': a})\n"
    "\n"
    "print(make(1, 2))\n"
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


def test_literal_containers_take_the_fresh_root_protocol(tmp_path):
    _, ll_path = _compile(tmp_path, "make", TUPLE_FUNCTION, emit_llvm=True)
    text = ll_path.read_text(encoding="utf-8")
    body = re.search(
        r"define [^\n]*@user_[a-z0-9_]*make\([^\n]*\{\n(.*?)\n\}", text, re.S
    ).group(1)
    # The tuple and the dict are rooted and cleared through the fresh
    # protocol (the two-item list is built by its slot-call constructor,
    # which needs no temporary root).
    assert body.count("@pcc_gc_temp_root_enter_fresh(") == 2
    for container in ("tuple", "dict"):
        assert re.search(
            r"@pcc_gc_temp_root_enter_fresh\(ptr %" + container + r"\.tmp\.root", body
        ), container
        assert re.search(
            r"@pcc_gc_temp_root_leave_fresh\(ptr %" + container + r"\.tmp\.root", body
        ), container
    # No counted root store or pin of a literal's own container remains.
    for container in ("tuple", "list", "dict"):
        assert not re.search(
            r"@pcc_gc_store_root\(ptr %" + container + r"\.tmp\.root", body
        ), container


def test_literal_fresh_roots_match_cpython_on_every_backend(tmp_path):
    src, exe = _compile(tmp_path, "literals", PROGRAM, emit_llvm=False)
    expected = subprocess.run(
        [sys.executable, str(src)], text=True, capture_output=True, timeout=120, env=_env()
    )
    assert expected.returncode == 0, expected.stderr
    for backend in ("0", "1", "2", "3", "4"):
        env = _env()
        env["PCC_GC_BACKEND"] = backend
        run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=180, env=env)
        assert run.returncode == 0, (backend, run.stderr)
        assert run.stdout == expected.stdout, (backend, run.stdout, expected.stdout)
