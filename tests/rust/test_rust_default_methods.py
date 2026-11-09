"""Declaration-checked trait defaults, with unchanged-input rustc admission.

https://doc.rust-lang.org/reference/items/traits.html
https://doc.rust-lang.org/reference/items/associated-items.html
https://doc.rust-lang.org/reference/trait-bounds.html#sized
All authored execution obligations are UNRUN. A source-reviewed candidate is
not evidence of Rust support or acceptance of the unchanged original crate.
"""
from hashlib import sha256
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.standard_markers import CLONE, COPY, SIZED
from pcc.frontends.rust.syntax import Parser
from pcc.support.instantiation import InstanceState
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import actual_check_prerequisite, certify_source


PROGRAMS = (
    'default_static', 'default_override', 'default_inherent_shadow',
    'default_generic', 'default_supertraits', 'default_associated',
    'default_recursion', 'default_mutable', 'default_inline_unused',
    'default_bound_dispatch', 'default_parameter_dispatch',
    'default_unsized_self', 'default_associated_inherited',
)
MODEL_PROGRAMS = tuple(name for name in PROGRAMS if name != 'default_mutable')
# Preserve the exact former inline capability input as a checked positive.
FORMER_INLINE_GAP = 'trait T{#[inline]fn f(&self)->u64{42}}'
# Preserve the exact former marker-bound and where-clause capability inputs.
# These are declaration-only originals, not rewritten executable fixtures.
FORMER_GENERIC_DEFAULT_GAPS = (
    ('inline-bound', 'trait T{fn f<U:Copy>(u:U)->U{u}}', 'T'),
    ('where-bound', 'trait Ops{fn f<U>(u:U)->U where U:Copy{u}}', 'Ops'),
)


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def nodes(expression):
    pending = [expression]
    while pending:
        node = pending.pop()
        yield node
        pending.extend(node.children[1:] if node.kind == 'call' else node.children)


def targets(function):
    return {node.binding.declaration_name for node in nodes(function.body)
            if node.kind == 'call'}


def test_real_project_default_body_and_all_earlier_source_bytes_are_unchanged():
    original = Path(__file__).resolve().parents[2].joinpath(
        'projects/fallible-streaming-iterator-0.1.9/src/lib.rs').read_bytes()
    assert len(original) == 21146
    assert sha256(original).hexdigest() == '7fb7428997bdd89c3ee0818ecee642f8d93882c9c66171c9c6194b732f337089'
    lines = original.splitlines()
    assert lines[49].strip() == b'#[inline]'
    assert lines[50].strip().startswith(b'fn next(&mut self)')
    assert lines[68].strip().startswith(b'while let ')
    assert lines[209].strip() == b'if let None = self.get() {'


def test_parser_keeps_required_and_provided_bodies_and_mutable_parameters_distinct():
    text = ('trait T{fn required(&self);#[inline]fn provided(mut value:u64)->u64'
            '{value+=2;value}}')
    parser = Parser(text)
    assert parser.parse() == []
    required, provided = parser.traits[0].methods
    assert required.body is None and provided.body.kind == 'block'
    assert required.default_source is provided.default_source is None
    assert provided.params[0][2] and provided.inline_attributes[0].mode == 'hint'
    assert text[slice(*provided.inline_attributes[0].source_span)] == '#[inline]'


@pytest.mark.parametrize('name,text,trait', FORMER_GENERIC_DEFAULT_GAPS)
def test_exact_former_generic_defaults_keep_the_declared_copy_contract(name, text, trait):
    checker = analyze_source(text)
    parameter, = checker.traits.method_parameters[trait, 'f']
    _, signature, _ = checker.traits.defaults.entries[trait, 'f']
    assert signature.params[0][1] == signature.result == parameter
    assert checker.standard_markers.constraints[parameter] == frozenset({COPY, CLONE, SIZED})
    assert not checker.functions and not checker.function_generics.instances.entries


