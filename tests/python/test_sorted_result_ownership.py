"""Native sorted results are owned even in raw-ABI and branch joins."""

import os
import subprocess

import pytest


@pytest.mark.parametrize("expression", [
    "sorted(values)", "sorted(values, key=lambda x: -x)",
    "sorted(values, reverse=True)",
])
def test_sorted_result_is_released_and_generator_join_keeps_range_owner(
    tmp_path, pcc_py_runtime_archive, python_program_compiler, monkeypatch, expression,
):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "sorted_owner.py"
    source.write_text('''import gc
from pcc.extern import extern, c_int64
heap = extern("pcc_os_heap_in_use_bytes", (), c_int64)
def sequence(values, mode):
    if mode == 0:
        indices = range(len(values))
    else:
        indices = EXPRESSION
    for index in indices:
        yield index
def scan(values, mode):
    total = 0
    for index in sequence(values, mode):
        total += index
    assert total == len(values) * (len(values) - 1) // 2
def main():
    values = list(range(10000))
    for mode in range(2):
        scan(values, mode)
        for repeat in range(2):
            gc.collect()
            before = heap()
            scan(values, mode)
            gc.collect()
            print(heap() - before)
main()
'''.replace("EXPRESSION", expression))
    binary = tmp_path / "sorted_owner"
    python_program_compiler(str(source), str(binary), backend="self", libpython_mode="off",
                            runtime_archive=str(pcc_py_runtime_archive))
    for backend in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                                env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert result.returncode == 0, (backend, result.stdout, result.stderr)
        growth = list(map(int, result.stdout.split()))
        if backend == 0:
            assert all(value < 16384 for value in growth), growth


def test_sorted_custom_comparison_releases_result_storage(
    tmp_path, pcc_py_runtime_archive, python_program_compiler, monkeypatch,
):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "sorted_custom_owner.py"
    source.write_text('''import gc
from pcc.extern import extern, c_int64
heap = extern("pcc_os_heap_in_use_bytes", (), c_int64)
class Item:
    def __init__(self, value: int):
        self.value = value
    def __lt__(self, other):
        return self.value < other.value
def scan(count):
    values = [Item(3), Item(1), Item(2)]
    for repeat in range(count):
        ordered = sorted(values)
        first = ordered[0]
        last = ordered[2]
        assert first.value == 1
        assert last.value == 3
    original_first = values[0]
    assert original_first.value == 3
def main():
    scan(16)
    gc.collect()
    before = heap()
    scan(2000)
    gc.collect()
    print(heap() - before)
main()
''')
    binary = tmp_path / "sorted_custom_owner"
    python_program_compiler(str(source), str(binary), backend="self", libpython_mode="off",
                            runtime_archive=str(pcc_py_runtime_archive))
    for backend in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                                env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert result.returncode == 0, (backend, result.stdout, result.stderr)
        if backend == 0:
            assert int(result.stdout) < 16384, result.stdout
