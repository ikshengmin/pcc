"""A nested collect from a finalizer does not disrupt the outer collect."""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAMS = {
    # finalizers on plain (non-cycle) objects re-enter gc.collect
    "plain_reentrant": (
        "import gc\n"
        "done = []\n"
        "class Fin:\n"
        "    def __init__(self, tag):\n"
        "        self.tag = tag\n"
        "    def __del__(self):\n"
        "        done.append(self.tag)\n"
        "        gc.collect()\n"
        "def main():\n"
        "    f1 = Fin(1)\n"
        "    f2 = Fin(2)\n"
        "    f1 = 0\n"
        "    f2 = 0\n"
        "    gc.collect()\n"
        "    print(sorted(done))\n"
        "main()\n",
        "[1, 2]",
    ),
    # finalizers on members of an unreachable CYCLE re-enter gc.collect DURING
    # the outer sweep — the case that segfaulted before the reentrancy guard
    "cycle_reentrant": (
        "import gc\n"
        "done = []\n"
        "class Fin:\n"
        "    def __init__(self, tag):\n"
        "        self.tag = tag\n"
        "        self.peer = None\n"
        "    def __del__(self):\n"
        "        done.append(self.tag)\n"
        "        gc.collect()\n"
        "def main():\n"
        "    a = Fin(1)\n"
        "    b = Fin(2)\n"
        "    a.peer = b\n"
        "    b.peer = a\n"
        "    a = 0\n"
        "    b = 0\n"
        "    gc.collect()\n"
        "    print(sorted(done))\n"
        "main()\n",
        "[1, 2]",
    ),
}


@pytest.fixture(scope="module")
def _exes(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_reentrancy")
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
def test_gc_collect_reentrancy_is_safe(_exes, name):
    expected = _PROGRAMS[name][1]
    env = os.environ.copy()
    r = subprocess.run([_exes[name]], text=True, capture_output=True, timeout=60, env=env)
    assert r.returncode == 0, f"{name} rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:1] == [expected], f"{name}: {r.stdout!r}"
