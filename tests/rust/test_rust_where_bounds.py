"""Structural free-function where predicates using existing finite obligations.

https://doc.rust-lang.org/reference/items/generics.html#where-clauses
https://doc.rust-lang.org/reference/trait-bounds.html
https://doc.rust-lang.org/error_codes/E0203.html
Inline and where syntax share checking, inference and trait dispatch, not a new
solver. Same-input rustc certification remains mandatory for accepted explicit
marker/local bounds and all references. These CI originals are not local results.
"""
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.standard_markers import COPY, CLONE, SIZED
from pcc.frontends.rust.syntax import Parser, WherePredicate
from tests.rust.analysis_only import analyze_source
from tests.rust.certification_support import actual_check_prerequisite, certify_source

PROGRAMS = ('where_copy_inference', 'where_local_dispatch', 'where_mutable',
            'where_sized_order', 'where_shadow')
PRIOR_CAPABILITIES = (
    ('generic-function', 'fn bad<T>(x:T)->T where T:Copy{x}'),
    ('standard-marker', 'fn f<T>(x:T)->T where T:Copy{x}'),
    ('local-trait', 'trait A{}fn unused<T>(x:T)->T where T:A{x}'),
)
SYNTAX_POSITIVES = ('fn f() where {}', 'fn f<T>(x:T)where T: {}',
                    'fn f<T:>(x:T){}', 'fn f<T:Copy+>(x:T)where T:Copy+,{}')


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def test_where_predicates_retain_source_structure_and_parameter_tokens():
    text = 'fn f<T:Copy,U>(x:T,u:U)->T\nwhere T:Sized, U:Copy+Copy, T:Copy, {x}'
    function, = Parser(text).parse()
    assert function.where_token.text == 'where' and function.where_token.line == 2
    assert len(function.parameter_bounds) == 1
    assert [predicate.parameter.text for predicate in function.where_predicates] == ['T', 'U', 'T']
    assert all(isinstance(predicate, WherePredicate) for predicate in function.where_predicates)
    assert [tuple(b.path for b in p.bounds) for p in function.where_predicates] == [
        ('Sized',), ('Copy', 'Copy'), ('Copy',)]
    merged = {parameter.text: tuple(b.path for b in bounds)
              for parameter, bounds in function.effective_parameter_bounds}
    assert merged == {'T': ('Copy', 'Sized', 'Copy'), 'U': ('Copy', 'Copy')}


def test_inline_and_where_forms_share_canonical_obligations_and_registry():
    text = ('fn inline<T:Copy>(x:T)->(T,T){(x,x)}'
            'fn clause<U>(x:U)->(U,U)where U:Copy{(x,x)}'
            'fn forward<V:Copy>(x:V)->(V,V)where V:Sized{clause(x)}'
            'fn f()->u64{let a=clause(21u64);let b=clause::<u64>(21);let c=forward(21u64);a.0+b.1+c.0}')
    checker = analyze_source(text)
    expected = frozenset({COPY, CLONE, SIZED})
    for name in ('inline', 'clause', 'forward'):
        parameter, = checker.function_generics.parameters[name]
        assert checker.standard_markers.constraints[parameter] == expected
    instances = [entry.value for entry in checker.function_generics.instances.entries.values()]
    assert {(f.declaration_name, f.instance_arguments) for f in instances} == {
        ('clause', ('u64',)), ('forward', ('u64',))}
    assert len(instances) == len({f.identity for f in instances}) == 2


@pytest.mark.parametrize('declaration', [
    'fn f<T:?Sized>() where T:Sized {}',
    'fn f<T:Sized>() where T:?Sized {}',
    'fn f<T>() where T:?Sized,T:Sized,T:Sized {}',
    'fn f<T>() where T:Sized,T:?Sized,T:Sized {}',
])
def test_relaxation_never_erases_explicit_sized_regardless_of_order(declaration):
    checker = analyze_source(declaration)
    parameter, = checker.function_generics.parameters['f']
    assert checker.standard_markers.constraints[parameter] == frozenset({SIZED})


@pytest.mark.parametrize('declaration', [
    'fn f<T:?Sized>() where T:Copy {}',
    'fn f<T:Copy>() where T:?Sized {}',
    'fn f<T>() where T:?Sized,T:Copy {}',
    'fn f<T>() where T:Copy,T:?Sized,T:Copy {}',
])
def test_copy_implied_sized_survives_relaxation_regardless_of_order(declaration):
    checker = analyze_source(declaration)
    parameter, = checker.function_generics.parameters['f']
    assert checker.standard_markers.constraints[parameter] == frozenset({COPY, CLONE, SIZED})


