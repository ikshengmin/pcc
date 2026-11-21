"""Default collectors release foreign payloads through their typed owner."""
import os
import subprocess
from pathlib import Path


PROGRAM = r'''import gc
from pcc.extern import c_int64, c_obj, c_void, extern

new_handle = extern("fixture_new_foreign_handle", (), c_obj)
release_count = extern("fixture_foreign_release_count", (), c_int64)
refcount_delta = extern("fixture_foreign_handle_refcount_delta", (c_obj,), c_int64)
hold = extern("fixture_foreign_handle_hold", (c_obj,), c_void)
drop = extern("fixture_foreign_handle_drop", (), c_void)
backend = extern("pcc_gc_backend", (), c_int64)

def create(keep_c_owner):
    # The constructor returns one managed owner; normal scope exit drops it.
    handle = new_handle()
    assert handle is not None
    if keep_c_owner:
        assert refcount_delta(handle) == 1
        hold(handle)

def main():
    # GC0 is the migration baseline for C-API reference ownership. Every
    # collector still runs the ordinary foreign-payload release check.
    keep_c_owner = backend() == 0
    create(keep_c_owner)
    # A normal later allocation ends the allocator's last-allocation root.
    keep = [17, 19]
    for attempt in range(4):
        gc.collect()
    if keep_c_owner:
        assert release_count() == 0
        drop()
        assert release_count() == 1
    assert release_count() == 1
    assert keep == [17, 19]
    print("foreign-release-once")
main()
'''


def test_default_collectors_release_foreign_payload_once(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.python.test_owned_external_tls import _c_ir

    fixture = Path(__file__).with_name("fixtures") / "gc_foreign_handle_payload.c"
    target = host_target_triple()
    helper = tmp_path / "foreign_payload.o"
    helper.write_bytes(emit_owned_object(_c_ir(fixture.read_text(), target), target))
    source = tmp_path / "foreign_collection.py"
    source.write_text(PROGRAM)
    binary = tmp_path / "foreign_collection"
    python_program_compiler(str(source), str(binary), backend="self",
                            runtime_archive=str(pcc_runtime_archive), object_inputs=(str(helper),))
    for backend in range(5):
        environment = dict(os.environ, PCC_GC_BACKEND=str(backend))
        environment.pop("PCC_GC_TRACING_ONLY", None)
        environment.pop("PCC_GC4_TRACING_ONLY", None)
        result = subprocess.run([str(binary)], env=environment, capture_output=True,
                                text=True, timeout=20)
        assert (result.returncode, result.stdout, result.stderr) == (
            0, "foreign-release-once\n", ""
        ), (backend, result)
