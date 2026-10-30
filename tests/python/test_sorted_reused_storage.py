"""Sorting reuses private storage without changing Python owners or callbacks."""
import pytest

from tests.python.test_explicit_machine_arithmetic_boundaries import _run_native_all_gc


PROGRAM = r'''import gc
import weakref

destroyed = []
references = []
calls = []
selected = ValueError("chosen sort error")
fail_compare = False

class Item:
    def __init__(self, rank, label):
        self.rank = rank
        self.label = label
        references.append(weakref.ref(self))
    def __lt__(self, other):
        gc.collect()
        if fail_compare:
            raise selected
        return self.rank < other.rank
    def __del__(self):
        destroyed.append(self.label)
        gc.collect()

def key(item):
    calls.append(item.label)
    gc.collect()
    return item

def exercise(reverse, use_key):
    start = len(references)
    source = [Item(2, "a"), Item(1, "b"), Item(2, "c"), Item(0, "d")]
    calls.clear()
    if use_key:
        result = sorted(source, key=key, reverse=reverse)
        assert calls == ["a", "b", "c", "d"]
    else:
        result = sorted(source, reverse=reverse)
    assert result is not source
    assert [item.label for item in source] == ["a", "b", "c", "d"]
    indices = [0, 2, 1, 3] if reverse else [3, 1, 0, 2]
    source.clear()
    gc.collect()
    for index in range(4):
        assert result[index] is references[start + indices[index]]()
    result.clear()

def mutating_key_source():
    source = [3, 1, 2, 1]
    visits = []
    def rank(value):
        visits.append(value)
        source.clear()
        gc.collect()
        return value
    result = sorted(source, key=rank)
    assert source == [] and visits == [3, 1, 2, 1]
    assert result == [1, 1, 2, 3]

def failing_callbacks():
    global fail_compare
    source = [Item(2, "e"), Item(1, "f"), Item(0, "g")]
    def bad_key(item):
        gc.collect()
        if item.label == "f":
            raise selected
        return item
    try:
        sorted(source, key=bad_key)
    except ValueError as error:
        assert error is selected
    else:
        raise AssertionError("key failure lost")
    assert [item.label for item in source] == ["e", "f", "g"]
    fail_compare = True
    try:
        sorted(source)
    except ValueError as error:
        assert error is selected
    else:
        raise AssertionError("comparison failure lost")
    finally:
        fail_compare = False
    assert [item.label for item in source] == ["e", "f", "g"]
    source.clear()

def main():
    global selected
    for reverse in (False, True):
        for use_key in (False, True):
            before = len(destroyed)
            exercise(reverse, use_key)
            gc.collect()
            assert len(destroyed) == before + 4
    mutating_key_source()
    before = len(destroyed)
    failing_callbacks()
    selected = None  # Release CPython traceback owners before testing finalization.
    gc.collect()
    assert len(destroyed) == before + 3
    assert all(reference() is None for reference in references)
    big = (1 << 120) + 31
    small = -big
    source = [big, 0, small, big]
    result = sorted(source)
    assert result[0] is small and result[2] is big and result[3] is big
    assert source[0] is big and source[2] is small
    assert sorted([]) == []
    assert sorted([big])[0] is big
    assert sorted([True, 1, False, 0]) == [False, 0, True, 1]
    for count in (2, 3, 7, 8, 9, 16, 17, 64, 129):
        values = list(range(count, -1, -1))
        result = sorted(values)
        assert result == list(range(count + 1))
        gc.collect()
        assert result == list(range(count + 1))
        result.append(big)
        assert result[-1] is big
        result.clear()
    print("SORTED_REUSE_OK")

main()
'''


@pytest.mark.integration
def test_sorted_reused_storage_native_all_collectors(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
):
    _run_native_all_gc(tmp_path, monkeypatch, pcc_runtime_archive,
                       python_program_compiler, PROGRAM, "SORTED_REUSE_OK")


# The variable reverse entry remains a required regression above. This second
# program reaches the runtime storage change independently of that pre-existing
# frontend gap; both orders still execute, with the same identity/lifetime checks.
CONSTANT_REVERSE_PROGRAM = PROGRAM.replace(
    """    if use_key:
        result = sorted(source, key=key, reverse=reverse)
        assert calls == ["a", "b", "c", "d"]
    else:
        result = sorted(source, reverse=reverse)
""",
    """    if use_key:
        if reverse:
            result = sorted(source, key=key, reverse=True)
        else:
            result = sorted(source, key=key, reverse=False)
        assert calls == ["a", "b", "c", "d"]
    else:
        if reverse:
            result = sorted(source, reverse=True)
        else:
            result = sorted(source, reverse=False)
""",
)


@pytest.mark.integration
def test_sorted_working_storage_constant_reverse_all_collectors(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
):
    _run_native_all_gc(tmp_path, monkeypatch, pcc_runtime_archive,
                       python_program_compiler, CONSTANT_REVERSE_PROGRAM,
                       "SORTED_REUSE_OK")
