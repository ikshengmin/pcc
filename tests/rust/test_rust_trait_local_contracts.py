"""Required local-trait method contracts versus weaker implementation bodies.

https://doc.rust-lang.org/reference/items/associated-items.html
https://doc.rust-lang.org/reference/trait-bounds.html#bound-satisfaction
https://doc.rust-lang.org/error_codes/E0276.html
No new solver, blanket impls or borrow admission in this inline-contract module.
All execution is delegated to CI with actual same-input rustc certification.
"""
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import actual_check_prerequisite, certify_source

PROGRAMS = ('trait_local_nested_dispatch', 'trait_local_weaker_identity', 'trait_local_result',
            'trait_local_mutable', 'trait_local_marker_names')
PRIOR_CAPABILITIES = (
    ('value-receiver', 'trait A{}trait B{fn f<T:A>(self,x:T)->T;}'),
    ('associated', 'trait A{}trait B{fn f<U:A>(u:U)->U;}'),
)
DECLARATION_CONTROLS = (
    ('forward-trait', 'trait Ops{fn f<U:Later>(u:U)->U;}trait Later{}'),
    ('self-trait', 'trait Ops{fn f<U:Ops>(u:U)->U;}'),
    ('redundant-supertrait', 'trait A{}trait B:A{}trait Ops{fn f<U:B>(u:U)->U;}struct R{x:u64}impl Ops for R{fn f<V:A+B>(v:V)->V{v}}'),
)


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def test_required_local_contract_and_weaker_body_are_separate_canonical_environments():
    text = ('trait Ops{fn select<U:Child>(self,value:U)->u64;}'
            'trait Read{fn read(self)->u64;}trait Child:Read{}'
            'struct Value{x:u64}struct Host{x:u64}'
            'impl Read for Value{fn read(self)->u64{self.x}}impl Child for Value{}'
            'impl Value{fn read(self)->u64{901}}'
            'impl Ops for Host{fn select<V:Read>(self,value:V)->u64{value.read()}}'
            'impl Host{fn select<V>(self,value:V)->u64{900}}'
            'fn invoke<H:Ops,U:Child>(host:H,value:U)->u64{host.select(value)}'
            'fn f()->u64{invoke(Host{x:0},Value{x:42})+Host{x:0}.select(Value{x:0})}')
    checker = analyze_source(text)
    registry = checker.function_generics
    required, = checker.traits.method_parameters['Ops', 'select']
    actual, = registry.parameters['<Host as Ops>::select']
    inherent, = registry.parameters['Host::select']
    assert checker.traits.parameter_bounds[required] == frozenset({'Child', 'Read'})
    assert checker.traits.parameter_bounds[actual] == frozenset({'Read'})
    assert checker.traits.call_bounds[actual] == frozenset({'Child', 'Read'})
    assert checker.traits.parameter_bounds[inherent] == frozenset()
    assert inherent not in checker.traits.call_bounds
    outer, argument = registry.parameters['invoke']
    assert tuple(registry.bound_calls['invoke'].values()) == (('Ops', outer, 'select'),)
    assert tuple(registry.bound_calls['<Host as Ops>::select'].values()) == (('Read', actual, 'read'),)
    instances = [entry.value for entry in registry.instances.entries.values()]
    assert {(fn.declaration_name, fn.instance_arguments) for fn in instances} == {
        ('invoke', ('Host', 'Value')), ('<Host as Ops>::select', ('Value',)),
        ('Host::select', ('Value',))}
    assert len(instances) == len({fn.identity for fn in instances}) == 3
    for function in instances:
        if function.declaration_name == 'invoke':
            assert function.body.children[-1].binding.declaration_name == '<Host as Ops>::select'
        elif function.declaration_name == '<Host as Ops>::select':
            assert function.body.children[-1].binding.declaration_name == '<Value as Read>::read'


