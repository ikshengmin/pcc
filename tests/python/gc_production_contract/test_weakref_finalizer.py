"""Weakref resolution, intermediate lifetime and finalizer-once contracts."""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAM = (
    "import gc\n"
    "import weakref\n"
    "class Obj:\n"
    "    def __init__(self, n):\n"
    "        self.n = n\n"
    "_dead = []\n"
    "class Fin:\n"
    "    def __del__(self):\n"
    "        _dead.append(1)\n"
    "def main():\n"
    "    o = Obj(7)\n"
    "    r = weakref.ref(o)\n"
    "    print(1 if r() is o else 0)\n"          # weakref resolves while alive
    "    o = None\n"
    "    gc.collect()\n"
    "    print(1 if r() is None else 0)\n"        # weakref invalidated after collect
    "    f = Fin()\n"
    "    f = None\n"
    "    gc.collect()\n"
    "    print(len(_dead))\n"                      # __del__ ran exactly once
    "main()\n"
)
_EXPECTED = ["1", "1", "1"]

_ATTR_PROGRAM = (
    "import gc\n"
    "import weakref\n"
    "class Obj:\n"
    "    def __init__(self, n):\n"
    "        self.n = n\n"
    "def main():\n"
    "    o = Obj(9)\n"
    "    r = weakref.ref(o)\n"
    "    print(r().n)\n"
    "    o = None\n"
    "    gc.collect()\n"
    "    print(1 if r() is None else 0)\n"
    "main()\n"
)
_ATTR_EXPECTED = ["9", "1"]



@pytest.fixture(scope="module")
def _wf_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_wf")
    src = tmp / "wf.py"
    src.write_text(_PROGRAM, encoding="utf-8")
    exe = tmp / "wf_bin"
    env = os.environ.copy()
    build = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--ir-scaffold=on", str(src), "-o", str(exe)],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return str(exe)


@pytest.fixture(scope="module")
def _wf_attr_exe(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_wf_attr")
    src = tmp / "wf_attr.py"
    src.write_text(_ATTR_PROGRAM, encoding="utf-8")
    exe = tmp / "wf_attr_bin"
    env = os.environ.copy()
    build = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--ir-scaffold=on", str(src), "-o", str(exe)],
        text=True, capture_output=True, timeout=420, env=env,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    return str(exe)


def test_weakref_finalizer_contract(_wf_exe):
    env = os.environ.copy()
    r = subprocess.run([_wf_exe], text=True, capture_output=True, timeout=60, env=env)
    assert r.returncode == 0, f"rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:3] == _EXPECTED, r.stdout


def test_weakref_call_intermediate_attr_releases_temp(_wf_attr_exe):
    env = os.environ.copy()
    r = subprocess.run(
        [_wf_attr_exe],
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert r.returncode == 0, f"rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:2] == _ATTR_EXPECTED, r.stdout
