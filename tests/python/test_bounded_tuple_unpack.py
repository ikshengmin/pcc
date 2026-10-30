"""Exact small tuple unpacking shares one bounded source-address lease."""

import os
import re
import subprocess
import sys

import pytest

from tests.python.test_slot_call_operand_roots import _emit, _probe_function


@pytest.mark.parametrize("count", (1, 2, 8, 9))
def test_bounded_unpack_guards_shape_before_extracting_or_activating_iterator_roots(count):
    targets = ", ".join("item" + str(index) for index in range(count))
    if count == 1:
        targets += ","
    body = _probe_function(_emit(
        "def probe(source):\n    " + targets + " = source\n    return item0\n"
    ))
    if count > 8:
        assert "unpack.tuple.lease" not in body
        return
    leases = re.findall(r"%unpack\.tuple\.lease[^\n]* = call[^\n]*", body)
    assert len(leases) == 1
    assert "@pcc_gc_foreign_lease_acquire" in leases[0]
    assert "@py_tuple_len(" in body
    assert len(re.findall(r"%unpack\.tuple\.item[^\n]* = call[^\n]*@py_obj_getitem_i64", body)) == count
    # Every NEW getter result is stored before a call can park. Success retires
    # the one source lease before joining the target-store phase.
    for match in re.finditer(r"(%unpack\.tuple\.item[^ ]*) = call[^\n]*\n", body):
        next_line = body[match.end():].splitlines()[0]
        assert re.search(r"store ptr " + re.escape(match.group(1)) + r",", next_line)
    assert "unpack.tuple.unlease" in body
    assert "unpack.tuple.fallback" in body
    assert "unpack.generic.ready" in body


PROGRAM = '''import gc
import weakref

events = []


class Item:
    def __init__(self, name):
        self.name = name

    def __del__(self):
        events.append(self.name)


class Reordered(tuple):
    def __iter__(self):
        events.append('subclass iter')
        return iter([21, 34])


class Broken:
    def __iter__(self):
        raise selected


selected = ValueError('selected error')


def unpack(source):
    first, second = source
    gc.collect()
    return first, second


def suspended(source):
    first, second = source
    yield first
    gc.collect()
    yield second


def index(number):
    events.append(number)
    gc.collect()
    return number


def target_order(source):
    target = [None, None]
    try:
        target[index(0)], target[index(1)] = source
    except ValueError as error:
        print(str(error), error is selected)
    print(target)


def lifetime():
    source = (Item('left'), Item('right'))
    left = weakref.ref(source[0])
    right = weakref.ref(source[1])
    first, second = source
    source = None
    gc.collect()
    assert left() is first and right() is second
    first = None
    gc.collect()
    assert left() is None and right() is second
    second = None
    gc.collect()
    assert right() is None


def main():
    shared = []
    a, b = unpack((shared, shared))
    assert a is shared and b is shared
    print(unpack((2**100, -(2**101))))
    print(unpack((1.5, 2.5)))
    print(unpack(Reordered((1, 2))))
    print(list(suspended((7, 8))))
    target_order((11, 12))
    target_order((11,))
    target_order((11, 12, 13))
    target_order([11, 12])
    target_order(Broken())
    lifetime()
    print(events)
    for number in range(100):
        first, second = (number, number + 1)
        assert second - first == 1
    print('bounded-tuple-unpack-ok')


main()
'''


def test_bounded_tuple_unpack_native_five_gc(tmp_path, python_program_compiler, pcc_runtime_archive):
    source = tmp_path / "bounded_tuple_unpack.py"
    source.write_text(PROGRAM)
    reference = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=30,
    )
    assert reference.returncode == 0, reference.stderr
    (tmp_path / "expected.stdout").write_text(reference.stdout)
    binary = tmp_path / "bounded_tuple_unpack"
    python_program_compiler(
        str(source), str(binary), backend="self", libpython_mode="off",
        runtime_archive=str(pcc_runtime_archive),
    )
    for backend in range(5):
        run = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend), PATH="/nonexistent"),
        )
        (tmp_path / f"gc{backend}.stdout").write_text(run.stdout)
        (tmp_path / f"gc{backend}.stderr").write_text(run.stderr)
        assert (run.returncode, run.stdout, run.stderr) == (0, reference.stdout, ""), backend
