"""Concrete associated types with actual same-input rustc admission.

Rust Reference, consulted 2026-11-04:
https://doc.rust-lang.org/reference/items/associated-items.html#associated-types
https://doc.rust-lang.org/reference/trait-bounds.html#sized
https://doc.rust-lang.org/reference/paths.html#qualified-paths
https://doc.rust-lang.org/error_codes/E0221.html
These authored obligations are unrun; no project or execution pass is implied.
"""
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.attribute_metadata import offset
from pcc.frontends.rust.standard_markers import COPY, CLONE, SIZED
from pcc.frontends.rust.syntax import Parser
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import actual_check_prerequisite, certify_source


PROGRAMS = (
    'associated_type_values', 'associated_type_generic_method',
    'associated_type_inherited', 'associated_type_concrete_owners',
    'associated_type_relaxed', 'associated_type_self',
    'associated_type_trait_identity', 'associated_type_relaxed_return',
    'associated_type_relaxed_parameter', 'associated_type_hierarchy_names',
    'associated_type_diamond',
)
FORMER_CAPABILITY = 'trait T{type Item;}'
# The review counterexample is retained verbatim, without adding a Sized bound
# or rewriting the required return type. Both originals enter the same-input
# reference/object obligations below as well as this non-certifying model.
DIRECT_REQUIRED = (
    ('return', 'trait T { type Item: ?Sized; fn f(self) -> Self::Item; } struct R { x: u64 } impl T for R { type Item = u64; fn f(self) -> Self::Item { self.x } } fn g() -> u64 { R { x: 42 }.f() }'),
    ('parameter', 'trait T { type Item: ?Sized; fn f(self, item: Self::Item) -> u64; } struct R { x: u64 } impl T for R { type Item = u64; fn f(self, item: Self::Item) -> u64 { self.x + item } } fn g() -> u64 { R { x: 20 }.f(22u64) }'),
)
RECURSIVE_GENERIC_RESULT = 'trait T{type Item:?Sized;fn f<U:?Sized>(self)->U;}struct R{x:u64}impl T for R{type Item=u64;fn f<U:?Sized>(self)->U{self.f::<U>()}}'
# The exact former parser control now has a parsed loop body. Retain its
# expected E0277 obligation, but require the owned impl-result Sized boundary.
LOOP_SIZED_CONTROLS = (
    ('relaxed-impl-generic-result-loop', 'trait T{type Item:?Sized;fn f<U:?Sized>(self)->U;}struct R{x:u64}impl T for R{type Item=u64;fn f<U:?Sized>(self)->U{loop{}}}', 'canonical Sized', 'E0277'),
)


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


POSITIVE_ORIGINALS = (FORMER_CAPABILITY, *(text for _, text in DIRECT_REQUIRED),
                      *(source(name) for name in PROGRAMS))


def test_associated_syntax_preserves_bounds_docs_and_impl_identity():
    text = ('trait T{/// item\n type Item:?Sized;fn f(self,x:&Self::Item);}'
            'struct R{x:u64}impl T for R{fn f(self,x:&Self::Item){}type Item=u64;}')
    parser = Parser(text)
    functions = parser.parse()
    item = parser.traits[0].associated_types[0]
    assert item.token.text == 'Item' and item.value is None
    assert item.bounds[0].path == 'Sized' and item.bounds[0].relaxed
    assert item.documentation[0].text == ' item'
    implementation = parser.implementations[0]
    assert implementation.associated_types[0].value == 'u64'
    assert functions[0].implementation is implementation
    start = offset(parser, item.token)
    assert text[start:start + len(item.token.text)] == 'Item'


def test_associated_identities_and_bounds_are_declaration_owned():
    checker = analyze_source('trait A{type Item;type Copy:Copy;type Unsized:?Sized;}'
                             'trait B{type Item;}')
    associated = checker.traits.associated
    a = associated.parameters['A', 'Item']
    b = associated.parameters['B', 'Item']
    assert a != b
    assert checker.standard_markers.constraints[a] == frozenset({SIZED})
    assert checker.standard_markers.constraints[associated.parameters['A', 'Copy']] == frozenset({COPY, CLONE, SIZED})
    assert checker.standard_markers.constraints[associated.parameters['A', 'Unsized']] == frozenset()


