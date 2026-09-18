"""Execute self-emitted Darwin TLS; the C thread driver is an external oracle."""

import os
import platform
import shutil
import subprocess

import pytest

from pcc.backend import BackendUnavailable
from pcc.backend.arm64_asm_driver import assemble_file, assemble_lines
from pcc.backend.macho_exec import link_executable
from pcc.backend.macho_obj import emit_object
from pcc.backend.native_object import decode_native_object
from pcc.backend.self_backend_aarch64_darwin import (
    emit_aarch64_darwin_asm, emit_aarch64_darwin_indexed_transport,
)
from pcc.backend.self_backend_aarch64_darwin_addr import materialize_global_address
from pcc.backend.self_backend_module_symbols import prepare_module_symbols
from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.self_backend_indexed_codec import encode_indexed_module_file
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from tests.python.process_timeout import run_process_group_timeout


_TLS_IR = '''target triple = "arm64-apple-darwin"
@counter = thread_local global i64 37, align 8
@zero = internal thread_local global ptr null, align 8
define i64 @tls_read() {
entry:
  %value = load i64, ptr @counter
  ret i64 %value
}
define i64 @tls_update(i64 %value) {
entry:
  %old = load i64, ptr @counter
  store i64 %value, ptr @counter
  ret i64 %old
}
define ptr @tls_address() {
entry:
  ret ptr @counter
}
define ptr @tls_zero() {
entry:
  %value = load ptr, ptr @zero
  ret ptr %value
}
define void @tls_set_zero(ptr %value) {
entry:
  store ptr %value, ptr @zero
  ret void
}
define i64 @tls_live_arguments(i64 %a, i64 %b, i64 %c) {
entry:
  %value = load i64, ptr @counter
  %x = add i64 %a, %b
  %y = add i64 %x, %c
  %z = add i64 %y, %value
  ret i64 %z
}
'''


