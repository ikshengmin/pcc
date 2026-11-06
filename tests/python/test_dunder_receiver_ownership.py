"""Python data model 3.3: implicit special calls consume temporary receivers.

A hinted attribute receiver has a NEW field-load owner. The direct special
method path must retire it just as the ordinary method path does.
"""
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.xdist_group(name="dunder_receiver_owners")

PROGRAM = r'''import gc
from threading import Thread
counts = [0,0,0,0,0,0,0,0,0]
class Value:
    def __init__(self, kind):
        self.kind = kind
    def __del__(self):
        counts[self.kind] += 1
    def __len__(self):
        if self.kind == 8:
            raise ValueError("length")
        return 3
    def __abs__(self):
        return 4
    def __getitem__(self, index):
        return index + 5
    def __eq__(self, other):
        return other == 7
    def __add__(self, other):
        return other + 8
    def __radd__(self, other):
        return other + 9
    def __neg__(self):
        return -10
class Holder:
    def __init__(self, value: Value):
        self.value = value
def check(kind):
    holder = Holder(Value(kind))
    if kind == 0:
        assert len(holder.value) == 3
    elif kind == 1:
        assert abs(holder.value) == 4
    elif kind == 2:
        assert holder.value[2] == 7
    elif kind == 3:
        assert holder.value == 7
    elif kind == 4:
        assert holder.value + 2 == 10
    elif kind == 5:
        assert 2 + holder.value == 11
    elif kind == 6:
        assert -holder.value == -10
    elif kind == 7:
        assert len(holder.value) == 3
        assert len(holder.value) == 3
        assert holder.value.kind == kind
    else:
        try:
            len(holder.value)
        except ValueError as error:
            assert str(error) == "length"
        else:
            raise AssertionError("length did not fail")
    return holder
def produce():
    holders = []
    for index in range(101):
        for kind in range(9):
            holders.append(check(kind))
    return holders
holders = []
def worker():
    holders.extend(produce())
def main():
    # Retire the producer's stack before measuring finalization. Tracing
    # collectors may conservatively retain dead words on a live stack.
    thread = Thread(target=worker)
    thread.start()
    thread.join()
    live = holders[-9:]
    holders.clear()
    for attempt in range(8):
        gc.collect()
        if counts == [100,100,100,100,100,100,100,100,100]:
            break
    print(counts)
    assert len(live) == 9
main()
'''


@pytest.fixture(scope="module")
def dunder_binary(tmp_path_factory):
    root = tmp_path_factory.mktemp("dunder-receiver")
    source = root / "dunder_receiver.py"
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True,
                               text=True, timeout=10)
    assert reference.returncode == 0, reference.stderr
    binary = root / "dunder_receiver"
    result = subprocess.run(
        [sys.executable, "-m", "pcc", "--backend", "self", str(source), "-o", str(binary)],
        capture_output=True, text=True, timeout=120,
        env=dict(os.environ, PCC_NO_AUTO_PCC1="1"),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return binary, reference.stdout


@pytest.mark.integration
@pytest.mark.parametrize("collector", range(5))
def test_direct_special_calls_retire_owned_receivers(dunder_binary, collector):
    binary, expected = dunder_binary
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                            env=dict(os.environ, PCC_GC_BACKEND=str(collector)))
    assert (result.returncode, result.stdout, result.stderr) == (0, expected, "")