def test_local_copy_and_sized_trait_names_do_not_become_builtin_marker_constraints():
    text = ('trait Copy{}trait Sized{}trait Ops{fn f<U:Copy+Sized>(u:U)->U;}'
            'struct R{x:u64}impl Ops for R{fn f<V>(v:V)->V{v}}')
    checker = analyze_source(text)
    required, = checker.traits.method_parameters['Ops', 'f']
    actual, = checker.function_generics.parameters['<R as Ops>::f']
    assert checker.traits.parameter_bounds[required] == frozenset({'Copy', 'Sized'})
    assert checker.traits.parameter_bounds[actual] == frozenset()
    assert checker.traits.call_bounds[actual] == frozenset({'Copy', 'Sized'})
    assert not checker.copy_type(required) and not checker.copy_type(actual)


@pytest.mark.parametrize('text', [PRIOR_CAPABILITIES[1][1], DECLARATION_CONTROLS[0][1],
    'trait A{}trait Ops{fn f<U>(u:U)->U;}struct R{x:u64}impl Ops for R{fn f<V:A>(v:V)->V{v}}'])
def test_unused_trait_signature_and_impl_bounds_require_real_token_before_sema(text, monkeypatch):
    from pcc.frontends.rust.semantics import Checker
    monkeypatch.setattr(Checker, 'check', lambda *a: pytest.fail('unchecked trait contract entered Sema'))
    with pytest.raises(RustError, match='local trait bounds require an actual'):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('name', PROGRAMS)
