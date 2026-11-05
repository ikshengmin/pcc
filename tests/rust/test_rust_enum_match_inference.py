"""Known enum owners share unsolved slots with patterns and match arms.

Rust Reference:
https://doc.rust-lang.org/reference/types/inferred.html
https://doc.rust-lang.org/reference/expressions/match-expr.html
https://doc.rust-lang.org/reference/patterns.html#tuple-struct-patterns
Only ordinary matches on a known enum owner are widened. Unknown nominal
owners, reference matching and conditional-let gates remain.
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


PROGRAMS = ('enum_match_result', 'enum_match_patterns',
            'enum_match_nested', 'enum_match_replay')


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


@pytest.mark.parametrize('text', [
    'enum E<T>{Some(T)}fn f()->u64{match E::<_>::Some(4294967296){E::Some(x)=>x}}',
    'enum E<T>{Some(T)}fn f()->u64{let e=E::<_>::Some(1);let x=match e{E::Some(x)=>x};e;x}',
    'enum E<T>{None,Some(T)}fn f()->u64{match E::<_>::None{E::None=>0,E::Some(x)=>x}}',
    'enum E<T>{Some(T)}fn f()->u64{let y=match E::<_>::Some(4294967296){E::Some(x)=>x};y}',
    'enum E<T>{Some(T)}fn take(x:u64){}fn f(){let y=match E::<_>::Some(4294967296){E::Some(x)=>x};take(y);}',
    'enum E<T>{Some(T)}fn f()->u64{match E::<_>::Some(4294967296){E::<u64>::Some(x)=>x}}',
    'enum E<T>{None,Some(T)}fn f()->u64{match E::<_>::None{E::None=>7,E::Some(false)=>0,E::Some(true)=>1}}',
    'enum E<T>{None,Some(T)}fn f(){match E::<_>::None{E::None=>(),E::Some(())=>()}}',
    'enum E<T>{Some(T)}fn f()->u64{match E::<_>::Some((4294967296,true)){E::Some((x,true))=>x,E::Some((_,false))=>0}}',
    'enum E<T>{Some(T)}fn f()->u64{match E::<_>::Some(E::<_>::Some(4294967296)){E::Some(E::Some(x))=>x}}',
    'enum E<T,U>{A(T),B(U)}fn f()->u64{match E::<_,_>::A(4294967296){E::A(x)=>x,E::B(false)=>0,E::B(true)=>1}}',
    'enum E<T>{Some(T)}fn wrap<U>(x:U)->E<U>{E::<_>::Some(x)}fn read<U>(x:U)->U{match wrap(x){E::Some(y)=>y}}',
    'enum E<T>{None,Some(T)}fn make<U>()->E<U>{E::<_>::None}fn f()->u64{match make(){E::None=>0,E::Some(x)=>x}}',
    'mod m{pub enum E<T>{Some(T)}}use m::E as Alias;fn f()->u64{match Alias::<_>::Some(4294967296){Alias::Some(x)=>x}}',
])
def test_known_enum_match_shares_pattern_arm_and_later_constraints(text):
    compile_rust(text, experimental_rustc_checked=True)


def test_match_constraints_finish_before_data_registry_keys(monkeypatch):
    original = GenericDeclarations.instantiate

    def resolved_only(self, ty, token):
        assert not pending_integer(ty), 'unfinished match type reached nominal registry'
        return original(self, ty, token)

    monkeypatch.setattr(GenericDeclarations, 'instantiate', resolved_only)
    text = ('enum E<T>{None,Some(T)}fn f()->u64{'
            'match E::<_>::None{E::None=>0,E::Some(x)=>x}}')
    parser = Parser(text)
    checker = Checker(parser.parse(), parser.records, parser.enums, parser.implementations)
    checker.check()
    entries = tuple(checker.generics.instances.entries.values())
    assert len(entries) == 1 and entries[0].value == TypeApplication('E', ('u64',))


INVALID = (
    ('ambiguous-unit', 'enum E<T>{None,Some(T)}fn f(){match E::<_>::None{E::None=>(),E::Some(_)=>()}}', 'cannot infer', 'E0282'),
    ('non-exhaustive', 'enum E<T>{None,Some(T)}fn f()->u64{match E::<_>::Some(4294967296){E::Some(x)=>x}}', 'non-exhaustive', 'E0004'),
    ('nested-coverage', 'enum E<T>{None,Some(T)}fn f()->u64{match E::<_>::None{E::None=>0,E::Some(false)=>1}}', 'non-exhaustive', 'E0004'),
    ('conflicting-payload', 'enum E<T>{None,Some(T)}fn f()->u64{match E::<_>::None{E::None=>0,E::Some(false)=>0,E::Some(1u64)=>1}}', 'type mismatch', 'E0308'),
    ('fixed-owner', 'enum E<T>{Some(T)}fn f()->u64{match E::<bool>::Some(true){E::Some(x)=>x}}', 'type mismatch', 'E0308'),
    ('wrong-nominal-owner', 'enum E<T>{Some(T)}enum F<T>{Some(T)}fn f()->u64{match E::<_>::Some(1){F::<u64>::Some(x)=>x}}', 'type mismatch', 'E0308'),
    ('wrong-pattern-arity', 'enum E<T>{Some(T)}fn f()->u64{match E::<_>::Some(1){E::Some(x,y)=>x}}', 'pattern shape', 'E0023'),
    ('overflow', 'enum E<T>{Some(T)}fn f()->i32{match E::<_>::Some(4294967296){E::Some(x)=>x}}', 'out of range', 'literal out of range'),
    ('noncopy-scrutinee', 'struct R{x:u64}enum E<T>{Some(T)}fn f()->u64{let e=E::<_>::Some(R{x:1});let x=match e{E::Some(x)=>x};e;x.x}', 'moved', 'E0382'),
    ('shadowed-pattern-owner', 'enum E<T>{V(T)}fn make()->E<u64>{E::<u64>::V(1)}fn bad<E>()->u64{match make(){E::V(x)=>x}}', 'generic arguments require', 'E0599'),
    ('abstract-payload-move', 'enum E<T>{Some(T)}fn bad<U>(x:U)->U{match E::<_>::Some(x){E::Some(y)=>{let z=y;y}}}', 'moved', 'E0382'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_invalid_inferred_matches_fail_before_ir(name, text, message, code, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid match reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('text,message', [
    ('enum E<T>{None,Some(T)}fn f()->u64{match E::<_>::None{E::None=>0,E::Some((x,y))=>x}}', 'cannot infer'),
    ('enum E<T>{Some(T)}fn f(p:&u64)->u64{match E::<_>::Some(p){E::Some(x)=>*x}}', 'actual source/options-bound CheckedRustSource'),
])
def test_inferred_enum_matches_do_not_widen_other_boundaries(text, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported match reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


def test_loop_bearing_replay_original_still_requires_certification():
    text = 'enum E<T>{Some(T),None}\nfn wrap<U>(x:U)->E<U>{E::<_>::Some(x)}\nfn identity<U>(x:U)->U{match wrap(x){E::Some(y)=>y,E::None=>loop{}}}\nfn empty<U>()->E<U>{E::<_>::None}\nfn zero()->u64{match empty(){E::Some(x)=>x,E::None=>0}}\npub fn answer()->u64{\n    if identity(true) && identity(9u32)==9u32 && zero()==0{\n        identity(0xfedcba9876543210)\n    }else{0}\n}\n'
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
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


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('text', [
    'enum E<T>{None,Some(T)}fn take(x:u64){}fn f(){if let (E::Some(x),)=(E::<_>::Some(4294967296),){take(x);}}',
    'enum E<T>{None,Some(T)}fn take(x:u64){}fn f(){while let (E::Some(x),)=(E::<_>::Some(4294967296),){take(x);break;}}',
])
def test_actual_certification_does_not_widen_nested_conditional_inference(text, tmp_path, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    from tests.rust.certification_support import certify_source
    checked = certify_source(text, tmp_path / 'certifier')
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('conditional inference reached IR'))
    with pytest.raises(RustError, match='unresolved inferred generic'):
        compile_rust(text, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_copy_payload_original_is_legal_in_actual_rustc(tmp_path):
    from tests.rust.certification_support import certify_source
    text = 'enum E<T>{Some(T)}fn f()->u64{let e=E::<_>::Some(1);let x=match e{E::Some(x)=>x};e;x}'
    checked = certify_source(text, tmp_path / 'certifier')
    compile_rust(text, rustc_check=checked)