def test_later_empty_predicate_does_not_restore_removed_implicit_default():
    checker = analyze_source('fn f<T>() where T:?Sized,T: {}')
    parameter, = checker.function_generics.parameters['f']
    assert checker.standard_markers.constraints[parameter] == frozenset()


def test_local_predicates_accumulate_supertraits_and_preserve_abstract_selection():
    text = ('trait A{fn read(self)->u64;}trait B:A{}struct R{x:u64}'
            'impl A for R{fn read(self)->u64{self.x}}impl B for R{}'
            'impl R{fn read(self)->u64{900}}'
            'fn invoke<T:A>(x:T)->u64 where T:B,T:A{x.read()}'
            'fn f()->u64{invoke(R{x:42})}')
    checker = analyze_source(text)
    parameter, = checker.function_generics.parameters['invoke']
    assert checker.traits.parameter_bounds[parameter] == frozenset({'A', 'B'})
    instance, = [entry.value for entry in checker.function_generics.instances.entries.values()]
    assert instance.body.children[-1].binding.declaration_name == '<R as A>::read'
    assert next(iter(checker.function_generics.bound_calls['invoke'].values()))[0] == 'A'


@pytest.mark.parametrize('text', SYNTAX_POSITIVES)
def test_empty_bounds_and_trailing_separators_are_structural_rust_syntax(text):
    function, = Parser(text).parse()
    assert function.body is not None


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('text', SYNTAX_POSITIVES)
def test_actual_rustc_accepts_the_same_empty_and_trailing_bound_forms(text, tmp_path, monkeypatch):
    from tests.rust.test_rust_local_trait_bounds import checked_owned
    checked_owned(text, tmp_path / 'check', monkeypatch)


