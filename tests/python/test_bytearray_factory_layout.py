"""The managed bytearray factory initializes its own layout, not bytes'."""

import pytest

from tests.python.owned_regression_support import (
    assert_owned_program, assert_reference_program, explicit_owned_runtime,
)
from tests.python.test_buffer_factory_ownership import BufferModel


@pytest.mark.parametrize(("kind", "expected"), (
    ("bytes", b""), ("bytes", b"\x00\xffQ"), ("count", b""), ("count", b"\0" * 4),
    ("private", b"\xa5" * 4),
))
def test_factory_reserves_and_initializes_its_own_payload(kind, expected):
    model = BufferModel(kind, relocate=True)
    ns = model.ns
    prefix = 3  # Only this model's mutable layout differs from bytes.
    ns["PYBYTEARRAYOBJECT_DATA_OFFSET"] = ns["PYBYTESOBJECT_DATA_OFFSET"] + prefix
    if kind == "count":
        model.source[0].value = len(expected)
    elif kind == "bytes":
        model.source[0].data = bytearray(expected + b"\0")
        model.source[0].length = len(expected)

    def allocate(size, tag, flags):
        result = model.allocate(size, tag, flags)
        result[0].data[:] = b"\xa5" * len(result[0].data)
        return result

    ns["pcc_gc_alloc"] = allocate
    if kind == "private":
        # A private reservation is not bytearray(count): leave its body
        # untouched until the caller fills it, but install its terminator now.
        result = ns["_bytearray_new_raw"](None, len(expected))
        model.store(model.external, 8, result)
    else:
        result = model.run(1)
    assert result[0].tag == ns["PY_TYPE_BYTEARRAY"] and result[0].length == len(expected)
    # An exact-fit allocation, untouched header padding, all logical bytes
    # initialized, and exactly one trailing NUL (also for empty buffers).
    assert result[0].data == b"\xa5" * prefix + expected + b"\0"
    assert model.pending is None and not model.leases and not model.pins


@pytest.mark.parametrize("mutable", (False, True))
@pytest.mark.parametrize("excess", (0, 1))
def test_count_limit_reserves_the_selected_header_and_terminator(mutable, excess):
    model = BufferModel("count", relocate=True)
    ns = model.ns
    ns["PYBYTEARRAYOBJECT_DATA_OFFSET"] = ns["PYBYTESOBJECT_DATA_OFFSET"] + 3
    maximum = (1 << 63) - 1
    model.source[0].value = maximum - ns["PYBYTESOBJECT_DATA_OFFSET"] - 1 + excess
    requested = []

    def refuse(size, tag, flags):
        requested.append((size, tag))
        return None  # Never attempt a large allocation, even if the guard is wrong.

    ns["pcc_gc_alloc"] = refuse
    assert model.run(int(mutable)) is None
    if mutable or excess:
        assert not requested and model.pending[0].message == "byte count is too large"
        assert model.pending[0].kind == (19 if mutable else 15)
    else:
        assert requested == [(maximum, ns["PY_TYPE_BYTES"])]
        assert (model.pending[0].kind, model.pending[0].message) == (19, "bytes: out of memory")
    assert not model.leases and not model.pins


PROGRAM = r"""def main():
    empty = bytearray()
    zero = bytearray(0)
    zeros = bytearray(4)
    assert len(empty) == 0 and len(zero) == 0 and bytes(zeros) == b'\0\0\0\0'
    a = bytearray(b'\x00\xffQ')
    copy = bytearray(a)
    a[0] = 65
    assert bytes(copy) == b'\x00\xffQ' and bytes(a) == b'A\xffQ'
    print(len(zero), len(zeros), copy[0], copy[1], a[0])

main()
"""
EXPECTED = "0 4 0 255 65\n"


def test_factory_layout_public_reference(tmp_path):
    assert_reference_program(PROGRAM, EXPECTED, tmp_path)


@pytest.mark.integration
def test_factory_layout_public_native(python_program_compiler, request,
                                      explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(PROGRAM, EXPECTED, tmp_path, python_program_compiler,
                         mode, explicit_owned_runtime, capfd)
