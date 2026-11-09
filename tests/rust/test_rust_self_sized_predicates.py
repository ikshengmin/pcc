"""Trait method `where Self: Sized` predicates.

https://doc.rust-lang.org/reference/items/traits.html#dyn-compatibility
https://doc.rust-lang.org/reference/trait-bounds.html#sized
https://doc.rust-lang.org/reference/items/generics.html#where-clauses
Trait Self has no implicit Sized bound. A method that declares one may take
or return Self by value and name it inside an aggregate; its default body
checks Self as Sized, and every call through an abstract owner must prove the
predicate. Implementations here are concrete local nominal types, so the
predicate holds for each of them. Other Self predicates stay explicit gaps.
The unchanged fallible-streaming-iterator trait methods `all`, `any`,
`by_ref`, `count` and `filter` are the first project inputs that needed it.
"""
from pathlib import Path

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import actual_check_prerequisite, certify_source


PROGRAMS = ('self_sized_by_value', 'self_sized_result', 'self_sized_generic_caller', 'self_sized_nested')
# Retained byte-for-byte from the former default-method capability gaps.
FORMER_GAP = 'trait T{fn value(self)->u64 where Self:Sized{42}}'
SIZED_TWICE = 'trait It{fn get(&self)->u64;fn twice(&self)->u64 where Self:Sized{self.get()*2}'
# Invalid Rust: a default without the predicate calls a method that needs it.
INVALID = (
    ('default-without-predicate-calls-sized-method', SIZED_TWICE + 'fn bad(&self)->u64{self.twice()}}',
     'requires Self: Sized', 'E0277'),
)
# Valid Rust that PCC keeps as explicit gaps.
GAPS = (
    ('copy-self-predicate', 'trait T{fn value(self)->u64 where Self:Copy{42}}',
     'where Self predicates other than canonical Sized'),
    ('local-trait-self-predicate', 'trait U{}trait T{fn value(&self)->u64 where Self:U{42}}',
     'where Self predicates naming local traits'),
    ('inherent-method-self-predicate', 'pub struct R{pub v:u64}impl R{pub fn value(&self)->u64 where Self:Sized{self.v}}',
     'where Self predicates on implementation methods'),
    # The next unchanged project predicate, `F: FnMut(&Self::Item) -> bool`.
    ('fn-trait-bound-sugar', 'pub fn f<F>(f:F)->u64 where F:FnMut(u64)->bool{0}',
     'parenthesized Fn-family trait bounds require closure traits'),
)


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def test_unchanged_project_methods_declare_self_sized():
    lines = Path(__file__).resolve().parents[2].joinpath(
        'projects/fallible-streaming-iterator-0.1.9/src/lib.rs').read_text().splitlines()
    assert lines[63:66] == ['    fn all<F>(&mut self, mut f: F) -> Result<bool, Self::Error>',
                            '    where', '        Self: Sized,']


def test_self_bounds_are_kept_apart_from_parameter_predicates():
    parser = Parser('trait T{fn f<U>(self,u:U)->u64 where Self:Sized,U:Copy,Self:core::marker::Sized;}')
    parser.parse()
    method, = parser.traits[0].methods
    assert [bound.path for bound in method.self_bounds] == ['Sized', 'core::marker::Sized']
    assert [(predicate.parameter.text, [bound.path for bound in predicate.bounds])
            for predicate in method.where_predicates] == [('U', ['Copy'])]


@pytest.mark.parametrize('name', PROGRAMS)
def test_self_sized_methods_are_checked_with_sized_self(name):
    checker = analyze_source(source(name))
    assert checker.traits.sized_self_methods


def test_former_self_sized_gap_is_checked_without_an_implementation():
    checker = analyze_source(FORMER_GAP)
    assert checker.traits.sized_self_methods == {('T', 'value')}
    assert checker.traits.defaults.entries


def test_default_with_the_predicate_may_call_another_sized_method():
    analyze_source(SIZED_TWICE + 'fn good(&self)->u64 where Self:Sized{self.twice()}}')


def test_required_nested_self_still_needs_the_predicate():
    with pytest.raises(RustError, match='nested trait Self types require Sized'):
        analyze_source('struct W<I>{it:I}trait It{fn wrap(self)->W<Self>;}')


@pytest.mark.parametrize('name,text,message,code', INVALID, ids=[row[0] for row in INVALID])
def test_unproven_self_sized_calls_are_rejected_before_ir(name, text, message, code, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('invalid source reached IR'))
    with pytest.raises(RustError, match=message):
        analyze_source(text)


@pytest.mark.parametrize('name,text,message', GAPS, ids=[row[0] for row in GAPS])
def test_other_self_predicates_stay_explicit_gaps(name, text, message):
    with pytest.raises(RustError, match=message):
        analyze_source(text, moves=False)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', PROGRAMS)
def test_actual_same_input_check_precedes_owned_object(name, tmp_path):
    text = source(name)
    checked = certify_source(text, tmp_path / 'certifier')
    result = compile_rust(text, rustc_check=checked)
    assert result.emit_object().startswith(b'\x7fELF')


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,message', GAPS, ids=[row[0] for row in GAPS])
def test_valid_self_predicate_gaps_remain_certified_gaps(name, text, message, tmp_path):
    checked = certify_source(text, tmp_path / name)
    with pytest.raises(RustError, match=message):
        compile_rust(text, rustc_check=checked)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_native_self_sized_originals_match_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_native_marker_originals_match_rustc(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_unproven_self_sized_calls(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