@pytest.mark.parametrize('name', [name for name in PROGRAMS if name != 'associated_type_relaxed'])
def test_associated_model_normalizes_concrete_signatures_without_new_lowering(name):
    # Facts only: reference-bearing examples need real certification for IR.
    # This model neither emits an object nor pretends to prove borrow legality.
    checker = analyze_source(source(name))
    for function in checker.functions:
        if function.implementation is not None and function.implementation.trait is not None:
            assert 'Self::' not in str(function.result)
            assert all('Self::' not in str(ty) for _, ty, _ in function.params)
    assert not checker.type_scope and not checker.abstract_function


def test_inherited_aliases_resolve_even_when_parent_impl_is_later():
    checker = analyze_source(source('associated_type_inherited'))
    associated = checker.traits.associated
    assert associated.scope('Child', 'R') == {'Self::Item': 'u64'}
    assert checker.names['<R as Child>::second'].result == 'u64'


@pytest.mark.parametrize('position,text', DIRECT_REQUIRED)
def test_relaxed_required_direct_types_wait_for_concrete_impl_layout(position, text):
    checker = analyze_source(text)
    parameter = checker.traits.associated.parameters['T', 'Item']
    assert checker.standard_markers.constraints[parameter] == frozenset()
    _, required = checker.traits.signatures['T', 'f']
    concrete = checker.names['<R as T>::f']
    if position == 'return':
        assert required.result == parameter
        assert len(concrete.params) == 1
    else:
        assert required.params[1][1] == parameter
        assert tuple(ty for _, ty, _ in concrete.params) == ('R', 'u64')
    assert concrete.result == checker.names['g'].result == 'u64'
    assert not checker.type_scope and not checker.abstract_function


def test_inherited_same_names_remain_distinct_without_silent_shadowing():
    checker = analyze_source(source('associated_type_hierarchy_names'))
    associated = checker.traits.associated
    assert associated.scope('Child') == associated.scope('Child', 'R') == {}
    assert associated.scope('Both') == associated.scope('Both', 'R') == {}
    parent = associated.parameters['Parent', 'Item']
    child = associated.parameters['Child', 'Item']
    left = associated.parameters['Left', 'Item']
    right = associated.parameters['Right', 'Item']
    assert len({parent, child, left, right}) == 4
    assert associated.bindings('Child', 'R') == {parent: 'u64', child: 'i64'}
    assert associated.bindings('Both', 'R') == {left: 'u64', right: 'i64'}


def test_diamond_inherits_one_declaration_identity_not_two_paths():
    checker = analyze_source(source('associated_type_diamond'))
    associated = checker.traits.associated
    parameter = associated.parameters['Root', 'Item']
    assert associated.scope('Bottom') == {'Self::Item': parameter}
    assert associated.scope('Bottom', 'R') == {'Self::Item': 'u64'}
    assert associated.bindings('Bottom', 'R') == {parameter: 'u64'}
    _, signature = checker.traits.signatures['Bottom', 'bottom']
    assert signature.result == signature.params[1][1] == parameter
    assert checker.names['<R as Bottom>::bottom'].result == 'u64'


@pytest.mark.parametrize('name,text,message,code', LOOP_SIZED_CONTROLS)
def test_earlier_loop_original_now_reaches_impl_sized_boundary(name, text, message, code):
    function, = Parser(text).parse()
    assert function.body.children[0].kind == 'loop'
    with pytest.raises(RustError, match=message) as rejected:
        analyze_source(text)
    assert str(rejected.value).startswith(f'{function.token.line}:{function.token.column}:')


