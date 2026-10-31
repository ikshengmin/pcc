"""Rust aggregate semantics, not rustc layout/borrow checking certification."""
from __future__ import annotations

import hashlib
import platform
import shutil
import sys
from pathlib import Path

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.test_rust_frontend import (
    test_development_only_rustc_checks_the_same_source as _rustc_check,
    test_unchanged_rust_source_runs_owned_native as _run_owned,
)
from tests.rust.test_rust_targets import (
    test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite as _cross_emit,
)


def compile_source(source):
    return compile_rust(source, experimental_rustc_checked=True)


RECORD = "struct P {value:i64} "
CONSUME = "fn consume(p:P){} "


@pytest.mark.parametrize("body", [
    "let p=P{value:1}; let q=p; p.value;",
    "let p=P{value:1}; consume(p); p.value;",
    "let p=P{value:1}; let t=(p,p);",
    "let p=P{value:1}; if true {consume(p);} p.value;",
    "let p=P{value:1}; true && {consume(p); true}; p.value;",
    "let p=P{value:1}; false || {consume(p); false}; p.value;",
    "let mut p=P{value:1}; consume(p); if true {p=P{value:2};} p.value;",
    "let p=P{value:1}; while true {consume(p);}",
    "let mut p=P{value:1}; while true {consume(p); if true {continue;} p=P{value:2};}",
    "let p=P{value:1}; while true {consume(p); break;} p.value;",
    "let p=P{value:1}; {let p=p; p.value;} p.value;",
])
def test_noncopy_move_and_control_flow_rejections(body):
    with pytest.raises(RustError, match="moved"):
        compile_source(RECORD + CONSUME + "fn f(){" + body + "}")


@pytest.mark.parametrize("body", [
    "let p=P{value:1}; let copy=p.value; let copy2=p.value; consume(p);",
    "let mut p=P{value:1}; consume(p); p=P{value:2}; consume(p);",
    "let mut p=P{value:1}; p=p; consume(p);",
    "let p=P{value:1}; if true {consume(p); return;} consume(p);",
    "let mut p=P{value:1}; while true {let q=p; p=q; continue;}",
    "let mut p=P{value:1}; while true {let q=p; p=q; break;} consume(p);",
    "let p=P{value:1}; let p=p; consume(p);",
    "let t=(1,(true,2)); let a=t; let b=t; a; b; t;",
    "let p=P{value:1}; if true {consume(p);} else {consume(p);}",
])
def test_copy_and_reinitialized_or_diverging_paths_are_valid(body):
    assert "define" in compile_source(RECORD + CONSUME + "fn f(){" + body + "}").ir_text


@pytest.mark.parametrize("body", [
    "let p=Pair{a:P{value:1},b:P{value:2}}; let a=p.a; p.a.value;",
    "let p=Pair{a:P{value:1},b:P{value:2}}; let a=p.a; let all=p;",
    "let p=Pair{a:P{value:1},b:P{value:2}}; if true {let a=p.a;} p.a.value;",
    "let mut p=Pair{a:P{value:1},b:P{value:2}}; let a=p.a; p.a.value=2;",
    "let p=P{value:1}; Pair{b:p,a:p};",
])
def test_partial_moves_and_field_order_fail_closed(body):
    with pytest.raises(RustError, match="moved"):
        compile_source(RECORD + "struct Pair{a:P,b:P} fn f(){" + body + "}")


@pytest.mark.parametrize("source, message", [
    ("struct P{x:i32} fn f(){P{};}", "missing struct field"),
    ("struct P{x:i32} fn f(){P{x:1,x:2};}", "duplicate field initializer"),
    ("struct P{x:i32} fn f(){P{y:1};}", "unknown struct field"),
    ("struct P{x:i32} fn f(){P{x:true};}", "type mismatch"),
    ("struct P{x:i32,x:i32}", "duplicate struct field"),
    ("struct P{x:P}", "infinite size"),
    ("struct P{x:Q} struct Q{x:P}", "infinite size"),
    ("struct P{x:Unknown}", "unsupported Rust type"),
    ("struct P{x:i32} struct P{x:i32}", "duplicate"),
    ("struct P{x:i32} fn f(){let p=P{x:1}; p.x=2;}", "immutable"),
    ("fn f(){let t=(1,2); t.0=3;}", "immutable"),
    ("fn f(){let t=(1,2); t.2;}", "unknown tuple field"),
    ("fn f(){let t=(1,2); t.x;}", "unknown tuple field"),
    ("fn f(){1.x;}", "field access requires"),
    ("struct P{x:i32} fn f(){let p=P{x:1}; p.y;}", "unknown struct field"),
    ("struct A{x:i32} struct B{x:i32} fn f(){let p:A=B{x:1};}", "type mismatch"),
    ("fn f(){let t:(i32,i64)=(1i64,2i32);}", "type mismatch"),
    ("fn f(){let t:(i32,)=(1,2);}", "type mismatch"),
    ("struct P{x:i32} extern \"C\" fn f(p:P){}", "aggregate ABI"),
    ("#[repr(C)] struct P{x:i32} extern \"C\" fn f(p:P){}", "aggregate ABI"),
    ("extern \"C\" fn f(p:(i32,i32)){}", "aggregate ABI"),
    ("#[derive(Copy,Clone)] struct P{x:i32}", "unsupported Rust attribute"),
    ("#[repr(C)] fn f(){}", "applies only to a struct"),
    ("fn f(){let t=(return,1);}", "diverging tuple operands"),
    ("fn f(){let t=(1,(return,2));}", "diverging tuple operands"),
    ("fn f(){while true {let t=(break,1);}}", "diverging tuple operands"),
])
def test_aggregate_type_and_abi_errors(source, message):
    with pytest.raises(RustError, match=message):
        compile_source(source)


