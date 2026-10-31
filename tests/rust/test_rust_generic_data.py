"""Original Rust generic-data cases; not a core Option/Result implementation."""
import hashlib
import platform
import shutil
import sys
from pathlib import Path

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.semantics import Checker
from pcc.frontends.rust.syntax import Parser
from pcc.frontends.rust.types import TypeApplication, TypeParameter
from tests.rust.test_rust_frontend import test_development_only_rustc_checks_the_same_source as _rustc
from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution as _native
from tests.rust.test_rust_targets import test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite as _cross


PROGRAMS = ('generic_records', 'generic_enums', 'generic_nested', 'generic_moves', 'generic_effects')


def compile_source(source, **options):
    return compile_rust(source, experimental_rustc_checked=True, **options)


@pytest.mark.parametrize('source,message', [
    ('struct S<T,T>{x:T}', 'duplicate generic parameter'),
    ('enum E<T,T>{Value(T)}', 'duplicate generic parameter'),
    ('struct S<T>{x:Missing}', 'unsupported Rust type'),
    ('enum E<T>{Value(Missing)}', 'unsupported Rust type'),
    ('struct S<T>{x:T,y:Unknown<T>}', 'unsupported Rust type'),
    ('struct S<T>{x:T<i32>}', 'generic arguments require'),
    ('struct S<T>{x:i32}', 'unused generic parameter'),
    ('enum E<T>{Empty}', 'unused generic parameter'),
    ('struct S<T>{x:T,x:T}', 'duplicate struct field'),
    ('enum E<T>{Value(T),Value(T)}', 'duplicate enum variant'),
    ('struct S<T>{x:T} struct S{x:i32}', 'duplicate'),
    ('struct S<T>{x:S<T>}', 'infinite size'),
    ('struct S<T>{x:S<(T,T)>}', 'generic'),
    ('struct A<T>{x:B<T>} struct B<T>{x:A<T>}', 'infinite size'),
    ('struct Wrap<T>{value:T} struct Bad{value:Wrap<Bad>}', 'infinite size'),
    ('struct Wrap<T>{value:T} struct Bad{value:Wrap}', 'explicit type arguments'),
    ('struct Pair<T,U>{a:T,b:U} struct Bad{value:Pair<i32>}', 'argument count'),
    ('struct Wrap<T>{value:T} fn f(x:Wrap<i32,u32>){}', 'argument count'),
    ('struct S<T>{x:T} fn f(x:T){}', 'unsupported Rust type'),
    ('struct S<T>{x:&T}', 'lifetime analysis'),
    ('struct S<T>{x:T} fn f(){S::<bool>{x:1};}', 'type mismatch'),
    ('struct S<T>{x:T} fn f(){S{x:1};}', 'explicit type arguments'),
    ('enum E<T>{Value(T)} fn f(){E::Value(1);}', 'explicit type arguments'),
    ('struct S<T>{x:T} fn take(x:S<i64>){} fn f(x:S<u64>){take(x);}', 'type mismatch'),
    ('struct S<T>{x:T} struct R<T>{x:T} fn take(x:S<i64>){} fn f(x:R<i64>){take(x);}', 'type mismatch'),
    ('enum E<T>{Value(T)} fn f(x:E<i64>){match x{E::<u64>::Value(n)=>()}}', 'type mismatch'),
    ('enum E<T>{Empty,Value(T)} fn f(x:E<i64>){match x{E::Empty=>()}}', 'non-exhaustive'),
    ('struct S<T>{x:T} extern "C" fn f(x:S<i64>){}', 'aggregate ABI'),
    ('#[repr(C)] struct S<T>{x:T} extern "C" fn f(x:&S<i64>){}', 'per-instance qualification'),
    ('struct S<T:Copy>{x:T}', 'bounds and default'),
    ('struct S<T=i32>{x:T}', 'bounds and default'),
    ('struct S<T> where T:Copy {x:T}', 'where constraints'),
    ('enum E<T> where T:Copy {Value(T)}', 'where constraints'),
    ('struct S<const N:u32>{x:i32}', 'const generic'),
    ('fn unused<T>(value:T)->T{missing(value)}', 'unknown function'),
    ('struct S<T>{x:T} fn f(){S::<>{x:1};}', 'empty/inferred'),
    ('struct S<T>{x:T} fn f(){S::<_>{x:1};}', 'unsupported Rust type'),
    ('struct S<i32>{x:i32}', 'shadowing primitive'),
])
def test_generic_declarations_and_uses_are_checked_before_lowering_even_when_unused(source, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid or unsupported generic declaration reached lowering')
    monkeypatch.setattr(Lowerer, 'lower', forbidden)
    with pytest.raises(RustError, match=message):
        compile_source(source)


@pytest.mark.parametrize('source', [
    'struct S<T>{x:T} fn f(x:S<i64>){let y=x;x;}',
    'struct P{x:i64} struct S<T>{x:T} fn f(x:S<P>){let y=x.x;x;}',
    'struct P{x:i64} struct S<T>{x:T} fn f(x:S<P>,b:bool){if b{let y=x.x;}x;}',
    'struct P{x:i64} struct S<T>{x:T} fn take(x:S<P>){} fn f(x:S<P>){while true{take(x);}}',
    'enum E<T>{None,Value(T)} fn f(){let x=E::<i64>::None;let y=x;x;}',
    'struct P{x:i64} enum E<T>{None,Value(T)} fn f(x:E<P>){match x{E::None=>(),E::Value(p)=>()}x;}',
])
def test_generic_data_does_not_invent_copy_or_erase_partial_moves(source):
    with pytest.raises(RustError, match='moved'):
        compile_source(source)


@pytest.mark.parametrize('source', [
    'struct S<T>{x:T}',
    'enum E<T,U>{First(T),Second(U),None}',
    'struct Wrap<T>{x:T} struct Nest<T>{x:Wrap<Wrap<T>>}',
    'struct Wrap<T>{x:T} fn f(x:Wrap<Wrap<i64>>)->i64{x.x.x}',
    'struct S<T>{x:T} fn f()->S<()>{S::<()>{x:()}}',
    'enum E<T>{None,Value(T)} fn f()->E<()>{E::<()>::Value(())}',
    'struct S<T>{x:T} fn f()->i64{let x:S<i64>=S::<i64>{x:42};x.x}',
    'struct S<T>{x:T} fn f()->i64{S::<i64,>{x:42}.x}',
    'enum E<T>{Value(T)} fn f(x:E<(i64,bool)>)->i64{match x{E::Value((n,true))=>n,E::Value((_,false))=>0}}',
])
def test_generic_data_valid_symbolic_and_concrete_shapes(source):
    compile_source(source)


def test_instantiation_keeps_nominal_arguments_and_template_fields_separate():
    from pcc.support.instantiation import InstanceRegistry, InstanceState
    parser = Parser('struct Wrap<T>{value:T} fn a(x:Wrap<i64>){} fn b(x:Wrap<u64>){} fn c(x:Wrap<i64>){}')
    checker = Checker(parser.parse(), parser.records, parser.enums)
    checker.check()
    signed, unsigned = TypeApplication('Wrap', ('i64',)), TypeApplication('Wrap', ('u64',))
    assert signed != unsigned and len(checker.generics.instances) == 2
    assert checker.records[signed].fields[0][1] == 'i64'
    assert checker.records[unsigned].fields[0][1] == 'u64'
    assert isinstance(checker.generics.definitions['Wrap'].fields[0][1], TypeParameter)
    assert not checker.copy_type(signed) and not checker.copy_type(unsigned)
    registry = checker.generics.instances
    assert isinstance(registry, InstanceRegistry)
    assert [entry.value for entry in registry.pending] == [signed, unsigned]
    assert all(entry.state is InstanceState.COMPLETE for entry in registry.pending)
    assert all(entry.result is checker.records[entry.value] for entry in registry.pending)
    assert registry.drain(lambda entry: pytest.fail('completed data rebuilt')) == []


@pytest.mark.parametrize('source', [
    'struct Unused<T>{value:Missing<T>}',
    'struct Unused<T>{value:Unused<T>}',
    'struct Unused<T>{value:Unused<(T,T)>}',
])
def test_invalid_unused_declarations_do_not_rely_on_instance_cycle_states(source, monkeypatch):
    from pcc.support.instantiation import InstanceRegistry
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid unused declaration reached instance registration')
    monkeypatch.setattr(InstanceRegistry, 'request', forbidden)
    with pytest.raises(RustError):
        compile_source(source)


@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'} else 'requires Linux x86-64 owned execution')
@pytest.mark.parametrize('name', PROGRAMS)
def test_original_generic_data_program_runs_full_width_owned_native(name, tmp_path, monkeypatch):
    _native(name, tmp_path, monkeypatch)


@pytest.mark.parametrize('name', PROGRAMS)
def test_original_generic_data_program_cross_emits_arm64_macho(name, monkeypatch):
    _cross(name, monkeypatch)


@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
@pytest.mark.parametrize('name', PROGRAMS)
def test_development_only_rustc_checks_identical_generic_data_source(name, tmp_path):
    _rustc(name, tmp_path)


@pytest.mark.parametrize('name,sha,declaration', [
    ('core_option.rs', '53b549b18552144e911416aa2773e8b726da88c190d5cb27007c3c08740894a5', 'pub enum Option<T>'),
    ('core_result.rs', '768b0584d514fce9255a762e76bb51eb1646b7bcabd6db660752960321bfc51f', 'pub enum Result<T, E>'),
])
def test_complete_pinned_core_sources_are_dependencies_not_replacement_success(name, sha, declaration):
    path = Path(__file__).parent / 'upstream/rust-1.90.0' / name
    original = path.read_bytes()
    assert hashlib.sha256(original).hexdigest() == sha
    assert declaration in original.decode()
    with pytest.raises(RustError, match='documentation attributes'):
        compile_source(original.decode())
    assert path.read_bytes() == original
