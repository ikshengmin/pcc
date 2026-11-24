"""Reachable roots survive collection during worker allocation and churn."""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAMS = {
    "root_survives_concurrent_alloc": (
        "import gc\n"
        "import threading\n"
        "shared = []\n"
        "def worker():\n"
        "    i = 0\n"
        "    while i < 50:\n"
        "        shared.append([i, i + 1])\n"
        "        i = i + 1\n"
        "def main():\n"
        "    keep = [10, 20, 30]\n"
        "    t = threading.Thread(target=worker)\n"
        "    t.start()\n"
        "    gc.collect()\n"
        "    t.join()\n"
        "    print(keep[0] + keep[1] + keep[2], len(shared))\n"
        "main()\n",
        "60 50",
    ),
    "root_survives_concurrent_churn": (
        "import gc\n"
        "import threading\n"
        "def worker():\n"
        "    i = 0\n"
        "    while i < 200:\n"
        "        junk = [i, i, i]\n"
        "        junk = 0\n"
        "        i = i + 1\n"
        "def main():\n"
        "    keep = [10, 20, 30]\n"
        "    t = threading.Thread(target=worker)\n"
        "    t.start()\n"
        "    j = 0\n"
        "    while j < 5:\n"
        "        gc.collect()\n"
        "        j = j + 1\n"
        "    t.join()\n"
        "    print(keep[0] + keep[1] + keep[2])\n"
        "main()\n",
        "60",
    ),
}


@pytest.fixture(scope="module")
def _exes(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_threaded")
    env = os.environ.copy()
    built = {}
    for name, (src, _expected) in _PROGRAMS.items():
        p = tmp / f"{name}.py"
        p.write_text(src, encoding="utf-8")
        exe = tmp / f"{name}_bin"
        b = subprocess.run(
            ["uv", "run", "pcc", "--backend", "self", "--ir-scaffold=on", str(p), "-o", str(exe)],
            text=True, capture_output=True, timeout=420, env=env,
        )
        assert b.returncode == 0, f"{name}: {b.stdout + b.stderr}"
        built[name] = str(exe)
    return built


@pytest.mark.parametrize("name", list(_PROGRAMS))
def test_threaded_root_survives_gc(_exes, name):
    expected = _PROGRAMS[name][1]
    env = os.environ.copy()
    r = subprocess.run([_exes[name]], text=True, capture_output=True, timeout=60, env=env)
    assert r.returncode == 0, f"{name} rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:1] == [expected], f"{name}: {r.stdout!r}"