@pytest.mark.parametrize('name', PROGRAMS)
def test_where_bounds_cannot_use_the_bare_experimental_flag(name, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('uncertified where source reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust(source(name), experimental_rustc_checked=True)


def test_unused_local_where_bound_needs_token_before_scheduling(monkeypatch):
    from pcc.frontends.rust.semantics import Checker
    monkeypatch.setattr(Checker, 'check', lambda *a: pytest.fail('unchecked local where predicate entered Sema'))
    with pytest.raises(RustError, match='local trait bounds require an actual'):
        compile_rust(PRIOR_CAPABILITIES[2][1], experimental_rustc_checked=True)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text', PRIOR_CAPABILITIES)
def test_exact_prior_where_controls_are_actual_checked_positive(name, text, tmp_path, monkeypatch):
    from tests.rust.test_rust_local_trait_bounds import checked_owned
    checked_owned(text, tmp_path / name, monkeypatch)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', PROGRAMS)
def test_actual_certification_precedes_owned_where_lowering(name, tmp_path, monkeypatch):
    from tests.rust.test_rust_local_trait_bounds import checked_owned
    checked_owned(source(name), tmp_path / name, monkeypatch)


INVALID = (
    ('concrete-copy', 'struct R{x:u64}fn need<T>(x:T)->T where T:Copy{x}fn f()->R{need(R{x:42})}', 'canonical Copy', 'E0277'),
    ('forward-copy', 'fn need<T>(x:T)->T where T:Copy{x}fn unused<U>(x:U)->U{need(x)}', 'canonical Copy', 'E0277'),
    ('later-cannot-erase-copy', 'fn need<T>(x:T)->T where T:Copy,T:Sized{x}fn unused<U>(x:U)->U{need(x)}', 'canonical Copy', 'E0277'),
    ('relaxed-forward', 'fn need<T>(){}fn unused<U>() where U:?Sized{need::<U>();}', 'canonical Sized', 'E0277'),
    ('explicit-sized-forward', 'fn need<T:?Sized>()where T:Sized{}fn unused<U:?Sized>(){need::<U>();}', 'canonical Sized', 'E0277'),
    ('copy-requires-sized', 'fn need<T:?Sized>()where T:Copy{}fn unused<U:?Sized>(){need::<U>();}', 'canonical Sized', 'E0277'),
    ('local-missing', 'trait A{}struct R{x:u64}fn need<T>(x:T)->T where T:A{x}fn f()->R{need(R{x:42})}', 'unsatisfied local trait bound', 'E0277'),
    ('local-forward', 'trait A{}fn need<T>(x:T)->T where T:A{x}fn unused<U>(x:U)->U{need(x)}', 'unsatisfied local trait bound', 'E0277'),
    ('ambiguous', 'trait A{fn n(self)->u64;}trait B{fn n(self)->u64;}fn unused<T>(x:T)->u64 where T:A,T:B{x.n()}', 'ambiguous', 'E0034'),
    ('shadow', 'trait A{}fn unused<A,T>(x:T)where T:A{}', 'shadows', 'E0404'),
    ('local-copy-not-builtin', 'trait Copy{}fn unused<T>(x:T)->(T,T)where T:Copy{(x,x)}', 'moved', 'E0382'),
    ('unused-body', 'fn unused<T,U>(x:U)->(U,U)where T:Copy{(x,x)}', 'moved', 'E0382'),
    ('unknown-subject', 'fn unused<T>(x:T)where U:Copy{}', 'subjects must be declared', 'E0412'),
    ('duplicate-relaxed', 'fn unused<T>()where T:?Sized+?Sized{}', 'only one relaxed canonical', 'E0203'),
    ('trait-strengthening-still-invalid', 'trait A{fn f<U>(x:U)->U;}struct R{x:u64}impl A for R{fn f<U:Copy>(x:U)->U{x}}fn unused<T>()where T:A{}', 'bounds are stricter', 'E0276'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_where_obligations_and_unused_bodies_fail_without_lowering(name, text, message, code):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


REFERENCE_INVALID = (
    ('shared-mutation', 'trait A{fn step(&mut self);}fn unused<T>(x:&T)where T:A{x.step()}', '', 'E0596'),
    ('moved-reference-receiver', 'trait A{fn n(&self)->u64;}fn unused<T>(x:T)where T:A{let y=x;x.n();}', '', 'E0382'),
)
CAPABILITY = (
    # Exact inherent associated-where control is retained as a positive in
    # test_rust_inherent_local_bounds; trait contracts remain a separate gap.
    # Exact trait-method where originals now checked in test_rust_trait_where_contracts.
    ('concrete', 'fn f()where u64:Copy{}', 'subjects must be declared'),
    ('compound', 'fn f<T>()where [T;0]:Copy{}', 'subjects must be declared'),
    ('projection', 'fn f<T>()where T:Iterator,T::Item:Copy{}', 'generic trait|where projections|only canonical'),
    ('qualified-projection', 'fn f<T>()where <T as Iterator>::Item:Copy,T:Iterator{}', 'subjects must be declared'),
    ('lifetime', "fn f<'a,'b>(x:&'a u64)->u64 where 'a:'b{*x}", 'lifetime and higher-ranked where'),
    ('higher-ranked', "fn f<F>()where for<'a> F:Fn(&'a u64){}", 'lifetime and higher-ranked where'),
    ('generic-trait', 'fn f<T>()where T:Into<u64>{}', 'generic trait bounds'),
    ('parenthesized-bound', 'fn f<T>(x:T)where T:(Copy){}', 'parenthesized trait bounds'),
    ('dst', 'fn f<T>(x:&T)where T:?Sized{}fn g(x:&[u64]){f(x)}', 'slice types'),
    ('local-relaxed', 'trait A{fn n(&self)->u64;}fn f<T>(x:&T)->u64 where T:?Sized+A{x.n()}', 'relaxed local-trait parameter layouts'),
)


@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_unimplemented_predicate_domains_remain_explicit(name, text, message):
    with pytest.raises(RustError, match=message):
        analyze_source(text)


@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text,message', CAPABILITY)
def test_valid_unsupported_predicates_are_not_rust_rejection_claims(name, text, message, tmp_path):
    checked = certify_source(text, tmp_path / name)
    with pytest.raises(RustError, match=message):
        compile_rust(text, rustc_check=checked)


def test_predicate_and_combined_bound_limits_remain_bounded():
    with pytest.raises(RustError, match='where predicates exceed'):
        Parser('fn f<T>()where ' + ','.join(['T:'] * 65) + '{}').parse()
    with pytest.raises(RustError, match='combined type parameter bounds exceed'):
        analyze_source('fn f<T>()where ' + ','.join(['T:Copy'] * 17) + '{}')


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_actual_native_where_programs_match_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_standard_markers as shared
    monkeypatch.setattr(shared, 'PROGRAMS', PROGRAMS)
    monkeypatch.setattr(shared, 'source', source)
    shared.test_actual_native_marker_originals_match_rustc(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_exact_invalid_where_originals(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID + REFERENCE_INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
