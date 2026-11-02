"""Bounded nominal inherent impl contracts, not generic trait coherence.

https://doc.rust-lang.org/reference/items/implementations.html
https://rustc-dev-guide.rust-lang.org/hir-typeck/method-lookup.html
Impl predicates govern candidate availability; method predicates remain call
obligations. Source models do not certify borrowing. All execution belongs to CI.
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

PROGRAMS = ('impl_bound_dispatch', 'impl_bound_copy', 'impl_bound_mutable',
            'impl_bound_result', 'impl_bound_lookup_order')
PRIOR_CAPABILITIES = (
    ('empty-tight', 'struct R<T>{x:T}impl<T:Copy>R<T>{}'),
    ('empty-spaced-owner', 'struct R<T>{x:T}impl<T:Copy> R<T>{}'),
    ('empty-spaced-impl', 'struct R<T>{x:T} impl<T:Copy> R<T>{}'),
    ('method', 'struct H<T>{x:T}impl<T:Copy>H<T>{fn f(self)->T{self.x}}'),
)


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def test_impl_header_predicates_remain_separate_from_method_predicate_ast():
    parser = Parser('struct Pair<X,Y>{first:X,second:Y}impl<A:Copy,B>Pair<B,A>where A:Sized{fn f<U:Copy>(self,u:U)->U{u}}')
    function, = parser.parse()
    implementation, = parser.implementations
    assert [p.text for p in implementation.type_parameters] == ['A', 'B']
    assert [p.parameter.text for p in implementation.where_predicates] == ['A']
    assert [(p.text, tuple(b.path for b in bs)) for p, bs in implementation.effective_parameter_bounds] == [('A', ('Copy', 'Sized'))]
    assert [(p.text, tuple(b.path for b in bs)) for p, bs in function.effective_parameter_bounds] == [('U', ('Copy',))]
    assert function.owner_parameter_count == 2


def test_ineligible_impl_falls_back_without_scheduling_an_unproven_instance():
    text = ('trait Read{fn read(self)->u64;}trait Fallback{fn read(self)->u64;}'
            'struct R{x:u64}struct N{x:u64}struct H<T>{x:T}'
            'impl Read for R{fn read(self)->u64{self.x}}'
            'impl R{fn read(self)->u64{901}}'
            'impl<T:Read>H<T>{fn read(self)->u64{self.x.read()}}'
            'impl Fallback for H<N>{fn read(self)->u64{900}}'
            'fn f()->u64{H::<N>{x:N{x:0}}.read()+H::<R>{x:R{x:42}}.read()}')
    checker = analyze_source(text)
    registry = checker.function_generics
    owner, = registry.parameters['H::read']
    assert checker.traits.parameter_bounds[owner] == frozenset({'Read'})
    assert tuple(registry.bound_calls['H::read'].values()) == (('Read', owner, 'read'),)
    instances = [entry.value for entry in registry.instances.entries.values()]
    assert [(fn.declaration_name, fn.instance_arguments) for fn in instances] == [('H::read', ('R',))]
    assert instances[0].body.children[-1].binding.declaration_name == '<R as Read>::read'
    left = checker.names['f'].body.children[-1].children[0]
    assert left.binding.declaration_name == '<H<N> as Fallback>::read'


def test_header_markers_resolve_before_suffix_shadowing_and_keep_permuted_prefix_order():
    text = ('struct R{x:u64}struct Pair<X,Y>{first:X,second:Y}'
            'impl<A:Copy,B>Pair<B,A>{fn dup<Copy>(self,marker:Copy)->(A,A,Copy){(self.second,self.second,marker)}}'
            'fn f()->(u64,u64,R){Pair::<bool,u64>{first:true,second:42}.dup(R{x:1})}')
    checker = analyze_source(text)
    parameters = checker.function_generics.parameters['Pair::dup']
    assert [checker.copy_type(p) for p in parameters] == [True, False, False]
    instance, = checker.function_generics.instances.entries.values()
    assert instance.value.instance_arguments == ('u64', 'bool', 'R')


@pytest.mark.parametrize('text', [
    'trait A{}struct H<T>{x:T}impl<T:A>H<T>{}',
    'trait A{}struct H<T>{x:T}impl<T>H<T>where T:A{}',
])
def test_empty_local_impl_header_requires_actual_token_before_checking(text, monkeypatch):
    from pcc.frontends.rust.semantics import Checker
    monkeypatch.setattr(Checker, 'check', lambda *a: pytest.fail('unchecked empty impl entered Sema'))
    with pytest.raises(RustError, match='local trait bounds require an actual'):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('text', [text for _, text in PRIOR_CAPABILITIES])
def test_empty_or_unused_marker_impl_cannot_authorize_owned_ir_with_a_boolean(text, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unchecked impl reached IR'))
    with pytest.raises(RustError, match='standard marker bounds require an actual'):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.parametrize('name', PROGRAMS)
def test_impl_originals_keep_genuine_source_certification(name, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unchecked impl source reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(source(name), experimental_rustc_checked=True)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text', PRIOR_CAPABILITIES + (
    ('empty-local', 'trait A{}struct H<T>{x:T}impl<T:A>H<T>{}'),
    ('empty-where', 'trait A{}struct H<T>{x:T}impl<T>H<T>where T:A{}'),
))
def test_actual_checked_empty_and_former_impl_controls_are_positive(name, text, tmp_path, monkeypatch):
    from tests.rust.test_rust_local_trait_bounds import checked_owned
    checked_owned(text, tmp_path / name, monkeypatch)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', PROGRAMS)
def test_actual_same_input_impl_check_precedes_owned_phase(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_local_trait_bounds import checked_owned
    checked_owned(source(name), tmp_path / name, monkeypatch)


INVALID = (
    ('missing-header', 'trait A{}struct H<T>{x:T}impl<T:A>H<T>{fn f(self){}}fn f(){H::<bool>{x:true}.f();}', 'unsatisfied inherent impl bounds', 'E0599'),
    ('qualified-missing-header', 'trait A{}struct H<T>{x:T}impl<T:A>H<T>{fn f(self){}}fn f(){H::<bool>::f(H::<bool>{x:true});}', 'unsatisfied inherent impl bounds', 'E0599'),
    ('missing-copy', 'struct N{x:u64}struct H<T>{x:T}impl<T:Copy>H<T>{fn f(self)->(T,T){(self.x,self.x)}}fn f(){H::<N>{x:N{x:0}}.f();}', 'unsatisfied inherent impl bounds', 'E0599'),
    ('unused-forward', 'trait A{}struct H<T>{x:T}impl<T:A>H<T>{fn f(self){}}fn bad<U>(value:H<U>){value.f();}', 'unsatisfied inherent impl bounds', 'E0599'),
    ('unbounded-sibling', 'trait A{}struct H<T>{x:T}impl<T:A>H<T>{fn f(self){}}impl<T>H<T>{fn bad(self){self.f();}}', 'unsatisfied inherent impl bounds', 'E0599'),
    ('sibling-copy-not-inherited', 'struct H<T>{x:T}impl<T:Copy>H<T>{fn f(self)->(T,T){(self.x,self.x)}}impl<T>H<T>{fn bad(self)->(T,T){(self.x,self.x)}}', 'moved', 'E0382'),
    ('duplicate-bounded-method', 'trait A{}trait B{}struct H<T>{x:T}impl<T:A>H<T>{fn f(self){}}impl<T:B>H<T>{fn f(self){}}', 'duplicate inherent', 'E0592'),
    ('duplicate-unbounded-method', 'trait A{}struct H<T>{x:T}impl<T:A>H<T>{fn f(self){}}impl<T>H<T>{fn f(self){}}', 'duplicate inherent', 'E0592'),
    ('method-obligation-not-fallback', 'trait A{}trait Fallback{fn pick<U>(&self,u:U)->u64;}struct H<T>{x:T}impl<T:Copy>H<T>{fn pick<U:A>(&self,u:U)->u64{42}}impl Fallback for H<u64>{fn pick<U>(&self,u:U)->u64{900}}fn f()->u64{let h=H::<u64>{x:0};h.pick(true)}', 'unsatisfied local trait bound', 'E0277'),
    ('local-copy-not-builtin', 'trait Copy{}struct H<T>{x:T}impl<T:Copy>H<T>{fn bad(self)->(T,T){(self.x,self.x)}}', 'moved', 'E0382'),
    ('header-trait-shadow', 'trait A{}struct H<A>{x:A}impl<A:A>H<A>{}', 'shadows', 'E0404'),
    ('header-unknown-trait', 'struct H<T>{x:T}impl<T:Unknown>H<T>{}', 'only canonical', 'E0405'),
    ('unconstrained-header', 'struct H<T>{x:T}impl<T,U:Copy>H<T>{}', 'coherence', 'E0207'),
    ('header-needs-sized-layout', 'struct H<T>{x:T}impl<T:?Sized>H<T>{}', 'canonical Sized', 'E0277'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_invalid_impl_headers_calls_and_unused_bodies_fail_in_model(name, text, message, code):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


REFERENCE_INVALID = (
    ('immutable-impl-receiver', 'trait Step{fn step(&mut self);}struct H<T>{x:T}impl<T:Step>H<T>{fn bad(&self){self.x.step();}}', '', 'E0596'),
    ('moved-impl-field', 'trait Read{fn read(&self)->u64;}struct H<T>{x:T}impl<T:Read>H<T>{fn bad(self){let moved=self.x;self.x.read();}}', '', 'E0382'),
)
CAPABILITY = (
    ('generic-trait-impl', 'trait A{}trait Ops{}struct H<T>{x:T}impl<T:A>Ops for H<T>{}', 'blanket/generic'),
    ('specialized-owner', 'struct H<T>{x:T}impl H<u64>{fn f(self)->u64{self.x}}', 'coherence'),
    ('repeated-owner', 'struct H<X,Y>{x:X,y:Y}impl<T:Copy>H<T,T>{fn f(self)->T{self.x}}', 'coherence'),
    ('self-predicate', 'struct H{x:u64}impl H where Self:Sized{}', 'where Self predicates'),
)


@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_remaining_impl_and_coherence_capabilities_are_explicit(name, text, message):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_actual_valid_unsupported_impl_shapes_are_separate(name, text, message, tmp_path):
    checked = certify_source(text, tmp_path / name)
    with pytest.raises(RustError, match=message):
        compile_rust(text, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_inherent_impl_originals_match_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_native_marker_originals_match_rustc(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_invalid_inherent_impl_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID + REFERENCE_INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