@pytest.mark.parametrize('name', MODEL_PROGRAMS)
def test_noncertifying_default_models_use_the_existing_function_and_move_owners(name):
    checker = analyze_source(source(name))
    inherited = checker.traits.defaults.inherited
    assert inherited and all(function.body is not None for function in inherited)
    assert all(function in checker.functions for function in inherited)
    assert all(function.implementation.owner != 'Self' for function in checker.functions
               if function.implementation is not None)
    assert checker.type_scope == {} and not checker.abstract_function
    assert checker.current_function is None and checker.loops == []


def test_missing_members_inherit_fresh_bodies_without_replacing_explicit_overrides():
    checker = analyze_source(source('default_override'))
    inherited = checker.names['<R as Value>::answer']
    overridden = checker.names['<S as Value>::answer']
    declaration = checker.traits.traits['Value']['answer']
    assert inherited.default_source == ('Value', 'answer')
    assert overridden.default_source is None
    assert inherited.body is not declaration.body
    assert sum(function.declaration_name == '<S as Value>::answer'
               for function in checker.functions) == 1
    assert targets(inherited) == {'<R as Value>::base'}
    assert targets(overridden) == {'<S as Value>::base'}


@pytest.mark.parametrize('name,function,expected', [
    ('default_static', '<R as Value>::answer', '<R as Value>::base'),
    ('default_inherent_shadow', '<R as Read>::answer', '<R as Read>::value'),
    ('default_supertraits', '<R as Top>::answer', '<R as Left>::middle'),
    ('default_supertraits', '<R as Left>::middle', '<R as Base>::base'),
    ('default_recursion', '<R as Count>::count', '<R as Count>::count'),
])
def test_default_dispatch_keeps_the_declaring_trait_identity(name, function, expected):
    checker = analyze_source(source(name))
    assert targets(checker.names[function]) == {expected}
    selected = tuple(checker.function_generics.bound_calls[function].values())
    assert selected and all(owner == 'R' for _, owner, _ in selected)


def test_inherited_generics_keep_checked_templates_and_the_shared_instance_registry():
    checker = analyze_source(source('default_generic'))
    registry = checker.function_generics
    template = checker.names['<R as Helpers>::compose']
    assert template.type_parameters and template.body.ty is not None
    assert targets(template) == {'<R as Helpers>::pair', '<R as Helpers>::supplied'}
    raw = registry.raw_bodies[template.declaration_name]
    assert raw is not template.body and raw.ty is None
    instances = [function for function in checker.functions
                 if function.declaration_name == template.declaration_name
                 and function.instance_arguments is not None]
    assert {function.instance_arguments for function in instances} == {('u64',), ('bool',)}
    assert all(function.default_source == ('Helpers', 'compose') for function in instances)
    assert all(targets(function) == {'<R as Helpers>::pair', '<R as Helpers>::supplied'}
               for function in instances)
    entries = tuple(registry.instances.entries.values())
    assert entries and all(entry.state is InstanceState.COMPLETE for entry in entries)
    assert len({entry.value.identity for entry in entries}) == len(entries)


def test_generic_default_parameter_dispatch_is_pinned_through_both_replays():
    checker = analyze_source(source('default_parameter_dispatch'))
    template = checker.names['<Runner as Apply>::apply']
    selected = tuple(checker.function_generics.bound_calls[template.declaration_name].values())
    parameter, = checker.function_generics.parameters[template.declaration_name]
    assert selected == (('Read', parameter, 'read'),)
    instance, = [function for function in checker.functions
                 if function.declaration_name == template.declaration_name
                 and function.instance_arguments is not None]
    assert instance.instance_arguments == ('R',)
    assert targets(instance) == {'<R as Read>::read'}
    assert 'R::read' in targets(checker.names['answer'])


