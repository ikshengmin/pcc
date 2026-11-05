"""Ordinary struct binding matches defer inference until body constraints finish.

Rust Reference:
https://doc.rust-lang.org/reference/expressions/match-expr.html
https://doc.rust-lang.org/reference/patterns.html#identifier-patterns
https://doc.rust-lang.org/reference/patterns.html#wildcard-pattern
No struct destructuring, reference ergonomics or conditional-let widening.
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


PROGRAMS = ('struct_match_result', 'struct_match_wildcard', 'struct_match_replay')


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


@pytest.mark.parametrize('text', [
    # Retain the exact former capability rejection as a positive regression.
    'struct R<T>{x:T}fn f()->u64{match (R::<_>{x:1}){r=>r.x}}',
    'struct R<T>{x:T}fn f()->u64{match (R::<_>{x:4294967296}){r=>r.x}}',
    'struct R<T>{x:T}fn f()->R<u64>{match (R::<_>{x:4294967296}){r=>r}}',
    'struct R<T>{x:T}fn take(x:u64){}fn f(){let x=match (R::<_>{x:4294967296}){r=>r.x};take(x);}',
    'struct R<T>{x:T}fn f()->u64{match (R::<_>{x:(4294967296,true)}){r=>if r.x.1{r.x.0}else{0}}}',
    'struct R<T>{x:T}fn f(){let mut r=R::<_>{x:1};match r{_=>()}r=R::<_>{x:4294967296u64};}',
    'struct R<T>{x:T}fn f()->u64{let r=R::<_>{x:4294967296};match r{_=>()}r.x}',
    'struct R<T>{x:T}fn f()->u64{let r=R::<_>{x:R::<_>{x:4294967296}};match r{_=>()}r.x.x}',
    'struct R<T>{x:T}fn read<U>(x:U)->U{match (R::<_>{x:x}){r=>r.x}}',
    'mod m{pub struct R<T>{pub x:T}}use m::R as Alias;fn f()->u64{match (Alias::<_>{x:4294967296}){r=>r.x}}',
])
def test_struct_bindings_share_fields_results_and_later_uses(text):
    compile_rust(text, experimental_rustc_checked=True)


def test_struct_match_inference_finishes_before_registry(monkeypatch):
    original = GenericDeclarations.instantiate

    def resolved_only(self, ty, token):
        assert not pending_integer(ty), 'unfinished struct match reached nominal registry'
        return original(self, ty, token)

    monkeypatch.setattr(GenericDeclarations, 'instantiate', resolved_only)
    parser = Parser('struct R<T>{x:T}fn f()->u64{match (R::<_>{x:4294967296}){r=>r.x}}')
    checker = Checker(parser.parse(), parser.records, parser.enums, parser.implementations)
    checker.check()
    entries = tuple(checker.generics.instances.entries.values())
    assert len(entries) == 1 and entries[0].value == TypeApplication('R', ('u64',))


INVALID = (
    ('fixed-slot', 'struct R<T>{x:T}fn f()->u64{match (R::<bool>{x:true}){r=>r.x}}', 'type mismatch', 'E0308'),
    ('nominal-result', 'struct R<T>{x:T}struct S<T>{x:T}fn f()->S<u64>{match (R::<_>{x:1}){r=>r}}', 'type mismatch', 'E0308'),
    ('tuple-not-record', 'struct R<T>{x:T}fn f()->u64{match (R::<_>{x:1}){(x,)=>x}}', 'tuple pattern shape', 'E0308'),
    ('non-exhaustive', 'struct R<T>{x:T}fn f()->u64{match (R::<_>{x:1}){}}', 'non-exhaustive', 'E0004'),
    ('ambiguous', 'struct R<T>{x:T}enum E<T>{None,Some(T)}fn f(){match (R::<_>{x:E::<_>::None}){_=>()}}', 'cannot infer', 'E0282'),
    ('overflow', 'struct R<T>{x:T}fn f()->i32{match (R::<_>{x:4294967296}){r=>r.x}}', 'out of range', 'literal out of range'),
    ('whole-move', 'struct R<T>{x:T}fn f()->u64{let r=R::<_>{x:4294967296};let x=match r{s=>s.x};r;x}', 'moved', 'E0382'),
    ('abstract-move', 'struct R<T>{x:T}fn f<U>(value:U)->U{match (R::<_>{x:value}){r=>{let x=r.x;r.x}}}', 'moved', 'E0382'),
    ('private-field', 'mod m{pub struct R<T>{x:T}pub fn make()->R<u64>{R::<_>{x:1}}}fn f()->u64{match m::make(){r=>r.x}}', 'private field', 'E0616'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_bad_struct_matches_fail_before_lowering(name, text, message, code, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid struct match reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('text,message', [
    ('struct R<T>{x:T}fn f()->u64{match (R::<_>{x:[4294967296]}){r=>r.x[0]}}', 'array match coverage'),
    ('struct R<T>{x:T}fn f(p:&u64)->u64{match (R::<_>{x:p}){r=>*r.x}}', 'actual source/options-bound CheckedRustSource'),
])
def test_other_struct_match_boundaries_remain_explicit(text, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported struct match reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('name', PROGRAMS)
@pytest.mark.parametrize('target,magic', [
    ('x86_64-unknown-linux-gnu', b'\x7fELF'),
    ('arm64-apple-darwin', b'\xcf\xfa\xed\xfe'),
])
def test_struct_match_originals_emit_owned_target_objects(name, target, magic):
    result = compile_rust(source(name), experimental_rustc_checked=True, target_triple=target)
    assert result.emit_object().startswith(magic)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
) else 'requires owned Linux x86-64 execution')
@pytest.mark.parametrize('name', PROGRAMS)
def test_struct_match_originals_execute_full_width_owned(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution
    test_original_match_fixture_full_width_owned_execution(name, tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc')
) else 'requires Linux native rustc/system-linker reference')
def test_struct_match_originals_match_actual_rustc(tmp_path, monkeypatch):
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
    'struct R<T>{x:T}fn f()->u64{if let r=(R::<_>{x:4294967296}){r.x}else{0}}',
    'struct R<T>{x:T}fn take(x:u64){}fn f(){while let r=(R::<_>{x:4294967296}){take(r.x);break;}}',
])
def test_certified_conditional_struct_matches_keep_inference_gate(text, tmp_path, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    from tests.rust.certification_support import certify_source
    checked = certify_source(text, tmp_path / 'certifier')
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('conditional struct inference reached IR'))
    with pytest.raises(RustError, match='unresolved inferred generic'):
        compile_rust(text, rustc_check=checked)