@pytest.mark.parametrize("mode,optimize", [
    ("asm", False), ("asm", True), ("structured", False),
    pytest.param("pcc1-asm", False, marks=pytest.mark.integration),
    pytest.param("pcc1-pco", False, marks=pytest.mark.integration),
])
@pytest.mark.pcc_gate(unavailable=(
    None if platform.system() == "Darwin" and platform.machine() == "arm64"
    and shutil.which(os.environ.get("CC", "cc"))
    else "Darwin arm64 execution and an external C oracle driver are required"
))
def test_self_emitted_tls_initializers_and_thread_isolation(tmp_path, request, mode, optimize):
    asm = emit_aarch64_darwin_asm(_TLS_IR, optimize=optimize)
    (tmp_path / "tls.s").write_text(asm)
    parsed = parse_self_backend_module(_TLS_IR)
    if mode.startswith("pcc1-"):
        compiler = request.getfixturevalue("native_pcc1_compiler")
        ir_path = tmp_path / "tls.ll"
        ir_path.write_text(_TLS_IR)
        env = dict(os.environ, PCC_HOST_PYTHON="/usr/bin/false", PCC_HOST_PCC="/usr/bin/false")
        env.pop("LC_ALL", None)
        if mode == "pcc1-asm":
            asm_path = tmp_path / "native-tls.s"
            command = [str(compiler), "--pcc-self-backend-emit-worker", str(ir_path),
                       str(tmp_path / "native-result"), str(asm_path), ""]
        else:
            sidecar = tmp_path / "tls.pidx"
            for function in parsed.functions:
                get_indexed_function_kernel(function)
            encode_indexed_module_file(str(sidecar), parsed)
            pco_path = tmp_path / "tls.pco"
            command = [str(compiler), "--pcc-self-backend-indexed-emit-worker",
                       str(sidecar), str(pco_path), "PCO"]
        emitted = run_process_group_timeout(command, env=env, timeout=60)
        (tmp_path / "native-emit.stdout").write_text(emitted.stdout)
        (tmp_path / "native-emit.stderr").write_text(emitted.stderr)
        assert emitted.returncode == 0, emitted.stdout + emitted.stderr
        if mode == "pcc1-asm":
            sections, undefined = assemble_file(asm_path.read_text())
        else:
            obj = decode_native_object(pco_path.read_bytes())
            sections, undefined = obj.to_sections()
    elif mode == "asm":
        sections, undefined = assemble_file(asm)
    else:
        transport = emit_aarch64_darwin_indexed_transport(parsed, optimize=False)
        assert transport.native_finalized
        assert transport.fallback_instruction_count == 0
        sections, undefined = assemble_lines(
            transport.line_chunks, transport.structured_sections,
            transport.encoded_line_records, transport.structured_symbol_names,
        )
    tls_object = emit_object(sections, undefined=undefined)
    symbols = prepare_module_symbols(_TLS_IR, list(parsed.globals_), list(parsed.functions))
    flag_lines = [".section __TEXT,__text,regular,pure_instructions", ".globl _tls_flags",
                  ".p2align 2", "_tls_flags:", "  cmp xzr, xzr"]
    flag_lines.extend(materialize_global_address("counter", "x10", symbols))
    flag_lines.extend(["  cset w0, eq", "  ret"])
    flag_sections, flag_undefined = assemble_file("\n".join(flag_lines) + "\n")
    flag_object = emit_object(flag_sections, undefined=flag_undefined)
    driver = tmp_path / "driver.c"
    driver.write_text(r'''
        #include <pthread.h>
        #include <stdint.h>
        extern int64_t tls_read(void);
        extern int64_t tls_update(int64_t);
        extern void *tls_address(void);
        extern void *tls_zero(void);
        extern void tls_set_zero(void *);
        extern int64_t tls_live_arguments(int64_t, int64_t, int64_t);
        extern int tls_flags(void);
        static int ready;
        static int failed;
        static void *addresses[2];
        static void *worker(void *opaque) {
            intptr_t index = (intptr_t)opaque;
            int64_t value = 100 + index;
            if (tls_read() != 37 || tls_zero() != 0)
                __atomic_store_n(&failed, 1, __ATOMIC_RELEASE);
            addresses[index] = tls_address();
            if (tls_update(value) != 37)
                __atomic_store_n(&failed, 1, __ATOMIC_RELEASE);
            tls_set_zero(addresses[index]);
            __atomic_add_fetch(&ready, 1, __ATOMIC_RELEASE);
            while (__atomic_load_n(&ready, __ATOMIC_ACQUIRE) != 2) {}
            if (tls_read() != value || tls_zero() != addresses[index]
                || tls_live_arguments(11, 22, 33) != value + 66)
                __atomic_store_n(&failed, 1, __ATOMIC_RELEASE);
            return 0;
        }
        int main(void) {
            if (tls_flags() != 1) return 10;
            if (tls_read() != 37 || tls_zero() != 0) return 2;
            if (tls_update(55) != 37) return 3;
            pthread_t threads[2];
            if (pthread_create(&threads[0], 0, worker, (void *)0)) return 4;
            if (pthread_create(&threads[1], 0, worker, (void *)1)) return 5;
            if (pthread_join(threads[0], 0) || pthread_join(threads[1], 0)) return 6;
            if (__atomic_load_n(&failed, __ATOMIC_ACQUIRE)) return 7;
            if (tls_read() != 55 || tls_zero() != 0) return 8;
            if (addresses[0] == addresses[1] || addresses[0] == tls_address()
                || addresses[1] == tls_address()) return 9;
            return 0;
        }
    ''')
    driver_object = tmp_path / "driver.o"
    built = subprocess.run([os.environ.get("CC", "cc"), "-c", "-O1", "-pthread",
                            str(driver), "-o", str(driver_object)],
                           capture_output=True, text=True, timeout=30)
    assert built.returncode == 0, built.stdout + built.stderr
    executable = tmp_path / "tls"
    executable.write_bytes(link_executable([tls_object, flag_object, driver_object.read_bytes()]))
    executable.chmod(0o755)
    ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=20)
    assert ran.returncode == 0, ran.stdout + ran.stderr


@pytest.mark.parametrize("declaration", [
    "@value = thread_local(localexec) global i32 7",
    "@value = thread_local global [2 x i32] [i32 7, i32 9]",
    "@value = thread_local global ptr inttoptr (i64 1 to ptr)",
    '@value = thread_local global i32 7, section "custom"',
    "@value = weak thread_local global i32 7",
])
def test_unsupported_tls_definitions_fail_explicitly(declaration):
    with pytest.raises(BackendUnavailable, match="TLS"):
        emit_aarch64_darwin_asm('target triple = "arm64-apple-darwin"\n' + declaration)


def test_global_initializer_cannot_capture_one_threads_tls_address():
    with pytest.raises(BackendUnavailable, match="TLS address"):
        emit_aarch64_darwin_asm(_TLS_IR + "\n@escape = global ptr @counter\n")
