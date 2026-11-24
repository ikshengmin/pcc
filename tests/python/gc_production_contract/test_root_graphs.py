"""Generator, dictionary and set roots survive explicit GC."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest


_PROGRAMS = {
    # suspended generator frame local must survive gc.collect (frame roots)
    "generator_frame": (
        "import gc\n"
        "def gen():\n"
        "    obj = [10, 20, 30]\n"
        "    yield 0\n"
        "    yield obj[0] + obj[1] + obj[2]\n"
        "def main():\n"
        "    g = gen()\n"
        "    next(g)\n"
        "    gc.collect()\n"
        "    print(next(g))\n"
        "main()\n",
        "60",
    ),
    # nested dict -> list -> instance graph survives + stays mutable
    "container_graph": (
        "import gc\n"
        "class N:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "def main():\n"
        "    d = {'k': [N(1), N(2)]}\n"
        "    gc.collect()\n"
        "    d['k'].append(N(3))\n"
        "    gc.collect()\n"
        "    print(sum(x.v for x in d['k']))\n"
        "main()\n",
        "6",
    ),
    # set of custom-hash/eq instances survives gc.collect
    "set_graph": (
        "import gc\n"
        "class N:\n"
        "    def __init__(self, v):\n"
        "        self.v = v\n"
        "    def __hash__(self):\n"
        "        return self.v\n"
        "    def __eq__(self, o):\n"
        "        return self.v == o.v\n"
        "def main():\n"
        "    s = set()\n"
        "    s.add(N(1))\n"
        "    s.add(N(2))\n"
        "    gc.collect()\n"
        "    print(sum(x.v for x in s))\n"
        "main()\n",
        "3",
    ),
}


@pytest.mark.parametrize("name", list(_PROGRAMS))
def test_root_graph_survives_gc(
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
