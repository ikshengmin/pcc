"""min()/max() of a managed int container keep Python integers exact.

Python library reference, built-in functions ``max``/``min`` (an empty
iterable without ``default`` raises ValueError) and the language reference,
3.2 (integers have unlimited range). A ``list[int]`` or ``tuple[int, ...]``
stores Python int objects: the element annotation bounds nothing, so the
i64-accumulator fold, which narrowed every element through
``py_int_to_i64_lane``, raised OverflowError for a big element and returned 0
for an empty list. Such containers -- and a user iterator, whose elements are
unknown -- now go through ``py_obj_min_max``, which compares the objects and
raises CPython's ValueError; only declared ``pcc.i64`` elements keep the fold.
The run test compares every shape with CPython on all five collectors.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROGRAM = (
    "def above(items: list[int]) -> bool:\n"
    "    return max(items) > (1 << 62)\n"
    "\n"
    "def low_bits(items: list[int]) -> int:\n"
    "    m = min(items)\n"
    "    return m & 7\n"
    "\n"
    "def pair_extremes(pair: tuple[int, int]) -> int:\n"
    "    return max(pair) - min(pair)\n"
    "\n"
    "def empty(items: list[int]) -> str:\n"
    "    try:\n"
    "        return str(max(items))\n"
    "    except ValueError as exc:\n"
    "        return 'ValueError: ' + str(exc)\n"
    "\n"
    "class Values:\n"
    "    def __init__(self, vals):\n"
    "        self.vals = vals\n"
    "    def __iter__(self):\n"
    "        self.i = 0\n"
    "        return self\n"
    "    def __next__(self):\n"
    "        if self.i >= len(self.vals):\n"
    "            raise StopIteration\n"
    "        v = self.vals[self.i]\n"
    "        self.i += 1\n"
    "        return v\n"
    "\n"
    "def main() -> None:\n"
    "    big = (1 << 100) + 5\n"
    "    print(above([1, big]), above([1, 2]))\n"
    "    print(low_bits([big, big + 1]), low_bits([-big, 3]))\n"
    "    print(pair_extremes((big, -big)), pair_extremes((3, 9)))\n"
    "    print(max([3, -big, 7]), min([3, -big, 7]))\n"
    "    print(empty([]), empty([4]))\n"
    "    print(max(Values([3, big, 2])), min(Values([3, -big, 2])))\n"
    "    print(max(Values(['b', 'c', 'a'])), min(Values(['b', 'c', 'a'])))\n"
    "\n"
    "main()\n"
)


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    return env


def test_min_max_of_int_containers_match_cpython_on_every_backend(tmp_path):
    src = tmp_path / "min_max_ints.py"
    src.write_text(PROGRAM, encoding="utf-8")
    exe = tmp_path / "min_max_ints.bin"
    built = subprocess.run(
        ["uv", "run", "pcc", "--backend", "self", "--ir-scaffold=on", str(src), "-o", str(exe)],
        text=True, capture_output=True, timeout=600, env=_env(),
    )
    assert built.returncode == 0, built.stderr
    expected = subprocess.run(
        [sys.executable, str(src)], text=True, capture_output=True, timeout=120, env=_env()
    )
    assert expected.returncode == 0, expected.stderr
    for backend in ("0", "1", "2", "3", "4"):
        env = _env()
        env["PCC_GC_BACKEND"] = backend
        run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=120, env=env)
        assert run.returncode == 0, (backend, run.stderr)
        assert run.stdout == expected.stdout, (backend, run.stdout, expected.stdout)
