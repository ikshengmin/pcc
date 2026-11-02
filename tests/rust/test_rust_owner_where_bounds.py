"""Per-method predicates on the existing impl-owner inference prefix.

https://doc.rust-lang.org/reference/trait-bounds.html#bound-satisfaction
https://doc.rust-lang.org/reference/expressions/method-call-expr.html
These are method predicates, not bounded/specialized impl selection. A failed
predicate must not silently select a different trait method. CI owns execution;
internal abstract models below do not certify borrowing or execute owned IR.
"""
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import actual_check_prerequisite, certify_source

PROGRAMS = ('owner_where_dispatch', 'owner_where_copy', 'owner_where_mutable',
            'owner_where_result', 'owner_where_precedence')
PRIOR_CAPABILITIES = (
    ('owner-marker', 'struct H<T>{x:T}impl<T>H<T>{fn f(&self)where T:Copy{}}'),
    ('owner-local', 'trait A{}struct H<T>{x:T}impl<T>H<T>{fn f(&self)where T:A{}}'),
)


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def test_owner_predicates_stay_structural_and_do_not_expand_the_method_suffix():
    parser = Parser('struct H<T>{x:T}impl<T>H<T>{fn f<U:Copy>(self,u:U)->U where T:Sized,U:Sized{u}}')
    function, = parser.parse()
    assert [p.text for p in function.type_parameters] == ['T', 'U']
    assert function.owner_parameter_count == 1
    assert [p.parameter.text for p in function.where_predicates] == ['T', 'U']
    assert [(p.text, tuple(b.path for b in bounds)) for p, bounds in function.effective_parameter_bounds] == [
        ('U', ('Copy', 'Sized')), ('T', ('Sized',))]


def test_owner_local_dispatch_is_declaration_pinned_without_affecting_siblings():
    text = ('trait Read{fn read(self)->u64;}struct R{x:u64}struct S{x:u64}'
            'impl Read for R{fn read(self)->u64{self.x}}impl Read for S{fn read(self)->u64{self.x}}'
            'impl R{fn read(self)->u64{900}}struct H<T>{x:T}impl<T>H<T>{'
            'fn take(self)->u64 where T:Read{self.x.read()}'
            'fn unrelated(self)->T{self.x}}'
            'fn f()->u64{H::<R>{x:R{x:42}}.take()+H::<S>{x:S{x:43}}.take()}')
    checker = analyze_source(text)
    registry = checker.function_generics
    owner, = registry.parameters['H::take']
    other, = registry.parameters['H::unrelated']
    assert owner != other
    assert checker.traits.parameter_bounds[owner] == frozenset({'Read'})
    assert checker.traits.parameter_bounds[other] == frozenset()
    assert tuple(registry.bound_calls['H::take'].values()) == (('Read', owner, 'read'),)
    instances = [entry.value for entry in registry.instances.entries.values()]
    assert {(fn.declaration_name, fn.instance_arguments) for fn in instances} == {
        ('H::take', ('R',)), ('H::take', ('S',))}
    assert len(instances) == len({fn.identity for fn in instances}) == 2
    for function in instances:
        call = function.body.children[-1]
        assert call.binding.declaration_name == f'<{function.instance_arguments[0]} as Read>::read'


def test_permuted_fixed_owner_arguments_use_the_declaration_parameter_identity():
    text = ('struct R{x:u64}struct Pair<X,Y>{first:X,second:Y}'
            'impl<A,B>Pair<B,A>{fn duplicate(self)->(A,A)where A:Copy{(self.second,self.second)}'
            'fn unrelated(self)->B{self.first}}'
            'fn f()->(u64,u64){Pair::<R,u64>{first:R{x:1},second:42}.duplicate()}')
    checker = analyze_source(text)
    registry = checker.function_generics
    copied, opaque = registry.parameters['Pair::duplicate']
    assert checker.copy_type(copied) and not checker.copy_type(opaque)
    assert all(not checker.copy_type(p) for p in registry.parameters['Pair::unrelated'])
    instance, = registry.instances.entries.values()
    assert instance.value.instance_arguments == ('u64', 'R')


@pytest.mark.parametrize('predicates', ['T:Copy,T:Sized', 'T:Sized,T:Copy', 'T:Copy,T:Copy'])
def test_duplicate_owner_predicates_preserve_marker_closure_and_suffix_relaxation(predicates):
    from pcc.frontends.rust.standard_markers import COPY, CLONE, SIZED
    text = ('struct H<T>{x:T}impl<T>H<T>{fn f<U:?Sized>(self,u:&U)where ' + predicates + '{}}')
    checker = analyze_source(text)
    owner, suffix = checker.function_generics.parameters['H::f']
    assert checker.standard_markers.constraints[owner] == frozenset({COPY, CLONE, SIZED})
    assert checker.standard_markers.constraints[suffix] == frozenset()


def test_unused_value_only_owner_local_bound_needs_actual_token_before_scheduling(monkeypatch):
    from pcc.frontends.rust.semantics import Checker
    monkeypatch.setattr(Checker, 'check', lambda *a: pytest.fail('unchecked owner predicate entered Sema'))
    text = 'trait A{}struct H<T>{x:T}impl<T>H<T>{fn unused(self)where T:A{}}'
    with pytest.raises(RustError, match='local trait bounds require an actual'):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('name', PROGRAMS)
