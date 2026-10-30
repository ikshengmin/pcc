"""sorted() and list.sort() order like CPython's listsort, NaN included.

A NaN compares neither less nor greater than anything, so the result of a
sort containing one depends on exactly which comparisons the algorithm
makes. The general path ran a bottom-up merge sort and placed NaNs where
CPython's timsort does not. It now runs CPython 3.15's listsort over the
key index array: count_run, binary insertion to minrun, powersort merging
and galloping, with reverse=True sorting a reversed array and reversing the
result. Sizes cross the 64-element minrun boundary.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import random
import subprocess
import sys
import types

import pytest

from pcc.frontends.python.pipeline import compile_python


PROGRAM = r'''
NAN = float("nan")


class Lcg:
    # A fixed generator: the comparison is about sorting, not about random.
    def __init__(self, seed):
        self.state = seed

    def random(self):
        self.state = (self.state * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return (self.state >> 11) / float(1 << 53)

    def randrange(self, stop):
        return int(self.random() * stop)


class Ranked:
    def __init__(self, rank, label):
        self.rank = rank
        self.label = label

    def __lt__(self, other):
        return self.rank < other.rank


def show(values):
    return [("nan" if v != v else v) for v in values]


def nan_positions(values):
    return [index for index, value in enumerate(values) if value != value]


def main():
    print(show(sorted([3.0, NAN, 1.0, 2.0])))
    print(show(sorted([NAN, 3.0, 1.0, 2.0])))
    print(show(sorted([2.0, 1.0, NAN, 0.5, 4.0, 3.0])))
    print(show(sorted([5, NAN, 2, NAN, 1, 7, 3], reverse=True)))
    print(show(sorted([3.0, NAN, 1.0], key=lambda v: -v if v == v else v)))
    data = [3.0, NAN, 1.0, 2.0]
    data.sort()
    print(show(data))
    mixed = [3, NAN, 1.0, 2, NAN, 0.5]
    mixed.sort()
    print(show(mixed))
    rng = Lcg(5)
    for size in (8, 30, 63, 64, 65, 100, 300, 1000, 2500):
        values = [rng.random() for _ in range(size)]
        for index in range(0, size, 7):
            values[index] = NAN
        ascending = sorted(values)
        descending = sorted(values, reverse=True)
        print(size, nan_positions(ascending)[:12], nan_positions(descending)[:12],
              show(ascending[:2]), show(descending[-2:]))
    runs = [float(index % 50) for index in range(400)] + [NAN] * 3 + [float(400 - index) for index in range(400)]
    print(nan_positions(sorted(runs))[:6], show(sorted(runs)[395:405]))
    people = [Ranked(rng.randrange(5), str(index)) for index in range(200)]
    print("".join(person.label[-1] for person in sorted(people)[:40]))
    print("".join(person.label[-1] for person in sorted(people, reverse=True)[:40]))


main()
'''


@pytest.mark.integration
def test_sort_order_matches_cpython_listsort_on_five_collectors(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "timsort_order.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=120)
    assert expected.returncode == 0, expected.stderr
    executable = tmp_path / "timsort_order"
    compile_python(str(source), str(executable), backend="self",
                   libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, expected.stdout, ""), backend


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime/py/py_obj_ops_compare.py"


class _Pointer:
    def __init__(self, buffer, offset=0):
        self.buffer, self.offset = buffer, offset


def _listsort_port(keys):
    """The ported merge over a byte-buffer memory, comparing keys[a] < keys[b]."""
    tree = ast.parse(RUNTIME.read_text())
    nodes = [node for node in tree.body
             if (isinstance(node, ast.FunctionDef)
                 and (node.name.startswith("_tim_") or node.name == "_sorted_merge_indices"))
             or (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                 and node.targets[0].id.startswith("_TIM_"))]

    def load_i64(pointer, offset):
        start = pointer.offset + offset
        assert 0 <= start and start + 8 <= len(pointer.buffer)
        return int.from_bytes(pointer.buffer[start:start + 8], "little", signed=True)

    def store_i64(pointer, offset, value):
        start = pointer.offset + offset
        assert 0 <= start and start + 8 <= len(pointer.buffer)
        pointer.buffer[start:start + 8] = value.to_bytes(8, "little", signed=True)

    def memmove(destination, source, size):
        assert size >= 0
        chunk = bytes(source.buffer[source.offset:source.offset + size])
        assert len(chunk) == size
        destination.buffer[destination.offset:destination.offset + size] = chunk

    namespace = {
        "load_i64": load_i64, "store_i64": store_i64, "memmove": memmove,
        "ptr_add": lambda pointer, offset: _Pointer(pointer.buffer, pointer.offset + offset),
        "stack_alloc": lambda size: _Pointer(bytearray(size)),
        "_sorted_less": lambda slots, tokens, first, second: int(keys[first] < keys[second]),
        "ptr_is_null": lambda pointer: pointer is None,
    }
    code = compile(ast.Module(nodes, type_ignores=[]), str(RUNTIME), "exec")
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            namespace[const.co_name] = types.FunctionType(const, namespace)
    for node in nodes:
        if isinstance(node, ast.Assign):
            namespace[node.targets[0].id] = node.value.value
    return namespace["_sorted_merge_indices"]


def _ported_permutation(keys, reverse):
    count = len(keys)
    order = _Pointer(bytearray(b"".join(index.to_bytes(8, "little") for index in range(count))))
    scratch = _Pointer(bytearray(8 * count))
    assert _listsort_port(keys)("slots", "tokens", order, scratch, count, int(reverse)) == 0
    result = [int.from_bytes(order.buffer[i * 8:i * 8 + 8], "little") for i in range(count)]
    assert scratch.buffer == order.buffer
    return result


def test_ported_listsort_matches_cpython_permutations():
    nan = float("nan")
    chooser = random.Random(15)
    sizes = list(range(2, 72)) + [100, 127, 128, 129, 200, 500, 1000, 3000]
    for size in sizes:
        for trial in range(3):
            shapes = (
                [chooser.random() for _ in range(size)],
                [float(chooser.randrange(4)) for _ in range(size)],
                [float(index) for index in range(size)],
                [float(size - index) for index in range(size)],
                [float(index % 9) for index in range(size)],
            )
            for keys in shapes:
                keys = list(keys)
                for index in range(trial, size, 5 + trial):
                    keys[index] = nan
                for reverse in (False, True):
                    expected = sorted(range(size), key=keys.__getitem__, reverse=reverse)
                    assert _ported_permutation(keys, reverse) == expected, (size, trial, reverse)
