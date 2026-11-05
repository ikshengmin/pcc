"""Omitted enum constructor arguments share whole-body inference constraints.

Rust Reference:
https://doc.rust-lang.org/reference/paths.html#paths-in-expressions
https://doc.rust-lang.org/reference/items/enumerations.html
https://doc.rust-lang.org/reference/expressions.html#evaluation-order-of-operands
Bare type annotations and associated-method owners remain explicit boundaries.
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

PROGRAMS = ('enum_omitted_order', 'enum_omitted_composites', 'enum_omitted_replay')


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


@pytest.mark.parametrize('text', [
    # Preserve both exact former capability-boundary originals.
    'enum E<T>{Some(T)}fn f(){E::Some(1u64);}',
    'enum E<T>{Value(T)} fn f(){E::Value(1);}',
    'enum E<T>{None,Some(T)}fn f(){let a:E<u64>=E::None;let b:E<bool>=E::None;E::Some(1u64);E::Some(true);}',
    'enum E<T>{None,Some(T)}fn f()->E<u64>{E::None}',
    'enum E<T>{Some(T)}fn f()->E<u64>{E::Some(4294967296)}',
    'enum E<T>{None,Some(T)}fn take(x:E<u64>){}fn f(){let x=E::None;take(x);}',
    'enum E<T>{Some(T)}fn f()->E<u64>{let x:E<_>=E::Some(4294967296);x}',
    'enum E<T,U>{Pair(T,U)}fn f()->E<u64,bool>{E::Pair(4294967296,true)}',
    'enum E<T>{Some(T)}fn f()->E<(u64,bool)>{E::Some((4294967296,true))}',
    'enum E<T>{Some(T)}fn f()->E<[u64;2]>{E::Some([4294967296,1])}',
    'enum E<T>{Some(T)}fn f()->E<[u64;0]>{E::Some([])}',
    'enum E<T>{Some(T)}fn f()->E<E<u64>>{E::Some(E::Some(4294967296))}',
    'enum E<T>{None,Some(T)}fn wrap<U>(x:U)->E<U>{E::Some(x)}',
    'enum E<T>{None,Some(T)}fn empty<U>()->E<U>{E::None}',
    'enum E<T>{None,Some(T)}fn f(b:bool)->E<u64>{if b{E::None}else{E::Some(4294967296)}}',
    'enum E<T>{None,Some(T)}fn f()->E<u64>{let mut x=E::None;x=E::Some(4294967296u64);x}',
    'enum E<T>{Some(T)}fn f()->u64{match E::Some(4294967296){E::Some(x)=>x}}',
    'mod m{pub enum E<T>{Some(T)}}fn f()->m::E<u64>{m::E::Some(4294967296)}',
    'mod m{pub enum E<T>{None,Some(T)}}use m::E as Alias;fn f()->Alias<u64>{Alias::None}',
    'enum E<T>{None,Some(T)}impl<T>E<T>{fn empty()->Self{Self::None}}fn f()->E<u64>{E::<u64>::empty()}',
])
def test_omitted_enum_owner_arguments_share_constraints(text):
    compile_rust(text, experimental_rustc_checked=True)


def test_omitted_enum_registry_is_resolved_and_nominally_reused(monkeypatch):
    original = GenericDeclarations.instantiate

    def concrete_only(self, ty, token):
        assert not pending_integer(ty), 'unresolved enum owner reached data registry'
        return original(self, ty, token)

    monkeypatch.setattr(GenericDeclarations, 'instantiate', concrete_only)
    parser = Parser('enum E<T>{None,Some(T)}fn take(x:E<u64>){}fn f(){'
                    'let x=E::None;take(x);let y=E::Some(4294967296);take(y);}')
    checker = Checker(parser.parse(), parser.records, parser.enums, parser.implementations)
    checker.check()
    entries = tuple(checker.generics.instances.entries.values())
    assert len(entries) == 1 and entries[0].value == TypeApplication('E', ('u64',))


INVALID = (
    ('unconstrained-unit', 'enum E<T>{None,Some(T)}fn f(){E::None;}', 'cannot infer', 'E0282'),
    ('unconstrained-empty-payload', 'enum E<T>{Some(T)}fn f(){E::Some([]);}', 'cannot infer', 'E0282'),
    ('repeated-slot', 'enum E<T>{Pair(T,T)}fn f(){E::Pair(1u64,true);}', 'type mismatch', 'E0308'),
    ('nominal-result', 'enum E<T>{Some(T)}enum Other<T>{Some(T)}fn f()->Other<u64>{E::Some(1u64)}', 'type mismatch', 'E0308'),
    ('payload-arity', 'enum E<T>{Some(T)}fn f(){E::Some(1u64,2u64);}', 'argument count', 'E0061'),
    ('unit-call', 'enum E<T>{None,Some(T)}fn f()->E<u64>{E::None()}', 'unit variant', 'E0618'),
    ('unknown-variant', 'enum E<T>{Some(T)}fn f()->E<u64>{E::Missing}', 'unknown enum variant', 'E0599'),
    ('overflow', 'enum E<T>{Some(T)}fn f()->E<i32>{E::Some(4294967296)}', 'out of range', 'literal out of range'),
    ('noncopy-move', 'enum E<T>{Some(T)}fn take<T>(x:E<T>){}fn f(){let x=E::Some(1u64);take(x);take(x);}', 'moved', 'E0382'),
    ('unused-abstract-move', 'enum E<T>{Some(T)}fn bad<U>(x:U)->U{E::Some(x);x}', 'moved', 'E0382'),
    ('private-owner', 'mod m{enum E<T>{Some(T)}}fn f(){m::E::Some(1u64);}', 'private', 'E0603'),
    ('shadowed-owner', 'enum E<T>{None,Some(T)}fn f<E>(){E::None;}', 'unknown enum variant', 'E0599'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_invalid_omitted_enum_originals_stop_before_lowering(name, text, message, code, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid enum reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('text,message', [
    ('enum E<T>{Some(T)}fn f(x:E){}', 'requires explicit type arguments'),
    ('enum E<T>{Some(T)}fn f(){E::Some::<u64>(1);}', 'constructor segments'),
    ('enum E<T>{Some(T)}impl<T>E<T>{fn wrap(x:T)->Self{Self::Some(x)}}fn f()->E<u64>{E::wrap(1)}', 'requires explicit type arguments'),
    ('enum E<T>{Some(T)}fn f(p:&u64){E::Some(p);}', 'actual source/options-bound CheckedRustSource'),
])
def test_omitted_enum_arguments_preserve_other_boundaries(text, message):
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('name', PROGRAMS)
@pytest.mark.parametrize('target,magic', [
    ('x86_64-unknown-linux-gnu', b'\x7fELF'),
    ('arm64-apple-darwin', b'\xcf\xfa\xed\xfe'),
])
def test_omitted_enum_originals_emit_owned_objects(name, target, magic):
    assert compile_rust(source(name), experimental_rustc_checked=True,
                        target_triple=target).emit_object().startswith(magic)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
) else 'requires owned Linux x86-64 execution')
@pytest.mark.parametrize('name', PROGRAMS)
def test_omitted_enum_originals_execute_full_width_owned(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution
    test_original_match_fixture_full_width_owned_execution(name, tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc')
) else 'requires Linux native rustc/system-linker reference')
def test_omitted_enum_originals_match_actual_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference, 'PROGRAMS', PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_same_invalid_omitted_enum_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)


def test_original_wildcard_let_remains_a_parser_gap():
    # The first authored composite program exposed the existing wildcard-let
    # parser gap. Preserve those exact bytes separately from the valid program.
    with pytest.raises(RustError, match='expected an identifier'):
        compile_rust(source('enum_omitted_values'), experimental_rustc_checked=True)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_original_wildcard_let_is_valid_rust_but_outside_parser(tmp_path):
    from tests.rust.certification_support import certify_source
    certify_source(source('enum_omitted_values'), tmp_path / 'certifier')
    test_original_wildcard_let_remains_a_parser_gap()
