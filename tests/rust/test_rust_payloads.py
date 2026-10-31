"""Nonheap tagged payloads and moves, not generic core/Drop/borrow support."""
import hashlib
import platform
import shutil
import sys
from pathlib import Path

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution as _native
from tests.rust.test_rust_frontend import test_development_only_rustc_checks_the_same_source as _rustc
from tests.rust.test_rust_targets import test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite as _cross


def compile_source(source):
    return compile_rust(source, experimental_rustc_checked=True)


@pytest.mark.parametrize('source,message',[
    ('enum E{A(E)}','infinite size'),
    ('enum E{A(P)} struct P{x:E}','infinite size'),
    ('enum E{A((i32,E))}','infinite size'),
    ('enum E{A(Unknown)}','unsupported Rust type'),
    ('enum E{A(&P)} #[repr(C)] struct P{x:i64}','lifetime analysis'),
    ('enum E{A()}','empty tuple variants'),
    ('enum E{A(i32)=7}','require an integer repr'),
    ('enum E{A(i32),B=7}','require an integer repr'),
    ('enum E{A(i32)} fn f(){E::A;}','requires constructor arguments'),
    ('enum E{A(i32)} fn f(){E::A();}','argument count'),
    ('enum E{A(i32)} fn f(){E::A(1,2);}','argument count'),
    ('enum E{A(i32)} fn f(){E::A(true);}','type mismatch'),
    ('enum E{A} fn f(){E::A();}','unit variant does not take'),
    ('enum E{A(i32)} fn f(){E::A(1) as i64;}','cannot be cast'),
    ('enum E{A(i32)} fn f(x:E){match x{E::A=>()}}','requires fields'),
    ('enum E{A(i32)} fn f(x:E){match x{E::A()=>()}}','pattern shape'),
    ('enum E{A(i32)} fn f(x:E){match x{E::A(_,_)=>()}}','pattern shape'),
    ('enum E{A} fn f(x:E){match x{E::A()=>()}}','pattern shape'),
    ('enum E{A(bool),B} fn f(x:E){match x{E::A(true)=>(),E::B=>()}}','non-exhaustive'),
    ('enum E{A(i32),B} fn f(x:E){match x{E::A(7)=>(),E::B=>()}}','non-exhaustive'),
    ('enum E{A(bool,bool)} fn f(x:E){match x{E::A(true,_)=>(),E::A(_,true)=>()}}','non-exhaustive'),
    ('enum I{A,B} enum E{X(I),Y} fn f(x:E){match x{E::X(I::A)=>(),E::Y=>()}}','non-exhaustive'),
    ('fn f(x:(i32,i32)){match x{(y,y)=>()}}','duplicate binding'),
    ('fn f(x:i32){match x{y=>()};y;}','unknown local'),
    ('fn f(x:i32){match x{y|y=>()}}','bindings inside or-patterns'),
    ('enum E{A(i32),B(i32)} fn f(x:E){match x{E::A(y)|E::B(y)=>()}}','bindings inside or-patterns'),
    ('fn f(x:i32){match x{ref y=>()}}','reference, range'),
    ('fn f(x:i32){match x{y=>{y=3;}}}','immutable'),
    ('enum E{A(i32)} impl Drop for E {}','trait impls'),
    ('enum E{A(i32)} extern "C" fn f(x:E){}','aggregate ABI'),
])
def test_payload_and_binding_capability_or_type_errors(source,message):
    with pytest.raises(RustError,match=message):
        compile_source(source)


PREFIX='struct P{x:i64} enum E{None,Pair(P,P)} fn take(p:P){} fn consume(e:E){} '
# Exact former positive retained as a negative after the rustc E0382 finding.
RETAINED_ENUM_REMATCH = PREFIX+'fn f(e:E){match e{E::None=>(),E::Pair(p,_)=>take(p)};match e{E::None=>(),E::Pair(_,q)=>take(q)};}'


