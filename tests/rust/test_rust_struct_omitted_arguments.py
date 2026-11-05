"""Omitted named-field struct arguments infer without changing type syntax.

Rust Reference:
https://doc.rust-lang.org/reference/expressions/struct-expr.html
https://doc.rust-lang.org/reference/paths.html#paths-in-expressions
https://doc.rust-lang.org/reference/expressions.html#evaluation-order-of-operands
No omitted annotation/method-owner arguments or new reference admission.
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


PROGRAMS = ('struct_omitted_order', 'struct_omitted_composites', 'struct_omitted_replay')


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


@pytest.mark.parametrize('text', [
    # Preserve the exact prior capability-boundary source.
    'struct R<T>{x:T}fn f(){R{x:1u64};}',
    'struct S<T>{x:T} fn f(){S{x:1};}',
    'struct R<T>{x:T}fn f()->u64{R{x:4294967296}.x}',
    'struct R<T>{x:T}fn f()->R<u64>{R{x:4294967296}}',
    'struct R<T>{x:T}fn take(x:R<u64>){}fn f(){let x=R{x:4294967296};take(x);}',
    'struct R<T>{x:T}fn f()->u64{let x:R<_>=R{x:4294967296};x.x}',
    'struct Pair<T,U>{a:T,b:U}fn f()->(u64,bool){let p=Pair{b:true,a:4294967296};(p.a,p.b)}',
    'struct R<T>{x:T}fn f()->(u64,bool){R{x:(4294967296,true)}.x}',
    'struct R<T>{x:T}fn f()->[u64;2]{R{x:[4294967296,1]}.x}',
    'struct R<T>{x:T}fn f()->[u64;0]{R{x:[]}.x}',
    'struct R<T>{x:T}fn f()->u64{R{x:R{x:4294967296}}.x.x}',
    'struct R<T>{x:T}fn outer<U>(x:U)->R<U>{R{x:x}}',
    'struct R<T>{x:T}fn f()->R<u64>{let mut x=R{x:1};x=R{x:4294967296u64};x}',
    'mod m{pub struct R<T>{pub x:T}}use m::R as Alias;fn f()->u64{Alias{x:4294967296}.x}',
    'mod m{pub struct R<T>{pub x:T}}fn f()->u64{m::R{x:4294967296}.x}',
    'struct R<T>{x:T}fn f()->u64{match (R{x:4294967296}){r=>r.x}}',
])
def test_omitted_struct_arguments_share_fields_results_and_later_constraints(text):
    compile_rust(text, experimental_rustc_checked=True)


def test_omitted_struct_arguments_resolve_before_registry(monkeypatch):
    original = GenericDeclarations.instantiate

    def resolved_only(self, ty, token):
        assert not pending_integer(ty), 'unfinished omitted owner reached nominal registry'
        return original(self, ty, token)

    monkeypatch.setattr(GenericDeclarations, 'instantiate', resolved_only)
    text = 'struct R<T>{x:T}fn f()->u64{let a=R{x:4294967296};let b=R::<u64>{x:1};a.x+b.x}'
    parser = Parser(text)
    checker = Checker(parser.parse(), parser.records, parser.enums, parser.implementations)
    checker.check()
    entries = tuple(checker.generics.instances.entries.values())
    assert len(entries) == 1 and entries[0].value == TypeApplication('R', ('u64',))


INVALID = (
    ('ambiguous', 'struct R<T>{x:T}fn f(){R{x:[]};}', 'cannot infer', 'E0282'),
    ('repeated-slot', 'struct R<T>{x:T,y:T}fn f(){R{x:1u64,y:true};}', 'type mismatch', 'E0308'),
    ('nominal-result', 'struct R<T>{x:T}struct S<T>{x:T}fn f()->S<u64>{R{x:1}}', 'type mismatch', 'E0308'),
    ('missing-field', 'struct R<T>{x:T,y:bool}fn f(){R{x:1u64};}', 'missing struct field', 'E0063'),
    ('duplicate-field', 'struct R<T>{x:T}fn f(){R{x:1u64,x:2u64};}', 'duplicate field', 'E0062'),
    ('unknown-field', 'struct R<T>{x:T}fn f(){R{y:1u64};}', 'unknown struct field', 'E0560'),
    ('overflow', 'struct R<T>{x:T}fn f()->R<i32>{R{x:4294967296}}', 'out of range', 'literal out of range'),
    ('noncopy-move', 'struct R<T>{x:T}fn take<T>(x:R<T>){}fn f()->u64{let r=R{x:1u64};take(r);r.x}', 'moved', 'E0382'),
    ('abstract-move', 'struct R<T>{x:T}fn f<U>(x:U)->U{let r=R{x:x};x}', 'moved', 'E0382'),
    ('shadowed-owner', 'struct R<T>{x:T}fn f<R>(){R{x:1u64};}', 'unknown struct', 'E0574'),
    ('private-owner', 'mod m{struct R<T>{pub x:T}}fn f(){m::R{x:1u64};}', 'private item', 'E0603'),
    ('private-field', 'mod m{pub struct R<T>{x:T}}fn f(){m::R{x:1u64};}', 'private field', 'E0451'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_invalid_omitted_constructors_fail_before_lowering(name, text, message, code, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid omitted constructor reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('text,message', [
    ('struct R<T>{x:T}fn f(){let x:R=R{x:1u64};}', 'requires explicit type arguments'),
    ('struct R<T>{x:T}impl<T>R<T>{fn take(self)->T{self.x}}fn f()->u64{R::<_>::take(R{x:1u64})}', "unsupported Rust type '_'"),
    ('struct R<T>{x:T}fn f(p:&u64){R{x:p};}', 'actual source/options-bound CheckedRustSource'),
])
def test_other_omitted_and_reference_boundaries_remain_explicit(text, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported owner reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('name', PROGRAMS)
@pytest.mark.parametrize('target,magic', [
    ('x86_64-unknown-linux-gnu', b'\x7fELF'),
    ('arm64-apple-darwin', b'\xcf\xfa\xed\xfe'),
])
def test_omitted_constructor_originals_emit_owned_target_objects(name, target, magic):
    result = compile_rust(source(name), experimental_rustc_checked=True, target_triple=target)
    assert result.emit_object().startswith(magic)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
) else 'requires owned Linux x86-64 execution')
@pytest.mark.parametrize('name', PROGRAMS)
def test_omitted_constructor_originals_execute_full_width_owned(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution
    test_original_match_fixture_full_width_owned_execution(name, tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc')
) else 'requires Linux native rustc/system-linker reference')
def test_omitted_constructor_originals_match_actual_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference, 'PROGRAMS', PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_same_invalid_constructor_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
