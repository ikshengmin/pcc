"""Separate required trait call contracts from weaker impl body environments.

https://doc.rust-lang.org/reference/items/associated-items.html
https://doc.rust-lang.org/error_codes/E0276.html
The exact two former capability controls are retained as checked positives.
Generic impls/where/trait solvers/DST remain excluded. CI must supply genuine
same-input rustc checks; no locally invented marker or borrow authorization.
"""
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.standard_markers import COPY, CLONE, SIZED
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import actual_check_prerequisite, certify_source

PROGRAMS = ('trait_weaker_identity', 'trait_weaker_sized', 'trait_weaker_receivers')
PRIOR_CAPABILITIES = (
    ('weaker-copy', 'struct R{x:u64}trait T{fn f<U:Copy>(u:U)->U;}impl T for R{fn f<V>(u:V)->V{u}}'),
    ('weaker-sized', 'struct R{x:u64}trait T{fn f<U>(u:&U);}impl T for R{fn f<V:?Sized>(u:&V){}}'),
)


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def test_trait_call_environment_does_not_strengthen_weaker_impl_body():
    copy = analyze_source(PRIOR_CAPABILITIES[0][1])
    parameter = copy.function_generics.parameters['<R as T>::f'][0]
    assert copy.standard_markers.constraints[parameter] == frozenset({SIZED})
    assert copy.standard_markers.call_constraints[parameter] == frozenset({COPY, CLONE, SIZED})
    assert not copy.copy_type(parameter)
    sized = analyze_source(PRIOR_CAPABILITIES[1][1])
    parameter = sized.function_generics.parameters['<R as T>::f'][0]
    assert sized.standard_markers.constraints[parameter] == frozenset()
    assert sized.standard_markers.call_constraints[parameter] == frozenset({SIZED})
    assert not sized.standard_markers.proves_sized(parameter)


def test_same_named_inherent_call_does_not_inherit_trait_obligations():
    checker = analyze_source(source('trait_weaker_identity'))
    inherent = checker.function_generics.parameters['R::id'][0]
    trait = checker.function_generics.parameters['<R as Identity>::id'][0]
    assert inherent not in checker.standard_markers.call_constraints
    assert COPY in checker.standard_markers.call_constraints[trait]
    instances = [entry.value for entry in checker.function_generics.instances.entries.values()]
    assert any(f.declaration_name == 'R::id' and f.instance_arguments == ('NonCopy',) for f in instances)
    assert {f.instance_arguments for f in instances if f.declaration_name == '<R as Identity>::empty'} == {('u32',), ('bool',)}
    assert len(instances) == len({f.identity for f in instances})


@pytest.mark.parametrize('name', PROGRAMS)
def test_public_weaker_contract_still_needs_actual_token(name, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('uncertified weaker contract reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(source(name), experimental_rustc_checked=True)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text', PRIOR_CAPABILITIES)
def test_exact_former_weaker_controls_are_checked_positive(name, text, tmp_path, monkeypatch):
    from pcc.frontends.rust.moves import MoveChecker
    import subprocess
    checked = certify_source(text, tmp_path / 'check')
    def forbidden(*args, **kwargs):
        pytest.fail('external process or partial borrow admission in owned phase')
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(MoveChecker, 'check', forbidden)
        patch.setattr(MoveChecker, 'check_function', forbidden)
        result = compile_rust(text, rustc_check=checked)
        assert result.emit_object().startswith(b'\x7fELF')
    assert result.externally_checked and result.standard_marker_contract is not None
    assert (tmp_path / 'check/original.rs').read_text() == text


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', PROGRAMS)
def test_actual_weaker_marker_check_precedes_owned_object_phase(name, tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_same_input_marker_check_precedes_owned_phase(name, tmp_path, monkeypatch)


INVALID = (
    ('dot-copy-contract', 'struct R{x:u64}struct N{x:u64}trait T{fn f<U:Copy>(self,u:U)->U;}impl T for R{fn f<V>(self,v:V)->V{v}}fn f()->N{R{x:0}.f(N{x:1})}', 'canonical Copy', 'E0277'),
    ('type-copy-contract', 'struct R{x:u64}struct N{x:u64}trait T{fn f<U:Copy>(self,u:U)->U;}impl T for R{fn f<V>(self,v:V)->V{v}}fn f()->N{R::f(R{x:0},N{x:1})}', 'canonical Copy', 'E0277'),
    ('qualified-copy-contract', 'struct R{x:u64}struct N{x:u64}trait T{fn f<U:Copy>(self,u:U)->U;}impl T for R{fn f<V>(self,v:V)->V{v}}fn f()->N{<R as T>::f::<N>(R{x:0},N{x:1})}', 'canonical Copy', 'E0277'),
    ('unused-forward-copy', 'struct R{x:u64}trait T{fn f<U:Copy>(u:U)->U;}impl T for R{fn f<V>(v:V)->V{v}}fn unused<W>(w:W)->W{R::f(w)}', 'canonical Copy', 'E0277'),
    ('unused-forward-sized', 'struct R{x:u64}trait T{fn f<U>(u:&U);}impl T for R{fn f<V:?Sized>(v:&V){}}fn unused<W:?Sized>(w:&W){R::f(w)}', 'canonical Sized', 'E0277'),
    ('body-does-not-inherit-copy', 'fn need<U:Copy>(u:U)->U{u}struct R{x:u64}trait T{fn f<U:Copy>(u:U)->U;}impl T for R{fn f<V>(v:V)->V{need(v)}}', 'canonical Copy', 'E0277'),
    ('body-does-not-inherit-sized', 'fn need<U>(u:&U){}struct R{x:u64}trait T{fn f<U>(u:&U);}impl T for R{fn f<V:?Sized>(v:&V){need(v)}}', 'canonical Sized', 'E0277'),
    ('unused-body-move', 'struct R{x:u64}trait T{fn f<U:Copy>(u:U)->(U,U);}impl T for R{fn f<V>(v:V)->(V,V){(v,v)}}', 'moved', 'E0382'),
    ('stronger-still-rejected', 'struct R{x:u64}trait T{fn f<U>(u:U)->U;}impl T for R{fn f<V:Copy>(v:V)->V{v}}', 'bounds are stricter', 'E0276'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_required_calls_and_declared_body_obligations_stay_distinct(name, text, message, code):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_weaker_contract_originals_match_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_native_marker_originals_match_rustc(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_weaker_contract_negative_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