def test_trait_local_originals_cannot_use_only_the_opt_in_boolean(name, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unchecked trait contract reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(source(name), experimental_rustc_checked=True)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text', PRIOR_CAPABILITIES + DECLARATION_CONTROLS)
def test_actual_checked_required_trait_controls_are_positive(name, text, tmp_path, monkeypatch):
    from tests.rust.test_rust_local_trait_bounds import checked_owned
    checked_owned(text, tmp_path / name, monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', PROGRAMS)
def test_actual_same_input_check_precedes_owned_trait_contract_lowering(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_local_trait_bounds import checked_owned
    checked_owned(source(name), tmp_path / name, monkeypatch)


INVALID = (
    ('stronger-local', 'trait A{}trait Ops{fn f<U>(u:U)->U;}struct R{x:u64}impl Ops for R{fn f<V:A>(v:V)->V{v}}', 'bounds are stricter', 'E0276'),
    ('stronger-subtrait', 'trait A{}trait B:A{}trait Ops{fn f<U:A>(u:U)->U;}struct R{x:u64}impl Ops for R{fn f<V:B>(v:V)->V{v}}', 'bounds are stricter', 'E0276'),
    ('unrelated-local', 'trait A{}trait B{}trait Ops{fn f<U:A>(u:U)->U;}struct R{x:u64}impl Ops for R{fn f<V:B>(v:V)->V{v}}', 'bounds are stricter', 'E0276'),
    ('marker-strengthening', 'trait A{}trait Ops{fn f<U:A>(u:U)->U;}struct R{x:u64}impl Ops for R{fn f<V:A+Copy>(v:V)->V{v}}', 'bounds are stricter', 'E0276'),
    ('body-does-not-inherit-local', 'trait A{fn n(self)->u64;}trait Ops{fn f<U:A>(u:U)->u64;}struct R{x:u64}impl Ops for R{fn f<V>(v:V)->u64{v.n()}}', 'unknown inherent or concrete trait method', 'E0599'),
    ('body-does-not-inherit-subtrait', 'trait A{}trait B:A{}fn need<T:B>(t:T)->T{t}trait Ops{fn f<U:B>(u:U)->U;}struct R{x:u64}impl Ops for R{fn f<V:A>(v:V)->V{need(v)}}', 'unsatisfied local trait bound', 'E0277'),
    ('weak-impl-dot-call', 'trait A{}trait Ops{fn f<U:A>(self,u:U)->U;}struct R{x:u64}impl Ops for R{fn f<V>(self,v:V)->V{v}}fn f()->bool{R{x:0}.f(true)}', 'unsatisfied local trait bound', 'E0277'),
    ('weak-impl-type-call', 'trait A{}trait Ops{fn f<U:A>(self,u:U)->U;}struct R{x:u64}impl Ops for R{fn f<V>(self,v:V)->V{v}}fn f()->bool{R::f(R{x:0},true)}', 'unsatisfied local trait bound', 'E0277'),
    ('weak-impl-qualified-call', 'trait A{}trait Ops{fn f<U:A>(self,u:U)->U;}struct R{x:u64}impl Ops for R{fn f<V>(self,v:V)->V{v}}fn f()->bool{<R as Ops>::f::<bool>(R{x:0},true)}', 'unsatisfied local trait bound', 'E0277'),
    ('unused-generic-forward', 'trait A{}trait Ops{fn f<U:A>(self,u:U)->U;}fn bad<H:Ops,V>(h:H,v:V)->V{h.f(v)}', 'unsatisfied local trait bound', 'E0277'),
    ('weaker-supertrait-call', 'trait A{}trait B:A{}struct V{x:u64}impl A for V{}trait Ops{fn f<U:B>(u:U)->U;}struct R{x:u64}impl Ops for R{fn f<U:A>(u:U)->U{u}}fn f()->V{R::f(V{x:0})}', 'unsatisfied local trait bound', 'E0277'),
    ('result-contract', 'trait A{}trait Ops{fn empty<U:A>()->[U;0];}struct R{x:u64}impl Ops for R{fn empty<V>()->[V;0]{[]}}fn f()->[bool;0]{R::empty()}', 'unsatisfied local trait bound', 'E0277'),
    ('local-copy-not-move-permission', 'trait Copy{}trait Ops{fn f<U:Copy>(u:U)->(U,U);}struct R{x:u64}impl Ops for R{fn f<V:Copy>(v:V)->(V,V){(v,v)}}', 'moved', 'E0382'),
    ('unused-body-ambiguity', 'trait A{fn n(self)->u64;}trait B{fn n(self)->u64;}trait Ops{fn f<U:A+B>(u:U)->u64;}struct R{x:u64}impl Ops for R{fn f<V:A+B>(v:V)->u64{v.n()}}', 'ambiguous', 'E0034'),
    ('required-trait-shadow', 'trait A{}trait Ops{fn f<A,U:A>(u:U)->U;}', 'shadows', 'E0404'),
    ('impl-trait-shadow', 'trait A{}trait Ops{fn f<U:A>(u:U)->U;}struct R{x:u64}impl Ops for R{fn f<A:A>(v:A)->A{v}}', 'shadows', 'E0404'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_required_call_and_declared_body_obligations_remain_distinct(name, text, message, code):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


REFERENCE_INVALID = (
    ('immutable-argument', 'trait Step{fn step(&mut self);}trait Ops{fn f<U:Step>(u:&U);}struct R{x:u64}impl Ops for R{fn f<V:Step>(v:&V){v.step();}}', '', 'E0596'),
    ('moved-argument', 'trait Read{fn read(&self)->u64;}trait Ops{fn f<U:Read>(u:U);}struct R{x:u64}impl Ops for R{fn f<V:Read>(v:V){let moved=v;v.read();}}', '', 'E0382'),
)
CAPABILITY = (
    # Exact trait-method where originals now checked in test_rust_trait_where_contracts.
    ('relaxed-local-layout', 'trait A{}trait Ops{fn f<U:?Sized+A>(u:&U);}', 'relaxed local-trait parameter layouts'),
)


@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_unimplemented_trait_contract_contexts_stay_explicit(name, text, message):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_actual_valid_unsupported_contract_contexts_are_separate(name, text, message, tmp_path):
    checked = certify_source(text, tmp_path / name)
    with pytest.raises(RustError, match=message):
        compile_rust(text, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_trait_local_contract_originals_match_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_native_marker_originals_match_rustc(tmp_path, monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_invalid_trait_contracts(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID + REFERENCE_INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
