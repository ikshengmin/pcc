"""Owned C/Python native buffer regression, distinct from host body models.

Host pcc0 compiles C with its owned frontend and self object emitter, then
links Python with the source-matched runtime fixture. No external cc fallback.
The depth fixture disables automatic GC. The separate public payload-copy
fixture uses normal execution. Neither qualifies general relocation safety or pcc1.
"""

import os
from pathlib import Path
import subprocess

import pytest

from tests.python.owned_regression_support import (
    _record_execution, assert_reference_program, explicit_owned_runtime,
)


PROGRAM = '''
import gc
from pcc.extern import c_int64, c_obj, extern
probe = extern("buffer_owner_depth", (c_obj, c_int64, c_int64, c_int64), c_int64)
def main():
    gc.disable()
    for depth in (0, 15, 16, 17, 32, 64):
        for reverse in (0, 1):
            data = b"lease\\x00x"
            assert probe(data, depth, 0, reverse) == 0
            assert data == b"lease\\x00x"
            mutable = bytearray(b"lease\\x00x")
            assert probe(mutable, depth, 1, reverse) == 0
            assert mutable == bytearray(b"lease\\x00x")
    print("BUFFER_OWNER_DEPTH_OK")
main()
'''


@pytest.fixture(scope="module")
def buffer_owner_depth_binary(tmp_path_factory, pcc_runtime_archive):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.python.test_owned_external_tls import _c_ir

    root = tmp_path_factory.mktemp("buffer_owner_depth")
    fixture = Path(__file__).with_name("fixtures") / "buffer_owner_depth.c"
    target = host_target_triple()
    helper = root / "buffer_depth.o"
    helper.write_bytes(emit_owned_object(_c_ir(fixture.read_text(), target), target))
    source = root / "program.py"
    source.write_text(PROGRAM)
    binary = root / "program"
    compile_python(
        str(source), str(binary), backend="self",
        runtime_archive=str(pcc_runtime_archive), object_inputs=(str(helper),),
    )
    return binary


@pytest.mark.integration
@pytest.mark.parametrize("backend", range(5))
def test_buffer_owner_depth_owned_native(buffer_owner_depth_binary, backend):
    result = subprocess.run(
        [str(buffer_owner_depth_binary)], capture_output=True, text=True, timeout=30,
        env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend),
                 PCC_GC_TRACING_ONLY="0", PCC_GC4_TRACING_ONLY="0"),
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        0, "BUFFER_OWNER_DEPTH_OK\n", "",
    ), (backend, result)


@pytest.mark.parametrize("depth", (0, 15, 16, 17, 32, 64))
@pytest.mark.parametrize("writable", (False, True))
@pytest.mark.parametrize("reverse", (False, True))
def test_cpython_stdlib_memoryview_reference(depth, writable, reverse):
    # Labeled host stdlib semantic reference, not a production-body model or
    # native PCC result. CPython can flatten bases; no chain-layout claim.
    source = bytearray(b"lease\x00x") if writable else b"lease\x00x"
    outer = source
    owned = []
    try:
        for _ in range(depth):
            outer = memoryview(outer)
            owned.append(outer)
        exports = [memoryview(outer), memoryview(outer)]
        try:
            for view in exports:
                assert view.tobytes() == b"lease\x00x"
                assert view.nbytes == 7 and view.readonly == (not writable)
            if writable:
                exports[0][0] = ord("L")
                assert exports[1][0] == ord("L")
                exports[0][0] = ord("l")
            first, last = reversed(exports) if reverse else exports
            first.release()
            assert last.tobytes() == b"lease\x00x"
        finally:
            for view in exports:
                view.release()
    finally:
        for view in reversed(owned):
            view.release()


# Copy only the small fixture's logical bytes while its public export is live.
# Release before allocating the returned Python bytes. No address escapes.
COPY_C = """#include <Python.h>
#include <string.h>
PyObject *copy_export(PyObject *obj, int writable) {
    char bytes[3];
    Py_buffer view;
    if (PyObject_GetBuffer(obj, &view, writable ? PyBUF_WRITABLE : PyBUF_SIMPLE) != 0)
        return NULL;
    Py_ssize_t n = view.len;
    if (n < 0 || n > 3) {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_ValueError, "expected at most three bytes");
        return NULL;
    }
    if (n > 0) memcpy(bytes, view.buf, n);
    PyBuffer_Release(&view);
    return PyBytes_FromStringAndSize(bytes, n);
}
"""
COPY_REFERENCE = """def take(obj, writable):
    with memoryview(obj) as view:
        assert not writable or not view.readonly
        return view.tobytes()
"""
COPY_NATIVE = """from pcc.extern import c_int32, c_obj, extern
take = extern("copy_export", (c_obj, c_int32), c_obj)
"""
COPY_PROGRAM = r"""def main():
    raw = b'\x00\xffA'
    owner = bytearray(b'Q\x00\xff')
    a = take(raw, 0)
    b = take(owner, 1)
    assert take(memoryview(memoryview(raw)), 0) == a
    c = take(memoryview(memoryview(owner)), 1)
    empty = take(bytearray(), 1)
    assert a == raw and b == b'Q\x00\xff' and c == b and empty == b''
    print(a[0], a[1], a[2], b[0], c[1], c[2], len(empty))

main()
"""
COPY_EXPECTED = "0 255 65 81 0 255 0\n"


def test_export_leaf_copy_reference(tmp_path):
    assert_reference_program(COPY_REFERENCE + COPY_PROGRAM, COPY_EXPECTED, tmp_path)


@pytest.mark.integration
def test_export_leaf_copy_native(tmp_path, explicit_owned_runtime, python_program_compiler):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from scripts.owned_image_linkage import inspect_owned_image

    _, oracle = assert_reference_program(COPY_REFERENCE + COPY_PROGRAM, COPY_EXPECTED, tmp_path)
    root = Path(__file__).resolve().parents[2]
    target = host_target_triple()
    c_source = tmp_path / "copy_export.c"
    c_source.write_text(COPY_C)
    (unit,) = CEvaluator(backend="self", target_triple=target).compile_translation_units(
        [TranslationUnit(c_source.name, str(c_source), COPY_C)],
        use_system_cpp=False, use_compile_cache=False,
        include_dirs=[str(root / "pcc/runtime/include"), str(root / "utils/fake_libc_include")],
    )
    helper, source, binary = (tmp_path / name for name in ("copy_export.o", "copy.py", "copy.out"))
    helper.write_bytes(emit_owned_object(unit[1], target))
    source.write_text(COPY_NATIVE + COPY_PROGRAM)
    python_program_compiler(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                            runtime_archive=str(explicit_owned_runtime), object_inputs=(str(helper),))
    assert inspect_owned_image(binary.read_bytes())["libpython"] == "off"
    for backend in range(5):
        result = _record_execution(tmp_path, f"gc{backend}", [str(binary)],
                                   dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert (result["returncode"], result["stdout"], result["stderr"]) == (
            oracle["returncode"], oracle["stdout"], oracle["stderr"],
        ), result
