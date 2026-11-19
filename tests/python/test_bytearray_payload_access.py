"""Locked bytearray operations must not derive a payload from the bytes layout."""

import pytest

from tests.python.owned_regression_support import (
    assert_owned_program, assert_reference_program, explicit_owned_runtime,
)
from tests.python.test_buffer_factory_ownership import RUNTIME, _functions
from tests.python.test_bytearray_extended_slice_snapshot import _integer, _slice_model


@pytest.mark.parametrize("mutable_rhs", (False, True))
def test_locked_writers_use_the_leaf_payload(mutable_rhs):
    # Only in this host model, give mutable leaves a prefix that immutable
    # bytes do not have. Exercise the real runtime bodies, not a native ABI.
    model = _slice_model()
    ns = model.ns
    prefix = b"PAD"
    ns["PYBYTEARRAYOBJECT_DATA_OFFSET"] = ns["PYBYTESOBJECT_DATA_OFFSET"] + len(prefix)
    ns.update(memmove=model.memcpy, py_int_from_i64=int)
    _functions(RUNTIME, {"py_bytearray_setitem", "py_bytearray_clear"}, ns)

    expected = bytearray(b"A\0BCDE")
    target = model.source
    target[0].data = bytearray(prefix + expected + b"\0")
    target[0].length = len(expected)
    rhs = model.new("rhs", ns["PY_TYPE_BYTEARRAY"] if mutable_rhs else ns["PY_TYPE_BYTES"])
    rhs_bytes = b"\xffX"
    rhs[0].data = bytearray((prefix if mutable_rhs else b"") + rhs_bytes + b"\0")
    rhs[0].length = len(rhs_bytes)

    def check():
        assert target[0].length == len(expected)
        assert target[0].data[:len(prefix) + len(expected) + 1] == prefix + expected + b"\0"

    expected[-1] = 128
    assert ns["py_bytearray_setitem"](target, _integer(model, -1), _integer(model, 128)) == 0
    check()
    expected[1:5] = rhs_bytes
    assert ns["py_bytearray_set_slice"](target, _integer(model, 1), _integer(model, 5),
                                        None, rhs) == 0
    check()
    expected[::2] = rhs_bytes
    assert ns["py_bytearray_set_slice"](target, None, None, _integer(model, 2), rhs) == 0
    check()
    assert ns["py_bytearray_pop"](target, _integer(model, 1)) == expected.pop(1)
    check()
    expected.clear()
    assert ns["py_bytearray_clear"](target) is None
    check()
    assert not model.leases and not model.raw and model.frames == {model.external: 2}


def test_aliased_reverse_writes_the_private_scratch_payload():
    model = _slice_model()
    ns = model.ns
    target = model.source
    target[0].data = bytearray(b"ABCD\0")
    target[0].length = 4
    # Stand in only for the private scratch's layout. Its actual allocation,
    # lease, buffer copy, reverse traversal and release still run normally.
    backing = model.new("scratch-backing", ns["PY_TYPE_BYTES"])
    backing[0].data, backing[0].length = bytearray(5), 4
    original_data = ns["_bytes_data"]

    def data(leaf):
        if leaf[0] is target[0]:
            return original_data(leaf)
        assert leaf[0].tag == ns["PY_TYPE_BYTEARRAY"] and leaf[0].length == 4
        return original_data(backing)

    ns["_bytes_data"] = data
    assert ns["py_bytearray_set_slice"](target, None, None, _integer(model, -1), target) == 0
    assert model.content(backing) == b"ABCD" and model.content(target) == b"DCBA"
    assert not model.leases and not model.raw and model.frames == {model.external: 2}


PROGRAM = r"""import gc

def main():
    data = bytearray(b'A\x00BCDE')
    alias = data
    data[-1] = 128
    assert bytes(alias) == b'A\x00BCD\x80'
    data[1:5] = bytearray(b'\xffX')
    assert bytes(alias) == b'A\xffX\x80'
    data[::2] = b'\x00Z'
    assert bytes(alias) == b'\x00\xffZ\x80'
    data[::-1] = memoryview(alias)
    before = bytes(alias)
    assert before == b'\x80Z\xff\x00'
    popped = data.pop(1)
    tail = bytes(alias)
    assert tail == b'\x80\xff\x00'
    gc.collect()
    data.clear()
    assert alias is data and bytes(alias) == b''
    print(popped, len(alias), before[0], before[-1], tail[1])

main()
"""
EXPECTED = "90 0 128 0 255\n"


def test_payload_access_public_reference(tmp_path):
    assert_reference_program(PROGRAM, EXPECTED, tmp_path)


@pytest.mark.integration
def test_payload_access_public_native(python_program_compiler, request,
                                      explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(PROGRAM, EXPECTED, tmp_path, python_program_compiler,
                         mode, explicit_owned_runtime, capfd)
