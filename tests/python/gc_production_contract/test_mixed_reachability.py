"""Reclaim unreachable cycles without touching reachable objects at scale."""
from __future__ import annotations
import os
import subprocess

import pytest

_PROGRAMS = {
    # reachable chain survives intact; unreachable cycle is reclaimed
    "mixed_boundary": (
        "import gc\n"
        "collected = []\n"
        "class N:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "        self.link = None\n"
        "    def __del__(self):\n"
        "        collected.append(self.v)\n"
        "def main():\n"
        "    keep = N(1)\n"
        "    keep.link = N(2)\n"
        "    x = N(100)\n"
        "    y = N(200)\n"
        "    x.link = y\n"
        "    y.link = x\n"
        "    x = 0\n"
        "    y = 0\n"
        "    gc.collect()\n"
        "    print(keep.v + keep.link.v, sorted(collected))\n"
        "main()\n",
        "3 [100, 200]",
    ),
    # 200 reachable survive + 200 unreachable self-cycles reclaimed (scale)
    "large_graph": (
        "import gc\n"
        "class N:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "def main():\n"
        "    items = []\n"
        "    i = 0\n"
        "    while i < 200:\n"
        "        items.append(N(i))\n"
        "        i = i + 1\n"
        "    j = 0\n"
        "    while j < 200:\n"
        "        junk = N(-1)\n"
        "        junk.self = junk\n"
        "        junk = 0\n"
        "        j = j + 1\n"
        "    gc.collect()\n"
        "    total = 0\n"
        "    k = 0\n"
        "    while k < len(items):\n"
        "        total = total + items[k].v\n"
        "        k = k + 1\n"
        "    print(total)\n"
        "main()\n",
        "19900",
    ),
}


@pytest.fixture(scope="module")
def _exes(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gc_mixed")
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
def test_collection_boundary_and_scale(_exes, name):
    expected = _PROGRAMS[name][1]
    env = os.environ.copy()
    r = subprocess.run([_exes[name]], text=True, capture_output=True, timeout=60, env=env)
    assert r.returncode == 0, f"{name} rc={r.returncode}: {r.stderr.strip()[:200]}"
    assert r.stdout.splitlines()[:1] == [expected], f"{name}: {r.stdout!r}"
