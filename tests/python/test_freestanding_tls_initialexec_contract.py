"""Explicit static initial-exec ABI for freestanding x64 Linux TLS only."""
from pathlib import Path

import pytest

from pcc.backend.self_backend_dispatch import emit_self_asm
from pcc.backend.target_objects import encode_assembly_object
from pcc.frontends.python.pipeline import compile_python
from tests.python import test_freestanding_gc_frame_registry as frame_gate

TARGET = "x86_64-unknown-linux-gnu"
SOURCE = '''from pcc import i64
from pcc.extern import c_abi_export
from pcc.unsafe import define_thread_local_i32, define_thread_local_ptr_null, global_addr, load_i32, store_i32, global_load_ptr, global_store_ptr, int_to_ptr, ptr_to_int
__pcc_freestanding__ = True
define_thread_local_i32("tls_integer", 37)
define_thread_local_ptr_null("tls_pointer")
@c_abi_export("exchange_integer")
def exchange_integer(value: i64) -> i64:
    slot = global_addr("tls_integer")
    old: i64 = load_i32(slot, 0)
    store_i32(slot, 0, value)
    return old
@c_abi_export("exchange_pointer")
def exchange_pointer(value: i64) -> i64:
    old = global_load_ptr("tls_pointer")
    global_store_ptr("tls_pointer", int_to_ptr(value))
    return ptr_to_int(old)
'''


def _ir(source, target, output):
    compile_python(str(source), str(output), emit_llvm_only=True, python_library=True,
                   backend="self", target_triple=target)
    return output.read_text()


@pytest.mark.parametrize("target", (TARGET, "aarch64-unknown-linux-gnu", "arm64-apple-darwin", "x86_64-pc-windows-msvc"))
def test_freestanding_tls_definition_model_is_target_specific(tmp_path, target):
    source = tmp_path / "tls_fixture.py"
    source.write_text(SOURCE)
    text = _ir(source, target, tmp_path / "fixture.ll")
    storage = "thread_local(initialexec)" if target == TARGET else "thread_local"
    assert "@tls_integer = " + storage + " global i32 37" in text
    assert "@tls_pointer = " + storage + " global ptr null" in text
    assert text.count("@llvm.threadlocal.address.p0(") >= 2
    assert encode_assembly_object(emit_self_asm(text), target)
    if target == TARGET:
        # Both spellings are already supported by the owned x64 backend and
        # must generate the same static TLS address value and object bytes.
        default = text.replace("thread_local(initialexec)", "thread_local")
        assert default != text
        assert emit_self_asm(default) == emit_self_asm(text)


def test_actual_frame_registry_explicit_initialexec_keeps_owned_raw_closure(tmp_path):
    text = _ir(frame_gate.FRAME_SOURCE, TARGET, tmp_path / "registry.ll")
    for name, tail in (("heads", "ptr null"), ("counts", "ptr null"), ("total", "i32 0")):
        assert "@pcc_gc_frame_node_pool_" + name + " = thread_local(initialexec) global " + tail in text
    assert "thread_local global" not in text
    prior = text.replace("thread_local(initialexec)", "thread_local")
    assembly = emit_self_asm(text)
    assert assembly == emit_self_asm(prior)
    object_bytes = encode_assembly_object(assembly, TARGET)
    assert object_bytes == encode_assembly_object(emit_self_asm(prior), TARGET)
    defined, undefined = frame_gate._object_external_symbols(object_bytes, TARGET)
    frame_gate._assert_exact_raw_imports(undefined, target_triple=TARGET, emitter="self")
    expected = frame_gate.PUBLIC_SYMBOLS | frame_gate.INTERNAL_SYMBOLS | frame_gate.TLS_STORAGE_SYMBOLS
    assert defined == expected
    assert "__tls_get_addr" not in undefined and "tls_get_addr" not in undefined
