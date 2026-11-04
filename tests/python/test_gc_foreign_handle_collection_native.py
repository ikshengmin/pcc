"""Default collectors release foreign payloads through their typed owner."""
import os
import subprocess


PROGRAM = r'''import gc
from pcc import i64
from pcc.extern import c_abi_export, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_rmw_i64, function_addr, load_i64, memset, stack_alloc,
)

set_release = extern("py_cpy_handle_set_release_fn", (c_ptr,), c_void)
new_handle = extern("py_cpy_handle_new", (c_ptr,), c_ptr)
release_handle = extern("pcc_gc_release", (c_ptr,), c_void)

@c_abi_export("record_foreign_release")
def record_foreign_release(counter: c_ptr) -> None:
    atomic_rmw_i64("add", counter, 0, 1, "relaxed")

def create(counter: c_ptr) -> None:
    handle = new_handle(counter)
    release_handle(handle)

def main():
    counter = stack_alloc(8)
    memset(counter, 0, 8)
    set_release(function_addr("record_foreign_release"))
    create(counter)
    # A normal later allocation ends the allocator's last-allocation root.
    keep = [17, 19]
    for attempt in range(4):
        gc.collect()
    released: i64 = i64(load_i64(counter, 0))
    assert released == 1
    assert keep == [17, 19]
    print("foreign-release-once")
main()
'''


def test_default_collectors_release_foreign_payload_once(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    source = tmp_path / "foreign_collection.py"
    source.write_text(PROGRAM)
    binary = tmp_path / "foreign_collection"
    python_program_compiler(str(source), str(binary), backend="self",
                            runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        environment = dict(os.environ, PCC_GC_BACKEND=str(backend))
        environment.pop("PCC_GC_TRACING_ONLY", None)
        environment.pop("PCC_GC4_TRACING_ONLY", None)
        result = subprocess.run([str(binary)], env=environment, capture_output=True,
                                text=True, timeout=20)
        assert (result.returncode, result.stdout, result.stderr) == (
            0, "foreign-release-once\n", ""
        ), (backend, result)
