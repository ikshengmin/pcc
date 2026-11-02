"""Inherent method suffix obligations reuse ordinary local-trait dispatch.

https://doc.rust-lang.org/reference/items/associated-items.html#methods
https://doc.rust-lang.org/reference/items/generics.html#where-clauses
The receiver fixes the impl prefix; only method-local parameters receive these
new predicates. Conditional owner/Self availability and trait-method contracts
remain explicit gaps. CI owns execution of all authored same-input oracles.
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

PROGRAMS = ('inherent_local_dispatch', 'inherent_local_result', 'inherent_local_mutable',
            'inherent_local_shadow', 'inherent_where_markers')
PRIOR_CAPABILITIES = (
    ('local', 'trait A{}struct R{x:u64}impl R{fn f<T:A>(self,x:T)->T{x}}'),
    ('local-copy', 'trait Copy{}struct R{x:u64}impl R{fn take<U:Copy>(self,u:U)->U{u}}'),
    ('marker-where', 'struct R{x:u64}impl R{fn take<U>(self,u:U)->U where U:Copy{u}}'),
    ('method-where', 'struct R{x:u64} impl R{fn bound<U>(self,x:U) where U:Copy{}}'),
    ('associated-where', 'struct R{x:u64}impl R{fn f<T>(x:T)->T where T:Copy{x}}'),
)


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def test_method_suffix_constraints_do_not_constrain_the_fixed_impl_prefix():
    text = ('trait Read{fn read(self)->u64;}struct R{x:u64}'
            'impl Read for R{fn read(self)->u64{self.x}}impl R{fn read(self)->u64{900}}'
            'struct Holder<T>{x:T}impl<T>Holder<T>{'
            'fn get<U:Read>(self,u:U)->u64{u.read()}'
            'fn forward<V>(self,v:V)->u64 where V:Read{self.get(v)}}'
            'fn f()->u64{Holder::<u64>{x:0}.forward(R{x:42})'
            '+Holder::<bool>{x:true}.get(R{x:42})+Holder::<u64>{x:0}.get(R{x:42})}')
    checker = analyze_source(text)
    generic = checker.function_generics
    owner, argument = generic.parameters['Holder::get']
    assert checker.traits.parameter_bounds[owner] == frozenset()
    assert checker.traits.parameter_bounds[argument] == frozenset({'Read'})
    assert not checker.copy_type(owner) and not checker.copy_type(argument)
    instances = [entry.value for entry in generic.instances.entries.values()]
    assert {(fn.declaration_name, fn.instance_arguments) for fn in instances} == {
        ('Holder::get', ('u64', 'R')), ('Holder::get', ('bool', 'R')),
        ('Holder::forward', ('u64', 'R'))}
    assert len(instances) == len({fn.identity for fn in instances}) == 3
    selected = next(iter(generic.bound_calls['Holder::get'].values()))
    assert selected == ('Read', argument, 'read')
    for function in instances:
        if function.declaration_name == 'Holder::get':
            assert function.body.children[-1].binding.declaration_name == '<R as Read>::read'


def test_method_where_ast_keeps_owner_prefix_distinct_from_predicate_subject():
    parser = Parser('struct R<T>{x:T}impl<T>R<T>{fn f<U:Copy>(self,u:U)->U where U:Sized{u}}')
    function, = parser.parse()
    assert [p.text for p in function.type_parameters] == ['T', 'U']
    assert function.owner_parameter_count == 1
    assert [p.parameter.text for p in function.where_predicates] == ['U']
    assert [(p.text, tuple(b.path for b in bounds)) for p, bounds in function.effective_parameter_bounds] == [
        ('U', ('Copy', 'Sized'))]


@pytest.mark.parametrize('text', [PRIOR_CAPABILITIES[0][1], PRIOR_CAPABILITIES[1][1]])
def test_unused_value_only_inherent_local_bounds_need_token_before_scheduling(text, monkeypatch):
    from pcc.frontends.rust.semantics import Checker
    monkeypatch.setattr(Checker, 'check', lambda *a: pytest.fail('unchecked method local bound entered Sema'))
    with pytest.raises(RustError, match='local trait bounds require an actual'):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('name', PROGRAMS)
def test_inherent_local_originals_cannot_use_only_the_boolean(name, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('uncertified method source reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(source(name), experimental_rustc_checked=True)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text', PRIOR_CAPABILITIES)
def test_exact_former_method_controls_are_actual_checked_positives(name, text, tmp_path, monkeypatch):
    from tests.rust.test_rust_local_trait_bounds import checked_owned
    checked_owned(text, tmp_path / name, monkeypatch)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', PROGRAMS)
def test_actual_check_precedes_owned_inherent_local_lowering(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_local_trait_bounds import checked_owned
    checked_owned(source(name), tmp_path / name, monkeypatch)


INVALID = (
    ('missing-impl', 'trait A{}struct H{x:u64}struct N{x:u64}impl H{fn f<U:A>(self,u:U)->U{u}}fn f()->N{H{x:0}.f(N{x:1})}', 'unsatisfied local trait bound', 'E0277'),
    ('unused-forward', 'trait A{}struct H<T>{x:T}impl<T>H<T>{fn need<U:A>(self,u:U)->U{u}fn bad<V>(self,v:V)->V{self.need(v)}}', 'unsatisfied local trait bound', 'E0277'),
    ('wrong-method', 'trait A{}trait B{fn n(self)->u64;}struct H{x:u64}impl H{fn bad<U:A>(self,u:U)->u64{u.n()}}', 'unknown inherent or concrete trait method', 'E0599'),
    ('ambiguous', 'trait A{fn n(self)->u64;}trait B{fn n(self)->u64;}struct H{x:u64}impl H{fn bad<U>(self,u:U)->u64 where U:A+B{u.n()}}', 'ambiguous', 'E0034'),
    ('reverse-supertrait', 'trait A{}trait B:A{}struct H{x:u64}impl H{fn need<U:B>(self,u:U)->U{u}fn bad<V:A>(self,v:V)->V{self.need(v)}}', 'unsatisfied local trait bound', 'E0277'),
    ('method-shadow', 'trait A{}struct H{x:u64}impl H{fn bad<A,U:A>(self,u:U){}}', 'shadows', 'E0404'),
    ('owner-shadow', 'trait A{}struct H<A>{x:A}impl<A>H<A>{fn bad<U:A>(self,u:U){}}', 'shadows', 'E0404'),
    ('local-copy-not-builtin', 'trait Copy{}struct H{x:u64}impl H{fn bad<U:Copy>(self,u:U)->(U,U){(u,u)}}', 'moved', 'E0382'),
    ('unused-inference', 'trait A{}struct H{x:u64}impl H{fn choose<U:A>(self){}}fn f(){H{x:0}.choose();}', 'cannot infer generic parameter', 'E0283'),
    ('explicit-missing-impl', 'trait A{}struct H{x:u64}impl H{fn f<U:A>(self,u:U)->U{u}}fn f()->bool{H{x:0}.f::<bool>(true)}', 'unsatisfied local trait bound', 'E0277'),
    ('result-obligation', 'trait A{fn make()->Self;}struct H{x:u64}impl H{fn f<U>(self)->U where U:A{U::make()}}fn f()->u64{H{x:0}.f()}', 'unsatisfied local trait bound', 'E0277'),
    ('sized-forward', 'struct H{x:u64}impl H{fn need<U:Sized>(self)where U:?Sized{}fn bad<V:?Sized>(self){self.need::<V>();}}', 'canonical Sized', 'E0277'),
    ('copy-forward', 'trait A{}struct H{x:u64}impl H{fn need<U>(self,u:U)->U where U:Copy{u}fn bad<V:A>(self,v:V)->V{self.need(v)}}', 'canonical Copy', 'E0277'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_abstract_method_and_call_obligations_fail_without_lowering(name, text, message, code):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


REFERENCE_INVALID = (
    ('immutable-argument', 'trait A{fn step(&mut self);}struct H{x:u64}impl H{fn f<U:A>(&self,u:&U){u.step()}}', '', 'E0596'),
    ('moved-reference-receiver', 'trait A{fn n(&self)->u64;}struct H{x:u64}impl H{fn f<U>(self,u:U)where U:A{let moved=u;u.n();}}', '', 'E0382'),
)
CAPABILITY = (
    ('owner-marker', 'struct H<T>{x:T}impl<T>H<T>{fn f(&self)where T:Copy{}}', 'impl-owner/Self'),
    ('owner-local', 'trait A{}struct H<T>{x:T}impl<T>H<T>{fn f(&self)where T:A{}}', 'impl-owner/Self'),
    ('self-predicate', 'struct H{x:u64}impl H{fn f(self)where Self:Sized{}}', 'impl-owner/Self'),
    ('trait-local', 'trait A{}trait B{fn f<U:A>(u:U)->U;}', 'local/user trait bounds'),
    ('trait-where', 'trait A{}trait B{fn f<U>(u:U)->U where U:A;}', 'where constraints'),
    ('impl-trait-where', 'trait A{fn f<U:Copy>(u:U)->U;}struct H{x:u64}impl A for H{fn f<U>(u:U)->U where U:Copy{u}}', 'where constraints'),
    ('method-lifetime', "struct H{x:u64}impl H{fn f<'a,'b>(&self,u:&'a u64)where 'a:'b{}}", 'lifetime and higher-ranked where'),
    ('relaxed-local', 'trait A{fn n(&self)->u64;}struct H{x:u64}impl H{fn f<U:?Sized+A>(&self,u:&U)->u64{u.n()}}', 'relaxed local-trait parameter layouts'),
)


@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_remaining_conditional_and_trait_contract_domains_are_explicit(name, text, message):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_actual_valid_unsupported_forms_are_not_rust_rejections(name, text, message, tmp_path):
    checked = certify_source(text, tmp_path / name)
    with pytest.raises(RustError, match=message):
        compile_rust(text, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_inherent_local_programs_match_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_native_marker_originals_match_rustc(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_invalid_method_local_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID + REFERENCE_INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
