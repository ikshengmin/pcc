"""GC0 sorted() of a list or tuple orders plain values without the protocol.

The general sorted() copies its source through leased roots and compares
through a slot frame per comparison: about 17k instructions for two tuples,
twice per JSON object with sort_keys. When every comparison it needs is
between exact str, int/bool/float or tuples of those, no callback can run,
so the copy is retained under one graph transaction and sorted in place.
A comparison that may call user code abandons the private copy and the
general protocol starts over, as nothing observable has happened.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

from pcc.frontends.python.pipeline import compile_python

ROOT = Path(__file__).resolve().parents[2]
COMPARE_SOURCE = ROOT / "pcc/runtime/py/py_obj_ops_compare.py"


class Words:
    def __init__(self, values):
        self.words = {index * 8: value for index, value in enumerate(values)}

    def load(self, base, offset):
        return self.words[base + offset] if base == 0 else self.scratch[offset]

    def store(self, base, offset, value):
        if base == 0:
            self.words[offset] = value
        else:
            self.scratch[offset] = value

    def values(self, count):
        return [self.words[index * 8] for index in range(count)]


def _sorters(less):
    namespace = {"C_POINTER_SIZE": 8, "_plain_less": less}
    tree = ast.parse(COMPARE_SOURCE.read_text())
    names = {"_sorted_plain_insertion", "_sorted_plain_merge", "_sorted_plain_reverse"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == names
    code = compile(ast.Module(nodes, type_ignores=[]), str(COMPARE_SOURCE), "exec")
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            namespace[const.co_name] = types.FunctionType(const, namespace)
    return namespace


def _run(algorithm, values, less):
    words = Words(values)
    words.scratch = {}
    ns = _sorters(less)
    ns.update(
        load_ptr=words.load, store_ptr=words.store,
        ptr_add=lambda base, offset: (base, offset),
    )

    def memmove(destination, source, size):
        if isinstance(destination, tuple):
            base, offset = destination
            moved = [words.load(0, source[1] + index) for index in range(0, size, 8)]
            for index, value in zip(range(0, size, 8), moved):
                words.store(0, offset + index, value)
        else:
            for index in range(0, size, 8):
                words.store(0, index, words.scratch[index])
    ns["memmove"] = memmove
    if algorithm == "insertion":
        status = ns["_sorted_plain_insertion"](0, len(values))
    else:
        status = ns["_sorted_plain_merge"](0, 1, len(values))
    return status, words.values(len(values))


@pytest.mark.parametrize("algorithm", ["insertion", "merge"])
def test_sorts_stably_by_key(algorithm):
    values = [(key, tag) for tag, key in enumerate([5, 3, 5, 1, 3, 9, 1, 5, 0, 7, 3, 2, 8, 1, 6, 4, 9, 0, 2, 5])]
    status, ordered = _run(algorithm, values, lambda left, right: 1 if left[0] < right[0] else 0)
    assert status == 0
    assert ordered == sorted(values, key=lambda pair: pair[0])


@pytest.mark.parametrize("algorithm", ["insertion", "merge"])
@pytest.mark.parametrize("refuse_after", [0, 1, 3, 7, 15])
def test_a_refused_comparison_leaves_a_permutation(algorithm, refuse_after):
    values = [(key, tag) for tag, key in enumerate([5, 3, 5, 1, 3, 9, 1, 5, 0, 7, 3, 2, 8, 1, 6, 4, 9, 0])]
    calls = []

    def less(left, right):
        calls.append(1)
        if len(calls) > refuse_after:
            return -1
        return 1 if left[0] < right[0] else 0
    status, ordered = _run(algorithm, values, less)
    assert status == -1
    assert sorted(ordered) == sorted(values)


def test_reverse_swaps_in_place():
    words = Words([1, 2, 3, 4, 5])
    ns = _sorters(None)
    ns.update(load_ptr=words.load, store_ptr=words.store)
    ns["_sorted_plain_reverse"](0, 5)
    assert words.values(5) == [5, 4, 3, 2, 1]


PROGRAM = r'''
class Ordered:
    def __init__(self, value):
        self.value = value

    def __lt__(self, other):
        return self.value < other.value

    def __repr__(self):
        return "Ordered(%r)" % (self.value,)


def attempt(values, **options):
    try:
        return sorted(values, **options)
    except TypeError as error:
        return "TypeError: " + str(error)


def main():
    print(sorted(["profile", "notifications", "name", "plan", "", "é", "Z"]))
    print(sorted([5, -3, 10 ** 30, -(10 ** 25), 0, 7, True, False, 2.5, -0.0, 0.0]))
    print(sorted([float("inf"), 1.0, float("-inf"), -2]))
    print(sorted([("b", 2), ("a", 9), ("b", 1), ("a",), ("a", 1, 0), ()]))
    print(sorted({"profile": {"name": "Ada"}, "notifications": ["ready"]}.items()))
    print(sorted((("x", [2]), ("x", [1]), ("w", [3]))))
    print(sorted([3, 1, 2], reverse=True), sorted(("b", "c", "a"), reverse=True))
    big_a = 10 ** 20 + 1
    big_b = 10 ** 20 + 1
    stable = sorted([big_a, 5, big_b], reverse=True)
    print(stable[0] is big_a, stable[1] is big_b, stable)
    print(attempt([1, "a"]), attempt([("a", 1), ("a", "b")]), attempt([[2], [1, 0], [1]]))
    print(sorted([Ordered(3), Ordered(1), Ordered(2)]))
    print(sorted([5, 3, 4], key=lambda value: -value), sorted({3, 1, 2}), sorted(value for value in (2, 1)))
    seed = 12345
    numbers = []
    words = []
    index = 0
    while index < 200:
        seed = (seed * 1103515245 + 12345) % 2147483648
        numbers.append(seed % 1000 - 500)
        words.append("w" + str(seed % 97))
        index += 1
    print(sorted(numbers)[:12], sorted(numbers)[-3:], sorted(words)[:6], sorted(words, reverse=True)[:3])
    print(sorted([]), sorted([1]), sorted(()))
    total = 0
    round_index = 0
    while round_index < 3000:
        data = {"profile": {"name": "Ada", "plan": "pro"}, "notifications": ["x"], "n": round_index}
        for key, value in sorted(data.items()):
            total += len(key)
        total += len(sorted(data["profile"].items()))
        round_index += 1
    print(total)


main()
'''


@pytest.mark.integration
def test_sorted_matches_cpython_on_five_collectors(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "sorted_plain.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=120)
    assert expected.returncode == 0, expected.stderr
    executable = tmp_path / "sorted_plain"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected.stdout, ""), backend
