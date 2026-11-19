"""PEP 3118 whole views and byte sequences compare by their logical bytes."""

import pytest

from tests.python.owned_regression_support import (
    assert_owned_program, assert_reference_program, explicit_owned_runtime,
)
from tests.python.test_buffer_factory_ownership import BufferModel, RUNTIME, _functions


@pytest.mark.parametrize("kind", ("bytearray", "nested-view"))
def test_compare_reads_the_exporters_payload(kind):
    model = BufferModel(kind, relocate=False)
    ns = model.ns
    padding = b"PAD"
    ns["PYBYTEARRAYOBJECT_DATA_OFFSET"] = ns["PYBYTESOBJECT_DATA_OFFSET"] + len(padding)

    # Neither walk (length or data) may bypass the GC read barrier for a
    # managed view's base. This fixture models layout, not concurrent movement.
    def read(ptr, offset):
        assert ptr[0].tag != ns["PY_TYPE_MEMORYVIEW"]
        return model.load(ptr, offset)

    ns["load_ptr"] = read
    _functions(RUNTIME, {"py_bytes_data_ptr"}, ns)
    _functions(RUNTIME.with_name("py_obj_ops_compare.py"), {"_bytes_cmp"}, ns)
    cmp = ns["_bytes_cmp"]
    mutable = model.source
    while mutable[0].tag == ns["PY_TYPE_MEMORYVIEW"]:
        mutable = mutable[0].base
    mutable[0].data, mutable[0].length = bytearray(padding + b"\0\x80"), 2
    rhs = model.new("immutable", ns["PY_TYPE_BYTES"])
    for data, order in ((b"\0\x80", 0), (b"\0\xff", -1), (b"\0\x7f", 1),
                        (b"\0", 1), (b"\0\x80\0", -1), (b"", 1)):
        rhs[0].data, rhs[0].length = bytearray(data), len(data)
        assert cmp(model.source, rhs) == order
        assert cmp(rhs, model.source) == -order
    assert not model.leases and not model.pins and model.pending is None


PROGRAM = r"""def rank(a, b):
    if a == b:
        return 0
    if a < b:
        return -1
    return 1

def main():
    a = bytearray(b'\0\x80')
    same = b'\0\x80'
    high = bytearray(b'\0\xff')
    v = memoryview(memoryview(a))
    # Direct mutable/immutable, and a nested whole view against its legal
    # orderable peer. bytes/memoryview do support equality in both orders.
    assert rank(a, same) == 0 and rank(same, a) == 0
    assert v == same and same == v
    direct_low = rank(a, b'\0\xff')
    direct_high = rank(b'\0\xff', a)
    view_low = rank(v, high)
    view_high = rank(high, v)
    a[1] = 255
    assert rank(v, high) == 0 and rank(high, v) == 0
    assert rank(bytearray(), b'') == 0
    print(direct_low, direct_high, view_low, view_high, rank(a, b'\0\xff\0'),
          rank(a, bytearray()), a[1])

main()
"""
EXPECTED = "-1 1 -1 1 -1 1 255\n"


def test_compare_payload_public_reference(tmp_path):
    assert_reference_program(PROGRAM, EXPECTED, tmp_path)


@pytest.mark.integration
def test_compare_payload_public_native(python_program_compiler, request,
                                      explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(PROGRAM, EXPECTED, tmp_path, python_program_compiler,
                         mode, explicit_owned_runtime, capfd)
