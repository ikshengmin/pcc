"""Reachable tuples, cycles and mixed containers survive explicit GC."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest


_PROGRAMS = {
    # tuple holding instances is a frame root; elements survive gc.collect
    "tuple_root": (
        "import gc\n"
        "class N:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "def main():\n"
        "    t = (N(1), N(2), N(3))\n"
        "    gc.collect()\n"
        "    print(t[0].v + t[1].v + t[2].v)\n"
        "main()\n",
        "6",
    ),
    # a reachable self-referential cycle must survive (only UNreachable cycles
    # are reclaimable)
    "reachable_cycle": (
        "import gc\n"
        "class N:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "        self.link = None\n"
        "def main():\n"
        "    a = N(10)\n"
        "    b = N(20)\n"
        "    a.link = b\n"
        "    b.link = a\n"
        "    root = a\n"
        "    gc.collect()\n"
        "    print(root.link.v + root.link.link.v)\n"
        "main()\n",
        "30",
    ),
    # deeply nested dict -> list -> tuple -> instance survives + traversable
    "deep_nest": (
        "import gc\n"
        "class N:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "def main():\n"
        "    d = {'k': [(N(5), N(6)), [N(7)]]}\n"
        "    gc.collect()\n"
        "    inner = d['k']\n"
        "    print(inner[0][0].v + inner[0][1].v + inner[1][0].v)\n"
        "main()\n",
        "18",
    ),
}


@pytest.mark.parametrize("name", list(_PROGRAMS))
def test_container_graph_survives_gc(
    tmp_path, name, pcc_runtime_archive, python_program_compiler,
):
    program, expected = _PROGRAMS[name]
    source = tmp_path / f"{name}.py"
    source.write_text(program, encoding="utf-8")
    ref = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=30,
    )
    assert (ref.returncode, ref.stdout, ref.stderr) == (0, expected + "\n", "")
    binary = tmp_path / f"{name}_bin"
    python_program_compiler(
        str(source), str(binary), backend="self", runtime_archive=str(pcc_runtime_archive),
    )
    native = subprocess.run(
        [str(binary)], text=True, capture_output=True, timeout=60,
        env=dict(os.environ, PATH="/nonexistent"),
    )
    assert (native.returncode, native.stdout, native.stderr) == (0, ref.stdout, "")
