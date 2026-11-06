"""Python reference 6.3.3 / 7.5: slicing deletion owns evaluated operands."""

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.xdist_group(name="slice_delete_owners")

BEHAVIOR = '''
def main():
    left = [0, 1, 2, 3, 4, 5]
    right = [8, 9, 10]
    data = bytearray(b"abcdef")
    del left[::2], right[1:], data[:2]
    print(left, right, data)
    del data[1::2]
    print(data)
    del data[::-2]
    print(data)
    try:
        del left[::0], right[:]
    except ValueError as error:
        print(type(error).__name__, str(error), left, right)
    else:
        raise AssertionError("zero step did not raise")
main()
'''

LIFETIME = '''import gc
from threading import Thread
counts = [0, 0, 0, 0, 0, 0]
calls = []
class Token:
    def __init__(self, kind):
        self.kind = kind
    def __del__(self):
        counts[self.kind] += 1
class Bound:
    def __init__(self, kind, fail):
        self.kind = kind
        self.fail = fail
    def __index__(self):
        calls.append(self.kind)
        gc.collect()
        if self.fail:
            raise ValueError("bound")
        return 0
    def __del__(self):
        counts[self.kind] += 1
class Holder:
    def __init__(self, fail):
        self.items = [Token(fail)]
        self.lo = Bound(2 + fail * 2, False)
        self.hi = Bound(3 + fail * 2, bool(fail))
def exercise(fail):
    holder = Holder(fail)
    try:
        del holder.items[holder.lo:holder.hi]
    except ValueError as error:
        assert fail == 1 and str(error) == "bound"
    else:
        assert fail == 0
    assert len(holder.items) == 1
    return holder
def produce():
    holders = []
    for index in range(101):
        holders.append(exercise(0))
        holders.append(exercise(1))
    return holders
holders = []
def worker():
    holders.extend(produce())
def main():
    thread = Thread(target=worker)
    thread.start()
    thread.join()
    live = holders[-2:]
    holders.clear()
    for attempt in range(8):
        gc.collect()
        if counts == [100, 100, 100, 100, 100, 100]:
            break
    print(counts)
    assert calls == [2, 3, 4, 5] * 101
    assert len(live[0].items) == 1 and len(live[1].items) == 1
main()
'''

MEMORY = '''from pcc.extern import c_int64, extern
live_bytes = extern("pcc_os_heap_in_use_bytes", (), c_int64)
class Holder:
    def __init__(self):
        self.data = bytearray(b"x" * 65536)
def replace(holder):
    holder.data = bytearray(b"x" * 65536)
    del holder.data[:0]
def main():
    holder = Holder()
    # Warm the same allocation/root path before checking steady growth.
    # The first batch initializes bookkeeping; a second fixed-work batch
    # must not keep the replaced bytearrays alive.
    for index in range(64):
        replace(holder)
    before = live_bytes()
    for index in range(64):
        replace(holder)
    after = live_bytes()
    print(len(holder.data), holder.data[0], holder.data[-1], after - before)
main()
'''


@pytest.fixture(scope="module", params=["behavior", "lifetime", "memory"])
def slice_delete_program(request, tmp_path_factory):
    name = request.param
    root = tmp_path_factory.mktemp("slice-delete-" + name)
    source = root / (name + ".py")
    source.write_text({"behavior": BEHAVIOR, "lifetime": LIFETIME, "memory": MEMORY}[name])
    expected = None
    if name != "memory":
        reference = subprocess.run(
            [sys.executable, str(source)], capture_output=True, text=True, timeout=20,
        )
        assert reference.returncode == 0, reference.stderr
        expected = reference.stdout
    binary = root / name
    compiled = subprocess.run(
        [sys.executable, "-m", "pcc", "--backend", "self", str(source), "-o", str(binary)],
        capture_output=True, text=True, timeout=120,
        env=dict(os.environ, PCC_NO_AUTO_PCC1="1"),
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    return name, binary, expected


@pytest.mark.integration
@pytest.mark.parametrize("collector", range(5))
def test_slice_delete_consumes_operands(slice_delete_program, collector):
    name, binary, expected = slice_delete_program
    result = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=20,
        env=dict(os.environ, PCC_GC_BACKEND=str(collector)),
    )
    assert (result.returncode, result.stderr) == (0, ""), result.stdout + result.stderr
    if name != "memory":
        assert result.stdout == expected
    else:
        size, first, last, retained = map(int, result.stdout.split())
        assert (size, first, last) == (65536, 120, 120)
        if collector == 0:
            # One leaked receiver per iteration retains over 4 MiB. GC0's
            # immediate accounting exposes that independently of GC cadence.
            assert retained < 262144, retained
