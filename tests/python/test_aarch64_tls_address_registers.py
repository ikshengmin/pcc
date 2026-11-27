"""A thread-local address is a register candidate like any call result.

The allocator kept every ``llvm.*`` intrinsic result out of its pools, so a
thread-local address (the runtime's thread state, read on every container
lock and release) was stored to its stack slot after the TLV resolver and
reloaded at each use, including across calls. Its emitter already commits
an allocated result, so the address now lives in a register: in x19-x28
when it crosses a call. The handler's runtime spent about 23k more executed
stack-slot accesses than LLVM's code generation of the same IR in a stepped
window (docs/status.md, the P0 notes of item 3). The programs run on two
threads, so each sees its own cell.
"""
import os
import re
import subprocess

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm
from tests.python.test_darwin_tls_leaf_protection import emit
from tests.python.test_owned_external_tls import _c_ir


IR = '''target triple = "arm64-apple-darwin"
@tls_counter = thread_local global i64 5, align 8
declare ptr @llvm.threadlocal.address.p0(ptr)
define i64 @bump(i64 %x) {
entry:
  %y = add i64 %x, 1
  ret i64 %y
}
define i64 @tls_across_call() {
entry:
  %address = call ptr @llvm.threadlocal.address.p0(ptr @tls_counter)
  %old = load i64, ptr %address
  %new = call i64 @bump(i64 %old)
  store i64 %new, ptr %address
  %again = load i64, ptr %address
  ret i64 %again
}
define i64 @tls_local() {
entry:
  %address = call ptr @llvm.threadlocal.address.p0(ptr @tls_counter)
  %old = load i64, ptr %address
  %new = add i64 %old, 10
  store i64 %new, ptr %address
  ret i64 %new
}
'''


def _body(assembly: str, name: str) -> str:
    return re.search(r"^_" + name + r":\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)",
                     assembly, re.S | re.M).group(1)


def test_thread_local_address_stays_in_a_register(monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    assembly = emit_aarch64_darwin_asm(IR, optimize=True)
    for name in ("tls_across_call", "tls_local"):
        body = _body(assembly, name)
        assert "blr x16" in body, (name, body)
        # No value slot traffic: the address is neither stored after the
        # resolver nor reloaded before its uses.
        assert not re.search(r"\b(?:stur|ldur|str|ldr) x\d+, \[x29", body), (name, body)
    across = _body(assembly, "tls_across_call")
    # It survives the call to bump in a callee-saved register.
    assert re.search(r"mov x(19|2[0-8]), x0", across), across


# executes the Darwin TLV resolver on two threads
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
@pytest.mark.parametrize("indexed", [False, True])
def test_thread_local_registers_run_per_thread(tmp_path, monkeypatch, indexed):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    unit = emit(IR, indexed, tmp_path)
    driver = '''
typedef void *pthread_t;
extern int pthread_create(pthread_t *, void *, void *(*)(void *), void *);
extern int pthread_join(pthread_t, void **);
extern long tls_across_call(void);
extern long tls_local(void);
static void *worker(void *arg) {
    (void)arg;
    for (long n = 0; n < 1000; ++n) {
        long expected = 5 + 11 * n + 1;
        if (tls_across_call() != expected) return (void *)1;
        if (tls_local() != expected + 10) return (void *)2;
    }
    return 0;
}
int main(void) {
    pthread_t threads[2];
    for (long i = 0; i < 2; ++i)
        if (pthread_create(&threads[i], 0, worker, 0)) return 3;
    for (int i = 0; i < 2; ++i) {
        void *result = 0;
        if (pthread_join(threads[i], &result) || result) return 4;
    }
    return worker(0) != 0;
}
'''
    driver_object = emit_owned_object(_c_ir(driver, "arm64-apple-darwin"), "arm64-apple-darwin")
    binary = tmp_path / "tls-address-registers"
    binary.write_bytes(link_executable([unit, driver_object]))
    binary.chmod(0o755)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                            env=dict(os.environ, PATH="/nonexistent"))
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