def test_concrete_owners_have_independent_associated_type_default_bodies():
    checker = analyze_source(source('default_associated'))
    first, second = (checker.names['<R as Carry>::echo'], checker.names['<S as Carry>::echo'])
    assert first.result == 'u64' and second.result == 'bool'
    assert first.params[1][1] == 'u64' and second.params[1][1] == 'bool'
    assert first.body is not second.body
    assert first.body.children[-1].binding is not second.body.children[-1].binding


def test_inherited_projection_and_supertrait_call_keep_the_same_owner_identity():
    checker = analyze_source(source('default_associated_inherited'))
    for owner, result in (('R', 'u64'), ('S', 'bool')):
        function = checker.names[f'<{owner} as Child>::relay']
        assert function.result == function.params[1][1] == result
        assert targets(function) == {f'<{owner} as Root>::echo'}
        assert tuple(checker.function_generics.bound_calls[function.declaration_name].values()) == (
            ('Root', owner, 'echo'),)


def test_unused_defaults_are_checked_but_not_emitted_as_abstract_self_functions():
    checker = analyze_source(source('default_inline_unused'))
    assert ('Unused', 'identity') in checker.traits.defaults.entries
    assert not any(function.implementation is not None
                   and function.implementation.trait == 'Unused'
                   for function in checker.functions)
    inherited = checker.names['<R as Value>::answer']
    original = checker.traits.traits['Value']['answer']
    assert inherited.inline_attributes is original.inline_attributes
    assert original.inline_attributes[0].mode == 'always'


