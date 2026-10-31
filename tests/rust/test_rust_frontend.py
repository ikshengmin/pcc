"""Unchanged Rust sources -> PCC IR -> owned native execution.

The optional rustc check is a DEVELOPMENT REFERENCE, not the production path.
Host-PCC evidence is not a pcc1 bootstrap or a borrow-checking qualification.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import lex

PROGRAMS = Path(__file__).parent / "programs"


def compile_source(source: str):
    return compile_rust(source, experimental_rustc_checked=True)


def test_borrow_checker_attestation_is_required():
    with pytest.raises(RustError, match="no borrow checker"):
        compile_rust("fn answer() -> i32 { 42 }")
    for value in (1, "true", None):
        with pytest.raises(RustError, match="no borrow checker"):
            compile_rust("fn answer() -> i32 { 42 }", experimental_rustc_checked=value)


def test_checked_overflow_is_not_silently_wrapped():
    with pytest.raises(RustError, match="panic support"):
        compile_rust("fn answer() {}", experimental_rustc_checked=True, overflow_checks=True)


def test_owned_ir_contract_and_explicit_capability_metadata():
    compiled = compile_source("fn answer() -> i32 { 42 }")
    assert compiled.experimental and not compiled.borrow_checked_by_pcc
    assert not compiled.overflow_checks
    assert compiled.module.__class__.__module__ == "pcc.ir.ir"
    assert "ret i32 42" in compiled.ir_text
    assert compiled.symbols == {"answer": "__pcc_rust_answer"}


def test_nested_comments_preserve_locations():
    tokens = lex("/* outer\n /* nested */ */\nfn f() {} // end")
    assert (tokens[0].text, tokens[0].line, tokens[0].column) == ("fn", 3, 1)
    assert tokens[-1].kind == "eof"


def test_contextual_inference_happens_before_i32_defaulting():
    result = compile_source("fn wide(x:i64)->i64 {x} fn answer()->i64 { let x=1; let y=x; wide(y) }")
    assert "alloca i64" in result.ir_text
    assert "alloca i32" not in result.ir_text


def test_shadowing_has_distinct_slots_and_scope():
    result = compile_source("fn answer()->i32 { let x=1; {let x=x+1; x;}; x }")
    assert result.ir_text.count("alloca i32") == 2


def test_loop_local_slots_are_allocated_once_in_entry():
    result = compile_source("fn answer()->i32 { let mut n=0; while n<3 { let x=n+1; n=x; } n }")
    assert "alloca" not in result.ir_text.split("while.body:", 1)[1]


@pytest.mark.parametrize("source, message", [
    ("fn f()->i32 { let x=1; x=2; x }", "immutable"),
    ("fn f()->i32 { missing }", "unknown local"),
    ("fn f()->i32 { {let x=1;}; x }", "unknown local"),
    ("fn f() { true + false; }", "does not accept bool"),
    ("fn f() { let x:i64=1i32; }", "type mismatch"),
    ("fn f()->i32 { true }", "type mismatch"),
    ("fn f()->i32 { 1; }", "type mismatch"),
    ("fn f() { if 1 {} }", "type mismatch"),
    ("fn f() { while 1 {} }", "type mismatch"),
    ("fn f()->i32 { if true {1} else {false} }", "type mismatch"),
    ("fn f() { if true {1} }", "type mismatch"),
    ("fn f() { {1} let x=2; }", "type mismatch"),
    ("fn f() { while true {1} }", "type mismatch"),
    ("fn f() { break; }", "outside a loop"),
    ("fn f() { continue; }", "outside a loop"),
    ("fn f()->i32 { return; }", "type mismatch"),
    ("fn f() { return 1; }", "type mismatch"),
    ("fn f() { return; missing; }", "unknown local"),
    ("fn f(){} fn f(){}", "duplicate function"),
    ("fn f(x:i32,x:i32){}", "duplicate parameter"),
    ("fn f(x:i32){} fn g(){f();}", "expects 1 arguments"),
    ("fn f(x:i32){} fn g(){f(true);}", "type mismatch"),
    ("fn f() { unknown(); }", "unknown function"),
    ("fn f(){} fn g(){let f=1; f();}", "unshadowed direct"),
    ("fn f()->i32 { 2147483648 }", "out of range"),
    ("fn f()->u32 { 4294967296u32 }", "out of range"),
    ("fn f()->u64 { 18446744073709551616u64 }", "out of range"),
    ("fn f()->i32 { -2147483649 }", "out of range"),
    ("fn f()->i32 { 2147483647+1 }", "constant integer overflow"),
    ("fn f()->i32 { -(-2147483648) }", "constant integer overflow"),
    ("fn f()->u32 { -1u32 }", "signed integer"),
    ("fn f()->bool { 1 as bool }", "supported casts"),
    ("fn f(x:i32)->i32 { 10/x }", "panic-capable"),
    ("fn f(x:i32)->i32 { x/0 }", "panic-capable"),
    ("fn f(x:i32)->i32 { x/-1 }", "panic-capable"),
    ("fn f(x:i32)->i32 { x%-1 }", "panic-capable"),
    ("fn f(){ let mut x=true; x+=false; }", "requires integer"),
    ("fn f(){ 1<2<3; }", "cannot be chained"),
    ("fn f(){ let x=1 x; }", "expected ';'"),
    ("fn f(){ 1 2 }", "expected ';'"),
    ("fn f(){", "unterminated block"),
    ("/* never closed", "unterminated block comment"),
    ("fn f(){ 0b102; }", "invalid or unsupported integer"),
    ("fn f(){ 12i8; }", "invalid or unsupported integer"),
    ("fn f(x:&i32){}", "unsupported Rust type"),
    ("fn f(){ let x=1; let y=&x; }", "borrowing"),
    ("fn f(){ panic!(); }", "expected ';'"),
    ("trait T {}", "expected 'fn'"),
    ("struct S {}", "zero-field structs"),
    ("fn f<T>(x:T){}", "expected '('"),
    ("fn f(){ [1,2]; }", "unsupported expression"),
    ("fn f(){ let x=Box::new(1); }", "unknown enum constructor"),
    ("fn f(){ 1<<2; }", "expected ';'"),
    ("extern \"C\" fn f(x:bool){}", "extern C currently supports"),
    ("#[no_mangle] fn f(){}", "no_mangle is supported only on extern C"),
    ("fn __pcc_rust_bad(){}", "reserved"),
])
def test_strict_diagnostics(source, message):
    with pytest.raises(RustError, match=message.replace("(", r"\(")):
        compile_source(source)


@pytest.mark.parametrize("source", [
    "fn f(x:())->() { x } fn answer() {f(());}",
    "fn f(mut x:i32)->i32 {x+=1; x}",
    "fn f()->i32 { if true {return 1;} else {return 2;} }",
    "fn f()->i32 { let x=if true {return 1;} else {2}; x }",
    "fn f()->bool { !false | (true & false) ^ true }",
    "fn f()->i32 { if true { () } else { () }; 42 }",
    "fn f()->i32 { 0x2a_i32 }",
])
def test_valid_shapes_lower(source):
    assert "define" in compile_source(source).ir_text


@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith("linux") and platform.machine() in {"x86_64", "AMD64"} else "requires Linux x86-64 owned execution")
@pytest.mark.parametrize("name, width, expected", [
    ("control", 4, 42),
    ("values", 4, 42),
    ("short_circuit", 4, 42),
    ("integer_rules", 4, 42),
    ("grouping", 4, 42),
    ("high_bits", 8, 0xFEDCBA9876543210),
])
def test_unchanged_rust_source_runs_owned_native(name, width, expected, tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable
    from pcc.backend.self_backend_x86_64_linux import emit_x86_64_linux_asm
    from pcc.backend.x86_64_asm_driver import assemble_file

    original = (PROGRAMS / (name + ".rs")).read_bytes()
    # All code generation, assembly and linking run with subprocess forbidden.
    def forbidden(*args, **kwargs):
        raise AssertionError("Rust production compile invoked an external tool")

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "Popen", forbidden)
        result = compile_source(original.decode())
        result.module.triple = "x86_64-unknown-linux-gnu"
        asm = emit_x86_64_linux_asm(result.ir_text)
        start = f""".intel_syntax noprefix
