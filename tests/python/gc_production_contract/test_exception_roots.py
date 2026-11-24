"""A caught exception retains its message through explicit collection."""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAM = (
    "import gc\n"
    "def boom(payload):\n"
    "    raise ValueError(payload)\n"
    "def main():\n"
    "    saved = None\n"
    "    try:\n"
    "        boom([1, 2, 3])\n"
    "    except ValueError as e:\n"
    "        saved = e\n"
    "    gc.collect()\n"
    "    print(str(saved))\n"
    "main()\n"
)
_EXPECTED = "[1, 2, 3]"


@pytest.fixture(scope="module")
def _exc_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_exc")
    src = tmp / "exc.py"
    src.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp / "exc_bin"
    env = os.environ.copy()
    b = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--ir-scaffold=on", str(src), "-o", str(exe)],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert b.returncode == 0, b.stdout + b.stderr
    return str(exe)


def test_exception_referents_survive_gc(_exc_exe):
    env = os.environ.copy()
    r = subprocess.run([_exc_exe], text=True, capture_output=True, timeout=60, env=env)
    assert r.returncode == 0, f"rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:1] == [_EXPECTED], r.stdout
