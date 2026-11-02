"""Argument-constrained generic inference, not trait selection or lifetime solving.

Rust Reference (retrieved 2026-10-31):
https://doc.rust-lang.org/reference/items/generics.html
https://doc.rust-lang.org/reference/expressions/call-expr.html
https://doc.rust-lang.org/reference/expressions/literal-expr.html#integer-literal-expressions
https://doc.rust-lang.org/reference/expressions.html#evaluation-order-of-operands
Ledger feature: rust.generics.argument-constrained-call-inference.
"""
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.semantics import Checker
from pcc.frontends.rust.syntax import Parser
from pcc.support.instantiation import InstanceRegistry
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import actual_check_prerequisite, darwin_check_prerequisite, source_has_references
from tests.rust.test_rust_frontend import test_development_only_rustc_checks_the_same_source as _rustc
from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution as _native
from tests.rust.test_rust_targets import test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite as _cross

PROGRAMS=('inferred_primitives','inferred_later_constraints','inferred_nominal','inferred_recursion','inferred_order','inferred_shared','inferred_tuples')
RETAINED='fn bad<T>(x:T)->T{x} fn answer()->i64{bad(1)}'


def compile_source(source):
    return compile_rust(source,experimental_rustc_checked=True)


@pytest.mark.parametrize('source',[
    RETAINED,
    'fn id<T>(x:T)->T{x} fn f()->bool{id(true)}',
    'fn id<T>(x:T)->T{x} fn f()->i64{let x=id(4294967296);x}',
    'fn id<T>(x:T)->T{x} fn wide(x:u64){} fn f(){let x=id(4294967296);wide(x);}',
    'fn id<T>(x:T)->T{x} fn outer<U>(x:U)->U{id(x)}',
    'fn id<T>(x:T)->T{x} fn f()->i64{id(id(4294967296))}',
    'fn pair<T,U>(a:T,b:U)->(T,U){(a,b)} fn f()->(i64,u64){pair(-1,4294967296)}',
    'struct R{x:i64} fn id<T>(x:T)->T{x} fn f()->i64{id(R{x:42}).x}',
    'struct R<T>{x:T} fn make<T>(x:T)->R<T>{R::<T>{x:x}} fn f()->u64{make(4294967296).x}',
    'struct R<T>{x:T} fn id<T>(x:T)->T{x} fn f()->R<u64>{id(R::<u64>{x:42})}',
    'fn choose<T>(a:T,b:T)->T{b} fn f()->u64{choose(1,2u64)}',
])
def test_argument_and_later_body_constraints_precede_integer_fallback(source):
    compile_source(source)


def test_inferred_and_explicit_calls_share_one_semantic_instance_registry():
    source='fn id<T>(x:T)->T{x} fn f()->u64{let x=id(4294967296);let y=id::<u64>(x);id(y)}'
    parser=Parser(source);functions=parser.parse();checker=Checker(functions,parser.records,parser.enums);checker.check()
    registry=checker.function_generics.instances
    assert isinstance(registry,InstanceRegistry) and len(registry)==1
    entry=next(iter(registry.entries.values()))
    assert entry.value.instance_arguments==('u64',)
    assert entry.value is entry.result
    first,second=compile_source(source),compile_source(source)
    assert first.ir_text==second.ir_text and first.emit_object()==second.emit_object()
    assert 'id::<u64>' in first.symbols and 'id::<i32>' not in first.symbols


@pytest.mark.parametrize('source,message',[
    ('fn same<T>(a:T,b:T)->T{a} fn f(){same(1i32,2u64);}','type mismatch'),
    ('fn same<T>(a:T,b:T)->T{a} fn f(){same(true,1);}','type mismatch'),
    ('struct A{x:i32} struct B{x:i32} fn same<T>(a:T,b:T){} fn f(){same(A{x:1},B{x:1});}','type mismatch'),
    ('fn id<T>(x:T)->T{x} fn f(){id();}','expects 1 arguments'),
    ('fn id<T>(x:T)->T{x} fn f(){id(1).x;}','field access requires'),
    ('fn id<T>(x:T)->T{x} fn f(){let id=1;id(2);}','unshadowed'),
    ('fn bad<T>(x:T)->(T,T){(x,x)}','moved'),
    ('fn id<T>(x:T)->T{x} fn bad<T>(x:T)->T{let y=id(x);x}','moved'),
    ('fn id<T>(x:T)->T{x} fn bad<T>(x:T){while true{id(x);}}','moved'),
    ('fn id<T>(x:T)->T{x} fn f()->i32{id(4294967296)}','out of range'),
    ('fn unused<T>(){} fn f(){unused();}','result-context inference'),
    ('fn id<T>(x:T)->T{x} fn f(p:&u64){id(p);}','reference arguments'),
    ('struct R<T>{x:T} impl<T> R<T>{fn get(self)->T{self.x}} fn make<T>(x:T)->R<T>{R::<T>{x:x}} fn f()->u64{make(1).get()}','unresolved inferred receiver'),
])
def test_invalid_or_unimplemented_inference_fails_before_ir(source,message,monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer,'lower',lambda *a:pytest.fail('invalid inference reached IR'))
    with pytest.raises(RustError,match=message):
        if source_has_references(source): analyze_source(source)
        else: compile_source(source)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux') and platform.machine()in{'x86_64','AMD64'}else'requires owned Linux execution')
@pytest.mark.parametrize('name',[pytest.param(n,marks=pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())) if n=='inferred_shared' else n for n in PROGRAMS])
def test_original_inferred_call_program_full_width_owned_execution(name,tmp_path,monkeypatch):
    _native(name,tmp_path,monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.parametrize('name',[pytest.param(n,marks=pytest.mark.pcc_gate(unavailable=darwin_check_prerequisite())) if n=='inferred_shared' else n for n in PROGRAMS])
def test_original_inferred_call_program_arm64_macho_cross_only(name,monkeypatch,tmp_path):
    _cross(name,monkeypatch,tmp_path)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc')else'development rustc unavailable')
@pytest.mark.parametrize('name',PROGRAMS)
def test_original_inferred_call_program_rustc_typechecks(name,tmp_path):
    _rustc(name,tmp_path)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux')and platform.machine()in{'x86_64','AMD64'}and shutil.which('rustc')and shutil.which('cc')else'requires Linux native rustc reference')
def test_original_inferred_call_programs_match_native_rustc(tmp_path,monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference,'PROGRAMS',PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path,monkeypatch)
