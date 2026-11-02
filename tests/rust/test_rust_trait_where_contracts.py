"""Structural required/impl method predicates with distinct call/body contracts.

https://doc.rust-lang.org/reference/items/associated-items.html
https://doc.rust-lang.org/reference/items/generics.html#where-clauses
https://doc.rust-lang.org/error_codes/E0276.html
Only existing method type parameters and canonical marker/local traits; no Self
predicates, associated projections, lifetime solver or generic trait impls.
Internal models do not certify borrowing. Execution belongs to CI.
"""
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.standard_markers import CLONE, COPY, SIZED
from pcc.frontends.rust.syntax import Parser
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import actual_check_prerequisite, certify_source

PROGRAMS = ('trait_where_dispatch', 'trait_where_markers', 'trait_where_result',
            'trait_where_receivers', 'trait_where_signature')
# Exact formerly valid-but-unsupported originals, not rewritten inputs.
PRIOR_CAPABILITIES = (
    ('required-local', 'trait A{}trait Ops{fn f<U>(u:U)->U where U:A;}'),
    ('impl-local', 'trait A{}trait Ops{fn f<U:A>(u:U)->U;}struct R{x:u64}impl Ops for R{fn f<V>(v:V)->V where V:A{v}}'),
    ('owner-module-control', 'trait A{fn f<U>(u:U)->U where U:Copy;}'),
    ('inherent-module-control', 'trait A{}trait B{fn f<U>(u:U)->U where U:A;}'),
    ('impl-marker', 'trait A{fn f<U:Copy>(u:U)->U;}struct H{x:u64}impl A for H{fn f<U>(u:U)->U where U:Copy{u}}'),
    ('trait-module-control', 'trait T{fn f<U>(u:U)->U where U:Copy;}'),
    ('free-module-control', 'trait A{fn f<T>(x:T)->T where T:Copy;}'),
)


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def test_required_predicate_ast_stops_at_semicolon_and_preserves_duplicates():
    parser = Parser('trait A{}trait Ops{fn f<U:Copy>(u:U)->U where U:A,U:Copy,;fn g<V>(v:V)->V where V:;}')
    assert parser.parse() == []
    first, second = parser.traits[1].methods
    assert [p.parameter.text for p in first.where_predicates] == ['U', 'U']
    assert [(p.text, tuple(b.path for b in bs)) for p, bs in first.effective_parameter_bounds] == [('U', ('Copy', 'A', 'Copy'))]
    assert first.where_token.text == 'where' and first.body is None
    assert second.where_predicates[0].bounds == () and second.body is None


def test_required_where_contract_is_not_granted_to_weaker_impl_body():
    checker = analyze_source(source('trait_where_dispatch'))
    registry = checker.function_generics
    required, = checker.traits.method_parameters['Ops', 'select']
    actual, = registry.parameters['<Host as Ops>::select']
    assert checker.traits.parameter_bounds[required] == frozenset({'Child', 'Read'})
    assert checker.traits.parameter_bounds[actual] == frozenset({'Read'})
    assert checker.traits.call_bounds[actual] == frozenset({'Child', 'Read'})
    assert tuple(registry.bound_calls['<Host as Ops>::select'].values()) == (('Read', actual, 'read'),)
    outer, argument = registry.parameters['forward']
    assert tuple(registry.bound_calls['forward'].values()) == (('Ops', outer, 'select'),)
    instances = [entry.value for entry in registry.instances.entries.values()]
    assert {(fn.declaration_name, fn.instance_arguments) for fn in instances} == {
        ('forward', ('Host', 'Value')), ('<Host as Ops>::select', ('Value',)),
        ('Host::select', ('Value',))}
    assert len(instances) == len({fn.identity for fn in instances}) == 3
    for function in instances:
        if function.declaration_name == 'forward':
            assert function.body.children[-1].binding.declaration_name == '<Host as Ops>::select'
        elif function.declaration_name == '<Host as Ops>::select':
            assert function.body.children[-1].binding.declaration_name == '<Value as Read>::read'


