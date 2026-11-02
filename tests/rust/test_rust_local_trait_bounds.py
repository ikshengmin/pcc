"""Finite free-function local bounds, checked before concrete instantiation.

https://doc.rust-lang.org/reference/trait-bounds.html#bound-satisfaction
https://doc.rust-lang.org/reference/expressions/method-call-expr.html
The abstract trait choice survives replay even when the concrete type has a
same-named inherent method. No blanket impl, arbitrary solver, DST or owned
borrow-checker claim; CI must execute the unchanged-source rustc oracles below.
"""
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import actual_check_prerequisite, certify_source

PROGRAMS = ('local_bound_dispatch', 'local_bound_mutable', 'local_bound_supertraits',
            'local_bound_method_inference', 'local_bound_marker_shadow', 'local_bound_qualified')
PRIOR_CAPABILITIES = (
    ('local-copy', 'trait Copy{}struct R{x:u64}impl Copy for R{}fn f<T:Copy>(x:T)->T{x}'),
    ('local-sized', 'trait Sized{}fn f<T:Sized>(){}'),
    ('local-free-bound', 'trait T{} fn f<U:T>(x:U){}'),
)


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def test_abstract_dispatch_is_pinned_per_declaration_and_source_call_not_concrete_name():
    # Value-only model: no non-certifying mutable-reference analysis is used.
    text = ('trait Read{fn read(self)->u64;}struct R{x:u64}struct S{x:u64}'
            'impl Read for R{fn read(self)->u64{self.x}}'
            'impl Read for S{fn read(self)->u64{self.x}}'
            'impl R{fn read(self)->u64{900}}'
            'fn bound<T:Read>(x:T)->u64{x.read()}'
            'fn explicit<T:Read>(x:T)->u64{<T as Read>::read(x)}'
            'fn f()->u64{bound(R{x:42})+bound(S{x:43})+explicit(R{x:42})+R{x:0}.read()}')
    checker = analyze_source(text)
    registry = checker.function_generics
    assert set(name for name, calls in registry.bound_calls.items() if calls) == {'bound', 'explicit'}
    for name in ('bound', 'explicit'):
        calls = registry.bound_calls[name]
        assert len(calls) == 1
        call_id, (trait, owner, method) = next(iter(calls.items()))
        assert type(call_id) is int and call_id > 0
        assert (trait, method) == ('Read', 'read')
        assert owner == registry.parameters[name][0]
        assert registry.raw_bodies[name].children[-1].source_call_id == call_id
    instances = [entry.value for entry in registry.instances.entries.values()]
    assert len(instances) == 3
    assert len({f.identity for f in instances}) == 3
    assert {(f.declaration_name, f.instance_arguments) for f in instances} == {
        ('bound', ('R',)), ('bound', ('S',)), ('explicit', ('R',))}
    for function in instances:
        call = function.body.children[-1]
        assert call.binding.declaration_name == f'<{function.instance_arguments[0]} as Read>::read'
        assert call.binding.bound_trait is None
    direct = checker.names['f'].body.children[-1].children[-1]
    assert direct.binding.declaration_name == 'R::read'


def test_diamond_obligations_retain_declaring_trait_without_duplicate_candidates():
    text = ('trait Root{fn value(self)->u64;}trait Left:Root{}trait Right:Root{}'
            'trait Leaf:Left+Right{}fn forward<T:Leaf>(x:T)->u64{x.value()}')
    checker = analyze_source(text)
    parameter = checker.function_generics.parameters['forward'][0]
    assert checker.traits.parameter_bounds[parameter] == frozenset({'Root', 'Left', 'Right', 'Leaf'})
    assert [selection[0] for selection in checker.function_generics.bound_calls['forward'].values()] == ['Root']
    assert len(checker.function_generics.instances) == 0


def test_local_marker_names_never_supply_standard_copy():
    checker = analyze_source(PRIOR_CAPABILITIES[0][1])
    parameter = checker.function_generics.parameters['f'][0]
    assert checker.traits.parameter_bounds[parameter] == frozenset({'Copy'})
    assert not checker.copy_type(parameter) and not checker.copy_type('R')


