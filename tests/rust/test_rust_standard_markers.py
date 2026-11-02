"""Finite standard marker contract; not a loaded core crate or owned borrowck.

https://doc.rust-lang.org/reference/trait-bounds.html#sized
https://doc.rust-lang.org/std/marker/trait.Copy.html
Actual stable rustc version/options are captured by the unchanged check adapter.
"""
from hashlib import sha256
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.standard_markers import COPY, CLONE, SIZED, CONTRACT_VERSION
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import actual_check_prerequisite, certify_source

PROGRAMS = ('standard_copy_values', 'standard_copy_forwarding', 'standard_marker_shadow',
            'standard_marker_reference', 'standard_marker_module')


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def test_canonical_contract_and_transitive_obligations_are_not_local_names():
    text = ('fn a<T:Copy>(x:T)->(T,T){(x,x)}'
            'fn b<U:core::marker::Copy>(x:U)->U{x}'
            'fn c<V:std::marker::Copy>(x:V)->V{x}')
    checker = analyze_source(text)
    constraints = list(checker.standard_markers.constraints.values())
    assert constraints == [frozenset({COPY, CLONE, SIZED})] * 3
    assert COPY.defining_crate == SIZED.defining_crate == 'core'
    assert COPY.contract_version == SIZED.contract_version == CONTRACT_VERSION
    assert checker.copy_type('Copy') is False


def test_implicit_sized_and_relaxation_survive_restored_scopes():
    checker = analyze_source('fn a<T>(){}fn b<U:?Sized>(){}fn c<V:?Sized+Copy>(x:V)->V{x}')
    constraints = checker.standard_markers.constraints
    assert checker.type_scope == {}
    assert [constraints[p] for p in checker.function_generics.parameters['a']] == [frozenset({SIZED})]
    assert [constraints[p] for p in checker.function_generics.parameters['b']] == [frozenset()]
    parameter = checker.function_generics.parameters['c'][0]
    assert constraints[parameter] == frozenset({COPY, CLONE, SIZED})
    assert checker.copy_type(parameter)


def test_argument_and_result_inference_share_the_existing_instance_registry():
    checker = analyze_source(source('standard_copy_forwarding'))
    # Concrete instances are exposed in the existing checked functions vector;
    # no marker-specific instantiation cache or special lowering is introduced.
    instances = [f for f in checker.functions if f.instance_arguments is not None]
    assert len({f.identity for f in instances}) == len(instances)
    assert any(f.declaration_name == 'empty' and f.instance_arguments == ('bool',) for f in instances)
    assert any(f.declaration_name == 'twice' and f.instance_arguments == ('u64',) for f in instances)


