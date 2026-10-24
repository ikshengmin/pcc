"""The generated cold reporter transfers its NEW exception owner to TLS."""

import os
from pathlib import Path
import platform
import subprocess

import pytest


DRIVER = '''
typedef long long i64;
extern void py_gc_init(void);
extern i64 probe(i64);
extern void *py_current_exception(void);
extern void py_clear_exception(void);
extern i64 pcc_gc_backend(void);
int main(void) {
    py_gc_init();
    i64 backend = pcc_gc_backend();
    int index = 0;
    while (index < 64) {
        if (probe(0) != 17 || py_current_exception()) return 10;
        if (probe(-1) != -1) return 11;
        void *error = py_current_exception();
        if (!error) return 12;
        int leaked = backend == 0 && __atomic_load_n((i64 *)error, 2) != 1;
        /* A pre-existing exception must not allocate or replace its owner. */
        if (probe(-1) != -1 || !py_current_exception()) return 13;
        if (backend == 0 && py_current_exception() != error) return 14;
        py_clear_exception();
        if (leaked) return 15;
        if (py_current_exception()) return 16;
        index = index + 1;
    }
    return 0;
}
'''


@pytest.mark.integration
@pytest.mark.skipif(platform.system() != "Darwin" or platform.machine() != "arm64",
                    reason="the existing reporter IR harness selects Darwin arm64")
def test_status_reporter_releases_constructor_owner_native(tmp_path, pcc_runtime_archive):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import _run_self_link_command
    from tests.python.test_owned_external_tls import _c_ir
    from tests.python.test_slot_call_status_reporting import _sites

    codegen, _function = _sites()
    ir = str(codegen.module)
    (tmp_path / "reporter.ll").write_text(ir)
    helper = tmp_path / "reporter.o"
    helper.write_bytes(emit_owned_object(ir, "arm64-apple-darwin"))
    (tmp_path / "driver.c").write_text(DRIVER)
    driver_ir = _c_ir(DRIVER, "arm64-apple-darwin")
    (tmp_path / "driver.ll").write_text(driver_ir)
    driver = tmp_path / "driver.o"
    driver.write_bytes(emit_owned_object(driver_ir, "arm64-apple-darwin"))
    binary = tmp_path / "native"
    _run_self_link_command(['/usr/bin/false'], None, binary, pcc_runtime_archive,
                           (str(helper), str(driver)), False, needs_libpython=False)
    for gc in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=25,
                                env=dict(os.environ, PCC_GC_BACKEND=str(gc), PATH="/nonexistent"))
        (tmp_path / ("gc" + str(gc) + ".stdout")).write_text(result.stdout)
        (tmp_path / ("gc" + str(gc) + ".stderr")).write_text(result.stderr)
        assert result.returncode == 0, (gc, result.returncode, result.stdout, result.stderr)
