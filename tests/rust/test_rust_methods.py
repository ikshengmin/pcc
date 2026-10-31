"""Inherent value methods preserve Rust moves, not Go receiver-copy semantics.

Rust Reference (retrieved 2026-10-31), edition 2021 test subset:
https://doc.rust-lang.org/reference/items/implementations.html#inherent-implementations
https://doc.rust-lang.org/reference/items/associated-items.html#methods
https://doc.rust-lang.org/reference/expressions/method-call-expr.html
https://doc.rust-lang.org/reference/expressions.html#evaluation-order-of-operands
https://doc.rust-lang.org/reference/expressions.html#moved-and-copied-types

Reference autoref/autoderef, traits and explicit receiver types remain gated.
"""
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.semantics import Checker
from pcc.frontends.rust.syntax import Parser
from pcc.support.instantiation import InstanceState
from tests.rust.test_rust_frontend import test_development_only_rustc_checks_the_same_source as _rustc
from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution as _native
from tests.rust.test_rust_targets import test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite as _cross

PROGRAMS = ('inherent_records', 'inherent_generic_records', 'inherent_generic_enum',
            'inherent_order', 'inherent_recursion', 'inherent_self_types')


def compile_source(source):
    return compile_rust(source, experimental_rustc_checked=True)


@pytest.mark.parametrize('source,message', [
    ('impl Unknown {}', 'local nominal'),
    ('impl i64 {fn f(self){}}', 'local nominal'),
    ('struct R{x:i64} impl<T> R{}', 'unconstrained'),
    ('struct R<T>{x:T} impl<T,T> R<T>{}', 'duplicate impl'),
    ('struct R<T>{x:T} impl R{}', 'complete type arguments'),
    ('struct R<T>{x:T} impl R<i64>{}', 'coherence'),
    ('struct R<T,U>{x:T,y:U} impl<T,U> R<T,T>{}', 'coherence'),
    ('struct R<T>{x:T} impl<T,U> R<T>{}', 'coherence'),
    ('struct R{x:i64} impl R{fn f(self){} fn f(self){}}', 'duplicate inherent'),
    ('struct R{x:i64} impl R{fn f(self){}} impl R{fn f(self){}}', 'duplicate inherent'),
    ('struct R{x:i64} impl R{fn f(x:i64,self){}}', 'first parameter'),
    ('fn f(self){}', 'inherent method'),
    ('struct R{x:i64} impl R{fn f(self:Self){}}', 'explicit receiver'),
    ('struct R{x:i64} impl R{fn f(&self){}}', 'reference receivers'),
    ('struct R{x:i64} impl R{fn f(&mut self){}}', 'reference receivers'),
    ('struct R{x:i64} impl R{fn f<T>(self,x:T){}}', 'method-local generic'),
    ('struct R{x:i64} impl Trait for R{}', 'trait impls'),
    ('struct R<T>{x:T} impl<T:Copy> R<T>{}', 'bounds'),
    ('struct R{x:i64} impl R{fn f(self){self.x=1;}}', 'immutable'),
    ('struct R{x:i64} impl R{fn f(self)->i64{missing(self)}}', 'unknown function'),
    ('struct R<T>{x:T} impl<T> R<T>{fn f(self)->T{self.x+self.x}}', 'operator'),
    ('struct R<T>{x:T} impl<T> R<T>{fn f(self)->(T,T){(self.x,self.x)}}', 'moved'),
    ('struct R{x:i64} impl R{fn f(self){}} fn g(r:R){r.f();r.x;}', 'moved'),
    ('struct R{x:i64} impl R{fn f(self){}} fn g(r:R,b:bool){if b{r.f();}r.x;}', 'moved'),
    ('struct R{x:i64} impl R{fn f(self){}} fn g(r:R){while true{r.f();}}', 'moved'),
    ('struct R{x:i64} impl R{fn f(self){}} fn g(r:R){f(r);}', 'unknown function'),
    ('struct R{x:i64} impl R{fn f()->i64{42}} fn g(r:R){r.f();}', 'no self receiver'),
    ('struct R{x:i64} impl R{fn f(self){}} fn g(){R::f();}', 'expects 1 arguments'),
    ('struct R{x:i64} impl R{fn f(self){}} fn g(r:R){r.nope();}', 'unknown inherent'),
    ('struct R{x:i64} impl R{unsafe fn f(self){}} fn g(r:R){r.f();}', 'unsafe context'),
    ('#[repr(C)] struct R{x:i64} impl R{fn f(self){}} fn g(r:&R){r.f();}', 'autoderef'),
])
def test_invalid_or_unimplemented_impls_and_methods_fail_before_ir(source, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid inherent method reached IR'))
    with pytest.raises(RustError, match=message):
        compile_source(source)


@pytest.mark.parametrize('source', [
    'struct R{x:i64} impl R{}',
    'struct R{x:i64} impl R{fn value(self)->i64{self.x}} fn f(r:R)->i64{R::value(r)}',
    'struct R{x:i64} impl R{fn value(self)->i64{self.x}} fn f()->i64{(R{x:42}).value()}',
    'struct R{x:i64} impl R{fn make(x:i64)->Self{Self{x:x}} fn value(self)->i64{self.x}} fn f()->i64{R::make(42).value()}',
    'struct R{x:i64} impl R{fn value(mut self)->i64{self.x+=1;self.x}}',
    'struct R{x:i64} impl R{unsafe fn value(self)->i64{self.x}} fn f(r:R)->i64{unsafe{r.value()}}',
    'struct R<T>{x:T} impl<T> R<T>{fn into(self)->T{self.x}}',
    'struct R<T,U>{x:T,y:U} impl<T,U> R<U,T>{fn second(self)->T{self.y}} fn f()->bool{R::<u64,bool>{x:42,y:true}.second()}',
    'enum E<T>{None,Some(T)} impl<T> E<T>{fn value(self,fallback:T)->T{match self{Self::None=>fallback,Self::Some(x)=>x}}}',
    'struct R{x:i64} impl R{fn value(self)->i64{self.x}} fn f(r:R){let x=r.value();let y=R{x:x};y.value();}',
])
def test_value_self_scope_qualified_calls_and_nominal_impl_parameters(source):
    compile_source(source)


def test_methods_use_actual_shared_function_worklist_and_distinct_declaration_identity():
    source = ('struct R<T>{x:T} impl<T> R<T>{fn into(self)->T{self.x}}'
              'struct S<T>{x:T} impl<T> S<T>{fn into(self)->T{self.x}}'
              'fn f()->u64{R::<u64>{x:7}.into()+S::<u64>{x:9}.into()}')
    parser = Parser(source)
    functions = parser.parse()
    checker = Checker(functions, parser.records, parser.enums, parser.implementations)
    checker.check()
    entries = tuple(checker.function_generics.instances.entries.values())
    assert len(entries) == 2 and all(e.state is InstanceState.COMPLETE for e in entries)
    assert {e.value.declaration_name for e in entries} == {'R::into', 'S::into'}
    assert len({e.value.identity for e in entries}) == 2
    first, second = compile_source(source), compile_source(source)
    assert first.ir_text == second.ir_text and first.emit_object() == second.emit_object()


@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux') and platform.machine() in {'x86_64','AMD64'} else 'requires Linux x86-64 owned execution')
@pytest.mark.parametrize('name', PROGRAMS)
def test_original_inherent_program_has_full_width_owned_native_result(name, tmp_path, monkeypatch):
    _native(name, tmp_path, monkeypatch)


@pytest.mark.parametrize('name', PROGRAMS)
def test_original_inherent_program_cross_emits_arm64_macho(name, monkeypatch):
    _cross(name, monkeypatch)


@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
@pytest.mark.parametrize('name', PROGRAMS)
def test_development_only_rustc_checks_original_inherent_source(name, tmp_path):
    _rustc(name, tmp_path)


def test_local_allocation_visits_actual_operands_not_untyped_method_callee(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    original = Lowerer.allocate_locals
    visited = []
    def observe(self, expression):
        assert expression.ty is not None, 'nonvalue callee syntax reached allocation'
        visited.append(expression)
        return original(self, expression)
    monkeypatch.setattr(Lowerer, 'allocate_locals', observe)
    compile_source('struct R{x:i64} impl R{fn make(x:i64)->Self{Self{x:x}}'
                   'fn value(self)->i64{self.x}} fn f()->i64{R::make(42).value()}')
    assert visited and len(visited) == len({id(expression) for expression in visited})
