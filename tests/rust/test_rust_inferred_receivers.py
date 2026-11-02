"""Known nominal receiver arguments share deferred call constraints.

Rust Reference, retrieved 2026-11-02:
https://doc.rust-lang.org/reference/expressions/method-call-expr.html
https://doc.rust-lang.org/reference/items/generics.html
https://doc.rust-lang.org/reference/expressions.html#evaluation-order-of-operands
Unconditional inherent selection only; no general delayed trait lookup or new
borrow proof. Native and same-original rustc obligations are authored for CI.
"""
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.semantics import Checker
from pcc.frontends.rust.syntax import Parser
from pcc.support.instantiation import InstanceState
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import (
    actual_check_prerequisite,
    darwin_check_prerequisite,
)

PROGRAMS = (
    'inferred_receiver_result', 'inferred_receiver_suffix',
    'inferred_receiver_empty', 'inferred_receiver_order',
    'inferred_receiver_references',
)
VALUE_PROGRAMS = PROGRAMS[:-1]
BASE = ('struct R<T>{x:T} impl<T> R<T>{fn get(self)->T{self.x}} '
        'fn make<T>(x:T)->R<T>{R::<T>{x:x}} ')
# The exact former capability negative from test_rust_call_inference.py.
RETAINED = 'struct R<T>{x:T} impl<T> R<T>{fn get(self)->T{self.x}} fn make<T>(x:T)->R<T>{R::<T>{x:x}} fn f()->u64{make(1).get()}'


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


@pytest.mark.parametrize('text', [
    RETAINED,
    BASE + 'fn f(){let x=make(4294967296).get();let y:u64=x;}',
    BASE + 'fn f()->u64{let x=make(1).get();x}',
    BASE + 'fn f()->u64{make((make(4294967296).get(),true)).get().0}',
    'struct R<T>{x:T}impl<T>R<T>{fn take<U>(self,u:U)->(T,U){(self.x,u)}}'
    'fn make<T>(x:T)->R<T>{R::<T>{x:x}}fn f()->(u64,bool){make(1).take(true)}',
    'struct R<T>{x:T}impl<T>R<T>{fn take<U>(self,u:U)->(T,U){(self.x,u)}}'
    'fn make<T>(x:T)->R<T>{R::<T>{x:x}}fn f()->(u64,bool){make(1).take::<bool>(true)}',
    'struct R<T>{x:T}impl<T>R<T>{fn take<U>(self,u:U)->(T,U){(self.x,u)}}'
    'fn make<T>(x:T)->R<T>{R::<T>{x:x}}fn outer<V>(v:V)->(u64,V){make(1).take(v)}'
    'fn f()->(u64,bool){outer(true)}',
])
def test_known_nominal_owner_keeps_result_and_later_constraints(text):
    assert 'define' in compile_rust(text, experimental_rustc_checked=True).ir_text


def test_unresolved_owner_arguments_never_become_registry_keys_or_early_i32():
    text = BASE + ('fn f()->u64{let x=make(4294967296).get();'
                   'let y=R::<u64>::get(R::<u64>{x:1});x+y}')
    parser = Parser(text)
    checker = Checker(parser.parse(), parser.records, parser.enums, parser.implementations)
    checker.check()
    entries = tuple(checker.function_generics.instances.entries.values())
    assert {(entry.value.declaration_name, entry.value.instance_arguments)
            for entry in entries} == {('make', ('u64',)), ('R::get', ('u64',))}
    assert len(entries) == 2
    assert all(entry.state is InstanceState.COMPLETE and entry.value is entry.result
               for entry in entries)


@pytest.mark.parametrize('text,message', [
    (BASE + 'fn f()->bool{make(1).get()}', 'type mismatch'),
    (BASE + 'fn f()->i32{make(4294967296).get()}', 'out of range'),
    (BASE + 'fn f(){let r=make(1);let a=r.get();let b=r.get();}', 'moved'),
    ('struct R<T>{x:T}impl<T>R<T>{fn get(self)->T{self.x}}'
     'fn empty<T>()->R<[T;0]>{R::<[T;0]>{x:[]}}fn f(){empty().get();}',
     'cannot infer generic parameter'),
    ('struct R<T>{x:T}impl<T>R<T>{fn take<U>(self,u:U)->U{u}}'
     'fn make<T>(x:T)->R<T>{R::<T>{x:x}}fn f(){make(1).take::<bool,u64>(true);}',
     'method argument count'),
    ('struct R<T>{x:T}impl<T>R<T>{fn take<U>(self,u:U)->U{u}}'
     'fn make<T>(x:T)->R<T>{R::<T>{x:x}}fn f(){make(1).take::<bool>(1u64);}',
     'type mismatch'),
])
def test_invalid_inferred_receivers_fail_before_ir(text, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid receiver reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


CAPABILITIES = (
    'fn unknown<T>()->T{unknown()} fn f()->u64{unknown().get()}',
    'struct R<T>{x:T}impl<T:Copy>R<T>{fn get(self)->T{self.x}}'
    'fn make<T>(x:T)->R<T>{R::<T>{x:x}}fn f()->u64{make(1).get()}',
    'trait A{}struct R<T>{x:T}impl<T:A>R<T>{fn get(self)->T{self.x}}'
    'fn empty<T>()->R<[T;0]>{R::<[T;0]>{x:[]}}fn f(){empty().get();}',
    'trait Other{fn get(self)->u64;}struct R<T>{x:T}'
    'impl<T>R<T>{fn get(self)->T{self.x}}impl Other for R<u64>{fn get(self)->u64{99}}'
    'fn make<T>(x:T)->R<T>{R::<T>{x:x}}fn f()->u64{make(1).get()}',
)


@pytest.mark.parametrize('text', CAPABILITIES)
def test_unknown_bound_dependent_or_competing_lookup_stays_explicit(text):
    # Internal model only: these are capability gaps, not Rust-invalid claims
    # and not a source-certification substitute for public compilation.
    with pytest.raises(RustError, match='unresolved inferred receiver'):
        analyze_source(text)


def test_receiver_inference_does_not_mint_reference_certification(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unchecked receiver reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(source(PROGRAMS[-1]), experimental_rustc_checked=True)


@pytest.mark.parametrize('name', VALUE_PROGRAMS)
@pytest.mark.parametrize('target,magic', [
    ('x86_64-unknown-linux-gnu', b'\x7fELF'),
    ('arm64-apple-darwin', b'\xcf\xfa\xed\xfe'),
])
def test_inferred_receiver_originals_emit_owned_target_objects(name, target, magic):
    result = compile_rust(source(name), experimental_rustc_checked=True, target_triple=target)
    assert result.emit_object().startswith(magic)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
) else 'requires owned Linux x86-64 execution')
@pytest.mark.parametrize('name', [
    *VALUE_PROGRAMS,
    pytest.param(PROGRAMS[-1], marks=pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())),
])
def test_unchanged_inferred_receiver_original_full_width_owned_execution(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution
    test_original_match_fixture_full_width_owned_execution(name, tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'
))
def test_unchanged_inferred_receiver_originals_match_native_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference, 'PROGRAMS', PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path, monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=darwin_check_prerequisite())
def test_reference_receiver_cross_object_requires_matching_target_check(tmp_path, monkeypatch):
    from tests.rust.test_rust_targets import test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite
    test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite(
        PROGRAMS[-1], monkeypatch, tmp_path=tmp_path)
