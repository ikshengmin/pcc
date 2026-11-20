"""The scalar bytearray readers and writer use its own logical length."""

import pytest

from tests.python.owned_regression_support import (
    assert_owned_program, assert_reference_program, explicit_owned_runtime,
)
from tests.python.runtime_body_model import load_functions
from tests.python.test_buffer_factory_ownership import BufferModel, RUNTIME


@pytest.mark.parametrize("consumer", ("bytes", "bytearray", "view", "get", "set"))
def test_scalar_length_uses_each_exact_family_layout(consumer):
    model = BufferModel("bytearray", relocate=False)
    ns = model.ns
    mutable = model.source
    immutable = model.new("immutable", ns["PY_TYPE_BYTES"])
    immutable[0].data, immutable[0].length = bytearray(b"AB"), 2
    view = model.new("view", ns["PY_TYPE_MEMORYVIEW"])
    view[0].base = mutable
    key, value = model.new("key", ns["PY_TYPE_INT"]), model.new("value", ns["PY_TYPE_INT"])
    key[0].value, value[0].value = -1, 255
    # Only this host model distinguishes the two length offsets. The real
    # ABI, allocator, payload offset, and view's base field do not change.
    immutable_length, mutable_length = 16, 40
    ns.update(PYBYTESOBJECT_BYTE_LEN_OFFSET=immutable_length,
              PYBYTEARRAYOBJECT_BYTE_LEN_OFFSET=mutable_length)
    locked, visits = [], []

    def resolve(slot, offset):
        assert locked and model.frames[slot] == -1
        assert model.load(slot, offset) is mutable
        return mutable

    def load_size(base, offset):
        if base is immutable:
            assert offset == immutable_length and not locked
        elif base is mutable:
            assert offset == mutable_length, "mutable length read used the immutable layout"
            assert bool(locked) == (consumer in ("get", "set"))
        else:
            return model.load(base, offset)
        visits.append(offset)
        return model.load(base, 16)

    ns.update(load_i64=load_size, py_int_from_i64=int,
              pcc_py_gc_minor_graph_lock=lambda: locked.append(True),
              pcc_py_gc_minor_graph_unlock=locked.pop,
              pcc_gc_resolve_borrowed_root_slot_unlocked=resolve)
    load_functions(RUNTIME, {"py_bytes_len", "py_bytes_getitem", "py_bytearray_setitem"}, ns)
    if consumer == "bytes":
        assert ns["py_bytes_len"](immutable) == 2
    elif consumer == "bytearray":
        assert ns["py_bytes_len"](mutable) == 3
    elif consumer == "view":
        assert ns["py_bytes_len"](view) == 3
    elif consumer == "get":
        assert ns["py_bytes_getitem"](mutable, key) == ord("C")
    else:
        assert ns["py_bytearray_setitem"](mutable, key, value) == 0
        assert model.content(mutable) == b"AB\xff"
    assert visits == [immutable_length if consumer == "bytes" else mutable_length]
    assert not locked and model.frames == {model.external: 2}


PROGRAM = r"""def size(value):
    return len(value)

def get(value, key):
    return value[key]

def put(value, key, item):
    value[key] = item

def main():
    mutable = bytearray(b'A\x00Q')
    alias = mutable
    immutable = bytes(mutable)
    assert size(immutable) == size(alias) == 3
    tail = get(alias, -1)
    assert get(immutable, 0) == 65 and get(immutable, -1) == tail
    put(alias, -1, 255)
    put(mutable, 0, 0)
    assert bytes(alias) == b'\x00\x00\xff' and immutable == b'A\x00Q'
    errors = 0
    for key in (-4, 3):
        try:
            get(alias, key)
        except IndexError:
            errors += 1
        try:
            put(alias, key, 7)
        except IndexError:
            errors += 1
    for item in (-1, 256):
        try:
            put(alias, 1, item)
        except ValueError:
            errors += 1
    assert alias is mutable and bytes(alias) == b'\x00\x00\xff'
    assert size(memoryview(alias)) == 3 and size(bytearray()) == 0
    print(size(immutable), size(alias), tail, get(alias, 0), get(mutable, -1), errors)

main()
"""
EXPECTED = "3 3 81 0 255 6\n"


def test_scalar_length_public_reference(tmp_path):
    assert_reference_program(PROGRAM, EXPECTED, tmp_path)


@pytest.mark.integration
def test_scalar_length_public_native(python_program_compiler, request,
                                     explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(PROGRAM, EXPECTED, tmp_path, python_program_compiler,
                         mode, explicit_owned_runtime, capfd)
