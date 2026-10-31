"""Owned enum/match semantics, with whole official inputs kept as frontiers."""
import hashlib
import platform
import shutil
import sys
from pathlib import Path

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.test_rust_frontend import test_development_only_rustc_checks_the_same_source as _rustc
from tests.rust.test_rust_targets import test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite as _cross
from tests.rust.test_rust_ffi import record_run


def compile_source(source, **options):
    return compile_rust(source, experimental_rustc_checked=True, **options)


@pytest.mark.parametrize("source,message", [
    ("enum E{}", "empty enums"),
    ("enum E{A{x:i32}}", "named enum payload fields"),
    ("enum E{A,A}", "duplicate enum variant"),
    ("enum E{A=3,B=3}", "duplicate enum discriminant"),
    ("enum E{A=3,B,C=4}", "duplicate enum discriminant"),
    ("enum E{A} enum E{B}", "duplicate"),
    ("enum E{A} struct E{x:i32}", "duplicate"),
    ("#[repr(i32)] enum E{A=2147483647,B}", "discriminant overflow"),
    ("#[repr(u32)] enum E{A=-1}", "unary '-'"),
    ("enum E{A=1i64}", "unsuffixed"),
    ("enum E{A=true}", "unsuffixed"),
    ("#[repr(C)] enum E{A}", "enum repr"),
    ("#[repr(i32)] struct P{x:i32}", "only to an enum"),
    ("#[repr(i8)] enum E{A}", "unsupported representation"),
    ("enum E{A} fn f(){E::B;}", "unknown enum variant"),
    ("enum E{A} fn f(){let x=E::A;let y=x;x;}", "moved"),
    ("enum E{A} fn f(){let x=E::A;x as i32;x;}", "moved"),
    ("enum E{A} fn f(){E::A==E::A;}", "does not accept"),
    ("enum E{A} extern \"C\" fn f(x:E){}", "aggregate ABI"),
    ("#[repr(u32)] enum E{A} extern \"C\" fn f()->E{E::A}", "aggregate ABI"),
    ("fn f(x:bool)->i32{match x{true=>1}}", "non-exhaustive"),
    ("fn f(x:i32)->i32{match x{1=>1,2=>2}}", "non-exhaustive"),
    ("enum E{A,B} fn f(x:E){match x{E::A=>()}}", "non-exhaustive"),
    ("fn f(x:(bool,bool)){match x{(true,_)=>(),(_,true)=>()}}", "non-exhaustive"),
    ("fn f(x:bool){match x{}}", "non-exhaustive"),
    ("fn f(x:bool){match x{1=>(),_=>()}}", "type mismatch"),
    ("fn f(x:i32){match x{1i64=>(),_=>()}}", "type mismatch"),
    ("fn f(x:i32){match x{2147483648=>(),_=>()}}", "out of range"),
    ("enum E{A} enum F{A} fn f(x:E){match x{F::A=>()}}", "type mismatch"),
    ("fn f(x:(bool,bool)){match x{(true,)=>(),_=>()}}", "tuple pattern shape"),
    ("fn f(x:bool)->i32{match x{true=>1,false=>false}}", "type mismatch"),
    ("fn f(x:bool){match x{true if true=>(),_=>()}}", "match guards"),
    ("fn f(x:i32){match x{1+2=>(),_=>()}}", "expected '=>'"),
    ("fn f(x:i32){match x{0..=2=>(),_=>()}}", "expected '=>'"),
    ("fn f(x:bool){match x{true=>1 false=>2}}", "expected ','"),
    ("fn f(){(match true{_=>()}) let x=1;}", "expected ';'"),
])
def test_enum_and_match_diagnostics(source,message):
    with pytest.raises(RustError, match=message):
        compile_source(source)


