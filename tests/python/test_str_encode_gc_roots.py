"""The encoder reads its operands where they are, on every collector.

No collector moves an object while a mutator runs: GC0-GC3 never move one,
and GC4 relocates only inside a stopped world, leaving in place whatever a
thread's stack or registers name. The caller keeps the three operands alive
for the call, so the encoder takes no frame, address lease, pin or graph
lock: it runs the codec on the operands it was given and returns the new
bytes object, or NULL with the codec's exception. The model executes the
checked-in entry bodies; the codec itself has its own source-body tests
(test_str_encode_binding.py), and the native cases below run the compiled
runtime on all five collectors.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

from pcc.runtime.py import py_abi_constants as abi
from test_int_bytes_gc_roots import _BytesMemory

ROOT = Path(__file__).resolve().parents[2]
PORT = ROOT / "pcc/runtime/py/py_str_accessors.py"
GUARDS = (
    "pcc_gc_frame_enter", "pcc_gc_frame_leave", "pcc_gc_foreign_lease_acquire",
    "pcc_gc_foreign_lease_release", "pcc_gc_pin", "pcc_gc_take_pinned_slot",
    "pcc_py_gc_minor_graph_lock", "pcc_py_gc_minor_graph_unlock", "pcc_gc_store_root",
)


class _EncoderMemory(_BytesMemory):
    def __init__(self, pending=None, codec_error=False):
        super().__init__(phase="none", pending=pending)
        self.codec_error = codec_error
        self.codec_calls = []
        self.released = []
        self.value.tag = abi.PY_TYPE_STR
        self.signed.tag = abi.PY_TYPE_STR
        namespace = self.namespace

        def guard(name):
            def called(*args):
                raise AssertionError(name + " in the encoder")
            return called

        namespace.update({name: guard(name) for name in GUARDS})
        namespace.update({
            "global_addr": lambda name: name,
            "py_err_occurred": lambda: int(self.error is not None),
            "py_decref": self.released.append,
        })
        tree = ast.parse(PORT.read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(PORT), "exec"), namespace)
        namespace["_str_encode_names_leased"] = self.encode_names
        namespace["_str_encode_codec"] = self.encode_codec
        self.namespace = namespace
        if pending is not None:
            self.error = (59, pending)

    def _codec(self, kind, operands):
        self.codec_calls.append((kind, operands))
        if self.codec_error:
            self.error = (59, "unicode")
            return None
        return self.make("result", abi.PY_TYPE_BYTES)

    def encode_names(self, value, encoding, errors):
        return self._codec("names", (value, encoding, errors))

    def encode_codec(self, value, codec, mode):
        return self._codec("codec", (value, codec, mode))

    def encode_call(self, symbol="py_str_encode_with_encoding"):
        if symbol == "py_str_encode_with_encoding":
            return self.namespace[symbol](self.value, self.order, self.signed)
        return self.namespace[symbol](self.value)


def test_the_encoder_runs_the_codec_on_its_operands_without_a_guard():
    memory = _EncoderMemory()
    result = memory.encode_call()
    assert result is memory.latest["result"] and memory.error is None
    # The very operands it was given, not reloaded or leased copies.
    assert memory.codec_calls == [("names", (memory.value, memory.order, memory.signed))]
    assert not memory.released


@pytest.mark.parametrize("symbol,codec,mode", (
    ("py_str_utf8_encode", 0, 0),
    ("py_str_ascii_encode", 1, 0),
    ("py_str_latin1_encode", 2, 0),
))
def test_fixed_codec_exports_pass_their_codec_and_strict_mode(symbol, codec, mode):
    memory = _EncoderMemory()
    result = memory.encode_call(symbol)
    assert result is memory.latest["result"] and memory.error is None
    assert memory.codec_calls == [("codec", (memory.value, codec, mode))]


@pytest.mark.parametrize("symbol", ("py_str_encode_with_encoding", "py_str_utf8_encode"))
def test_a_pending_exception_is_kept_and_nothing_is_encoded(symbol):
    memory = _EncoderMemory(pending="earlier")
    assert memory.encode_call(symbol) is None
    assert memory.error == (59, "earlier") and not memory.codec_calls


@pytest.mark.parametrize("symbol", ("py_str_encode_with_encoding", "py_str_latin1_encode"))
def test_a_codec_error_returns_null_with_its_exception(symbol):
    memory = _EncoderMemory(codec_error=True)
    assert memory.encode_call(symbol) is None
    assert memory.error == (59, "unicode")
    # The codec produced nothing, so nothing is released but NULL.
    assert memory.released == [None]


def test_encoder_binders_capture_results_before_releasing_leases(tmp_path):
    from pcc.frontends.python.pipeline import compile_python
    from tests.owned_ir_validation import verify_ir_text
    source = tmp_path / "encode_leases.py"
    source.write_text('''
def method(value: str, encoding, errors):
    return value.encode(encoding, errors)
def immutable(value: str):
    return bytes(value, "utf8")
def mutable(value: str):
    return bytearray(value, "latin1")
''')
    output = tmp_path / "encode_leases.ll"
    compile_python(str(source), str(output), backend="self",
                   emit_llvm_only=True, python_library=True)
    text = output.read_text()
    verify_ir_text(text)
    for name, arguments, publications in (("method", 3, 0), ("immutable", 1, 1), ("mutable", 2, 2)):
        match = re.search(r"^define[^\n]*@user_encode_leases_" + name +
                          r"\([^\n]*\)[^\n]*\{\n(.*?)^\}", text, re.M | re.S)
        assert match, name
        body = match[1]
        assert "strict.nolib.stub" not in body and "@py_cpy_" not in body
        assert body.count("@pcc_gc_foreign_lease_acquire(") == arguments + publications
        assert len(re.findall(r"%call\.slot\.publish\.lease[^\n]*@pcc_gc_foreign_lease_acquire\(", body)) == publications
        helpers = {"method": (), "immutable": ("py_str_utf8_encode",),
                   "mutable": ("py_str_latin1_encode", "py_bytearray_from_obj")}[name]
        for helper in helpers:
            result = re.search(r"(%[^ ]+) = call ptr[^\n]*@" + helper + r"\([^\n]*\n([^\n]+)\n([^\n]+)", body)
            assert result is not None
            store = re.search(r"store ptr " + re.escape(result[1]) + r", ptr (%[^ ]+)", result[2])
            assert store is not None, result[2]
            assert "@pcc_gc_foreign_lease_acquire(ptr " + store[1] + ")" in result[3]
        assert "@pcc_gc_foreign_lease_release(" in body
        assert "@pcc_gc_take_pinned_slot(" in body
        assert "encode.lease.cleanup.error" not in body


_GC3_SOURCE = r'''
import gc
from pcc.extern import c_abi_export, c_obj
from pcc.unsafe import ptr_to_int, load_i32, store_i32

@c_abi_export("encode_gc_address")
def address(value: c_obj) -> int:
    return ptr_to_int(value)

@c_abi_export("encode_gc_flags")
def flags(value: c_obj) -> int:
    return load_i32(value, 12)

@c_abi_export("encode_gc_legacy_pin")
def legacy_pin(value: c_obj, enabled: int) -> None:
    old = load_i32(value, 12)
    store_i32(value, 12, (old | 64) if enabled else (old & ~64))

def fresh(value):
    return ("@" + value)[1:]

def move(holder, result):
    assert flags(holder[0]) & 4096, "source is not nursery-backed"
    before = address(holder[0])
    alias = [holder[0]]
    legacy_pin(alias[0], 0)
    gc.collect()
    assert address(holder[0]) != before, "GC3 nursery movement was not exercised"
    return result

finished = 0
class InvalidError:
    def __init__(self, holder):
        self.holder = holder
    def __del__(self):
        global finished
        alias = [self.holder[0]]
        legacy_pin(alias[0], 0)
        gc.collect()
        assert self.holder[0] == "café"
        finished += 1

def moving_failure(holder):
    move(holder, "unused")
    raise ValueError("later argument")

def main():
    holder = [fresh("control")]
    move(holder, "unused")
    holder = [fresh("café")]
    assert holder[0].encode(encoding=move(holder, "ascii"), errors="replace") == b"caf?"
    codec = [fresh("ascii")]
    assert "café".encode(encoding=codec[0], errors=move(codec, "replace")) == b"caf?"
    errors = [fresh("replace")]
    assert "café".encode(errors=errors[0], encoding=move(errors, "ascii")) == b"caf?"
    alias = [fresh("utf8")]
    legacy_pin(alias[0], 1)
    assert alias[0].encode(encoding=alias[0], errors=move(alias, "strict")) == b"utf8"
    assert flags(alias[0]) & 64, "earliest prior pin was not restored"
    legacy_pin(alias[0], 0)
    for index in range(16):
        holder = [fresh("café")]
        try:
            holder[0].encode(errors=InvalidError(holder))
        except TypeError:
            pass
        else:
            raise AssertionError("missing encoding argument error")
        assert holder[0] == "café"
        holder = [fresh("café")]
        try:
            holder[0].encode(encoding=moving_failure(holder), errors="replace")
        except ValueError:
            pass
        else:
            raise AssertionError("missing later-argument failure")
        assert holder[0] == "café"
    gc.collect()
    assert finished == 16, "temporary error operands were not released once"
    print("encoder nursery movement and finalizers", finished)
main()
'''


@pytest.mark.integration
def test_native_gc3_encoder_movement_and_finalizers(
    tmp_path, pcc_diagnostic_runtime_archive, python_program_compiler, monkeypatch,
):
    import os
    import subprocess
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "encoder_gc3.py"
    source.write_text(_GC3_SOURCE)
    binary = tmp_path / "encoder_gc3"
    python_program_compiler(str(source), str(binary), backend="self",
                            runtime_archive=str(pcc_diagnostic_runtime_archive))
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60,
        env=dict(os.environ, PCC_GC_BACKEND="3", PCC_GC_MINOR_ALLOC_MAX="4096",
                 PATH="", PCC_HOST_PYTHON="/nonexistent/host-python"))
    (tmp_path / "gc3.stdout").write_text(result.stdout)
    (tmp_path / "gc3.stderr").write_text(result.stderr)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout == "encoder nursery movement and finalizers 16\n"


_DIRECT_IR = r'''
@encoder_direct_map = internal constant i32 2
@encoder_direct_text = internal constant [5 x i8] c"caf\C3\A9"
declare void @pcc_gc_frame_enter(ptr, ptr)
declare void @pcc_gc_frame_leave(ptr)
declare void @pcc_gc_store_root(ptr, ptr)
declare ptr @pcc_gc_load_ptr(ptr, ptr)
declare ptr @py_str_new(ptr, i64)
declare ptr @py_str_utf8_encode(ptr)
declare i64 @py_bytes_len(ptr)
declare i64 @py_err_occurred()

define i64 @test_encoder_direct_entry() {
entry:
  %slots = alloca [2 x ptr]
  %source = getelementptr [2 x ptr], ptr %slots, i64 0, i64 0
  %result = getelementptr [2 x ptr], ptr %slots, i64 0, i64 1
  store ptr null, ptr %source
  store ptr null, ptr %result
  call void @pcc_gc_frame_enter(ptr @encoder_direct_map, ptr %slots)
  br label %loop
loop:
  %index = phi i64 [0, %entry], [%next, %again]
  %string = call ptr @py_str_new(ptr @encoder_direct_text, i64 5)
  store ptr %string, ptr %source
  ; There is deliberately no counted caller lease. The runtime export must
  ; publish local roots and lease its own raw view before allocation.
  %encoded = call ptr @py_str_utf8_encode(ptr %string)
  store ptr %encoded, ptr %result
  %error = call i64 @py_err_occurred()
  %ok = icmp eq i64 %error, 0
  br i1 %ok, label %check, label %bad
check:
  %current = call ptr @pcc_gc_load_ptr(ptr null, ptr %result)
  %length = call i64 @py_bytes_len(ptr %current)
  %lenok = icmp eq i64 %length, 5
  br i1 %lenok, label %bytes, label %bad
bytes:
  %stable = call ptr @pcc_gc_load_ptr(ptr null, ptr %result)
  %data = getelementptr i8, ptr %stable, i64 24
  %a = load i8, ptr %data
  %lastp = getelementptr i8, ptr %data, i64 4
  %last = load i8, ptr %lastp
  %aok = icmp eq i8 %a, 99
  %lastok = icmp eq i8 %last, -87
  %bytesok = and i1 %aok, %lastok
  br i1 %bytesok, label %again, label %bad
again:
  call void @pcc_gc_store_root(ptr %result, ptr null)
  call void @pcc_gc_store_root(ptr %source, ptr null)
  %next = add i64 %index, 1
  %done = icmp eq i64 %next, 2000
  br i1 %done, label %finish, label %loop
finish:
  call void @pcc_gc_frame_leave(ptr %slots)
  ret i64 0
bad:
  call void @pcc_gc_store_root(ptr %result, ptr null)
  call void @pcc_gc_store_root(ptr %source, ptr null)
  call void @pcc_gc_frame_leave(ptr %slots)
  ret i64 -1
}
'''


@pytest.mark.integration
def test_native_legacy_encoder_direct_entry(tmp_path, pcc_diagnostic_runtime_archive):
    import os
    import subprocess
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    target = host_target_triple()
    helper = tmp_path / "encoder_direct.o"
    helper.write_bytes(emit_owned_object('target triple = "' + target + '"\n' + _DIRECT_IR, target))
    source = tmp_path / "encoder_direct.py"
    source.write_text('''from pcc.extern import extern, c_int64
probe = extern("test_encoder_direct_entry", (), c_int64)
def main():
    print(probe())
main()
''')
    binary = tmp_path / "encoder_direct"
    compile_python(str(source), str(binary), backend="self",
                   runtime_archive=str(pcc_diagnostic_runtime_archive), link_args=(str(helper),))
    for backend in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_GC_MINOR_ALLOC_MAX="4096",
                     PATH="", PCC_HOST_PYTHON="/nonexistent/host-python"))
        assert result.returncode == 0 and result.stdout == "0\n", (backend, result.stdout, result.stderr)


def test_deferred_native_controls_have_verified_ir(tmp_path):
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.owned_ir_validation import verify_ir_text
    source = tmp_path / "encoder_movement_control.py"
    source.write_text(_GC3_SOURCE)
    output = tmp_path / "encoder_movement_control.ll"
    compile_python(str(source), str(output), backend="self",
                   emit_llvm_only=True, python_library=True)
    text = output.read_text()
    verify_ir_text(text)
    assert "strict.nolib.stub" not in text
    assert not re.search(r"\bcall\b[^\n]*@py_cpy_", text)
    verify_ir_text('target triple = "' + host_target_triple() + '"\n' + _DIRECT_IR)
    assert "@pcc_gc_foreign_lease_acquire" not in _DIRECT_IR
