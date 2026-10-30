"""TLS resolver calls preserve live values, x0 aliases and return protection."""

import os
import platform
import subprocess

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm
from tests.python.test_darwin_tls_leaf_protection import emit
from tests.python.test_owned_external_tls import _c_ir


IR = '''target triple = "arm64-apple-darwin"
@tls_reg_value = thread_local global i64 37, align 8
declare ptr @llvm.threadlocal.address.p0(ptr)
define ptr @tls_alias(ptr %value) {
entry:
  %alias = getelementptr i8, ptr %value, i64 0
  %address = call ptr @llvm.threadlocal.address.p0(ptr @tls_reg_value)
  %old = load i64, ptr %address
  %next = add i64 %old, 1
  store i64 %next, ptr %address
  ret ptr %alias
}
define i64 @tls_pool(i64 %a, i64 %b, i64 %c, i64 %d, i64 %e, i64 %f, i64 %g) {
entry:
  %first = add i64 %a, 11
  %address = call ptr @llvm.threadlocal.address.p0(ptr @tls_reg_value)
  %old = load i64, ptr %address
  %ab = add i64 %first, %b
  %abc = add i64 %ab, %c
  %abcd = add i64 %abc, %d
  %abcde = add i64 %abcd, %e
  %abcdef = add i64 %abcde, %f
  %abcdefg = add i64 %abcdef, %g
  %result = add i64 %abcdefg, %old
  ret i64 %result
}
'''


def test_tls_resolver_protects_live_x0_alias_and_return_address(monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    assembly = emit_aarch64_darwin_asm(IR, optimize=True)
    alias = assembly.split("_tls_alias:", 1)[1].split("_tls_pool:", 1)[0]
    pool = assembly.split("_tls_pool:", 1)[1]
    assert "mov x19, x0" in alias
    assert "blr x16" in pool and "paciasp" in pool and "autiasp" in pool


@pytest.mark.skipif(platform.system() != "Darwin" or platform.machine() != "arm64",
                    reason="executes Darwin TLV resolver")
@pytest.mark.parametrize("indexed", [False, True])
@pytest.mark.parametrize("registers", ["0", "1"])
def test_tls_pool_and_x0_alias_survive_cold_resolver_on_two_threads(tmp_path, monkeypatch, indexed, registers):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    unit = emit(IR, indexed, tmp_path)
    driver = '''
typedef void *pthread_t;
extern int pthread_create(pthread_t *, void *, void *(*)(void *), void *);
extern int pthread_join(pthread_t, void **);
extern void *tls_alias(void *);
extern long tls_pool(long, long, long, long, long, long, long);
static void *worker(void *arg) {
    long identity = (long)arg;
    for (long n = 0; n < 1000; ++n) {
        if (tls_pool(identity, 2, 3, 4, 5, 6, 7) != identity + 75 + n)
            return (void *)1;
        if (tls_alias(&identity) != &identity) return (void *)2;
    }
    return 0;
}
int main(void) {
    pthread_t threads[2];
    for (long i = 0; i < 2; ++i)
        if (pthread_create(&threads[i], 0, worker, (void *)(i + 100))) return 3;
    for (int i = 0; i < 2; ++i) {
        void *result = 0;
        if (pthread_join(threads[i], &result) || result) return 4;
    }
    return worker((void *)7) != 0;
}
'''
    driver_object = emit_owned_object(_c_ir(driver, "arm64-apple-darwin"), "arm64-apple-darwin")
    binary = tmp_path / "tls-registers"
    binary.write_bytes(link_executable([unit, driver_object]))
    binary.chmod(0o755)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                            env=dict(os.environ, PATH="/nonexistent"))
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