def test_recursive_generic_result_parses_before_impl_sized_rejection():
    parser = Parser(RECURSIVE_GENERIC_RESULT)
    functions = parser.parse()
    assert len(functions) == 1
    function = functions[0]
    assert function.result == 'U'
    assert function.implementation.trait == 'T'
    call = function.body.children[0]
    assert call.kind == 'call'
    callee = call.children[0]
    assert callee.kind == 'field' and callee.value == 'f'
    assert callee.type_arguments == ('U',)
    assert callee.children[0].kind == 'name' and callee.children[0].value == 'self'
    # The concrete impl's result layout is validated before its body. Its
    # method token distinguishes this from an earlier required-signature gate.
    with pytest.raises(RustError, match='canonical Sized') as rejected:
        analyze_source(RECURSIVE_GENERIC_RESULT)
    assert str(rejected.value).startswith(f'{function.token.line}:{function.token.column}:')


@pytest.mark.parametrize('text', POSITIVE_ORIGINALS)
def test_new_public_associated_inputs_require_actual_check_not_boolean(text, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unchecked associated type reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(text, experimental_rustc_checked=True)


INVALID = (
    ('missing', 'trait T{type Item;}struct R{x:u64}impl T for R{}', 'missing required trait associated types', 'E0046'),
    ('extra', 'trait T{}struct R{x:u64}impl T for R{type Item=u64;}', 'not a member', 'E0437'),
    ('duplicate-declaration', 'trait T{type Item;type Item;}', 'duplicate trait associated type', 'E0428'),
    ('duplicate-binding', 'trait T{type Item;}struct R{x:u64}impl T for R{type Item=u64;type Item=i64;}', 'duplicate trait implementation associated type', 'E0201'),
    ('signature-result', 'trait T{type Item;fn f(self)->Self::Item;}struct R{x:u64}impl T for R{type Item=u64;fn f(self)->bool{true}}', 'signature mismatch', 'E0053'),
    ('signature-parameter', 'trait T{type Item;fn f(self,x:Self::Item);}struct R{x:u64}impl T for R{type Item=u64;fn f(self,x:bool){}}', 'signature mismatch', 'E0053'),
    ('copy-bound', 'trait T{type Item:Copy;}struct R{x:u64}impl T for R{type Item=R;}', 'canonical marker bounds', 'E0277'),
    ('supertrait', 'trait A{type Item;}trait B:A{}struct R{x:u64}impl B for R{}', 'missing required supertrait', 'E0277'),
    ('coherence', 'trait T{type Item;}struct R{x:u64}impl T for R{type Item=u64;}impl T for R{type Item=i64;}', 'conflicting concrete', 'E0119'),
    ('relaxed-signature-result', 'trait T{type Item:?Sized;fn f(self)->Self::Item;}struct R{x:u64}impl T for R{type Item=u64;fn f(self)->bool{true}}', 'signature mismatch', 'E0053'),
    ('relaxed-signature-parameter', 'trait T{type Item:?Sized;fn f(self,x:Self::Item);}struct R{x:u64}impl T for R{type Item=u64;fn f(self,x:bool){}}', 'signature mismatch', 'E0053'),
    ('relaxed-body-result', 'trait T{type Item:?Sized;fn f(self)->Self::Item;}struct R{x:u64}impl T for R{type Item=u64;fn f(self)->Self::Item{true}}', 'type mismatch', 'E0308'),
    ('relaxed-call-parameter', 'trait T{type Item:?Sized;fn f(self,x:Self::Item)->u64;}struct R{x:u64}impl T for R{type Item=u64;fn f(self,x:Self::Item)->u64{x}}fn g()->u64{R{x:1}.f(true)}', 'type mismatch', 'E0308'),
    # Required signatures may name direct unsized types; actual generic body
    # parameters/results and nested storage must still prove canonical Sized.
    ('relaxed-impl-generic-parameter', 'trait T{type Item:?Sized;fn f<U:?Sized>(self,x:U);}struct R{x:u64}impl T for R{type Item=u64;fn f<U:?Sized>(self,x:U){}}', 'canonical Sized', 'E0277'),
    ('relaxed-impl-generic-result', RECURSIVE_GENERIC_RESULT, 'canonical Sized', 'E0277'),
    ('relaxed-tuple-storage', 'trait T{type Item:?Sized;fn f(self)->(Self::Item,u64);}', 'canonical Sized', 'E0277'),
    ('relaxed-array-storage', 'trait T{type Item:?Sized;fn f(self,x:[Self::Item;1]);}', 'canonical Sized', 'E0277'),
    ('relaxed-generic-storage', 'struct Wrap<U>{value:U}trait T{type Item:?Sized;fn f(self)->Wrap<Self::Item>;}', 'canonical Sized', 'E0277'),
    ('parent-child-ambiguity', 'trait A{type Item;}trait B:A{type Item;fn f(self)->Self::Item;}', 'associated types on generic parameters', 'E0221'),
    ('dual-parent-ambiguity', 'trait A{type Item;}trait B{type Item;}trait C:A+B{fn f(self,x:Self::Item);}', 'associated types on generic parameters', 'E0221'),
    ('diamond-branch-shadow', 'trait Root{type Item;}trait Left:Root{type Item;}trait Right:Root{}trait Bottom:Left+Right{fn f(self)->Self::Item;}', 'associated types on generic parameters', 'E0221'),
    ('diamond-missing-root', 'trait Root{type Item;}trait Left:Root{}trait Right:Root{}trait Bottom:Left+Right{}struct R{x:u64}impl Bottom for R{}impl Left for R{}impl Right for R{}', 'missing required supertrait', 'E0277'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_invalid_associated_obligations_reject_in_owned_semantic_model(name, text, message, code):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


CAPABILITY = (
    ('generic-projection', 'trait T{type Item;}fn f<U:T>(x:U)->U{x}', 'projection normalization'),
    ('generic-associated', 'trait T{type Item<U>;}', 'generic associated types'),
    ('alias-projection', 'trait T{type A;type B;}struct R{x:u64}impl T for R{type A=u64;type B=Self::A;}', 'alias-to-alias'),
    ('qualified-projection', 'trait T{type Item;}struct R{x:u64}impl T for R{type Item=u64;}fn f(x:<R as T>::Item)->u64{x}', 'unsupported Rust type'),
    ('reference-return', 'trait T{type Item;fn get(&self)->&Self::Item;}', 'reference returns'),
    ('concrete-dst', 'trait T{type Item:?Sized;}struct R{x:u64}impl T for R{type Item=str;}', 'unsupported Rust type'),
)


@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_remaining_projection_and_layout_domains_are_explicit(name, text, message):
    with pytest.raises(RustError, match=message):
        analyze_source(text, moves=False)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('text', POSITIVE_ORIGINALS)
def test_actual_same_input_associated_check_precedes_owned_object_phase(text, tmp_path, monkeypatch):
    checked = certify_source(text, tmp_path / 'certifier')
    from pcc.frontends.rust.moves import MoveChecker
    def forbidden(*args, **kwargs):
        pytest.fail('external process or partial borrow admission in owned phase')
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(MoveChecker, 'check', forbidden)
        patch.setattr(MoveChecker, 'check_function', forbidden)
        result = compile_rust(text, rustc_check=checked)
        assert result.emit_object().startswith(b'\x7fELF')
    assert result.externally_checked and not result.borrow_checked_by_pcc
    assert (tmp_path / 'certifier' / 'original.rs').read_text() == text


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_associated_token_cannot_be_replayed_for_changed_source(tmp_path, monkeypatch):
    text = source('associated_type_values')
    checked = certify_source(text, tmp_path / 'certifier')
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('changed source reached IR'))
    with pytest.raises(RustError, match='unchanged Rust source bytes'):
        compile_rust(text + '\n', rustc_check=checked)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_valid_unsupported_associated_forms_remain_capability_gaps(name, text, message, tmp_path):
    checked = certify_source(text, tmp_path / name)
    with pytest.raises(RustError, match=message):
        compile_rust(text, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_associated_originals_match_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_native_marker_originals_match_rustc(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_invalid_associated_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    # The former parser control retains its exact external negative obligation.
    monkeypatch.setattr(oracle, 'INVALID', INVALID + LOOP_SIZED_CONTROLS)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
