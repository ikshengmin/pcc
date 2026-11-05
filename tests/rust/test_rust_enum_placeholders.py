"""Explicit enum-owner holes preserve payload and result constraints.

Rust Reference:
https://doc.rust-lang.org/reference/types/inferred.html
https://doc.rust-lang.org/reference/items/enumerations.html
https://doc.rust-lang.org/reference/expressions/call-expr.html
https://doc.rust-lang.org/reference/expressions.html#evaluation-order-of-operands
Associated-method owner holes remain an explicit capability boundary;
no reference certification is invented.
"""
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.call_inference import pending_integer
from pcc.frontends.rust.generics import GenericDeclarations
from pcc.frontends.rust.semantics import Checker
from pcc.frontends.rust.syntax import Parser
from pcc.frontends.rust.types import TypeApplication
from tests.rust.certification_support import actual_check_prerequisite


PROGRAMS = ('enum_placeholder_values', 'enum_placeholder_composites',
            'enum_placeholder_replay', 'enum_placeholder_order')


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


@pytest.mark.parametrize('text', [
    'enum E<T>{Some(T)}fn f(){E::<_>::Some(1u64);}',
    'enum E<T>{None,Some(T)}fn f()->E<u64>{E::<_>::None}',
    'enum E<T>{Some(T)}fn f()->E<u64>{E::<_>::Some(4294967296)}',
    'enum E<T>{None,Some(T)}fn take(x:E<u64>){}fn f(){let x=E::<_>::None;take(x);}',
    'enum E<T>{Some(T)}fn take(x:E<u64>){}fn f(){let x=E::<_>::Some(4294967296);take(x);}',
    'enum E<T>{Some(T)}fn f()->E<u64>{let x:E<_>=E::<_>::Some(4294967296);x}',
    'enum E<T,U>{Some(T,U)}fn f()->E<u64,bool>{E::<_,bool>::Some(4294967296,true)}',
    'enum E<T>{Some(T)}fn f()->E<(u64,bool)>{E::<(_,_)>::Some((4294967296,true))}',
    'enum E<T>{Some(T)}fn f()->E<[u64;2]>{E::<[_;2]>::Some([4294967296,1])}',
    'enum E<T>{Some(T)}fn f()->E<[u64;0]>{E::<[_;0]>::Some([])}',
    'enum E<T>{None,Some(T)}fn wrap<U>(x:U)->E<U>{E::<_>::Some(x)}',
    'enum E<T>{None,Some(T)}fn empty<U>()->E<U>{E::<_>::None}',
    'enum E<T>{None,Some(T)}fn f(b:bool)->E<u64>{if b{E::<_>::None}else{E::<_>::Some(4294967296)}}',
    'enum E<T>{None,Some(T)}fn f()->E<u64>{let mut x=E::<_>::None;x=E::<u64>::Some(4294967296);x}',
    'enum E<T>{Some(T)}fn f()->u64{match E::<_>::Some(4294967296u64){E::Some(x)=>x}}',
    'enum E<T>{Some(T)}impl<T>E<T>{fn wrap(x:T)->Self{Self::Some(x)}}fn f()->E<u64>{E::<u64>::wrap(1)}',
    'mod m{pub enum E<T>{Some(T)}}fn f()->m::E<u64>{m::E::<_>::Some(4294967296)}',
    'mod m{pub enum E<T>{Some(T)}}use m::E as Alias;fn f()->Alias<u64>{Alias::<_>::Some(4294967296)}',
])
def test_enum_owner_holes_share_payload_and_whole_body_constraints(text):
    compile_rust(text, experimental_rustc_checked=True)


def test_enum_registry_only_sees_resolved_arguments_and_reuses_nominal_identity(monkeypatch):
    original = GenericDeclarations.instantiate

    def concrete_only(self, ty, token):
        assert not pending_integer(ty), 'unresolved enum owner reached data registry'
        return original(self, ty, token)

    monkeypatch.setattr(GenericDeclarations, 'instantiate', concrete_only)
    text = ('enum E<T>{None,Some(T)}fn take(x:E<u64>){}fn f(){'
            'let x=E::<_>::None;take(x);let y=E::<_>::Some(4294967296);take(y);}')
    parser = Parser(text)
    checker = Checker(parser.parse(), parser.records, parser.enums, parser.implementations)
    checker.check()
    entries = tuple(checker.generics.instances.entries.values())
    assert len(entries) == 1 and entries[0].value == TypeApplication('E', ('u64',))


