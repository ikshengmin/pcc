"""Method-local canonical marker obligations reuse the fixed-owner registry.

https://doc.rust-lang.org/reference/items/associated-items.html
https://doc.rust-lang.org/reference/expressions/method-call-expr.html
https://doc.rust-lang.org/reference/trait-bounds.html#sized
No impl/data/where bounds, DST layout or owned borrowck claim.
"""
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.standard_markers import COPY, SIZED
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import actual_check_prerequisite, certify_source

PROGRAMS = ('method_marker_receivers', 'method_marker_owner_prefix',
            'method_marker_forward_result', 'method_marker_shadow', 'method_marker_explicit_sized')
# Exact prior valid-but-unsupported original from the free-function slice.
PRIOR_CAPABILITY = 'struct R{x:u64}impl R{fn f<T:Copy>(x:T)->T{x}}'


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def test_owner_prefix_is_not_strengthened_by_copy_method_suffix():
    checker = analyze_source(source('method_marker_owner_prefix'))
    params = checker.function_generics.parameters['Owner::pair']
    assert len(params) == 2
    assert checker.standard_markers.constraints[params[0]] == frozenset({SIZED})
    assert COPY in checker.standard_markers.constraints[params[1]]
    assert not checker.copy_type(params[0]) and checker.copy_type(params[1])
    entries = list(checker.function_generics.instances.entries.values())
    assert len(entries) == 2
    assert {entry.value.instance_arguments for entry in entries} == {('Payload', 'u64'), ('Payload', 'bool')}
    assert len({entry.value.identity for entry in entries}) == 2


def test_bounded_method_still_checks_unused_abstract_body_before_instances():
    text = ('struct R{x:u64}impl R{fn bad<U:Copy,V>(self,value:V)->(V,V){(value,value)}}'
            'fn f(){R{x:1}.bad::<bool,u64>(42);}')
    with pytest.raises(RustError, match='moved'):
        analyze_source(text)


@pytest.mark.parametrize('name', PROGRAMS)
def test_method_bounds_do_not_turn_public_flag_into_admission(name,monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('uncertified method bounds reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(source(name), experimental_rustc_checked=True)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_exact_previous_capability_original_now_has_actual_checked_positive(tmp_path,monkeypatch):
    checked = certify_source(PRIOR_CAPABILITY, tmp_path / 'check')
    from pcc.frontends.rust.moves import MoveChecker
    monkeypatch.setattr(MoveChecker, 'check', lambda *a: pytest.fail('partial move gate used'))
    monkeypatch.setattr(MoveChecker, 'check_function', lambda *a: pytest.fail('partial abstract move gate used'))
    result = compile_rust(PRIOR_CAPABILITY, rustc_check=checked)
    assert result.externally_checked and result.standard_marker_contract is not None
    assert (tmp_path / 'check/original.rs').read_text() == PRIOR_CAPABILITY


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', PROGRAMS)
def test_actual_method_marker_certification_precedes_owned_object_phase(name,tmp_path,monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_same_input_marker_check_precedes_owned_phase(name,tmp_path,monkeypatch)


INVALID = (
    ('method-copy-call', 'struct R{x:u64}struct N{x:u64}impl R{fn take<U:Copy>(self,u:U)->U{u}}fn f()->N{R{x:0}.take(N{x:42})}', 'canonical Copy', 'E0277'),
    ('method-copy-forward', 'struct R{x:u64}impl R{fn take<U:Copy>(&self,u:U)->U{u}fn unused<V>(&self,v:V)->V{self.take(v)}}', 'canonical Copy', 'E0277'),
    ('method-implicit-sized-forward', 'struct R{x:u64}impl R{fn need<U>(&self){}fn unused<V:?Sized>(&self){self.need::<V>();}}', 'canonical Sized', 'E0277'),
    ('method-explicit-sized-forward', 'struct R{x:u64}impl R{fn need<U:Sized>(&self){}fn unused<V:?Sized>(&self){self.need::<V>();}}', 'canonical Sized', 'E0277'),
    ('method-unsized-parameter', 'struct R{x:u64}impl R{fn unused<U:?Sized>(self,u:U){}}', 'canonical Sized', 'E0277'),
    ('method-unsized-array', 'struct R{x:u64}impl R{fn unused<U:?Sized>(&self,u:&[U;0]){}}', 'canonical Sized', 'E0277'),
    ('owner-not-copy', 'struct R<T>{x:T}impl<T>R<T>{fn unused<U:Copy>(&self)->T{self.x}}', 'non-Copy', 'E0507'),
    ('method-unused-move', 'struct R{x:u64}impl R{fn unused<U:Copy,V>(self,v:V)->(V,V){(v,v)}}', 'moved', 'E0382'),
    ('method-shadow', 'struct R{x:u64}impl R{fn unused<Copy,U:Copy>(self){}}', 'shadows', 'E0404'),
    ('trait-impl-extra-bound', 'struct R{x:u64}trait T{fn f<U>(u:U)->U;}impl T for R{fn f<U:Copy>(u:U)->U{u}}', 'bounds', 'E0276'),
    ('method-unused-inference', 'struct R{x:u64}impl R{fn unused<U:Copy>(self){}}fn f(){R{x:0}.unused();}', 'cannot infer', 'E0283'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_method_abstract_and_call_obligations_fail_without_lowering(name,text,message,code):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


CAPABILITY = (
    # Exact local-copy and method-where controls are now certified-positive
    # CI inputs in test_rust_inherent_local_bounds, not capability rejections.
    ('impl-bound', 'struct R<T>{x:T}impl<T:Copy> R<T>{}', 'bounds'),
    ('data-bound', 'struct R<T:Copy>{x:T}', 'bounds'),
    ('clone', 'struct R{x:u64}impl R{fn take<U:Clone>(self,u:U)->U{u}}', 'only canonical'),
    ('dst', 'struct R{x:u64}impl R{fn inspect<U:?Sized>(&self,u:&U){}}fn f(s:&[u64]){R{x:0}.inspect(s)}', 'slice types'),
)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_valid_excluded_method_contexts_are_actual_positive_owned_gaps(name,text,message,tmp_path,monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    checked = certify_source(text, tmp_path / 'check')
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('excluded method context reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64','AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_method_marker_originals_match_rustc(tmp_path,monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_native_marker_originals_match_rustc(tmp_path,monkeypatch)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_invalid_method_bound_originals(tmp_path,monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path,monkeypatch)