def test_owner_where_originals_cannot_replace_certification_with_a_boolean(name, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unchecked owner predicates reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(source(name), experimental_rustc_checked=True)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text', PRIOR_CAPABILITIES)
def test_exact_former_owner_capability_controls_are_checked_positives(name, text, tmp_path, monkeypatch):
    from tests.rust.test_rust_local_trait_bounds import checked_owned
    checked_owned(text, tmp_path / name, monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', PROGRAMS)
def test_actual_same_input_owner_check_precedes_owned_object_phase(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_local_trait_bounds import checked_owned
    checked_owned(source(name), tmp_path / name, monkeypatch)


INVALID = (
    ('missing-owner-local', 'trait A{}struct N{x:u64}struct H<T>{x:T}impl<T>H<T>{fn need(self)where T:A{}}fn f(){H::<N>{x:N{x:0}}.need();}', 'unsatisfied local trait bound', 'E0277'),
    ('qualified-owner-local', 'trait A{}struct N{x:u64}struct H<T>{x:T}impl<T>H<T>{fn need(self)where T:A{}}fn f(){H::<N>::need(H::<N>{x:N{x:0}});}', 'unsatisfied local trait bound', 'E0277'),
    ('missing-owner-copy', 'struct N{x:u64}struct H<T>{x:T}impl<T>H<T>{fn duplicate(self)->(T,T)where T:Copy{(self.x,self.x)}}fn f(){H::<N>{x:N{x:0}}.duplicate();}', 'canonical Copy', 'E0277'),
    ('unused-owner-forward', 'trait A{}struct H<T>{x:T}impl<T>H<T>{fn need(self)where T:A{}fn bad(self){self.need();}}', 'unsatisfied local trait bound', 'E0277'),
    ('unused-free-forward', 'trait A{}struct H<T>{x:T}impl<T>H<T>{fn need(self)where T:A{}}fn bad<U>(value:H<U>){value.need();}', 'unsatisfied local trait bound', 'E0277'),
    ('copy-not-inherited-by-sibling', 'struct H<T>{x:T}impl<T>H<T>{fn good(self)->(T,T)where T:Copy{(self.x,self.x)}fn bad(self)->(T,T){(self.x,self.x)}}', 'moved', 'E0382'),
    ('reverse-supertrait', 'trait A{}trait B:A{}struct H<T>{x:T}impl<T>H<T>{fn need(self)where T:B{}fn bad(self)where T:A{self.need();}}', 'unsatisfied local trait bound', 'E0277'),
    ('ambiguous-owner-method', 'trait A{fn n(self)->u64;}trait B{fn n(self)->u64;}struct H<T>{x:T}impl<T>H<T>{fn bad(self)->u64 where T:A+B{self.x.n()}}', 'ambiguous', 'E0034'),
    ('suffix-shadows-trait', 'trait A{}struct H<T>{x:T}impl<T>H<T>{fn bad<A>(self)where T:A{}}', 'shadows', 'E0404'),
    ('owner-shadows-trait', 'trait A{}struct H<A>{x:A}impl<A>H<A>{fn bad(self)where A:A{}}', 'shadows', 'E0404'),
    ('local-copy-not-builtin', 'trait Copy{}struct H<T>{x:T}impl<T>H<T>{fn bad(self)->(T,T)where T:Copy{(self.x,self.x)}}', 'moved', 'E0382'),
    ('result-inference-owner', 'trait A{}struct H<T>{x:T}impl<T>H<T>{fn empty<U>(self)->[U;0]where T:A{[]}}fn f()->[u64;0]{H::<bool>{x:true}.empty()}', 'unsatisfied local trait bound', 'E0277'),
    ('method-arity-excludes-owner', 'struct H<T>{x:T}impl<T>H<T>{fn choose<U>(self,u:U)->U where T:Copy{u}}fn f()->u64{H::<u64>{x:0}.choose::<u64,bool>(42)}', 'generic method argument count', 'E0107'),
    ('selected-inherent-does-not-fallback', 'trait A{}trait Pick{fn pick(self)->u64;}struct N{x:u64}struct H<T>{x:T}impl<T>H<T>{fn pick(self)->u64 where T:A{42}}impl Pick for H<N>{fn pick(self)->u64{900}}fn f()->u64{H::<N>{x:N{x:0}}.pick()}', 'unsatisfied local trait bound', 'E0277'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_owner_obligations_and_unused_bodies_fail_in_internal_model(name, text, message, code):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


REFERENCE_INVALID = (
    ('immutable-owner', 'trait A{fn update(&mut self);}struct H<T>{x:T}impl<T>H<T>{fn bad(&self)where T:A{self.x.update();}}', '', 'E0596'),
    ('moved-owner-field', 'trait A{fn read(&self)->u64;}struct H<T>{x:T}impl<T>H<T>{fn bad(self)where T:A{let moved=self.x;self.x.read();}}', '', 'E0382'),
)
CAPABILITY = (
    ('self', 'struct H{x:u64}impl H{fn f(self)where Self:Sized{}}', 'where Self predicates'),
    # Exact bounded impl control is retained in test_rust_inherent_impl_bounds.
    # Exact trait-method where originals now checked in test_rust_trait_where_contracts.
)


@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_unimplemented_owner_domains_remain_explicit(name, text, message):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_actual_valid_unsupported_owner_domains_are_separate(name, text, message, tmp_path):
    checked = certify_source(text, tmp_path / name)
    with pytest.raises(RustError, match=message):
        compile_rust(text, rustc_check=checked)


def test_method_cannot_relax_enclosing_owner_layout_or_implicit_sized():
    # This is an owned syntax boundary, not a claimed rustc rejection receipt.
    with pytest.raises(RustError, match='relaxed bounds on enclosing impl parameters'):
        Parser('struct H<T>{x:T}impl<T>H<T>{fn f(self)where T:?Sized{}}').parse()


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_owner_where_originals_match_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_native_marker_originals_match_rustc(tmp_path, monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_invalid_owner_obligations(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID + REFERENCE_INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