.text
.globl _start
_start:
  call {result.symbols['answer']}
  sub rsp, 16
  mov {"QWORD" if width == 8 else "DWORD"} PTR [rsp], {"rax" if width == 8 else "eax"}
  mov eax, 1
  mov edi, 1
  mov rsi, rsp
  mov edx, {width}
  syscall
  mov edi, 0
  mov eax, 60
  syscall
"""
        image = link_static_executable([assemble_file(asm), assemble_file(start)])
    assert image[:4] == b"\x7fELF"
    executable = tmp_path / name
    executable.write_bytes(image)
    executable.chmod(0o700)
    run = subprocess.run([str(executable)], capture_output=True, timeout=3)
    assert (run.returncode, run.stdout, run.stderr) == (0, expected.to_bytes(width, "little"), b"")
    assert (PROGRAMS / (name + ".rs")).read_bytes() == original


@pytest.mark.pcc_gate(unavailable=None if shutil.which("rustc") else "development-only rustc reference is not installed")
@pytest.mark.parametrize("name", ["control", "values", "short_circuit", "integer_rules", "grouping", "high_bits"])
def test_development_only_rustc_checks_the_same_source(name, tmp_path):
    source = PROGRAMS / (name + ".rs")
    original = source.read_bytes()
    result = subprocess.run([
        shutil.which("rustc"), "--crate-type=lib", "--edition=2021",
        "--emit=metadata", "-C", "overflow-checks=off", str(source),
        "-o", str(tmp_path / (name + ".rmeta")),
    ], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert source.read_bytes() == original


@pytest.mark.parametrize("source", [
    "fn f()->bool { (1<2)==true }",
    "fn f()->bool { (1<2)==(2<3) }",
    "fn f()->i32 { -((2147483648)) }",
    "fn f()->i32 {42} fn g()->i32 {(f)()}",
    "//// ordinary comment\nfn f(){}",
    "/*** ordinary comment */ fn f(){}",
    "/**/ fn f(){}",
    "/* nested /*! doc-shaped but inside ordinary comment */ */ fn f(){}",
])
def test_grouping_and_nondoc_comments_preserve_rust_syntax(source):
    assert "define" in compile_source(source).ir_text


@pytest.mark.parametrize("source", [
    "fn f(){ ({}) let x=1; }",
    "fn f(){ (if true {}) let x=1; }",
])
def test_parentheses_do_not_inherit_block_statement_semicolon_exemption(source):
    with pytest.raises(RustError, match="expected ';'"):
        compile_source(source)


@pytest.mark.parametrize("source", [
    "fn f(){ /// dangling doc\n }",
    "/// doc\nfn f(){}",
    "//! crate doc\nfn f(){}",
    "/** outer doc */ fn f(){}",
    "/*! inner doc */ fn f(){}",
    "/*!*/ fn f(){}",
])
def test_doc_attributes_fail_closed_instead_of_being_stripped(source):
    with pytest.raises(RustError, match="documentation attributes"):
        compile_source(source)


@pytest.mark.parametrize("separator", list("\t\n\v\f\r \u0085\u200e\u200f\u2028\u2029"))
def test_rust_pattern_whitespace_is_accepted(separator):
    assert "define" in compile_source("fn" + separator + "f(){}").ir_text


@pytest.mark.parametrize("separator", ["\u00a0", "\u1680", "\u2000", "\u202f", "\u3000", "\x1c"])
def test_python_only_whitespace_does_not_relax_rust_syntax(separator):
    with pytest.raises(RustError, match="unsupported Rust token"):
        compile_source("fn" + separator + "f(){}")


@pytest.mark.parametrize("expression", [
    "(2147483647i32 as i32) + 1",
    "(2147483647i32 | 0) + 1",
    "(2147483647i32 & 2147483647) + 1",
    "(2147483647i32 ^ 0) + 1",
    "(2147483647i32 / 1) + 1",
    "(2147483647i32 % 2) + 2147483647",
    "(!(-2147483648i32)) + 1",
    "({2147483647i32}) + 1",
    "(true as i32) + 2147483647",
    "((1 < 2) as i32) + 2147483647",
])
def test_literal_operator_constant_overflow_crosses_cast_and_bitwise_nodes(expression):
    with pytest.raises(RustError, match="constant integer overflow"):
        compile_source("fn f()->i32 {" + expression + "}")


@pytest.mark.parametrize("expression", [
    "(-1i32 as u64) / 3u64",
    "-8i32 / 3 + -8i32 % 3",
    "(!0u32) / 3u32",
    "2147483648u32 as i32",
])
def test_constant_casts_and_division_follow_rust_bit_and_sign_rules(expression):
    assert "define" in compile_source("fn f(){" + expression + ";}").ir_text
