"""Host-only checks for the old page model's missing i32 store provider."""

import pytest

from tests.python.test_gc_relocation_page_cursor_model import _SelectorMemory


@pytest.mark.parametrize("value,expected", [
    (0, 0), (1, 1), (-1, -1), (2147483647, 2147483647),
    (2147483648, -2147483648), (4294967295, -1), (4294967297, 1),
])
def test_atomic_store_i32_truncates_and_returns_none(value, expected):
    memory = _SelectorMemory()
    memory.lock()
    try:
        result = memory.namespace["atomic_store_i32"]("test_word", 0, value, "release")
        assert result is None
        assert memory.global_get("test_word") == expected
    finally:
        memory.unlock()


def test_atomic_store_i32_checks_four_byte_extent_without_touching_neighbor():
    memory = _SelectorMemory()
    pointer = memory.new(16)
    memory.lock()
    try:
        memory.namespace["atomic_store_i32"](pointer, 8, 73, "release")
        memory.namespace["atomic_store_i32"](pointer, 12, 1, "release")
        assert memory.cells[pointer][8] == 73
        assert memory.cells[pointer][12] == 1
        with pytest.raises(AssertionError):
            memory.namespace["atomic_store_i32"](pointer, 16, 1, "release")
    finally:
        memory.unlock()


def test_atomic_store_i32_requires_this_selector_locked_release_contract():
    memory = _SelectorMemory()
    with pytest.raises(AssertionError):
        memory.namespace["atomic_store_i32"]("test_word", 0, 1, "release")
    memory.lock()
    try:
        with pytest.raises(AssertionError):
            memory.namespace["atomic_store_i32"]("test_word", 0, 1, "acquire")
        with pytest.raises(AssertionError):
            memory.namespace["atomic_store_i32"]("test_word", 2, 1, "release")
    finally:
        memory.unlock()


@pytest.mark.parametrize("displacement,offset,aligned", [
    (4, 0, True), (2, 0, False), (2, 2, True), (4, 2, False),
])
def test_atomic_store_i32_checks_normalized_interior_address(displacement, offset, aligned):
    memory = _SelectorMemory()
    pointer = memory.new(16)
    interior = memory.ptr_add(pointer, displacement)
    memory.lock()
    try:
        if aligned:
            memory.namespace["atomic_store_i32"](interior, offset, 1, "release")
            assert memory.cells[pointer] == {displacement + offset: 1}
        else:
            with pytest.raises(AssertionError):
                memory.namespace["atomic_store_i32"](interior, offset, 1, "release")
            assert memory.cells[pointer] == {}
    finally:
        memory.unlock()
