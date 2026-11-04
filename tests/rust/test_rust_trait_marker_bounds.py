"""Canonical bounds on required static-trait methods, not a trait solver.

https://doc.rust-lang.org/reference/items/associated-items.html#associated-functions-and-methods
https://doc.rust-lang.org/error_codes/E0276.html
https://doc.rust-lang.org/reference/trait-bounds.html#sized
Required canonical call contracts remain separate from impl body assumptions;
Generic impls and DST remain gaps; default bodies and where contracts have
separate tests.
The analysis helper produces facts only. Every public marker/reference positive
needs a genuine source/options-bound rustc token before owned code generation.
"""
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.standard_markers import COPY, CLONE, SIZED
from pcc.frontends.rust.types import TypeParameter
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import actual_check_prerequisite, certify_source

PROGRAMS = ('trait_marker_receivers', 'trait_marker_identity', 'trait_marker_forward_result',
            'trait_marker_shadow', 'trait_marker_concrete_owner')
# Both exact formerly unsupported originals are retained as new checked positives.
PRIOR_CAPABILITIES = ('trait T{fn take<U:Copy>(&self,u:U)->U;}',
                      'trait T{fn f<U:Copy>(self,x:U);}')


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def test_trait_and_impl_constraints_are_alpha_equivalent_not_source_spelling():
    # Separate nonborrowing model original: the unchanged runtime source has
    # local/mutable references and belongs only to the actual-certified lane.
    checker = analyze_source(
        'struct Counter{x:u64}trait Observe{fn pair<U:Copy>(u:U)->(U,U);'
        'fn restored<U:Copy>(u:U)->U;}'
        'impl Observe for Counter{fn pair<V:core::marker::Copy>(v:V)->(V,V){(v,v)}'
        'fn restored<V:?Sized+std::marker::Copy>(v:V)->V{v}}')
    constraints = checker.standard_markers.constraints
    expected = frozenset({COPY, CLONE, SIZED})
    for name in ('pair', 'restored'):
        declaration = checker.traits.method_parameters['Observe', name]
        implementation = checker.function_generics.parameters['<Counter as Observe>::' + name]
        assert declaration != implementation
        assert [constraints[p] for p in declaration] == [expected]
        assert [constraints[p] for p in implementation] == [expected]
    assert TypeParameter('trait Observe', 0, 'Self') not in constraints


def test_trait_identity_and_inherent_identity_share_but_do_not_collapse_registry():
    checker = analyze_source(
        'struct R{x:u64}trait First{fn tag<U:Copy>(self,u:U)->u64;}'
        'trait Second{fn tag<U:Copy>(self,u:U)->u64;}'
        'impl First for R{fn tag<V:Copy>(self,v:V)->u64{41}}'
        'impl Second for R{fn tag<V:Copy>(self,v:V)->u64{42}}'
        'impl R{fn tag<U:Copy>(self,u:U)->u64{900}}'
        'fn f()->u64{<R as First>::tag(R{x:0},1u64)+'
        '<R as Second>::tag::<u64>(R{x:0},1)+R{x:0}.tag(true)}')
    entries = tuple(checker.function_generics.instances.entries.values())
    names = {entry.value.declaration_name for entry in entries}
    assert names == {'<R as First>::tag', '<R as Second>::tag', 'R::tag'}
    assert len(entries) == len({entry.value.identity for entry in entries}) == 3
    # The full identity original separately observes receiver candidate order
    # under real certification, not this nonlowering facts-only model.


def test_result_inference_and_relaxed_forwarding_keep_the_same_instance_worklist():
    checker = analyze_source(
        'struct Factory{x:u64}trait Build{fn empty<U:Copy>()->[U;0];'
        'fn inspect<U:?Sized>(u:&U)->u64;fn relaxed<U:?Sized>(u:&U)->u64;'
        'fn restored<U:?Sized+Copy>(u:U)->U;}'
        'impl Build for Factory{fn empty<V:Copy>()->[V;0]{[]}'
        'fn inspect<V:?Sized>(u:&V)->u64{42}'
        'fn relaxed<V:?Sized>(u:&V)->u64{Self::inspect(u)}'
        'fn restored<V:Copy>(v:V)->V{v}}'
        'fn f(){let mut late=Factory::empty();late=[0u32;0];'
        'let result:[bool;0]=Factory::empty();}')
    instances = [entry.value for entry in checker.function_generics.instances.entries.values()]
    assert {f.instance_arguments for f in instances if f.declaration_name == '<Factory as Build>::empty'} == {('u32',), ('bool',)}
    assert checker.standard_markers.constraints[checker.traits.method_parameters['Build', 'inspect'][0]] == frozenset()
    assert SIZED in checker.standard_markers.constraints[checker.traits.method_parameters['Build', 'restored'][0]]


