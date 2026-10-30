"""Explicit TLS addresses remain values across branches, calls, and threads."""
import os
import platform
import re
import subprocess

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm
from pcc.frontends.python.pipeline import compile_python
from tests.python.test_darwin_tls_leaf_protection import DRIVER, emit
from tests.python.test_owned_external_tls import _c_ir
from tests.python.test_self_backend_darwin_tls import _TLS_IR

TARGETS = ("arm64-apple-darwin", "aarch64-unknown-linux-gnu",
           "x86_64-unknown-linux-gnu", "x86_64-pc-windows-msvc")
FRONTEND = '''from pcc import i64
from pcc.extern import c_abi_export
from pcc.unsafe import define_thread_local_i32, define_global_i32, global_addr, load_i32, store_i32
__pcc_freestanding__ = True
define_thread_local_i32("front_counter", 37)
define_global_i32("ordinary_counter", 7)
@c_abi_export("tls_front")
def tls_front(delta: i64) -> i64:
    slot = global_addr("front_counter")
    old: i64 = load_i32(slot, 0)
    if delta > 0:
        store_i32(slot, 0, old + delta)
    else:
        store_i32(slot, 0, old - delta)
    return old + load_i32(slot, 0)
@c_abi_export("ordinary_read")
def ordinary_read() -> i64:
    return load_i32(global_addr("ordinary_counter"), 0)
'''


def address_ir(target="arm64-apple-darwin"):
    def rewrite(match):
        text = match.group(0)
        queries = []
        for name in ("counter", "zero"):
            if "@" + name in text:
                text = text.replace("@" + name, "%address_" + name)
                queries.append("  %address_" + name
                               + " = call ptr @llvm.threadlocal.address.p0(ptr @" + name + ")")
        text = text.replace("entry:\n", "entry:\n" + "\n".join(queries) + "\n")
        if "@tls_update(" in text:
            text = text.replace("  store i64", "  call void @tls_observe()\n  store i64")
        return text
    text = re.sub(r"^define [^\n]*\{\n.*?^}", rewrite, _TLS_IR, flags=re.M | re.S)
    text += """define i64 @tls_condition(i64 %left, i64 %right) {
entry:
  %equal = icmp eq i64 %left, %right
  %address = call ptr @llvm.threadlocal.address.p0(ptr @counter)
  %value = load i64, ptr %address
  %result = select i1 %equal, i64 %value, i64 -1
  ret i64 %result
}
"""
    return (text.replace("arm64-apple-darwin", target)
            + "declare ptr @llvm.threadlocal.address.p0(ptr)\ndeclare void @tls_observe()\n")


def frontend_ir(tmp_path, target):
    source = tmp_path / "address.py"
    source.write_text(FRONTEND)
    output = tmp_path / "address.ll"
    compile_python(str(source), str(output), emit_llvm_only=True, python_library=True,
                   libpython_mode="off", backend="self", target_triple=target)
    return output.read_text()


@pytest.mark.parametrize("target", TARGETS)
def test_frontend_keeps_one_tls_address_value_and_emits_owned_object(tmp_path, target):
    text = frontend_ir(tmp_path, target)
    queries = [line for line in text.splitlines()
               if " call " in line and "@llvm.threadlocal.address.p0(" in line]
    assert len(queries) == 1, queries
    assert emit_owned_object(text, target)
    if target == "arm64-apple-darwin":
        assembly = emit_aarch64_darwin_asm(text, optimize=True)
        assert assembly.count("blr x16") == 1
        assert "paciasp" in assembly and "autiasp" in assembly
        assert "mrs x17, nzcv" not in assembly
        assert "msr nzcv, x17" not in assembly
        assert "sub sp, sp, #48" not in assembly


@pytest.mark.parametrize("target", TARGETS)
def test_global_pointer_access_emits_owned_tls_on_every_target(tmp_path, target):
    source = tmp_path / "pointer_access.py"
    source.write_text('''from pcc import i64
from pcc.extern import c_abi_export
from pcc.unsafe import (
    define_thread_local_ptr_null, global_load_ptr, global_store_ptr,
    int_to_ptr, ptr_to_int,
)
__pcc_freestanding__ = True
define_thread_local_ptr_null("pointer_cell")
@c_abi_export("pointer_exchange")
def exchange(value: i64) -> i64:
    previous = global_load_ptr("pointer_cell")
    global_store_ptr("pointer_cell", int_to_ptr(value))
    return ptr_to_int(previous)
''')
    output = tmp_path / "pointer_access.ll"
    compile_python(str(source), str(output), emit_llvm_only=True, python_library=True,
                   libpython_mode="off", backend="self", target_triple=target)
    text = output.read_text()
    assert "@pointer_cell = thread_local global ptr null" in text
    assert emit_owned_object(text, target)
    if target == TARGETS[0]:
        assembly = emit_aarch64_darwin_asm(text, optimize=True)
        assert assembly.count("blr x16") == 2
        assert "paciasp" in assembly and "autiasp" in assembly


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("operand", ("ptr @ordinary", "ptr %source", "i64 0"))
def test_tls_address_rejects_non_tls_or_non_global_operands(target, operand):
    text = (f'target triple = "{target}"\n@ordinary = global i64 0\n'
            'declare ptr @llvm.threadlocal.address.p0(ptr)\n'
            'define ptr @invalid(ptr %source) {\nentry:\n'
            '  %address = call ptr @llvm.threadlocal.address.p0(' + operand + ')\n'
            '  ret ptr %address\n}\n')
    with pytest.raises(BackendUnavailable, match="thread-local global pointer"):
        emit_owned_object(text, target)


@pytest.mark.skipif(platform.system() != "Darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 execution")
@pytest.mark.parametrize("indexed", (False, True))
@pytest.mark.parametrize("registers", ("0", "1"))
def test_tls_address_values_survive_calls_and_remain_thread_local(tmp_path, monkeypatch, indexed, registers):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    data = emit(address_ir(), indexed, tmp_path)
    front = emit_owned_object(frontend_ir(tmp_path, TARGETS[0]), TARGETS[0])
    driver = DRIVER.replace("static int ready;", "extern long tls_front(long);\n"
                            "extern long tls_condition(long, long);\nstatic int ready;")
    driver = driver.replace("    long value = 100 + index;", """    long value = 100 + index;
    if (tls_front(index + 1) != 75 + index
        || tls_front(-2) != 78 + 2 * index)
        __atomic_store_n(&failed, 1, 3);""")
    driver = driver.replace("    if (tls_read() != 55", "    if (tls_front(3) != 77) return 11;\n    if (tls_read() != 55")
    driver = driver.replace("    tls_set_zero(addresses[index]);", """    tls_set_zero(addresses[index]);
    if (tls_condition(index, index) != value
        || tls_condition(index, index + 1) != -1)
        __atomic_store_n(&failed, 1, 3);""")
    driver += "\nvoid tls_observe(void) { (void)tls_read(); }\n"
    driver_object = emit_owned_object(_c_ir(driver, TARGETS[0]), TARGETS[0])
    executable = tmp_path / "address"
    executable.write_bytes(link_executable([data, front, driver_object]))
    executable.chmod(0o755)
    run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=20,
                         env=dict(os.environ, PATH="/nonexistent"))
    assert (run.returncode, run.stdout, run.stderr) == (0, "", "")
