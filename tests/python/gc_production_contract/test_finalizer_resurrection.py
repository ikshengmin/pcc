"""PEP 442: a finalizer can make its object reachable again."""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAM = (
    "import gc\n"
    "keeper = []\n"
    "class R:\n"
    "    def __init__(self, v):\n"
    "        self.v = v\n"
    "        self.peer = None\n"
    "    def __del__(self):\n"
    "        keeper.append(self)\n"
    "def main():\n"
    "    a = R(42)\n"
    "    b = R(43)\n"
    "    a.peer = b\n"
    "    b.peer = a\n"
    "    a = 0\n"
    "    b = 0\n"
    "    gc.collect()\n"
    "    n = len(keeper)\n"
    "    vals = []\n"
    "    i = 0\n"
    "    while i < n:\n"
    "        vals.append(keeper[i].v)\n"
    "        i = i + 1\n"
    "    print(n, sorted(vals))\n"
    "main()\n"
)
_EXPECTED = "2 [42, 43]"


@pytest.fixture(scope="module")
def _exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_resurrect")
    src = tmp / "resurrect.py"
    src.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp / "resurrect_bin"
    env = os.environ.copy()
    b = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--ir-scaffold=on", str(src), "-o", str(exe)],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert b.returncode == 0, b.stdout + b.stderr
    return str(exe)


def test_resurrected_object_survives_gc(_exe):
    env = os.environ.copy()
    r = subprocess.run([_exe], text=True, capture_output=True, timeout=60, env=env)
    assert r.returncode == 0, f"rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:1] == [_EXPECTED], r.stdout