def test_trait_self_does_not_silently_gain_sized_from_method_parameter_defaults():
    with pytest.raises(RustError, match='nested trait Self'):
        analyze_source('trait T{fn f<U:Copy>()->[Self;1];}')


@pytest.mark.parametrize('name', PROGRAMS)
def test_trait_marker_public_admission_still_requires_actual_check(name, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('uncertified trait marker reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(source(name), experimental_rustc_checked=True)


def test_unused_value_only_trait_signature_alone_requires_genuine_marker_context(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unused marker signature reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust('trait T{fn unused<U:Copy>(x:U)->U;}', experimental_rustc_checked=True)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('text', PRIOR_CAPABILITIES)
def test_exact_previous_trait_capability_original_has_checked_positive(text, tmp_path, monkeypatch):
    from pcc.frontends.rust.moves import MoveChecker
    import subprocess
    checked = certify_source(text, tmp_path / 'check')
    def forbidden(*args, **kwargs):
        pytest.fail('external process or partial move admission in owned phase')
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(MoveChecker, 'check', forbidden)
        patch.setattr(MoveChecker, 'check_function', forbidden)
        result = compile_rust(text, rustc_check=checked)
        assert result.emit_object().startswith(b'\x7fELF')
    assert result.externally_checked and result.standard_marker_contract is not None
    assert (tmp_path / 'check/original.rs').read_text() == text


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', PROGRAMS)
def test_actual_trait_marker_check_precedes_owned_object_phase(name, tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_same_input_marker_check_precedes_owned_phase(name, tmp_path, monkeypatch)


INVALID = (
    ('stronger-copy', 'struct R{x:u64}trait T{fn f<U>(u:U)->U;}impl T for R{fn f<U:Copy>(u:U)->U{u}}', 'bounds are stricter', 'E0276'),
    ('stronger-sized', 'struct R{x:u64}trait T{fn f<U:?Sized>(u:&U);}impl T for R{fn f<U>(u:&U){}}', 'bounds are stricter', 'E0276'),
    ('wrong-bound-position', 'struct R{x:u64}trait T{fn f<U:Copy,V>(u:U,v:V);}impl T for R{fn f<U,V:Copy>(u:U,v:V){}}', 'bounds are stricter', 'E0276'),
    ('noncopy-call', 'struct R{x:u64}struct N{x:u64}trait T{fn f<U:Copy>(u:U)->U;}impl T for R{fn f<U:Copy>(u:U)->U{u}}fn f()->N{R::f(N{x:42})}', 'canonical Copy', 'E0277'),
    ('unused-copy-forward', 'struct R{x:u64}trait T{fn f<U:Copy>(&self,u:U)->U;fn unused<V>(&self,v:V)->V;}impl T for R{fn f<U:Copy>(&self,u:U)->U{u}fn unused<V>(&self,v:V)->V{self.f(v)}}', 'canonical Copy', 'E0277'),
    ('unused-sized-forward', 'struct R{x:u64}trait T{fn need<U>(&self);fn unused<V:?Sized>(&self);}impl T for R{fn need<U>(&self){}fn unused<V:?Sized>(&self){self.need::<V>();}}', 'canonical Sized', 'E0277'),
    ('unused-move', 'struct R{x:u64}trait T{fn unused<U:Copy,V>(v:V)->(V,V);}impl T for R{fn unused<U:Copy,V>(v:V)->(V,V){(v,v)}}', 'moved', 'E0382'),
    ('ambiguous', 'struct R{x:u64}trait A{fn f<U:Copy>(&self,u:U);}trait B{fn f<U:Copy>(&self,u:U);}impl A for R{fn f<U:Copy>(&self,u:U){}}impl B for R{fn f<U:Copy>(&self,u:U){}}fn f(){R{x:0}.f(1u64);}', 'ambiguous', 'E0034'),
    ('parameter-shadow', 'trait T{fn f<Copy,U:Copy>();}', 'shadows', 'E0404'),
    ('unused-inference', 'struct R{x:u64}trait T{fn f<U:Copy>(self);}impl T for R{fn f<U:Copy>(self){}}fn f(){R{x:0}.f();}', 'cannot infer', 'E0283'),
    ('unsized-array-declaration', 'trait T{fn f<U:?Sized>(u:&[U;0]);}', 'canonical Sized', 'E0277'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_trait_contract_and_unused_body_errors_are_owned_analysis_not_certification(name, text, message, code):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


# Retain these exact older capability inputs. Local names are ordinary traits,
# not builtin marker permissions, even in unused required method signatures.
PRIOR_LOCAL_CAPABILITIES = (
    ('later-local-copy', 'trait T{fn f<U:Copy>(u:U)->U;}trait Copy{}', 'T', 'Copy'),
    ('current-local-copy', 'trait Copy{fn f<U:Copy>(u:U)->U;}', 'Copy', 'Copy'),
    ('local-sized', 'trait T{fn f<U:Sized>();}trait Sized{}', 'T', 'Sized'),
)


@pytest.mark.parametrize('name,text,trait,bound', PRIOR_LOCAL_CAPABILITIES)
def test_local_names_in_old_required_signatures_keep_local_canonical_identity(name, text, trait, bound):
    checker = analyze_source(text)
    parameter, = checker.traits.method_parameters[trait, 'f']
    assert checker.traits.parameter_bounds[parameter] == frozenset({bound})
    assert checker.standard_markers.constraints[parameter] == frozenset({SIZED})
    assert not checker.copy_type(parameter)


@pytest.mark.parametrize('name,text,trait,bound', PRIOR_LOCAL_CAPABILITIES)
def test_old_local_signature_controls_need_actual_token_before_sema(name, text, trait, bound, monkeypatch):
    from pcc.frontends.rust.semantics import Checker
    monkeypatch.setattr(Checker, 'check', lambda *a: pytest.fail('uncertified local signature reached Sema'))
    with pytest.raises(RustError, match='local trait bounds require an actual'):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,trait,bound', PRIOR_LOCAL_CAPABILITIES)
def test_exact_old_local_signature_controls_are_actual_checked_positives(name, text, trait, bound, tmp_path, monkeypatch):
    from tests.rust.test_rust_local_trait_bounds import checked_owned
    # Actual check precedes the helper's forbidden-Popen/MoveChecker owned phase.
    checked_owned(text, tmp_path / name, monkeypatch)


CAPABILITY = (
    # Exact local Copy/Sized inputs are certified positives below.
    ('clone', 'trait T{fn f<U:Clone>(u:U)->U;}', 'only canonical'),
    # Exact trait-method where originals now checked in test_rust_trait_where_contracts.
    # Exact default-body original is retained as a noncertifying model and
    # an actual-checked positive in test_rust_default_methods.
    ('generic-impl', 'struct R<X>{x:X}trait T{fn f<U:Copy>(u:U)->U;}impl<X>T for R<X>{fn f<U:Copy>(u:U)->U{u}}', 'blanket/generic'),
    ('dst', 'struct R{x:u64}trait T{fn f<U:?Sized>(&self,u:&U);}impl T for R{fn f<U:?Sized>(&self,u:&U){}}fn f(s:&[u64]){R{x:0}.f(s)}', 'slice types'),
)


@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_excluded_trait_contexts_fail_in_analysis_without_lowering(name, text, message):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_actual_valid_excluded_contexts_are_capability_gaps_not_invalid_rust(name, text, message, tmp_path, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    checked = certify_source(text, tmp_path / 'check')
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('excluded trait context reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_trait_marker_originals_match_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_native_marker_originals_match_rustc(tmp_path, monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_the_exact_invalid_trait_marker_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
