"""The C-created writable whole view owns a correctly initialized bytearray."""

import os

import pytest

from tests.python.owned_regression_support import (
    _record_execution, assert_reference_program, explicit_owned_runtime,
)
from tests.python.runtime_body_model import load_functions
from tests.python.test_buffer_factory_ownership import BufferModel, RUNTIME


CAPI = RUNTIME.with_name("py_capi_buffer_runtime.py")


@pytest.mark.parametrize("data,length", ((b"\x00\xffQ", 3), (None, 0), (None, 3)))
def test_c_factory_reserves_its_own_footprint_and_initializes_the_logical_leaf(data, length):
    model = BufferModel(relocate=True)
    ns = model.ns
    padding = 3  # Modeled only: bytes and bytearray no longer have the same data offset.
    ns["PYBYTEARRAYOBJECT_DATA_OFFSET"] = ns["PYBYTESOBJECT_DATA_OFFSET"] + padding
    load_functions(RUNTIME, {"py_bytes_data_ptr"}, ns)
    load_functions(CAPI, {"_bytearray_from_memory"}, ns)
    allocations = []

    def allocate(size, tag, flags):
        allocations.append((size, tag, flags))
        result = model.allocate(size, tag, flags)
        result[0].data[:] = b"\xa5" * len(result[0].data)
        return result

    ns["pcc_gc_alloc"] = allocate
    source = None
    if data is not None:
        source = model.raw_alloc(len(data))
        model.raw[source][:] = data
    result = ns["_bytearray_from_memory"](source, length)
    model.store(model.external, 8, result)
    assert allocations == [(48 + length + 1, ns["PY_TYPE_BYTEARRAY"], 0)]
    assert result[0].length == length and result[0].refs == 1
    # Only the logical bytes and their NUL may be written. A private null
    # source reserves uninitialized bytes; the public C API rejects null/positive.
    expected = bytearray(b"\xa5" * (48 + length + 1 - 24))
    if data is not None:
        expected[padding:padding + length] = data
        assert bytes(model.raw[source]) == data
    expected[padding + length] = 0
    assert result[0].data == expected
    assert model.pending is None and not model.leases and not model.pins


def test_c_factory_allocation_failure_preserves_primary_error():
    model = BufferModel(relocate=True, failure="allocation", pending="primary error")
    ns = model.ns
    load_functions(RUNTIME, {"py_bytes_data_ptr"}, ns)
    load_functions(CAPI, {"_bytearray_from_memory"}, ns)
    assert ns["_bytearray_from_memory"](None, 0) is None
    assert model.allocations == 1 and model.pending[0].message == "primary error"
    assert model.fresh is None and not model.leases and not model.pins


# Use the same public C signature in both programs. The caller owns writable
# storage for both calls; observe only Python-visible bytes, not backing aliases.
REFERENCE = r"""from ctypes import POINTER, c_char, c_int, c_ssize_t, create_string_buffer, py_object, pythonapi
from_memory = pythonapi.PyMemoryView_FromMemory
from_memory.argtypes = (POINTER(c_char), c_ssize_t, c_int)
from_memory.restype = py_object
def main():
    storage = create_string_buffer(b'\x00\xffQ')
"""
NATIVE = """from pcc.extern import c_int32, c_int64, c_obj, c_ptr, extern
from pcc.unsafe import stack_alloc, store_i8
from_memory = extern("PyMemoryView_FromMemory", (c_ptr, c_int64, c_int32), c_obj)
def main():
    storage = stack_alloc(3)
    store_i8(storage, 0, 0)
    store_i8(storage, 1, 255)
    store_i8(storage, 2, 81)
"""
BODY = """    empty = bytes(from_memory(storage, 0, 0x0200))
    data = bytes(from_memory(storage, 3, 0x0200))
    print(len(empty), len(data), data[0], data[1], data[2])

main()
"""
EXPECTED = "0 3 0 255 81\n"


def test_c_factory_public_reference(tmp_path):
    assert_reference_program(REFERENCE + BODY, EXPECTED, tmp_path)


@pytest.mark.integration
def test_c_factory_public_native(tmp_path, explicit_owned_runtime, python_program_compiler):
    from scripts.owned_image_linkage import inspect_owned_image

    _, oracle = assert_reference_program(REFERENCE + BODY, EXPECTED, tmp_path)
    source, binary = tmp_path / "capi_view.py", tmp_path / "capi_view.out"
    source.write_text(NATIVE + BODY)
    python_program_compiler(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                            runtime_archive=str(explicit_owned_runtime))
    assert inspect_owned_image(binary.read_bytes())["libpython"] == "off"
    for backend in (0,):
        result = _record_execution(tmp_path, f"gc{backend}", [str(binary)],
                                   dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert (result["returncode"], result["stdout"], result["stderr"]) == (
            oracle["returncode"], oracle["stdout"], oracle["stderr"],
        ), result
