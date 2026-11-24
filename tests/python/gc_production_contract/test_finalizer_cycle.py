"""PEP 442: finalizers see intact fields of unreachable cycle members."""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAM = (
    "import gc\n"
    "_log = []\n"
    "class Node:\n"
    "    def __init__(self, name):\n"
    "        self.name = name\n"
    "        self.ref = None\n"
    "    def __del__(self):\n"
    "        _log.append(self.name)\n"
    "def main():\n"
    "    a = Node('a')\n"
    "    b = Node('b')\n"
    "    a.ref = b\n"
    "    b.ref = a\n"
    "    a = None\n"
    "    b = None\n"
    "    gc.collect()\n"
    "    _log.sort()\n"
    "    print(','.join(_log))\n"          # CPython: 'a,b' (PEP 442)
    "main()\n"
)
_EXPECTED = "a,b"


@pytest.fixture(scope="module")
def _cycfin_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_cycfin")
    src = tmp / "cycfin.py"
    src.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp / "cycfin_bin"
    env = os.environ.copy()
    build = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--ir-scaffold=on", str(src), "-o", str(exe)],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return str(exe)


def test_cycle_member_finalizers_run(_cycfin_exe):
    env = os.environ.copy()
    r = subprocess.run([_cycfin_exe], text=True, capture_output=True, timeout=60, env=env)
    assert r.returncode == 0, f"rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:1] == [_EXPECTED], r.stdout