@pytest.mark.parametrize("source", [
    "enum E{A,B} fn f(x:E)->E{match x{E::A=>E::B,_=>x}}",
    "enum E{A,B} fn f(x:E)->i32{match x{E::A=>1,E::B=>2};x as i32}",
    "fn f(x:i64)->i32{match x{-9223372036854775808=>1,_=>2}}",
    "fn f(x:bool)->i32{match x{|true|false=>7}}",
    "fn f(x:bool)->i32{match x{_=>7,true=>9}}",
    "fn f(x:(bool,bool))->i32{match x{(true|false,_)=>7}}",
    "fn f()->i32{match () {()=>7}}",
    "fn f()->i32{match ((),()) {((),())=>7}}",
    "fn f()->i64{match 1 {1i64=>7,_=>8}}",
    "fn f(x:bool)->i64{match x{true=>return 7,false=>return 8}}",
    "fn f(x:bool){match x{true=>{} false=>{}} let y=1;}",
    "enum E{A} fn f(){let mut x=E::A;let y=x;x=E::A;match x{E::A=>()};x;}",
])
def test_valid_pattern_shapes(source):
    assert "define" in compile_source(source).ir_text


@pytest.mark.parametrize("body", [
    "match b{true=>{take(p);},false=>{}};p.x;",
    "while b{match b{true=>{take(p);},false=>{}}}",
    "while b{match b{true=>{take(p);continue;},false=>{}}p=P{x:1};}",
])
def test_match_joins_moves_on_every_live_edge(body):
    source='struct P{x:i32} fn take(p:P){} fn f(b:bool){let mut p=P{x:1};'+body+'}'
    with pytest.raises(RustError,match="moved"):
        compile_source(source)


def test_diverging_match_arm_does_not_poison_live_move_state():
    compile_source('struct P{x:i32} fn take(p:P){} fn f(b:bool){let p=P{x:1};match b{true=>{take(p);return;},false=>{}} take(p);}')


@pytest.mark.parametrize("name,sha,message", [
    ("core_cmp.rs","c48745bb89c4d3ef361923ac72346133f81a64c112f7bfc625f4beced52016c1","documentation attributes"),
    ("exhaustive-bool-match-sanity.rs","43a9442c59cf2640ef85947545c0a01954bcd5697f993183d688501fb57f80fb","expected ';'"),
])
def test_whole_official_sources_remain_explicit_frontiers(name,sha,message):
    path=Path(__file__).parent/'upstream'/'rust-1.90.0'/name
    original=path.read_bytes()
    assert hashlib.sha256(original).hexdigest()==sha
    with pytest.raises(RustError,match=message):
        compile_source(original.decode())
    assert path.read_bytes()==original


PROGRAMS = ["enum_match", "tuple_match", "match_effects", "enum_wide", "match_aggregate_result"]


@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith("linux") and platform.machine() in {"x86_64","AMD64"} else "requires Linux x86-64 owned execution")
@pytest.mark.parametrize("name",PROGRAMS)
def test_original_match_fixture_full_width_owned_execution(name,tmp_path,monkeypatch):
    import subprocess
    from pcc.backend.elf_x86_64 import emit_relocatable, link_static_executable, parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file
    path=Path(__file__).parent/'programs'/(name+'.rs')
    original=path.read_bytes()
    def forbidden(*args,**kwargs):
        raise AssertionError('owned Rust compilation invoked an external tool')
    with monkeypatch.context() as patch:
        patch.setattr(subprocess,'Popen',forbidden)
        result=compile_source(original.decode())
        obj=result.emit_object()
        observer=assemble_file('''.intel_syntax noprefix
.text
.globl _start
_start:
 call '''+result.symbols['answer']+'''
 sub rsp,16
 mov QWORD PTR [rsp],rax
 mov eax,1
 mov edi,1
 mov rsi,rsp
 mov edx,8
 syscall
 mov eax,60
 xor edi,edi
 syscall
''')
        binary=link_static_executable([parse_relocatable(obj),observer])
    (tmp_path/'source.rs').write_bytes(original)
    (tmp_path/'program.ll').write_text(result.ir_text)
    (tmp_path/'program.o').write_bytes(obj)
    (tmp_path/'observer.o').write_bytes(emit_relocatable(observer))
    record_run(binary, bytes.fromhex('1032547698badcfe'), tmp_path)
    assert path.read_bytes()==original


@pytest.mark.parametrize("name",PROGRAMS)
def test_match_original_source_cross_emits_darwin(name,monkeypatch):
    _cross(name,monkeypatch)


@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
@pytest.mark.parametrize("name",PROGRAMS)
def test_development_only_rustc_checks_match_sources(name,tmp_path):
    _rustc(name,tmp_path)
