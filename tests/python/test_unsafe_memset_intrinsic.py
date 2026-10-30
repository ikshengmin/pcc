"""Unsafe memset exposes byte writes while retaining evaluation and result semantics."""
import os
import platform
import re
import subprocess

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm
from pcc.frontends.python.pipeline import compile_python
from tests.python.test_darwin_tls_leaf_protection import emit
from tests.python.test_owned_external_tls import _c_ir

SIZES = (0, 1, 7, 8, 15, 16, 31, 32, 55, 56, 63, 64, 127, 128, 129)
TARGETS = ("arm64-apple-darwin", "aarch64-unknown-linux-gnu",
           "x86_64-unknown-linux-gnu", "x86_64-pc-windows-msvc")


def source_text():
    text = '''from pcc import i64
from pcc.extern import c_abi_export, c_ptr
from pcc.unsafe import memset, define_global_i64, global_addr, load_i64, store_i64
__pcc_freestanding__ = True
define_global_i64("memset_order", 0)
@c_abi_export("memset_eval_note")
def note(value: i64) -> None:
    slot = global_addr("memset_order")
    store_i64(slot, 0, load_i64(slot, 0) * 10 + value)
@c_abi_export("memset_eval_destination")
def destination(value: c_ptr) -> c_ptr:
    note(1)
    return value
@c_abi_export("memset_eval_fill")
def fill(value: i64) -> i64:
    note(2)
    return value
@c_abi_export("memset_eval_count")
def count(value: i64) -> i64:
    note(3)
    return value
@c_abi_export("fill_dynamic")
def fill_dynamic(target: c_ptr, value: i64, size: i64) -> c_ptr:
    return memset(target, value, size)
@c_abi_export("fill_ordered")
def fill_ordered(target: c_ptr, value: i64, size: i64) -> c_ptr:
    store_i64(global_addr("memset_order"), 0, 0)
    return memset(destination(target), fill(value), count(size))
@c_abi_export("read_order")
def read_order() -> i64:
    return load_i64(global_addr("memset_order"), 0)
'''
    for size in SIZES:
        text += (f'@c_abi_export("zero_{size}")\n'
                 f'def zero_{size}(target: c_ptr) -> c_ptr:\n'
                 f'    return memset(target, 0, {size})\n')
    return text


def compile_ir(tmp_path, target):
    source = tmp_path / "fills.py"
    source.write_text(source_text())
    output = tmp_path / "fills.ll"
    compile_python(str(source), str(output), emit_llvm_only=True, python_library=True,
                   backend="self", libpython_mode="off", target_triple=target)
    return output.read_text()


@pytest.mark.parametrize("target", TARGETS)
def test_unsafe_memset_reaches_existing_owned_intrinsic_path(tmp_path, target):
    text = compile_ir(tmp_path, target)
    calls = [line for line in text.splitlines() if " call " in line]
    assert any("@llvm.memset.p0.i64(" in line for line in calls)
    assert not any(re.search(r"@memset\(", line) for line in calls)
    assert emit_owned_object(text, target)
    if target == TARGETS[0]:
        assembly = emit_aarch64_darwin_asm(text, optimize=True)
        for size in SIZES:
            body = re.search(r"_zero_" + str(size) + r":\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)",
                             assembly, re.S).group(1)
            assert ("bl _memset" in body) == (size > 128), (size, body)


def driver_source():
    text = '''extern void *fill_dynamic(void *, long, long);
extern void *fill_ordered(void *, long, long);
extern long read_order(void);
'''
    for size in SIZES:
        text += f'extern void *zero_{size}(void *);\n'
    text += '''int main(void) {
    unsigned char buffer[176];
    for (int j = 0; j < 176; j++) buffer[j] = 165;
'''
    for size in SIZES:
        text += f'''    for (int j = 0; j < 176; j++) buffer[j] = 165;
    if (zero_{size}(buffer + 5) != buffer + 5) return 2;
    for (int j = 0; j < 176; j++) {{
        int expected = j >= 5 && j < {5 + size} ? 0 : 165;
        if (buffer[j] != expected) return 3;
    }}
'''
    for value in (-1, 0, 256, 511):
        text += f'''    for (int j = 0; j < 176; j++) buffer[j] = 165;
    if (fill_ordered(buffer + 3, {value}, 129) != buffer + 3) return 4;
    if (read_order() != 123) return 5;
    for (int j = 0; j < 176; j++) {{
        int expected = j >= 3 && j < 132 ? {value & 255} : 165;
        if (buffer[j] != expected) return 6;
    }}
'''
    text += '''    if (zero_0(0) != 0) return 7;
    if (fill_dynamic(buffer, 511, 0) != buffer) return 8;
    return 0;
}
'''
    return text


@pytest.mark.skipif(platform.system() != "Darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 execution")
@pytest.mark.parametrize("indexed", (False, True))
@pytest.mark.parametrize("registers", ("0", "1"))
def test_memset_return_order_bytes_and_unaligned_boundaries_execute(tmp_path, monkeypatch, indexed, registers):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    library = emit(compile_ir(tmp_path, TARGETS[0]), indexed, tmp_path)
    driver = emit_owned_object(_c_ir(driver_source(), TARGETS[0]), TARGETS[0])
    output = tmp_path / "fills"
    output.write_bytes(link_executable([library, driver]))
    output.chmod(0o755)
    run = subprocess.run([str(output)], capture_output=True, text=True, timeout=15,
                         env=dict(os.environ, PATH="/nonexistent"))
    assert (run.returncode, run.stdout, run.stderr) == (0, "", "")


def test_memset_result_remains_an_address_integer_in_application_code(tmp_path, pcc_runtime_archive):
    source = tmp_path / "address_result.py"
    source.write_text('''from pcc.unsafe import malloc, free, memset, load_i8

def fill(address):
    return memset(address, 260, 7)

def main():
    address = malloc(16)
    result = fill(address)
    print(isinstance(result, int), result == address, load_i8(result, 6))
    free(address)

main()
''')
    output = tmp_path / "address_result"
    compile_python(str(source), str(output), backend="self", libpython_mode="off",
                   runtime_archive=str(pcc_runtime_archive))
    for gc in range(5):
        run = subprocess.run([str(output)], capture_output=True, text=True, timeout=20,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc)))
        assert (run.returncode, run.stdout, run.stderr) == (0, "True True 4\n", ""), gc