SIZED_FORMS = (
    ('?Sized', 'U:Sized,U:Copy'), ('Copy', 'U:?Sized,U:Sized'),
    ('?Sized', 'U:Copy,U:Sized'), ('Copy', 'U:Sized,U:?Sized'),
    ('', 'U:?Sized,U:Copy,U:Copy'),
)


def sized_contract(inline, where):
    return ('trait Ops{fn pair<U' + (':' + inline if inline else '') + '>(u:U)->(U,U)where ' + where + ';}'
            'struct H{x:u64}impl Ops for H{fn pair<V:Copy>(v:V)->(V,V){(v,v)}}')


@pytest.mark.parametrize('inline,where', SIZED_FORMS)
def test_inline_and_where_merge_is_order_independent_for_sized_and_copy(inline, where):
    checker = analyze_source(sized_contract(inline, where))
    required, = checker.traits.method_parameters['Ops', 'pair']
    actual, = checker.function_generics.parameters['<H as Ops>::pair']
    assert checker.standard_markers.constraints[required] == frozenset({SIZED, COPY, CLONE})
    assert checker.standard_markers.call_constraints[actual] == frozenset({SIZED, COPY, CLONE})


def test_weaker_where_sized_impl_retains_only_its_declared_body_constraints():
    text = ('trait Ops{fn take<U>(u:&U)where U:Sized;}'
            'struct H{x:u64}impl Ops for H{fn take<V>(v:&V)where V:?Sized{}}')
    checker = analyze_source(text)
    actual, = checker.function_generics.parameters['<H as Ops>::take']
    assert checker.standard_markers.constraints[actual] == frozenset()
    assert checker.standard_markers.call_constraints[actual] == frozenset({SIZED})


def test_signatures_use_canonical_declaration_types_and_position_not_parameter_spelling():
    checker = analyze_source(source('trait_where_signature'))
    registry = checker.function_generics
    pair = checker.traits.impls['Pairs', 'Host']['pair']
    parameter, = registry.parameters[pair.declaration_name]
    assert parameter.name == 'Host' and pair.params[0][1] == 'Host'
    assert pair.params[1][1] == parameter
    assert pair.result.elements == ('Host', parameter)
    swap = checker.traits.impls['Pairs', 'Host']['swap']
    first, second = registry.parameters[swap.declaration_name]
    assert (first.name, second.name) == ('B', 'A')
    assert swap.result.elements == (second, first)


def test_local_marker_names_in_where_predicates_are_not_standard_markers():
    text = ('trait Copy{}trait Sized{}trait Ops{fn take<U>(u:U)->U where U:Copy,U:Sized;}'
            'struct H{x:u64}impl Ops for H{fn take<V>(v:V)->V{v}}')
    checker = analyze_source(text)
    required, = checker.traits.method_parameters['Ops', 'take']
    actual, = checker.function_generics.parameters['<H as Ops>::take']
    assert checker.traits.parameter_bounds[required] == frozenset({'Copy', 'Sized'})
    assert checker.traits.parameter_bounds[actual] == frozenset()
    assert checker.traits.call_bounds[actual] == frozenset({'Copy', 'Sized'})
    assert not checker.copy_type(required) and not checker.copy_type(actual)


@pytest.mark.parametrize('text', [PRIOR_CAPABILITIES[0][1], PRIOR_CAPABILITIES[1][1],
    'trait A{}trait Ops{fn f<U>(u:U)->U;}struct H{x:u64}impl Ops for H{fn f<V>(v:V)->V where V:A{v}}'])
def test_unused_local_where_contract_requires_actual_check_before_sema(text, monkeypatch):
    from pcc.frontends.rust.semantics import Checker
    monkeypatch.setattr(Checker, 'check', lambda *a: pytest.fail('unchecked local contract entered Sema'))
    with pytest.raises(RustError, match='local trait bounds require an actual'):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('name', PROGRAMS)