@pytest.mark.parametrize('text', [*(source(name) for name in PROGRAMS), FORMER_INLINE_GAP,
    *(text for _, text, _ in FORMER_GENERIC_DEFAULT_GAPS),
    'trait Unused{fn value()->u64{42}}fn answer()->u64{42}',
    'trait Unused{fn value<U>(x:U)->U{x}}',
])
def test_every_default_body_requires_actual_original_certification_before_ir(text, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('uncertified default reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(text, experimental_rustc_checked=True)


INVALID = (
    ('missing-required', 'trait T{fn required(&self);fn provided(&self){}}struct R{x:u64}impl T for R{}', 'missing required', 'E0046'),
    ('unused-result', 'trait T{fn value()->u64{true}}', 'type mismatch', 'E0308'),
    ('overridden-result', 'trait T{fn value()->u64{true}}struct R{x:u64}impl T for R{fn value()->u64{42}}', 'type mismatch', 'E0308'),
    ('unused-call', 'trait T{fn value()->u64{unknown()}}', 'unknown function', 'E0425'),
    ('concrete-field', 'trait T{fn value(&self)->u64{self.x}}struct R{x:u64}impl T for R{}', 'field access requires', 'E0609'),
    ('generic-move', 'trait T{fn twice<U>(&self,x:U)->(U,U){(x,x)}}', 'moved', 'E0382'),
    ('copy-obligation', 'trait T{fn required<U:Copy>(&self,x:U);fn provided<V>(&self,x:V){self.required(x)}}', 'canonical Copy', 'E0277'),
    ('self-sized-obligation', 'fn requires<U>(){}trait T{fn provided(&self){requires::<Self>();}}', 'canonical Sized', 'E0277'),
    ('discarded-unsized-self', 'trait T{fn make()->Self;fn bad(){Self::make();}}pub fn answer()->u64{42}', 'canonical Sized', 'E0277'),
    ('stored-unsized-self', 'trait T{fn make()->Self;fn bad(){let value=Self::make();}}pub fn answer()->u64{42}', 'canonical Sized', 'E0277'),
    ('overridden-unsized-self', 'trait T{fn make()->Self;fn bad(){Self::make();}}struct R{x:u64}impl T for R{fn make()->Self{R{x:42}}fn bad(){}}', 'canonical Sized', 'E0277'),
    ('discarded-unsized-associated', 'trait T{type Item:?Sized;fn make()->Self::Item;fn bad(){Self::make();}}', 'canonical Sized', 'E0277'),
    ('stricter-override', 'trait T{fn value<U>(&self,x:U)->U{x}}struct R{x:u64}impl T for R{fn value<V:Copy>(&self,x:V)->V{x}}', 'stricter', 'E0276'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_all_default_declarations_are_checked_even_without_use_or_after_override(name, text, message, code):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


@pytest.mark.parametrize('text', [
    'trait T{fn value(&self)->u64{42}}',
    'trait T{fn identity<U>(value:U)->U{value}}',
    'fn helper<U:?Sized>(value:&U){}trait T{fn provided(&self){helper(self);}}',
    FORMER_INLINE_GAP,
])
def test_no_implementation_is_required_for_an_independently_checked_default(text):
    checker = analyze_source(text)
    assert checker.traits.defaults.entries
    assert not any(function.implementation is not None for function in checker.functions)
    assert not checker.function_generics.instances.entries


# The former self-sized row is a checked positive in test_rust_self_sized_predicates.
CAPABILITY = (
    ('reference-return', 'trait T{fn value(&self)->&Self{self}}', 'reference returns'),
    ('generic-trait', 'trait T<U>{fn value(&self,x:U)->U{x}}', 'generic traits'),
)


@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_unimplemented_default_body_contracts_still_fail_explicitly(name, text, message):
    with pytest.raises(RustError, match=message):
        analyze_source(text, moves=False)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', PROGRAMS)
def test_actual_same_input_default_check_precedes_owned_object(name, tmp_path, monkeypatch):
    text = source(name)
    checked = certify_source(text, tmp_path / 'certifier')
    from pcc.frontends.rust.moves import MoveChecker
    def forbidden(*args, **kwargs):
        pytest.fail('external process or partial move/loan proof entered owned phase')
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
def test_exact_former_inline_default_gap_is_retained_as_an_actual_checked_positive(tmp_path, monkeypatch):
    checked = certify_source(FORMER_INLINE_GAP, tmp_path / 'certifier')
    from pcc.frontends.rust.moves import MoveChecker
    def forbidden(*args, **kwargs):
        pytest.fail('external process or partial move/loan proof entered owned phase')
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, 'Popen', forbidden)
        patch.setattr(MoveChecker, 'check', forbidden)
        patch.setattr(MoveChecker, 'check_function', forbidden)
        result = compile_rust(FORMER_INLINE_GAP, rustc_check=checked)
    hint, = result.function_inline_hints
    assert hint.selected_hint == 'hint' and hint.ignored_reason is None
    assert not hint.applied_by_owned_codegen
    assert result.externally_checked and not result.borrow_checked_by_pcc
    assert (tmp_path / 'certifier' / 'original.rs').read_text() == FORMER_INLINE_GAP


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,trait', FORMER_GENERIC_DEFAULT_GAPS)
def test_exact_former_generic_default_gaps_are_actual_checked_positives(name, text, trait, tmp_path, monkeypatch):
    from tests.rust.test_rust_local_trait_bounds import checked_owned
    # The shared helper checks these exact bytes with real rustc before the
    # owned phase forbids external processes and the partial move checker.
    checked_owned(text, tmp_path / name, monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_default_token_cannot_authorize_changed_original_or_options(tmp_path, monkeypatch):
    text = source('default_static')
    checked = certify_source(text, tmp_path / 'certifier')
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('changed input reached IR'))
    with pytest.raises(RustError, match='unchanged Rust source bytes'):
        compile_rust(text.replace('Self::base() + 2', 'Self::base() + 3'), rustc_check=checked)
    with pytest.raises(RustError):
        compile_rust(text, rustc_check=checked, edition=2015)
    with pytest.raises(RustError):
        compile_rust(text, rustc_check=checked, overflow_checks=True)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_valid_unsupported_default_contracts_remain_certified_gaps(name, text, message, tmp_path):
    checked = certify_source(text, tmp_path / name)
    with pytest.raises(RustError, match=message):
        compile_rust(text, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_default_originals_match_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_native_marker_originals_match_rustc(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_invalid_default_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
