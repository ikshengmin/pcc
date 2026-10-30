"""A Darwin TLS resolver is a call even when the input IR looks leaf-only."""

import os
import platform
import subprocess

import pytest

from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.macho_exec import link_executable
from pcc.backend.native_object import NativeObject
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
from pcc.backend.self_backend_indexed_codec import (
    decode_indexed_module_file,
    encode_indexed_module_file,
)
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.backend.self_backend_parse import parse_self_backend_module
from tests.python.test_owned_external_tls import _c_ir
from tests.python.test_self_backend_darwin_tls import _TLS_IR

PACIASP = bytes.fromhex("3f2303d5")
AUTIASP = bytes.fromhex("bf2303d5")


def emit(text, indexed, tmp_path):
    if not indexed:
        return emit_owned_object(text, "arm64-apple-darwin")
    module = parse_self_backend_module(text)
    for function in module.functions:
        get_indexed_function_kernel(function)
    path = tmp_path / "tls.pcir"
    encode_indexed_module_file(str(path), module)
    decoded = decode_indexed_module_file(str(path))
    transport = emit_aarch64_darwin_indexed_transport(
        decoded, optimize=True, structured_instructions=True,
    )
    sections, undefined = transport.assemble_sections()
    return NativeObject.from_sections(sections, undefined=undefined).to_macho()


def text_bytes(data):
    from pcc.backend.macho_spec import parse_object

    section = next(row for row in parse_object(data).sections()
                   if row["segname_str"] == "__TEXT" and row["sectname_str"] == "__text")
    return data[section["offset"]:section["offset"] + section["size"]]


@pytest.mark.parametrize("indexed", (False, True))
@pytest.mark.parametrize("body", (
    "%value = load i64, ptr @counter\n  ret i64 %value",
    "store i64 41, ptr @counter\n  ret i64 0",
    "%address = ptrtoint ptr @counter to i64\n  ret i64 %address",
    "%address = getelementptr i8, ptr @counter, i64 1\n  %value = load i8, ptr %address\n  %wide = zext i8 %value to i64\n  ret i64 %wide",
))
@pytest.mark.parametrize("external", (False, True))
def test_tls_hidden_call_has_protected_return_address(body, indexed, external, tmp_path, monkeypatch):
    monkeypatch.delenv("PCC_SELF_BRANCH_PROTECTION", raising=False)
    text = ('target triple = "arm64-apple-darwin"\n'
            '@counter = thread_local global i64 37, align 8\n'
            'define i64 @probe() {\nentry:\n  ' + body + '\n}\n')
    if external:
        text = text.replace('thread_local global i64 37', 'external thread_local global i64')
    data = emit(text, indexed, tmp_path)
    code = text_bytes(data)
    protected = PACIASP in code and AUTIASP in code
    assert protected, "TLS resolver spills LR: the caller return needs signing"


@pytest.mark.parametrize("indexed", (False, True))
def test_unused_tls_does_not_remove_plain_leaf_optimization(indexed, tmp_path, monkeypatch):
    monkeypatch.delenv("PCC_SELF_BRANCH_PROTECTION", raising=False)
    text = '''target triple = "arm64-apple-darwin"
@counter = thread_local global i64 37, align 8
@counter_suffix = global i64 41, align 8
define i64 @probe() {
entry:
  %value = load i64, ptr @counter_suffix
  ret i64 %value
}
'''
    data = emit(text, indexed, tmp_path)
    code = text_bytes(data)
    protected = PACIASP in code or AUTIASP in code
    assert not protected


def test_explicit_branch_protection_setting_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_SELF_BRANCH_PROTECTION", "0")
    text = '''target triple = "arm64-apple-darwin"
@counter = thread_local global i64 37, align 8
define i64 @probe() {
entry:
  %value = load i64, ptr @counter
  ret i64 %value
}
'''
    data = emit(text, False, tmp_path)
    code = text_bytes(data)
    protected = PACIASP in code or AUTIASP in code
    assert not protected


DRIVER = '''
typedef long intptr_t;
typedef void *pthread_t;
extern int pthread_create(pthread_t *, const void *, void *(*)(void *), void *);
extern int pthread_join(pthread_t, void **);
extern long tls_read(void);
extern long tls_update(long);
extern void *tls_address(void);
extern void *tls_zero(void);
extern void tls_set_zero(void *);
extern long tls_live_arguments(long, long, long);
static int ready;
static int failed;
static void *addresses[2];
static void *worker(void *opaque) {
    intptr_t index = (intptr_t)opaque;
    long value = 100 + index;
    if (tls_read() != 37 || tls_zero() != 0)
        __atomic_store_n(&failed, 1, 3);
    addresses[index] = tls_address();
    if (tls_update(value) != 37)
        __atomic_store_n(&failed, 1, 3);
    tls_set_zero(addresses[index]);
    __atomic_add_fetch(&ready, 1, 4);
    while (__atomic_load_n(&ready, 2) != 2) {}
    if (tls_read() != value || tls_zero() != addresses[index]
        || tls_live_arguments(11, 22, 33) != value + 66)
        __atomic_store_n(&failed, 1, 3);
    return 0;
}
int main(void) {
    if (tls_read() != 37 || tls_zero() != 0) return 2;
    if (tls_update(55) != 37) return 3;
    pthread_t threads[2];
    if (pthread_create(&threads[0], 0, worker, (void *)0)) return 4;
    if (pthread_create(&threads[1], 0, worker, (void *)1)) return 5;
    if (pthread_join(threads[0], 0) || pthread_join(threads[1], 0)) return 6;
    if (__atomic_load_n(&failed, 2)) return 7;
    if (tls_read() != 55 || tls_zero() != 0) return 8;
    if (addresses[0] == addresses[1] || addresses[0] == tls_address()
        || addresses[1] == tls_address()) return 9;
    return 0;
}
'''


@pytest.mark.skipif(platform.system() != "Darwin" or platform.machine() != "arm64",
                    reason="requires native Darwin arm64 execution")
@pytest.mark.parametrize("indexed", (False, True))
def test_protected_tls_executes_with_owned_c_driver(tmp_path, monkeypatch, indexed):
    monkeypatch.delenv("PCC_SELF_BRANCH_PROTECTION", raising=False)
    data = emit(_TLS_IR, indexed, tmp_path)
    code = text_bytes(data)
    protected = PACIASP in code and AUTIASP in code
    assert protected
    source = tmp_path / "driver.c"
    source.write_text(DRIVER)
    ir = _c_ir(DRIVER, "arm64-apple-darwin")
    (tmp_path / "driver.ll").write_text(ir)
    driver = emit_owned_object(ir, "arm64-apple-darwin")
    (tmp_path / "driver.o").write_bytes(driver)
    (tmp_path / "tls.o").write_bytes(data)
    executable = tmp_path / "tls"
    executable.write_bytes(link_executable([data, driver]))
    executable.chmod(0o755)
    run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=25,
                         env=dict(os.environ, PATH="/nonexistent"))
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
    assert run.stdout == "" and run.stderr == ""