@pytest.mark.parametrize('name', PROGRAMS)
def test_even_value_only_explicit_marker_contract_requires_actual_check(name, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('uncertified marker contract reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(source(name), experimental_rustc_checked=True)


def test_mutable_reference_is_not_copy_and_shared_reference_is_copy_in_the_model():
    from pcc.frontends.rust.types import SharedReference
    checker = analyze_source('fn f<T:Copy>(x:T)->T{x}')
    assert checker.copy_type(SharedReference('u64'))
    assert not checker.copy_type(SharedReference('u64', True))


def test_marker_context_descriptor_is_not_an_authorization_token(monkeypatch):
    from pcc.frontends.rust.standard_markers import CheckedMarkerContext
    from pcc.frontends.rust.lowering import Lowerer
    descriptor = CheckedMarkerContext(CONTRACT_VERSION, '0' * 64, '0' * 64,
                                      'rustc fake', '2021', 'x86_64-unknown-linux-gnu', 'std')
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('descriptor authorized IR'))
    with pytest.raises(RustError, match='caller-constructed external record'):
        compile_rust('fn f<T:Copy>(x:T)->T{x}', rustc_check=descriptor)


def test_generic_parameter_cannot_shadow_a_qualified_library_root():
    with pytest.raises(RustError, match='shadows'):
        analyze_source('fn f<core,T:core::marker::Copy>(){}')


INVALID = (
    ('copy-call', 'struct R{x:u64}fn take<T:Copy>(x:T)->T{x}fn f()->R{take(R{x:42})}', 'canonical Copy', 'E0277'),
    ('copy-forward', 'fn take<T:Copy>(x:T)->T{x}fn unused<U>(x:U)->U{take(x)}', 'canonical Copy', 'E0277'),
    ('sized-forward', 'fn take<T>(){}fn unused<U:?Sized>(){take::<U>();}', 'canonical Sized', 'E0277'),
    ('sized-parameter', 'fn unused<T:?Sized>(x:T){}', 'canonical Sized', 'E0277'),
    ('sized-result', 'fn unused<T:?Sized>()->T{unused()}', 'canonical Sized', 'E0277'),
    ('sized-array', 'fn unused<T:?Sized>(x:&[T;0]){}', 'canonical Sized', 'E0277'),
    ('sized-data', 'struct R<T>{x:T}fn unused<T:?Sized>(x:&R<T>){}', 'canonical Sized', 'E0277'),
    ('unused-move', 'fn unused<T:Copy,U>(x:U){let first=x;let second=x;}', 'moved', 'E0382'),
    ('type-shadow', 'fn unused<Copy,T:Copy>(){}', 'shadows', 'E0404'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_abstract_body_and_forwarded_obligations_are_checked_without_lowering(name,text,message,code):
    # Facts-only analysis exercises the owned constraint model, never bypasses
    # the public reference/marker admission requirement or produces IR.
    with pytest.raises(RustError, match=message):
        analyze_source(text)


CAPABILITY = (
    # Exact former local-copy/local-sized controls are checked positives in
    # test_rust_local_trait_bounds; neither spelling becomes a builtin marker.
    ('module-core', 'mod core{pub mod marker{pub struct Copy{pub x:u64}}}fn f<T:core::marker::Copy>(){}', 'local/user trait'),
    ('clone', 'fn f<T:Clone>(x:T)->T{x}', 'only canonical'),
    ('nostd-std', '#![no_std]fn f<T:std::marker::Copy>(x:T)->T{x}', 'unavailable'),
    ('local-relaxed', 'trait Sized{}fn f<T:?Sized>(){}', 'local/user trait'),
    ('data', 'struct R<T:Copy>{x:T}', 'bounds'),
    # Exact impl control is a checked positive in test_rust_inherent_impl_bounds.
    # Exact free-function where control retained in test_rust_where_bounds.
    ('dst', 'fn f<T:?Sized>(x:&T){}fn g(x:&[u64]){f(x)}', 'slice types'),
    ('no-core', '#![no_core]fn f<T:Copy>(x:T)->T{x}', 'no_core requires'),
    ('lang', '#[lang="copy"]trait Copy{}', 'unsupported Rust attribute'),
    ('lifetime-bound', "fn f<T,'a:'static>(x:T)->T{x}", 'bounds'),
)


@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_excluded_contexts_and_local_names_never_activate_builtin_contract(name,text,message,monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unsupported context reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', PROGRAMS)
def test_actual_same_input_marker_check_precedes_owned_phase(name,tmp_path,monkeypatch):
    text = source(name)
    checked = certify_source(text, tmp_path / 'check')
    from pcc.frontends.rust.moves import MoveChecker
    def forbidden(*args, **kwargs):
        pytest.fail('external process/partial borrow admission in owned phase')
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(MoveChecker, 'check', forbidden)
        patch.setattr(MoveChecker, 'check_function', forbidden)
        result = compile_rust(text, rustc_check=checked)
        assert result.emit_object().startswith(b'\x7fELF')
    context = result.standard_marker_contract
    assert context.contract_version == CONTRACT_VERSION
    assert context.source_sha256 == sha256(text.encode()).hexdigest()
    assert context.compiler_sha256 == checked.provenance.compiler_sha256
    assert context.compiler_version == checked.provenance.compiler_version
    assert context.edition == '2021' and context.target_triple == 'x86_64-unknown-linux-gnu'
    assert context.implicit_library == ('core' if text.startswith('#![no_std]') else 'std')
    assert result.externally_checked and not result.borrow_checked_by_pcc


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_valid_local_trait_and_dst_sources_remain_capability_gaps_not_rust_rejections(tmp_path):
    for name,text,message in CAPABILITY:
        if name not in {'clone', 'data', 'impl', 'dst'}:
            continue
        checked = certify_source(text, tmp_path / name)
        with pytest.raises(RustError, match=message):
            compile_rust(text, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64','AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_marker_originals_match_rustc(tmp_path,monkeypatch):
    from tests.rust import test_rust_method_reference as reference, test_rust_patterns as patterns
    native = reference._native
    def checked_native(name, directory, patcher):
        checked = certify_source(source(name), directory / 'standard-marker-check')
        def compile_checked(text, **options):
            options['rustc_check'] = checked
            return compile_rust(text, **options)
        with patcher.context() as patch:
            patch.setattr(patterns, 'compile_source', compile_checked)
            native(name, directory, patch)
    monkeypatch.setattr(reference, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(reference, '_native', checked_native)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path,monkeypatch)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_invalid_bound_originals(tmp_path,monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    # Reuse the existing strict three-command/status/raw-diagnostic witness;
    # infrastructure errors and timeouts cannot qualify as Rust rejections.
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path,monkeypatch)
