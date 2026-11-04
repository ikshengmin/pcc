"""Explicit call-site `_` slots share the existing deferred generic inference.

Rust Reference, retrieved 2026-11-02:
https://doc.rust-lang.org/reference/types/inferred.html
https://doc.rust-lang.org/reference/paths.html#paths-in-expressions
https://doc.rust-lang.org/reference/expressions/method-call-expr.html
https://doc.rust-lang.org/reference/expressions.html#evaluation-order-of-operands
No new trait solver, borrow proof, generic-reference layout or Drop semantics.
Ordinary/native/reference obligations are authored for CI, not local evidence.
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
from tests.rust.certification_support import (
    actual_check_prerequisite,
    certify_source,
    darwin_check_prerequisite,
)

UNUSED_GENERIC_ORIGINAL = 'fn id<T>(x:T)->T{x}fn outer<U>(x:U)->U{id::<_>(x)}'

VALUE_PROGRAMS = (
    'call_placeholder_values', 'call_placeholder_composites',
    'call_placeholder_methods', 'call_placeholder_replay',
    'call_placeholder_unused_root',
)
REFERENCE_PROGRAMS = ('call_placeholder_references', 'call_placeholder_order')
PROGRAMS = VALUE_PROGRAMS + REFERENCE_PROGRAMS


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


@pytest.mark.parametrize('text', [
    'fn id<T>(x:T)->T{x}fn f()->u64{id::<_>(4294967296)}',
    'fn id<T>(x:T)->T{x}fn f()->u64{let x=id::<_>(4294967296);x}',
    'fn pair<T,U>(x:T,y:U)->(T,U){(x,y)}fn f()->(u64,bool){pair::<_,bool>(1,true)}',
    'fn pair<T,U>(x:T,y:U)->(T,U){(x,y)}fn f()->(u64,bool){pair::<u64,_>(1,true)}',
    'fn id<T>(x:T)->T{x}fn f()->(u64,bool){id::<(_,_)>((1,true))}',
    'fn id<T>(x:T)->T{x}fn f()->[u64;2]{id::<[_;2]>([1,2])}',
    'fn empty<T>()->[T;0]{[]}fn f()->[u64;0]{empty::<_>()}',
    UNUSED_GENERIC_ORIGINAL,
    UNUSED_GENERIC_ORIGINAL + 'fn f()->i32{outer::<i32>(42)}',
    'fn id<T>(x:T)->T{x}fn f()->u64{id::<_>(id::<_>(4294967296))}',
    'struct R{x:u64}impl R{fn take<U>(self,u:U)->U{u}}fn f()->u64{R{x:0}.take::<_>(1)}',
    'struct R<T>{x:T}impl<T>R<T>{fn take<U>(self,u:U)->U{u}}'
    'fn f()->bool{R::<u64>::take::<_>(R::<u64>{x:1},true)}',
])
def test_explicit_holes_accept_argument_result_and_later_constraints(text):
    result = compile_rust(text, experimental_rustc_checked=True)
    if text == UNUSED_GENERIC_ORIGINAL:
        # Generic bodies are checked abstractly, not emitted without a concrete
        # call. Keep this exact original and its parameter ID as a positive;
        # demanding a definition here would invent a monomorphization root.
        parser = Parser(text)
        functions = parser.parse()
        checker = Checker(functions, parser.records, parser.enums, parser.implementations)
        checker.check()
        assert len(functions) == 2 and all(fn.type_parameters for fn in functions)
        assert not checker.function_generics.instances.entries
        assert result.symbols == {} and 'define' not in result.ir_text
    else:
        assert 'define' in result.ir_text


def test_original_local_annotation_capability_input_now_compiles():
    # Retain the exact former capability original. Broader constraint, native
    # execution and oracle cases live in test_rust_local_placeholders.py.
    compile_rust('fn f()->u64{let x:_=1u64;x}', experimental_rustc_checked=True)


def test_rooted_unused_generic_fixture_preserves_the_exact_original_prefix():
    assert source('call_placeholder_unused_root').startswith(UNUSED_GENERIC_ORIGINAL + '\n')


def test_holes_concrete_and_omitted_arguments_share_one_existing_registry():
    text = ('fn id<T>(x:T)->T{x}fn f(){'
            'let a:u64=id::<_>(4294967296);let b=id::<u64>(a);'
            'let c=id(b);let d=id::<_>(true);let e=id::<_>(3u32);}')
    parser = Parser(text)
    checker = Checker(parser.parse(), parser.records, parser.enums, parser.implementations)
    checker.check()
    entries = tuple(checker.function_generics.instances.entries.values())
    assert len(entries) == 3
    assert {entry.value.instance_arguments for entry in entries} == {('u64',), ('bool',), ('u32',)}
    assert all(entry.state is InstanceState.COMPLETE and entry.value is entry.result
               for entry in entries)


def test_method_holes_do_not_consume_or_renormalize_fixed_owner_slots():
    text = ('struct Pair<A,B>{a:A,b:B}impl<X,Y>Pair<Y,X>{fn take<U>(self,u:U)->U{u}}'
            'fn f(){let a=Pair::<u64,bool>{a:1,b:true}.take::<_>(3u32);'
            'let b=Pair::<u64,bool>::take::<u32>(Pair::<u64,bool>{a:2,b:false},a);}')
    parser = Parser(text)
    checker = Checker(parser.parse(), parser.records, parser.enums, parser.implementations)
    checker.check()
    entries = tuple(checker.function_generics.instances.entries.values())
    assert len(entries) == 1
    assert entries[0].value.declaration_name == 'Pair::take'
    assert entries[0].value.instance_arguments == ('bool', 'u64', 'u32')
    assert entries[0].state is InstanceState.COMPLETE


# Complete invalid originals are shared unchanged with the independent oracle.
# These are Rust-invalid claims, not the separate valid capability controls.
INVALID = (
    ('unconstrained', 'fn unused<T>(){}fn f(){unused::<_>();}', 'cannot infer', 'E0282'),
    ('nested-unconstrained', 'fn id<T>(x:T)->T{x}fn f(){id::<[_;0]>([]);}', 'cannot infer', 'E0282'),
    ('inconsistent', 'fn same<T>(a:T,b:T){}fn f(){same::<_>(1u32,2u64);}', 'type mismatch', 'E0308'),
    ('fixed-conflict', 'fn pair<T,U>(a:T,b:U){}fn f(){pair::<_,bool>(1u64,2u64);}', 'type mismatch', 'E0308'),
    ('function-arity', 'fn pair<T,U>(a:T,b:U){}fn f(){pair::<_>(1u64,true);}', 'generic function argument count', 'E0107'),
    ('nongeneric', 'fn take(x:u64){}fn f(){take::<_>(1);}', 'nongeneric function', 'E0107'),
    ('operand-arity', 'fn id<T>(x:T)->T{x}fn f(){id::<_>();}', 'expects 1 arguments', 'E0061'),
    ('result-conflict', 'fn id<T>(x:T)->T{x}fn f()->bool{id::<_>(1)}', 'type mismatch', 'E0308'),
    ('range', 'fn id<T>(x:T)->T{x}fn f()->i32{id::<_>(4294967296)}', 'out of range', 'literal out of range'),
    ('noncopy-move', 'struct R{x:u64}fn id<T>(x:T)->T{x}fn f()->u64{let r=R{x:1};id::<_>(r);r.x}', 'moved', 'E0382'),
    ('unused-generic-body', 'fn id<T>(x:T)->T{x}fn bad<U>(x:U)->U{let y=id::<_>(x);x}', 'moved', 'E0382'),
    ('method-arity', 'struct R<T>{x:T}impl<T>R<T>{fn take<U>(self,u:U)->U{u}}fn f(){R::<u64>{x:1}.take::<_,_>(true);}', 'method argument count', 'E0107'),
    ('item-parameter', 'fn invalid(x:_){}', "unsupported Rust type '_'", 'E0121'),
    ('item-field', 'struct Invalid{x:_}', "unsupported Rust type '_'", 'E0121'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_invalid_placeholder_constraints_fail_before_ir(name, text, message, code, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid placeholder call reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


REFERENCE_INVALID = (
    ('mutable-argument', 'fn set<T>(p:&mut T,x:T){*p=x;}fn f(){let x=1u64;set::<_>(&x,2u64);}', 'type mismatch', 'E0308'),
    ('immutable-receiver', 'struct R{x:u64}impl R{fn put<T>(&mut self,x:T){}}fn f(){let r=R{x:1};r.put::<_>(true);}', 'immutable receiver', 'E0596'),
    ('aliased-mutable', 'fn both<T>(a:&mut T,b:&mut T){}fn f(){let mut x=1u64;both::<_>(&mut x,&mut x);}', 'external borrow rejection', 'E0499'),
)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_invalid_placeholder_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID + REFERENCE_INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)


CAPABILITIES = (
    ('constructor', 'struct R<T>{x:T}fn f()->u64{R::<_>{x:1u64}.x}', 'only in function/method call type arguments'),
    ('owner', 'struct R<T>{x:T}impl<T>R<T>{fn take(self)->T{self.x}}fn f()->u64{R::<_>::take(R::<u64>{x:1})}', 'only in function/method call type arguments'),
    ('reference-argument', 'fn id<T>(x:T)->T{x}fn f(p:&u64){id::<_>(p);}', 'generic function reference arguments'),
    ('reference-pattern', 'fn id<T>(x:T)->T{x}fn f(p:&u64){id::<&_>(p);}', 'generic function reference arguments'),
    ('standard-drop', 'struct R{x:u64}impl Drop for R{fn drop(&mut self){}}fn id<T>(x:T)->T{x}fn f(){id::<_>(R{x:1});}', 'external traits/lang items are not implemented'),
)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,message', CAPABILITIES)
def test_valid_out_of_scope_placeholders_remain_capabilities_not_rust_rejections(name, text, message, tmp_path, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    checked = certify_source(text, tmp_path / 'check')
    assert (tmp_path / 'check/original.rs').read_text() == text
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unsupported placeholder reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, rustc_check=checked)


@pytest.mark.parametrize('name', REFERENCE_PROGRAMS)
def test_placeholder_inference_never_mints_reference_certification(name, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('uncertified placeholder reference reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(source(name), experimental_rustc_checked=True)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', REFERENCE_PROGRAMS)
def test_genuine_check_precedes_owned_phase_without_partial_borrow_admission(name, tmp_path, monkeypatch):
    import subprocess
    from pcc.frontends.rust.moves import MoveChecker
    text = source(name)
    checked = certify_source(text, tmp_path / 'check')

    def forbidden(*args, **kwargs):
        pytest.fail('external tool or partial borrow checker entered owned phase')

    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(MoveChecker, 'check', forbidden)
        patch.setattr(MoveChecker, 'check_function', forbidden)
        result = compile_rust(text, rustc_check=checked)
        assert result.emit_object().startswith(b'\x7fELF')
    assert result.externally_checked and not result.borrow_checked_by_pcc
    assert (tmp_path / 'check/original.rs').read_text() == text


@pytest.mark.parametrize('name', VALUE_PROGRAMS)
@pytest.mark.parametrize('target,magic', [
    ('x86_64-unknown-linux-gnu', b'\x7fELF'),
    ('arm64-apple-darwin', b'\xcf\xfa\xed\xfe'),
])
def test_placeholder_originals_emit_real_owned_target_objects(name, target, magic):
    result = compile_rust(source(name), experimental_rustc_checked=True, target_triple=target)
    assert result.emit_object().startswith(magic)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if (
    sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
) else 'requires owned Linux x86-64 execution')
@pytest.mark.parametrize('name', [
    *VALUE_PROGRAMS,
    *(pytest.param(name, marks=pytest.mark.pcc_gate(unavailable=actual_check_prerequisite()))
      for name in REFERENCE_PROGRAMS),
])
def test_unchanged_placeholder_original_full_width_owned_execution(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution
    test_original_match_fixture_full_width_owned_execution(name, tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'
))
def test_unchanged_placeholder_originals_match_native_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference, 'PROGRAMS', PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path, monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=darwin_check_prerequisite())
@pytest.mark.parametrize('name', REFERENCE_PROGRAMS)
def test_reference_placeholder_cross_object_requires_matching_target_check(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_targets import test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite
    test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite(name, monkeypatch, tmp_path=tmp_path)