@pytest.mark.parametrize('body',[
    'let e=E::Pair(P{x:1},P{x:2});match e{E::None=>(),E::Pair(p,_)=>take(p)};consume(e);',
    'let e=E::Pair(P{x:1},P{x:2});match e{E::None=>(),E::Pair(p,_)=>take(p)};match e{E::None=>(),E::Pair(p,_)=>take(p)};',
    'let e=E::Pair(P{x:1},P{x:2});let q=e;match e{E::None=>(),E::Pair(_,_)=>()};',
    'let e=E::Pair(P{x:1},P{x:2});match e{all=>{consume(all);consume(all);}}',
    'let e=E::Pair(P{x:1},P{x:2});match (e,){(_ ,)=>()};consume(e);',
    'let mut e=E::Pair(P{x:1},P{x:2});while true{match e{E::None=>(),E::Pair(p,_)=>take(p)}}',
    'let mut e=E::Pair(P{x:1},P{x:2});while true{match e{E::None=>(),E::Pair(p,q)=>{continue;e=E::Pair(p,q);}}}',
    'let p=P{x:1};E::Pair(p,p);',
])
def test_payload_moves_fail_on_reuse_live_join_and_backedges(body):
    with pytest.raises(RustError,match='moved'):
        compile_source(PREFIX+'fn f(){'+body+'}')


@pytest.mark.parametrize('source',[
    'enum E{A(i32)}',
    'fn f(x:i32){match x{y=>()}}',
    'struct P{x:i32} fn f(p:P){match p{_=>()}}',
    'enum E{A(i64)} fn f(e:E)->E{match e{E::A(x)=>{let y=x+x;}}e}',
    'fn f(x:(i64,bool))->i64{match x{(mut n,true)=>{n+=1;n},(n,false)=>n}}',
    PREFIX+'fn f(e:E){match e{E::None=>(),E::Pair(p,q)=>{take(p);take(q);}}}',
    PREFIX+'fn f(mut e:E){match e{E::None=>(),E::Pair(p,_)=>take(p)};e=E::None;consume(e);}',
    PREFIX+'fn f(e:E){match e{E::Pair(p,_)=>{take(p);return;},E::None=>()};consume(e);}',
    'enum E{A(bool),B} fn f(e:E){match e{E::A(true|false)|E::B=>()}}',
])
def test_binding_scope_copy_partial_move_and_coverage_valid(source):
    assert compile_source(source).ir_text


@pytest.mark.parametrize('name,sha',[
    ('core_option.rs','53b549b18552144e911416aa2773e8b726da88c190d5cb27007c3c08740894a5'),
    ('core_result.rs','768b0584d514fce9255a762e76bb51eb1646b7bcabd6db660752960321bfc51f'),
])
def test_complete_official_generic_sources_still_fail_at_real_frontier(name,sha):
    path=Path(__file__).parent/'upstream/rust-1.90.0'/name
    original=path.read_bytes()
    assert hashlib.sha256(original).hexdigest()==sha
    with pytest.raises(RustError,match='documentation attributes'):
        compile_source(original.decode())
    assert path.read_bytes()==original


# payload_partial_moves.rs is preserved unchanged and checked as an exact
# negative by test_rust_enum_rematch.py, not rewritten into a successful input.
PROGRAMS=['enum_payload','payload_tuple_partial_moves','payload_nested','payload_bindings','payload_effects','payload_value_snapshot']


@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux') and platform.machine() in {'x86_64','AMD64'} else 'requires Linux x86-64 owned execution')
@pytest.mark.parametrize('name',PROGRAMS)
def test_original_payload_program_full_width_native(name,tmp_path,monkeypatch):
    _native(name,tmp_path,monkeypatch)


@pytest.mark.parametrize('name',PROGRAMS)
def test_original_payload_program_cross_macho(name,monkeypatch):
    _cross(name,monkeypatch)


@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
@pytest.mark.parametrize('name',PROGRAMS)
def test_development_only_rustc_checks_original_payload_program(name,tmp_path):
    _rustc(name,tmp_path)


def test_private_payload_layout_is_typed_ir_and_not_a_foreign_enum_abi():
    result=compile_source('#[repr(u32)] enum E{A(i64),B(u32,bool),C} fn identity(x:E)->E{x}')
    dtype=result.module.globals[result.symbols['identity']].function_type.return_type
    assert dtype.elements[0].width==32
    assert [field.width for field in dtype.elements[1].elements]==[64]
    assert [field.width for field in dtype.elements[2].elements]==[32,1]
    assert result.module.data_layout==result.data_layout
    assert result.module.triple==result.target_triple
