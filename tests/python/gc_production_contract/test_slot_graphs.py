"""Pointer-bearing object slots survive explicit GC."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest


_PROGRAMS = {
    "slot_reachable_cycle": (
        "import gc\n"
        "class N:\n"
        "    __slots__ = ('v', 'link')\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "        self.link = None\n"
        "def main():\n"
        "    a = N(1)\n"
        "    b = N(2)\n"
        "    a.link = b\n"
        "    b.link = a\n"
        "    gc.collect()\n"
        "    print(a.link.v + b.link.v)\n"
        "main()\n",
        "3",
    ),
    "slot_mixed_boundary": (
        "import gc\n"
        "collected = []\n"
        "class N:\n"
        "    __slots__ = ('v', 'link')\n"
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
        "    x = None\n"
        "    y = None\n"
        "    gc.collect()\n"
        "    print(keep.v + keep.link.v, sorted(collected))\n"
        "main()\n",
        "3 [100, 200]",
    ),
}


@pytest.mark.parametrize("name", list(_PROGRAMS))
def test_slot_graph_survives_gc(
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