INVALID = (
    ('unconstrained-unit', 'enum E<T>{None,Some(T)}fn f(){E::<_>::None;}', 'cannot infer', 'E0282'),
    ('unconstrained-payload', 'enum E<T>{Some(T)}fn f(){E::<[_;0]>::Some([]);}', 'cannot infer', 'E0282'),
    ('fixed-slot', 'enum E<T,U>{Some(T,U)}fn f(){E::<_,bool>::Some(1u64,2u64);}', 'type mismatch', 'E0308'),
    ('repeated-slot', 'enum E<T>{Some(T,T)}fn f(){E::<_>::Some(1u64,true);}', 'type mismatch', 'E0308'),
    ('nominal-slot', 'struct R<T>{x:T}enum E<T>{Some(T)}fn f(){E::<R<_>>::Some(1u64);}', 'type mismatch', 'E0308'),
    ('array-length', 'enum E<T>{Some(T)}fn f(){E::<[_;2]>::Some([1u64]);}', 'type mismatch', 'E0308'),
    ('owner-arity', 'enum E<T>{Some(T)}fn f(){E::<_,_>::Some(1u64);}', 'argument count', 'E0107'),
    ('payload-arity', 'enum E<T>{Some(T)}fn f(){E::<_>::Some(1u64,2u64);}', 'argument count', 'E0061'),
    ('unit-call', 'enum E<T>{None,Some(T)}fn f()->E<u64>{E::<_>::None()}', 'unit variant', 'E0618'),
    ('unknown-variant', 'enum E<T>{Some(T)}fn f()->E<u64>{E::<_>::Missing}', 'unknown enum variant', 'E0599'),
    ('overflow', 'enum E<T>{Some(T)}fn f()->E<i32>{E::<_>::Some(4294967296)}', 'out of range', 'literal out of range'),
    ('noncopy-move', 'enum E<T>{Some(T)}fn take<T>(x:E<T>){}fn f(){let x=E::<_>::Some(1u64);take(x);take(x);}', 'moved', 'E0382'),
    ('unused-abstract-move', 'enum E<T>{Some(T)}fn bad<U>(x:U)->U{E::<_>::Some(x);x}', 'moved', 'E0382'),
    ('private-owner', 'mod m{enum E<T>{Some(T)}}fn f(){m::E::<_>::Some(1u64);}', 'private', 'E0603'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_invalid_enum_holes_fail_before_lowering(name, text, message, code, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid enum reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('text,message', [
    ('enum E<T>{Some(T)}fn f(){E::<u64>::Some::<_>(1);}', 'constructor segments'),
    ('enum E<T>{Some(T)}impl<T>E<T>{fn wrap(x:T)->Self{Self::Some(x)}}fn f()->E<u64>{E::<_>::wrap(1)}', "unsupported Rust type '_'"),
    ('enum E<T>{Some(T)}fn f(p:&u64){E::<_>::Some(p);}', 'actual source/options-bound CheckedRustSource'),
    ('enum E<T>{None,Some(T)}fn f()->bool{let x:E<u64>=E::<_>::None;if let E::None=x{true}else{false}}', 'actual source/options-bound CheckedRustSource'),
    ('mod m {} fn f<T>() { T::<u64>::missing(); }', 'generic arguments require'),
])
def test_enum_holes_preserve_other_capability_boundaries(text, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported enum path reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


def test_array_payload_matching_remains_an_explicit_gap():
    text = 'struct Wrapped<T>{value:T}\nenum Either<T,U>{First(T),Second(U)}\nfn read(value:Either<Wrapped<(u64,bool)>,[u64;2]>)->u64{\n    match value{\n        Either::First(x)=>if x.value.1{x.value.0}else{0},\n        Either::Second(x)=>x[0]+x[1],\n    }\n}\npub fn answer()->u64{\n    let a=Either::<Wrapped<(_,_)>,[_;2]>::First(Wrapped::<_>{value:(0xfedcba9876543210,true)});\n    let b=Either::<Wrapped<(_,_)>,[_;2]>::Second([1,2]);\n    if read(b)==3{read(a)}else{0}\n}\n'
    with pytest.raises(RustError, match='array match coverage and destructuring'):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('name', PROGRAMS)
@pytest.mark.parametrize('target,magic', [
    ('x86_64-unknown-linux-gnu', b'\x7fELF'),
    ('arm64-apple-darwin', b'\xcf\xfa\xed\xfe'),
])
def test_enum_hole_originals_emit_owned_target_objects(name, target, magic):
    result = compile_rust(source(name), experimental_rustc_checked=True, target_triple=target)
    assert result.emit_object().startswith(magic)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
) else 'requires owned Linux x86-64 execution')
@pytest.mark.parametrize('name', PROGRAMS)
def test_enum_hole_originals_execute_full_width_owned(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution
    test_original_match_fixture_full_width_owned_execution(name, tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc')
) else 'requires Linux native rustc/system-linker reference')
def test_enum_hole_originals_match_actual_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference, 'PROGRAMS', PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_same_invalid_enum_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