def test_tuple_and_struct_are_not_same_semantic_type_or_automatic_copy():
    with pytest.raises(RustError, match="type mismatch"):
        compile_source("struct P{x:i32} fn f(){let p:P=(1,);}")
    with pytest.raises(RustError, match="moved"):
        compile_source("struct P{x:i32} fn f(){let p=P{x:1}; let q=p; let r=p;}")


def test_move_through_loop_condition_is_visible_after_exit_and_on_backedge():
    with pytest.raises(RustError, match="moved"):
        compile_source(RECORD + "fn condition(p:P)->bool{false} fn f(){let p=P{value:1}; while condition(p) {} p.value;}")


def test_condition_struct_ambiguity_requires_parentheses():
    with pytest.raises(RustError):
        compile_source("struct P{x:bool} fn f(){if P{x:true}.x {}}")
    assert "define" in compile_source("struct P{x:bool} fn f(){if (P{x:true}).x {}}").ir_text


@pytest.mark.parametrize("keyword", ["if", "while"])
@pytest.mark.parametrize("condition", ["!P{x:true}.x", "!!P{x:true}.x", "-N{x:1}.x == 0"])
def test_unary_condition_does_not_bypass_struct_literal_restriction(keyword, condition):
    with pytest.raises(RustError):
        compile_source("struct P{x:bool} struct N{x:i32} fn f(){" + keyword + " " + condition + " {}}")


@pytest.mark.parametrize("keyword", ["if", "while"])
@pytest.mark.parametrize("condition", ["!(P{x:true}).x", "!!(P{x:true}).x", "-(N{x:1}).x == 0"])
def test_parenthesized_struct_in_unary_condition_is_accepted(keyword, condition):
    assert "define" in compile_source("struct P{x:bool} struct N{x:i32} fn f(){" + keyword + " " + condition + " {}}").ir_text


def test_upstream_meson_adder_is_unchanged_and_stops_at_next_real_boundary():
    path = Path(__file__).parent / "upstream" / "meson_adder.rs"
    source = path.read_bytes()
    assert hashlib.sha256(source).hexdigest() == "654d4f498384c21d51627908a842fe8454e5918ef7ba9317a740d7623c9c0933"
    with pytest.raises(RustError, match="external function declarations"):
        compile_source(source.decode())


@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith("linux") and platform.machine() in {"x86_64", "AMD64"} else "requires Linux x86-64 owned execution")
@pytest.mark.parametrize("name, expected", [
    ("aggregates", 42),
    ("partial_moves", 42),
    ("loop_moves", 42),
    ("copy_tuples", 42),
    ("aggregate_wide", 0xFEDCBA9876543210),
    ("unit_fields", 42),
    ("aggregate_branches", 0xFEDCBA9876543210),
    ("zero_sized_values", 42),
])
def test_aggregate_program_runs_owned_native_full_width(name, expected, tmp_path, monkeypatch):
    _run_owned(name, 8, expected, tmp_path, monkeypatch)


@pytest.mark.pcc_gate(unavailable=None if shutil.which("rustc") else "development-only rustc reference is not installed")
@pytest.mark.parametrize("name", ["aggregates", "partial_moves", "loop_moves", "copy_tuples", "aggregate_wide", "unit_fields", "aggregate_branches", "zero_sized_values"])
def test_development_only_rustc_aggregate_reference(name, tmp_path):
    _rustc_check(name, tmp_path)


@pytest.mark.parametrize("name", ["aggregates", "partial_moves", "loop_moves", "copy_tuples", "aggregate_wide", "unit_fields", "aggregate_branches", "zero_sized_values"])
def test_original_aggregate_program_cross_emits_arm64_macho(name, monkeypatch):
    # Cross-only evidence: the shared owned backend emits actual ARM64 Mach-O,
    # but neither this observer nor the Linux native gate executes on a Mac.
    _cross_emit(name, monkeypatch)
