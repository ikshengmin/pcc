"""A direct bytearray scalar read samples one live length and byte."""

import pytest

from tests.python.owned_regression_support import (
    assert_owned_program, assert_reference_program, explicit_owned_runtime,
)
from tests.python.runtime_body_model import load_functions
from tests.python.test_buffer_factory_ownership import BufferModel, RUNTIME


def _key(model, value):
    key = model.new("key", 1)
    key[0].value = value
    return key


def test_index_does_not_mix_pre_pop_length_and_post_pop_bytes():
    model = BufferModel("bytearray", relocate=False)
    ns = model.ns
    load_functions(RUNTIME, {"py_bytes_getitem", "py_bytearray_pop"}, ns)
    source = model.source
    initial = b"A\x00\xffB"
    source[0].length, source[0].data = len(initial), bytearray(initial + b"\0")
    key, first = _key(model, -2), _key(model, 0)
    popped, held = [], []

    def complete_pop():
        if not popped:
            popped.append(None)  # The rival operation acquires its own lock.
            popped[0] = ns["py_bytearray_pop"](source, first)

    def lock():
        assert not held
        complete_pop()
        held.append(True)

    def size(ptr, offset):
        value = model.load(ptr, offset)
        if ptr is source and offset == 16 and not held:
            # Stock reads size without exclusion. The stale -2 index still
            # names a *live* byte after the pop, but not the selected byte.
            complete_pop()
        return value

    ns.update(pcc_py_gc_minor_graph_lock=lock, pcc_py_gc_minor_graph_unlock=held.pop,
              load_i64=size, py_int_from_i64=int,
              memmove=model.memcpy)  # pop(0) shifts forward toward index zero.
    actual = ns["py_bytes_getitem"](source, key)
    expected = bytearray(initial)
    before = expected[-2]
    assert popped == [expected.pop(0)] and model.content(source) == expected
    assert actual == expected[-2] == before and not held


@pytest.mark.parametrize("index, value", [(0, 0), (-1, 255), (-2, 0), (2, None), (-3, None)])
def test_resolve_and_read_together_before_box_or_error(index, value):
    model = BufferModel("bytearray", relocate=True)
    ns = model.ns
    load_functions(RUNTIME, {"py_bytes_getitem"}, ns)
    source = model.source
    source[0].length, source[0].data = 2, bytearray(b"\x00\xff\x00")
    events, held = [], []

    def lock():
        assert not held
        model.boundary()  # Only the registered receiver slot follows its move.
        held.append(True)

    def resolve(slot, offset):
        assert held and model.frames[slot] == -1 and offset == 0
        events.append("resolve")
        return model.load(slot, offset)

    def observe(function, name, locked):
        def checked(*args):
            assert bool(held) == locked
            if not locked:
                assert model.frames == {model.external: 2}
            events.append(name)
            return function(*args)
        return checked

    ns.update(pcc_py_gc_minor_graph_lock=lock, pcc_py_gc_minor_graph_unlock=held.pop,
              pcc_gc_resolve_borrowed_root_slot_unlocked=resolve,
              py_int_value_i64=observe(ns["py_int_value_i64"], "key", False),
              load_i64=observe(model.load, "size", True), load_i8=observe(model.load, "byte", True),
              py_int_from_i64=observe(int, "box", False),
              py_exc_new=observe(model.error, "error", False),
              py_raise_owned=observe(model.raise_owned, "raise", False))
    assert ns["py_bytes_getitem"](source, _key(model, index)) == value
    if value is None:
        assert (model.pending[0].kind, model.pending[0].message) == (5, "bytes index out of range")
        assert events == ["key", "resolve", "size", "error", "raise"]
    else:
        assert model.pending is None and events == ["key", "resolve", "size", "byte", "box"]
    assert not held and model.frames == {model.external: 2}


@pytest.mark.parametrize("kind", ["bytes", "memoryview", "nested-view"])
def test_non_bytearray_receiver_keeps_existing_read_path(kind):
    model = BufferModel(kind, relocate=False)
    ns = model.ns
    load_functions(RUNTIME, {"py_bytes_getitem"}, ns)
    ns.update(py_int_from_i64=int, pcc_py_gc_minor_graph_lock=lambda: pytest.fail("direct path"))
    assert ns["py_bytes_getitem"](model.source, _key(model, -1)) == ord("C")


PROGRAM = r"""def main():
    data = bytearray(b'A\x00\xffB')
    alias = data
    before = data[-2]
    removed = alias.pop(0)
    after = data[-2]
    assert alias is data and bytes(alias) == b'\x00\xffB'
    missing = 0
    try:
        data[3]
    except IndexError:
        missing += 1
    try:
        data[-4]
    except IndexError:
        missing += 1
    assert memoryview(data)[-2] == b'\xff'[0]
    print(before, removed, after, data[0], alias[-1], missing)

main()
"""
EXPECTED = "255 65 255 0 66 2\n"


def test_scalar_read_public_reference(tmp_path):
    assert_reference_program(PROGRAM, EXPECTED, tmp_path)


@pytest.mark.integration
def test_scalar_read_public_native(python_program_compiler, request,
                                   explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(PROGRAM, EXPECTED, tmp_path, python_program_compiler,
                         mode, explicit_owned_runtime, capfd)
