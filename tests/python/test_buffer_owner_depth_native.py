"""Owned C/Python native buffer regression, distinct from host body models.

Host pcc0 compiles C with its owned frontend and self object emitter, then
links Python with the source-matched runtime fixture. No external cc fallback.
Automatic GC is disabled to bound this test to the depth/ABI defect; this does
not qualify generic GetBuffer allocation/parking/relocation safety or pcc1.
"""

import os
from pathlib import Path
import subprocess

import pytest


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