def test_where_originals_cannot_use_opt_in_boolean_instead_of_real_token(name, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unchecked where contract reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(source(name), experimental_rustc_checked=True)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text', PRIOR_CAPABILITIES + (
    ('empty-bounds', 'trait Ops{fn f<U>(u:U)->U where U:;}struct H{x:u64}impl Ops for H{fn f<V>(v:V)->V{v}}'),
    ('weaker-sized', 'trait Ops{fn f<U>(u:&U)where U:Sized;}struct H{x:u64}impl Ops for H{fn f<V>(v:&V)where V:?Sized{}}'),
    ('empty-where', 'trait Ops{fn f<U>(u:U)->U where;}struct H{x:u64}impl Ops for H{fn f<V>(v:V)->V where{v}}'),
))
def test_actual_checked_former_capabilities_and_where_controls(name, text, tmp_path, monkeypatch):
    from tests.rust.test_rust_local_trait_bounds import checked_owned
    checked_owned(text, tmp_path / name, monkeypatch)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('inline,where', SIZED_FORMS)
def test_actual_checked_sized_predicate_orderings(inline, where, tmp_path, monkeypatch):
    from tests.rust.test_rust_local_trait_bounds import checked_owned
    checked_owned(sized_contract(inline, where), tmp_path / 'check', monkeypatch)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', PROGRAMS)