@pytest.mark.parametrize('name', PROGRAMS)
def test_public_local_bound_originals_need_actual_source_token(name, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('uncertified local bounds reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(source(name), experimental_rustc_checked=True)


def test_unused_value_only_local_bound_is_gated_before_scheduling(monkeypatch):
    from pcc.frontends.rust.semantics import Checker
    monkeypatch.setattr(Checker, 'check', lambda *a: pytest.fail('uncertified local bounds entered scheduling'))
    with pytest.raises(RustError, match='local trait bounds require an actual'):
        compile_rust('trait Read{}fn unused<T:Read>(x:T)->T{x}', experimental_rustc_checked=True)


def checked_owned(text, directory, monkeypatch):
    checked = certify_source(text, directory)
    from pcc.frontends.rust.moves import MoveChecker
    def forbidden(*args, **kwargs):
        pytest.fail('external process or partial borrow admission in the owned phase')
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(MoveChecker, 'check', forbidden)
        patch.setattr(MoveChecker, 'check_function', forbidden)
        result = compile_rust(text, rustc_check=checked)
        assert result.emit_object().startswith(b'\x7fELF')
    assert result.externally_checked and not result.borrow_checked_by_pcc
    assert (directory / 'original.rs').read_text() == text
    return result


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text', PRIOR_CAPABILITIES)
def test_exact_former_local_bound_controls_are_checked_positives(name, text, tmp_path, monkeypatch):
    checked_owned(text, tmp_path / name, monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', PROGRAMS)
def test_actual_same_input_local_bound_check_precedes_owned_object_phase(name, tmp_path, monkeypatch):
    checked_owned(source(name), tmp_path / name, monkeypatch)


INVALID = (
    ('missing-impl', 'trait Read{fn read(self)->u64;}struct R{x:u64}fn invoke<T:Read>(x:T)->u64{x.read()}fn f()->u64{invoke(R{x:42})}', 'unsatisfied local trait bound', 'E0277'),
    ('unused-forward', 'trait Read{}fn need<T:Read>(x:T)->T{x}fn unused<U>(x:U)->U{need(x)}', 'unsatisfied local trait bound', 'E0277'),
    ('wrong-bound-method', 'trait A{}trait B{fn read(self)->u64;}fn unused<T:A>(x:T)->u64{x.read()}', 'unknown inherent or concrete trait method', 'E0599'),
    ('ambiguous', 'trait A{fn read(self)->u64;}trait B{fn read(self)->u64;}fn unused<T:A+B>(x:T)->u64{x.read()}', 'ambiguous trait method', 'E0034'),
    ('supertrait-ambiguous', 'trait A{fn read(self)->u64;}trait B:A{fn read(self)->u64;}fn unused<T:B>(x:T)->u64{x.read()}', 'ambiguous trait method', 'E0034'),
    ('type-shadow', 'trait A{}fn unused<A,T:A>(x:T){}', 'shadows', 'E0404'),
    ('qualified-shadow', 'trait A{fn read(self)->u64;}trait B:A{}fn unused<A,T:B>(x:T)->u64{<T as A>::read(x)}', 'qualified path requires', 'E0404'),
    ('qualified-without-bound', 'trait A{fn read(self)->u64;}fn unused<T>(x:T)->u64{<T as A>::read(x)}', 'unsatisfied local trait bound', 'E0277'),
    ('local-copy-not-builtin', 'trait Copy{}fn unused<T:Copy>(x:T)->(T,T){(x,x)}', 'moved', 'E0382'),
    ('unused-method-copy', 'trait Ops{fn pair<U:Copy>(self,u:U)->(U,U);}fn unused<T:Ops,U>(x:T,u:U)->(U,U){x.pair(u)}', 'canonical Copy', 'E0277'),
    ('reverse-forward', 'trait A{}trait B:A{}fn need<T:B>(x:T)->T{x}fn unused<U:A>(x:U)->U{need(x)}', 'unsatisfied local trait bound', 'E0277'),
    ('qualified-inherited-item', 'trait A{fn read(self)->u64;}trait B:A{}fn unused<T:B>(x:T)->u64{<T as B>::read(x)}', 'unknown trait associated method', 'E0576'),
    ('result-obligation', 'trait Make{fn make()->Self;}fn make<T:Make>()->T{T::make()}fn f()->u64{make()}', 'unsatisfied local trait bound', 'E0277'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_unused_bodies_and_forwarded_obligations_are_facts_only_checks(name, text, message, code):
    # These models produce no IR and cannot certify public reference input.
    with pytest.raises(RustError, match=message):
        analyze_source(text)


REFERENCE_INVALID = (
    ('shared-cannot-mutate', 'trait Step{fn step(&mut self);}fn unused<T:Step>(x:&T){x.step()}', '', 'E0596'),
    ('move-not-borrow-bypass', 'trait Read{fn read(&self)->u64;}fn unused<T:Read>(x:T){let moved=x;x.read();}', '', 'E0382'),
)
CAPABILITY = (
    # Exact method-local controls are retained as certified positives in
    # test_rust_inherent_local_bounds and test_rust_trait_local_contracts.
    ('relaxed-layout', 'trait A{fn read(&self)->u64;}fn unused<T:?Sized+A>(x:&T)->u64{x.read()}', 'relaxed local-trait parameter layouts'),
    # Exact free-function where control retained as an actual-certified
    # positive in test_rust_where_bounds; this table contains remaining gaps.
    ('blanket-impl', 'trait A{}struct R<T>{x:T}impl<T> A for R<T>{}', 'blanket/generic'),
)


@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_remaining_solver_and_layout_capabilities_are_explicit(name, text, message):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_valid_unsupported_bound_forms_are_not_rustc_negative_claims(name, text, message, tmp_path):
    checked = certify_source(text, tmp_path / name)
    with pytest.raises(RustError, match=message):
        compile_rust(text, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_local_bound_originals_match_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_native_marker_originals_match_rustc(tmp_path, monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_the_exact_invalid_local_bound_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID + REFERENCE_INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
