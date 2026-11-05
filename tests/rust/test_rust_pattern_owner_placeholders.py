"""Explicit enum pattern owner holes stay shared with match constraints.

Rust Reference:
https://doc.rust-lang.org/reference/types/inferred.html
https://doc.rust-lang.org/reference/patterns.html#tuple-struct-patterns
https://doc.rust-lang.org/reference/expressions/match-expr.html
Only ordinary enum patterns admit holes; conditional gates remain unchanged.
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


PROGRAMS = ('pattern_owner_values', 'pattern_owner_nested', 'pattern_owner_replay')


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


@pytest.mark.parametrize('text', [
    # Keep the exact earlier unsupported input, now an ordinary positive case.
    'enum E<T>{Some(T)}fn f()->u64{match E::<_>::Some(1){E::<_>::Some(x)=>x}}',
    'enum E<T>{Some(T)}fn f()->u64{match E::<u64>::Some(4294967296){E::<_>::Some(x)=>x}}',
    'enum E<T>{None,Some(T)}fn f()->u64{match E::<_>::None{E::<_>::None=>0,E::<_>::Some(x)=>x}}',
    'enum E<T>{Some(T)}fn f()->u64{match E::<_>::Some((4294967296,true)){E::<(_,bool)>::Some((x,true))=>x,E::<(_,_)>::Some((_,false))=>0}}',
    'enum E<T>{None,Some(T)}fn f(){match E::<_>::None{E::<_>::None=>(),E::<_>::Some(false)|E::<_>::Some(true)=>()}}',
    'enum E<T>{Some(T)}fn f()->u64{match E::<_>::Some(E::<_>::Some(4294967296)){E::<E<_>>::Some(E::<_>::Some(x))=>x}}',
    'enum E<T>{Some(T)}fn take(x:u64){}fn f(){let x=match E::<_>::Some(4294967296){E::<_>::Some(x)=>x};take(x);}',
    'enum E<T>{Some(T)}fn take<U>(x:E<U>)->U{match x{E::<_>::Some(y)=>y}}',
    'mod m{pub enum E<T>{Some(T)}}use m::E as Alias;fn f()->u64{match Alias::<_>::Some(4294967296){Alias::<_>::Some(x)=>x}}',
])
def test_explicit_pattern_holes_share_scrutinee_arm_and_later_constraints(text):
    compile_rust(text, experimental_rustc_checked=True)


def test_pattern_holes_finish_before_nominal_registry_keys(monkeypatch):
    original = GenericDeclarations.instantiate

    def resolved_only(self, ty, token):
        assert not pending_integer(ty), 'unfinished pattern owner reached nominal registry'
        return original(self, ty, token)

    monkeypatch.setattr(GenericDeclarations, 'instantiate', resolved_only)
    text = 'enum E<T>{None,Some(T)}fn f()->u64{match E::<_>::None{E::<_>::None=>0,E::<_>::Some(x)=>x}}'
    parser = Parser(text)
    checker = Checker(parser.parse(), parser.records, parser.enums, parser.implementations)
    checker.check()
    entries = tuple(checker.generics.instances.entries.values())
    assert len(entries) == 1 and entries[0].value == TypeApplication('E', ('u64',))


INVALID = (
    ('ambiguous', 'enum E<T>{None,Some(T)}fn f(){match E::<_>::None{E::<_>::None=>(),E::<_>::Some(_)=>()}}', 'cannot infer', 'E0282'),
    ('fixed-slot', 'enum E<T>{Some(T)}fn f()->u64{match E::<u64>::Some(1){E::<bool>::Some(x)=>0}}', 'type mismatch', 'E0308'),
    ('wrong-owner', 'enum E<T>{Some(T)}enum F<T>{Some(T)}fn f()->u64{match E::<u64>::Some(1){F::<_>::Some(x)=>x}}', 'type mismatch', 'E0308'),
    ('wrong-arity', 'enum E<T>{Some(T)}fn f()->u64{match E::<u64>::Some(1){E::<_,_>::Some(x)=>x}}', 'argument count mismatch', 'E0107'),
    ('wrong-variant', 'enum E<T>{Some(T)}fn f()->u64{match E::<u64>::Some(1){E::<_>::Other(x)=>x}}', 'unknown enum variant', 'E0599'),
    ('wrong-payload-arity', 'enum E<T>{Some(T)}fn f()->u64{match E::<u64>::Some(1){E::<_>::Some(x,y)=>x}}', 'pattern shape', 'E0023'),
    ('non-exhaustive', 'enum E<T>{None,Some(T)}fn f()->u64{match E::<u64>::Some(1){E::<_>::Some(x)=>x}}', 'non-exhaustive', 'E0004'),
    ('overflow', 'enum E<T>{Some(T)}fn f()->i32{match E::<_>::Some(4294967296){E::<_>::Some(x)=>x}}', 'out of range', 'literal out of range'),
    ('shadowed-owner', 'enum E<T>{Some(T)}fn make()->E<u64>{E::<u64>::Some(1)}fn f<E>()->u64{match make(){E::<_>::Some(x)=>x}}', 'generic arguments require', 'E0109'),
    ('noncopy-move', 'struct R{x:u64}enum E<T>{Some(T)}fn f()->u64{let e=E::<_>::Some(R{x:1});let x=match e{E::<_>::Some(x)=>x};e;x.x}', 'moved', 'E0382'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_invalid_pattern_holes_fail_before_lowering(name, text, message, code, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid pattern owner reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('name', PROGRAMS)
@pytest.mark.parametrize('target,magic', [
    ('x86_64-unknown-linux-gnu', b'\x7fELF'),
    ('arm64-apple-darwin', b'\xcf\xfa\xed\xfe'),
])
def test_pattern_owner_originals_emit_owned_target_objects(name, target, magic):
    result = compile_rust(source(name), experimental_rustc_checked=True, target_triple=target)
    assert result.emit_object().startswith(magic)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
) else 'requires owned Linux x86-64 execution')
@pytest.mark.parametrize('name', PROGRAMS)
def test_pattern_owner_originals_execute_full_width_owned(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution
    test_original_match_fixture_full_width_owned_execution(name, tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc')
) else 'requires Linux native rustc/system-linker reference')
def test_pattern_owner_originals_match_actual_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference, 'PROGRAMS', PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_same_invalid_pattern_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)

@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('text', [
    'enum E<T>{Some(T),None}fn f(e:E<u64>)->u64{if let E::<_>::Some(x)=e{x}else{0}}',
    'enum E<T>{Some(T),None}fn f(e:E<u64>){while let E::<_>::Some(x)=e{break;}}',
])
def test_certified_conditional_owner_holes_still_fail_closed(text, tmp_path, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    from tests.rust.certification_support import certify_source
    checked = certify_source(text, tmp_path / 'certifier')
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('conditional owner hole reached IR'))
    with pytest.raises(RustError, match="unsupported Rust type '_'"):
        compile_rust(text, rustc_check=checked)


@pytest.mark.parametrize('text', [
    'enum E<T>{Some(T)}fn f()->u64{match E::<_>::Some([4294967296,2]){E::<[_;2]>::Some(x)=>x[0]}}',
    source('pattern_owner_array'),
])
def test_original_array_patterns_keep_coverage_gate(text, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('array match reached IR'))
    with pytest.raises(RustError, match='array match coverage'):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_original_array_pattern_is_legal_but_outside_current_coverage(tmp_path, monkeypatch):
    from tests.rust.certification_support import certify_source
    text = source('pattern_owner_array')
    certify_source(text, tmp_path / 'certifier')
    test_original_array_patterns_keep_coverage_gate(text, monkeypatch)