def test_actual_same_input_check_precedes_owned_where_lowering(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_local_trait_bounds import checked_owned
    checked_owned(source(name), tmp_path / name, monkeypatch)


INVALID = (
    ('stronger-copy', 'trait Ops{fn f<U>(u:U)->U;}struct H{x:u64}impl Ops for H{fn f<V>(v:V)->V where V:Copy{v}}', 'bounds are stricter', 'E0276'),
    ('stronger-sized', 'trait Ops{fn f<U>(u:&U)where U:?Sized;}struct H{x:u64}impl Ops for H{fn f<V>(v:&V)where V:Sized{}}', 'bounds are stricter', 'E0276'),
    ('stronger-local', 'trait A{}trait B:A{}trait Ops{fn f<U>(u:U)->U where U:A;}struct H{x:u64}impl Ops for H{fn f<V>(v:V)->V where V:B{v}}', 'bounds are stricter', 'E0276'),
    ('unrelated-local', 'trait A{}trait B{}trait Ops{fn f<U>(u:U)->U where U:A;}struct H{x:u64}impl Ops for H{fn f<V>(v:V)->V where V:B{v}}', 'bounds are stricter', 'E0276'),
    ('weak-dot-call', 'trait A{}trait Ops{fn f<U>(self,u:U)->U where U:A;}struct H{x:u64}impl Ops for H{fn f<V>(self,v:V)->V{v}}fn f()->bool{H{x:0}.f(true)}', 'unsatisfied local trait bound', 'E0277'),
    ('weak-qualified-call', 'trait A{}trait Ops{fn f<U>(u:U)->U where U:A;}struct H{x:u64}impl Ops for H{fn f<V>(v:V)->V{v}}fn f()->bool{<H as Ops>::f(true)}', 'unsatisfied local trait bound', 'E0277'),
    ('weak-type-call', 'trait A{}trait Ops{fn f<U>(u:U)->U where U:A;}struct H{x:u64}impl Ops for H{fn f<V>(v:V)->V{v}}fn f()->bool{H::f(true)}', 'unsatisfied local trait bound', 'E0277'),
    ('result-obligation', 'trait A{}trait Ops{fn empty<U>()->[U;0]where U:A;}struct H{x:u64}impl Ops for H{fn empty<V>()->[V;0]{[]}}fn f()->[bool;0]{H::empty()}', 'unsatisfied local trait bound', 'E0277'),
    ('unused-forward', 'trait A{}trait Ops{fn f<U>(self,u:U)->U where U:A;}fn bad<H:Ops,V>(h:H,v:V)->V{h.f(v)}', 'unsatisfied local trait bound', 'E0277'),
    ('weak-body-local', 'trait A{fn n(self)->u64;}trait Ops{fn f<U>(u:U)->u64 where U:A;}struct H{x:u64}impl Ops for H{fn f<V>(v:V)->u64{v.n()}}', 'unknown inherent or concrete trait method', 'E0599'),
    ('weak-body-copy', 'fn need<U:Copy>(u:U)->U{u}trait Ops{fn f<U>(u:U)->U where U:Copy;}struct H{x:u64}impl Ops for H{fn f<V>(v:V)->V{need(v)}}', 'canonical Copy', 'E0277'),
    ('weak-body-sized', 'fn need<U>(u:&U){}trait Ops{fn f<U>(u:&U)where U:Sized;}struct H{x:u64}impl Ops for H{fn f<V>(v:&V)where V:?Sized{need(v)}}', 'canonical Sized', 'E0277'),
    ('duplicate-predicate-retains-copy', 'struct N{x:u64}trait Ops{fn f<U:?Sized>(u:U)->U where U:Copy,U:Sized;}struct H{x:u64}impl Ops for H{fn f<V>(v:V)->V{v}}fn f()->N{H::f(N{x:1})}', 'canonical Copy', 'E0277'),
    ('unused-ambiguity', 'trait A{fn n(self)->u64;}trait B{fn n(self)->u64;}trait Ops{fn f<U>(u:U)->u64 where U:A,U:B;}struct H{x:u64}impl Ops for H{fn f<V>(v:V)->u64 where V:A+B{v.n()}}', 'ambiguous', 'E0034'),
    ('declaration-trait-shadow', 'trait A{}trait Ops{fn f<A,U>(u:U)->U where U:A;}', 'shadows', 'E0404'),
    ('impl-trait-shadow', 'trait A{}trait Ops{fn f<U>(u:U)->U where U:A;}struct H{x:u64}impl Ops for H{fn f<A>(a:A)->A where A:A{a}}', 'shadows', 'E0404'),
    ('local-copy-is-not-builtin', 'trait Copy{}trait Ops{fn f<U>(u:U)->(U,U)where U:Copy;}struct H{x:u64}impl Ops for H{fn f<V>(v:V)->(V,V)where V:Copy{(v,v)}}', 'moved', 'E0382'),
    ('wrong-canonical-signature', 'struct H{x:u64}trait Ops{fn f<U>(h:H,u:U)->U where U:Copy;}impl Ops for H{fn f<H>(h:H,u:H)->H where H:Copy{u}}', 'signature mismatch', 'E0053'),
    ('wrong-parameter-position', 'trait Ops{fn f<A,B>(a:A,b:B)->A where A:Copy,B:Copy;}struct H{x:u64}impl Ops for H{fn f<B,A>(a:B,b:A)->A where B:Copy,A:Copy{b}}', 'signature mismatch', 'E0053'),
)
REFERENCE_INVALID = (
    ('shared-mutation', 'trait Step{fn step(&mut self);}trait Ops{fn f<U>(u:&U)where U:Step;}struct H{x:u64}impl Ops for H{fn f<V>(v:&V)where V:Step{v.step();}}', '', 'E0596'),
    ('moved-reference', 'trait Read{fn read(&self)->u64;}trait Ops{fn f<U>(u:U)where U:Read;}struct H{x:u64}impl Ops for H{fn f<V>(v:V)where V:Read{let moved=v;v.read();}}', '', 'E0382'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_invalid_contracts_and_unused_bodies_fail_in_noncertifying_model(name, text, message, code):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


CAPABILITY = (
    ('self', 'trait Ops{fn f(&self)where Self:Copy;}', 'where Self'),
    ('compound', 'trait Ops{fn f<U>(u:U)where (U,):Copy;}', 'subjects must be declared'),
    ('projection', 'trait Ops{fn f<U:Iterator>(u:U)where U::Item:Copy;}', 'where projections'),
    ('lifetime', "trait Ops{fn f<'a,U>(u:&'a U)where U:'a;}", 'lifetime bounds'),
    ('default-body', 'trait Ops{fn f<U>(u:U)->U where U:Copy{u}}', 'default trait method bodies'),
    ('generic-impl', 'trait Ops{fn f<U>(u:U)->U where U:Copy;}struct H<T>{x:T}impl<T>Ops for H<T>{fn f<V>(v:V)->V where V:Copy{v}}', 'generic trait impls'),
)


@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_other_trait_predicate_contexts_remain_explicit(name, text, message):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_actual_valid_unsupported_trait_contexts_are_not_invalid_oracles(name, text, message, tmp_path):
    checked = certify_source(text, tmp_path / name)
    with pytest.raises(RustError, match=message):
        compile_rust(text, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_where_contract_originals_match_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_native_marker_originals_match_rustc(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_where_contract_negatives(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID + REFERENCE_INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
