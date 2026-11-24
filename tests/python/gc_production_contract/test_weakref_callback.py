"""Collection invalidates the weakref and dispatches exactly one callback."""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAM = (
    "import gc\n"
    "import weakref\n"
    "log = []\n"
    "def cb(ref):\n"
    "    log.append(1)\n"
    "class T:\n"
    "    def __init__(self, v):\n"
    "        self.v = v\n"
    "def main():\n"
    "    t = T(5)\n"
    "    r = weakref.ref(t, cb)\n"
    "    t = 0\n"
    "    gc.collect()\n"
    "    print(len(log), r() is None)\n"
    "main()\n"
)
_EXPECTED = "1 True"


@pytest.fixture(scope="module")
def _exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_wrcb")
    src = tmp / "wrcb.py"
    src.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp / "wrcb_bin"
    env = os.environ.copy()
    b = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--ir-scaffold=on", str(src), "-o", str(exe)],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert b.returncode == 0, b.stdout + b.stderr
    return str(exe)


def test_weakref_callback_fires_on_collection(_exe):
    env = os.environ.copy()
    r = subprocess.run([_exe], text=True, capture_output=True, timeout=60, env=env)
    assert r.returncode == 0, f"rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:1] == [_EXPECTED], f"{r.stdout!r}"
