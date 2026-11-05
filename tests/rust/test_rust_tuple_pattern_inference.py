"""Tuple patterns contribute shape without guessing unknown element types.

Rust Reference:
https://doc.rust-lang.org/reference/patterns.html#tuple-patterns
https://doc.rust-lang.org/reference/expressions/match-expr.html
https://doc.rust-lang.org/reference/types/inferred.html
Ordinary match only; abstract parameters, other shapes and conditional gates
remain unchanged. Remaining ambiguous tuple slots must still fail.
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


PROGRAMS = ('tuple_pattern_values', 'tuple_pattern_nested', 'tuple_pattern_replay')


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


@pytest.mark.parametrize('text', [
    'enum E<T>{None,Some(T)}fn f()->u64{match E::<_>::None{E::None=>0,E::Some((x,true))=>x,E::Some((_,false))=>0}}',
    'enum E<T>{None,Some(T)}fn f()->u64{match E::<_>::None{E::None=>0,E::Some((x,flag))=>if flag{x}else{0}}}',
    'enum E<T>{None,Some(T)}fn f()->u64{match E::<_>::None{E::None=>0,E::Some((x,))=>x}}',
    'enum E<T>{None,Some(T)}fn f()->u64{match E::<_>::None{E::None=>0,E::Some((x,(flag,())))=>if flag{x}else{0}}}',
    'enum E<T>{None,Some(T)}fn f()->u64{match E::<_>::None{E::None=>0,E::Some((true,()))|E::Some((false,()))=>1}}',
    'enum E<T>{None,Some(T)}fn take(x:u64){}fn f(){let x=match E::<_>::None{E::None=>0,E::Some((x,true))=>x,E::Some((_,false))=>0};take(x);}',
    'enum E<T>{None,Some(T)}fn empty<U>()->E<U>{E::<_>::None}fn f()->u64{match empty(){E::None=>0,E::Some((x,true))=>x,E::Some((_,false))=>0}}',
    'enum E<T>{None,Some(T)}fn empty<U>()->E<U>{E::<_>::None}fn choose<U>(fallback:U)->U{match empty(){E::None=>fallback,E::Some((x,true))=>x,E::Some((x,false))=>x}}',
    'enum E<T>{None,Some(T)}fn f(){let mut e=E::<_>::None;match e{E::None=>(),E::Some((_,_))=>()}e=E::<_>::Some((1u64,true));}',
])
def test_tuple_patterns_constrain_shape_elements_and_later_uses(text):
    compile_rust(text, experimental_rustc_checked=True)


def test_tuple_pattern_variables_resolve_before_nominal_registry(monkeypatch):
    original = GenericDeclarations.instantiate

    def resolved_only(self, ty, token):
        assert not pending_integer(ty), 'unfinished tuple pattern reached nominal registry'
        return original(self, ty, token)

    monkeypatch.setattr(GenericDeclarations, 'instantiate', resolved_only)
    text = 'enum E<T>{None,Some(T)}fn f()->u64{match E::<_>::None{E::None=>0,E::Some((x,flag))=>if flag{x}else{0}}}'
    parser = Parser(text)
    checker = Checker(parser.parse(), parser.records, parser.enums, parser.implementations)
    checker.check()
    from pcc.frontends.rust.types import TupleType
    entries = tuple(checker.generics.instances.entries.values())
    assert len(entries) == 1 and entries[0].value == TypeApplication('E', (TupleType(('u64', 'bool')),))


INVALID = (
    ('original-ambiguous-slot', 'enum E<T>{None,Some(T)}fn f()->u64{match E::<_>::None{E::None=>0,E::Some((x,y))=>x}}', 'cannot infer', 'E0282'),
    ('ambiguous-wildcards', 'enum E<T>{None,Some(T)}fn f(){match E::<_>::None{E::None=>(),E::Some((_,_))=>()}}', 'cannot infer', 'E0282'),
    ('tuple-arity', 'enum E<T>{None,Some(T)}fn f()->u64{match E::<_>::None{E::None=>0,E::Some((x,true))=>x,E::Some((x,false,()))=>x}}', 'tuple pattern shape', 'E0308'),
    ('integer-not-tuple', 'enum E<T>{Some(T)}fn f()->u64{match E::<_>::Some(1){E::Some((x,))=>x}}', 'tuple pattern shape', 'E0308'),
    ('abstract-not-tuple', 'fn f<T>(x:T)->u64{match x{(a,true)=>a,(_,false)=>0}}', 'tuple pattern shape', 'E0308'),
    ('element-conflict', 'enum E<T>{None,Some(T)}fn f()->u64{match E::<_>::None{E::None=>0,E::Some((true,true))=>0,E::Some((1u64,false))=>1}}', 'type mismatch', 'E0308'),
    ('nested-coverage', 'enum E<T>{None,Some(T)}fn f()->u64{match E::<_>::None{E::None=>0,E::Some((true,true))=>1}}', 'non-exhaustive', 'E0004'),
    ('overflow', 'enum E<T>{None,Some(T)}fn f()->i32{match E::<_>::None{E::None=>0,E::Some((4294967296,x))=>x,E::Some((_,x))=>x}}', 'out of range', 'literal out of range'),
    ('recursive-slot', 'enum E<T>{None,Some(T)}fn f(){let mut e=E::<_>::None;match e{E::None=>(),E::Some((x,))=>{e=E::<_>::Some(x);}}}', 'infinite inferred', 'E0275'),
    ('abstract-move', 'enum E<T>{None,Some(T)}fn empty<U>()->E<U>{E::<_>::None}fn bad<U>(fallback:U)->U{match empty(){E::None=>fallback,E::Some((x,true))=>{let y=x;x},E::Some((x,false))=>x}}', 'moved', 'E0382'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_bad_tuple_constraints_fail_before_lowering(name, text, message, code, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid tuple inference reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('name', PROGRAMS)
@pytest.mark.parametrize('target,magic', [
    ('x86_64-unknown-linux-gnu', b'\x7fELF'),
    ('arm64-apple-darwin', b'\xcf\xfa\xed\xfe'),
])
def test_match_inference_originals_emit_owned_target_objects(name, target, magic):
    result = compile_rust(source(name), experimental_rustc_checked=True, target_triple=target)
    assert result.emit_object().startswith(magic)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
) else 'requires owned Linux x86-64 execution')
@pytest.mark.parametrize('name', PROGRAMS)
def test_match_inference_originals_execute_full_width_owned(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution
    test_original_match_fixture_full_width_owned_execution(name, tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc')
) else 'requires Linux native rustc/system-linker reference')
def test_match_inference_originals_match_actual_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference, 'PROGRAMS', PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_same_invalid_match_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
