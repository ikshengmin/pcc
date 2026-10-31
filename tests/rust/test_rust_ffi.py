"""Original Rust/C inputs through owned frontends; not whole-Meson success."""
from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.targets import DEFAULT_TARGET

HERE = Path(__file__).parent
UPSTREAM = HERE / "upstream"
RECORD = "#[repr(C)] struct P { value:i32 } "
ATTEST = {"experimental_rustc_checked": True}
NATIVE = None if sys.platform.startswith("linux") and platform.machine() in {"x86_64", "AMD64"} else "requires Linux x86-64 owned execution"
HASHES = {
    "meson_adder.rs": "654d4f498384c21d51627908a842fe8454e5918ef7ba9317a740d7623c9c0933",
    "meson_zero.c": "fc2751e904e85ba2e1ce45478534270489071fda1b7266df9ec409b2ae10ec31",
    "meson_zero_static.c": "862ddec242b5a9d0cbb741af84060ca46a6958c38987d92eb86d9a2a32b96e59",
}


def original(path):
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == HASHES[path.name]
    return data.decode()


def owned_c(source, name, target):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
    from pcc.frontends.c.parse import make_c_parser
    from pcc.frontends.c.passes.context import PassContext
    from pcc.frontends.c.preprocessor import preprocess

    ast = make_c_parser().parse(preprocess(source, target_triple=target), filename=name)
    generator = CCodeGenerator(translation_unit_name=name, pass_ctx=PassContext(opt_level=0, target_triple=target))
    generator.generate_code(ast)
    return emit_owned_object(str(generator.module), target)


def forbidden(*args, **kwargs):
    raise AssertionError("owned source compilation/assembly/link invoked an external tool")


def record_run(image, expected, tmp_path):
    binary = tmp_path / "owned-rust-c"
    binary.write_bytes(image)
    binary.chmod(0o700)
    run = subprocess.run([str(binary)], capture_output=True, timeout=3)
    (tmp_path / "stdout.bin").write_bytes(run.stdout)
    (tmp_path / "stderr.bin").write_bytes(run.stderr)
    (tmp_path / "execution.json").write_text(json.dumps({
        "binary_sha256": hashlib.sha256(image).hexdigest(), "returncode": run.returncode,
        "stdout_hex": run.stdout.hex(), "stderr_hex": run.stderr.hex(),
    }, indent=2))
    assert (run.returncode, run.stdout, run.stderr) == (0, expected, b"")


@pytest.mark.parametrize("source, message", [
    ('extern "C-unwind" { fn f(); }', "C-unwind"),
    ('extern "C-unwind" fn f(){}', "C-unwind"),
    ('extern "system" fn f(){}', "non-unwinding"),
    ('extern "C" {fn f();} fn g(){f();}', "unsafe context"),
    ('extern "C" {fn f();} fn g(){unsafe {} f();}', "unsafe context"),
    ('unsafe fn f(){} fn g(){f();}', "unsafe context"),
    ('extern "C" {fn f(mut x:i32);}', "parameter patterns"),
    ('extern "C" {fn f(x:i32,x:i32);}', "duplicate parameter"),
    ('extern "C" {fn f(){}}', "expected ';'"),
    ('#[no_mangle] fn f(){}', "only on extern C"),
    ('#[no_mangle] struct P{x:i32}', "only on extern C"),
    ('struct P{x:i32} fn f(p:&P){}', "repr\\(C\\)"),
    ('#[repr(C)] struct P{x:bool} fn f(p:&P){}', "only i32"),
    ('#[repr(C)] struct P{x:()} fn f(p:&P){}', "only i32"),
    (RECORD + 'fn f(p:&mut P){}', "mutable references"),
    (RECORD + 'fn f(p:&P)->&P{p}', "reference returns"),
    (RECORD + 'struct Q{p:&P}', "reference storage"),
    (RECORD + 'fn f(p:&P){let t=(p,);}', "reference storage"),
    (RECORD + 'fn f(p:&P){let t:(&P,)= (p,);}', "reference storage"),
    (RECORD + 'fn f(mut p:&P){p.value=2;}', "shared reference"),
    (RECORD + 'fn f(mut p:&P){unsafe {p.value+=2;}}', "shared reference"),
    (RECORD + 'fn f(p:P){let q=&p;}', "borrowing"),
    (RECORD + 'extern "C" fn f(p:P){}', "aggregate ABI"),
    ('extern "C" {fn __pcc_rust_f();}', "reserved"),
])
def test_ffi_capability_and_unsafe_errors_fail_before_object_emission(source, message):
    with pytest.raises(RustError, match=message):
        compile_rust(source, **ATTEST)


@pytest.mark.parametrize("source", [
    'extern "C" {fn f();} fn g(){unsafe{f();}}',
    'unsafe extern "C" {pub fn f();} fn g(){unsafe{f();}}',
    'extern {fn f();} unsafe fn g(){f();}',
    'unsafe fn f(){} fn g(){unsafe {unsafe {f();}}}',
    RECORD + 'fn f(p:&P)->i32{let q=p; q.value+p.value}',
    RECORD + '#[no_mangle] pub extern fn f(p:&P)->i32{p.value}',
])
def test_ffi_valid_source_shapes(source):
    assert "define" in compile_rust(source, **ATTEST).ir_text


