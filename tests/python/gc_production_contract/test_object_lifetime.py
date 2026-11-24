"""Object lifetime, cycle reclamation and reachable containers."""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAM = (
    "import gc\n"
    "class Node:\n"
    "    def __init__(self, name):\n"
    "        self.name = name\n"
    "        self.ref = None\n"
    "def main():\n"
    "    a = Node('a')\n"
    "    print(a.name)\n"
    "    x = Node('x')\n"
    "    y = Node('y')\n"
    "    x.ref = y\n"
    "    y.ref = x\n"
    "    x = None\n"
    "    y = None\n"
    "    gc.collect()\n"
    "    print('cycle-collected')\n"
    "    box = [Node('n0'), [Node('n1')]]\n"
    "    gc.collect()\n"
    "    print(box[0].name, box[1][0].name)\n"
    "main()\n"
)
_EXPECTED = ["a", "cycle-collected", "n0 n1"]


@pytest.fixture(scope="module")
def _lifetime_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_lifetime")
    src = tmp / "lifetime.py"
    src.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp / "lifetime_bin"
    env = os.environ.copy()
    build = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--ir-scaffold=on", str(src), "-o", str(exe)],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return str(exe)


def test_object_lifetime_contract(_lifetime_exe):
    env = os.environ.copy()
    r = subprocess.run([_lifetime_exe], text=True, capture_output=True,
                       timeout=60, env=env)
    assert r.returncode == 0, f"rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:3] == _EXPECTED, r.stdout