@pytest.mark.pcc_gate(unavailable=NATIVE)
def test_original_meson_rust_and_c_objects_link_and_execute_full_width(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file
    originals = {name: original(UPSTREAM / name) for name in HASHES}
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", forbidden)
        rust = compile_rust(originals["meson_adder.rs"], target_triple=DEFAULT_TARGET, **ATTEST)
        objects = [rust.emit_object()] + [owned_c(originals[name], name, DEFAULT_TARGET) for name in ("meson_zero.c", "meson_zero_static.c")]
        assert rust.symbols == {"zero":"zero", "zero_static":"zero_static", "adder_add_r":"adder_add_r"}
        asm = '''.intel_syntax noprefix
.text
.globl _start
_start:
  sub rsp, 32
  mov DWORD PTR [rsp], 3
  mov rdi, rsp
  mov esi, 4
  call adder_add_r
  mov DWORD PTR [rsp+16], eax
  mov DWORD PTR [rsp], 0x40000000
  mov rdi, rsp
  mov esi, 7
  call adder_add_r
  mov DWORD PTR [rsp+20], eax
  mov DWORD PTR [rsp], -41
  mov rdi, rsp
  mov esi, -1
  call adder_add_r
  mov DWORD PTR [rsp+24], eax
  mov eax, 1
  mov edi, 1
  lea rsi, [rsp+16]
  mov edx, 12
  syscall
  mov eax, 60
  mov edi, 0
  syscall
'''
        image = link_static_executable([*(parse_relocatable(obj) for obj in objects), assemble_file(asm)])
    (tmp_path / "rust.ll").write_text(rust.ir_text)
    for i, obj in enumerate(objects):
        (tmp_path / f"unit-{i}.o").write_bytes(obj)
    record_run(image, b"".join(value.to_bytes(4, "little", signed=True) for value in (7, 0x40000007, -42)), tmp_path)
    assert originals == {name: original(UPSTREAM / name) for name in HASHES}


def test_original_meson_sources_cross_emit_macho_with_true_external_relocations(monkeypatch):
    from pcc.backend import macho_spec
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", forbidden)
        rust = compile_rust(original(UPSTREAM / "meson_adder.rs"), target_triple="arm64-apple-darwin", **ATTEST)
        parsed = macho_spec.parse_object(rust.emit_object())
        symbols = parsed.symbols()
        assert "_adder_add_r" in {symbol["name"] for symbol in symbols}
        text = next(section for section in parsed.sections() if section["sectname"].rstrip(b"\0") == b"__text")
        branch_names = {symbols[reloc["r_symbolnum"]]["name"] for reloc in parsed.relocations(text) if reloc["r_extern"] and reloc["r_type"] == macho_spec.ARM64_RELOC_BRANCH26}
        assert {"_zero", "_zero_static"} <= branch_names
        for name, symbol in (("meson_zero.c", "_zero"), ("meson_zero_static.c", "_zero_static")):
            obj = macho_spec.parse_object(owned_c(original(UPSTREAM / name), name, "arm64-apple-darwin"))
            assert obj.header["cputype"] == macho_spec.CPU_TYPE_ARM64
            assert symbol in {item["name"] for item in obj.symbols()}


@pytest.mark.pcc_gate(unavailable=NATIVE)
def test_shared_repr_c_reference_layout_copy_and_rebinding_execute_full_width(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file
    source = HERE / "programs" / "references.rs"
    original_bytes = source.read_bytes()
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", forbidden)
        result = compile_rust(original_bytes.decode(), **ATTEST)
        obj = result.emit_object()
        asm = '''.intel_syntax noprefix
.text
.globl _start
_start:
  sub rsp, 80
  mov DWORD PTR [rsp], 20
  mov rax, 0xfedcba9876543210
  mov QWORD PTR [rsp+8], rax
  mov QWORD PTR [rsp+16], -22
  mov DWORD PTR [rsp+32], 22
  mov QWORD PTR [rsp+40], 17
  mov QWORD PTR [rsp+48], 0
  mov rdi, rsp
  lea rsi, [rsp+32]
  call inspect
  mov QWORD PTR [rsp+64], rax
  mov eax, 1
  mov edi, 1
  lea rsi, [rsp+64]
  mov edx, 8
  syscall
  mov eax, 60
  mov edi, 0
  syscall
'''
        image = link_static_executable([parse_relocatable(obj), assemble_file(asm)])
    (tmp_path / "rust.ll").write_text(result.ir_text)
    (tmp_path / "rust.o").write_bytes(obj)
    record_run(image, bytes.fromhex("1032547698badcfe"), tmp_path)
    assert source.read_bytes() == original_bytes


def test_shared_reference_program_cross_emits_arm64_macho(monkeypatch):
    from pcc.backend import macho_spec
    source = HERE / "programs" / "references.rs"
    original_bytes = source.read_bytes()
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", forbidden)
        result = compile_rust(original_bytes.decode(), target_triple="arm64-apple-darwin", **ATTEST)
        obj = macho_spec.parse_object(result.emit_object())
    assert obj.header["cputype"] == macho_spec.CPU_TYPE_ARM64
    assert "_inspect" in {symbol["name"] for symbol in obj.symbols()}
    assert source.read_bytes() == original_bytes


@pytest.mark.pcc_gate(unavailable=None if shutil.which("rustc") else "development-only rustc reference is not installed")
@pytest.mark.parametrize("relative", ["upstream/meson_adder.rs", "programs/references.rs"])
def test_development_only_rustc_checks_unchanged_ffi_source(relative, tmp_path):
    source = HERE / relative
    original_bytes = source.read_bytes()
    run = subprocess.run([shutil.which("rustc"), "--edition=2021", "--crate-type=lib", "--emit=metadata", "-C", "overflow-checks=off", str(source), "-o", str(tmp_path / "reference.rmeta")], capture_output=True, timeout=15)
    assert run.returncode == 0, run.stderr
    assert source.read_bytes() == original_bytes
