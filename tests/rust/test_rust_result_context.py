"""Result-context constraints share call variables; no trait solver or new cache.

Rust Reference, retrieved 2026-11-01:
https://doc.rust-lang.org/reference/types/inferred.html#type-inferred
https://doc.rust-lang.org/reference/items/generics.html
https://doc.rust-lang.org/reference/expressions/call-expr.html
https://doc.rust-lang.org/reference/expressions.html#evaluation-order-of-operands
Ledger: rust.generics.result-context-call-inference.
"""
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.semantics import Checker, InferredType, unify
from pcc.frontends.rust.syntax import Parser
from pcc.frontends.rust.types import TupleType
from tests.rust.certification_support import actual_check_prerequisite, darwin_check_prerequisite

PROGRAMS = (
    'result_context_nominal', 'result_context_arrays', 'result_context_later',
    'result_context_recursion', 'result_context_order', 'result_context_composite',
    'result_context_mutable',
)
VALUE_PROGRAMS = PROGRAMS[:-1]
MAYBE = 'enum Maybe<T>{Empty,Some(T)} fn empty<T>()->Maybe<T>{Maybe::<T>::Empty} '


def compile_source(source):
    return compile_rust(source, experimental_rustc_checked=True)


@pytest.mark.parametrize('name', VALUE_PROGRAMS)
@pytest.mark.parametrize('target,magic', [
    ('x86_64-unknown-linux-gnu', b'\x7fELF'),
    ('arm64-apple-darwin', b'\xcf\xfa\xed\xfe'),
])
def test_result_context_originals_have_real_target_objects(name, target, magic):
    source = Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()
    result = compile_rust(source, experimental_rustc_checked=True, target_triple=target)
    assert result.emit_object().startswith(magic)


@pytest.mark.parametrize('source', [
    MAYBE + 'fn f()->Maybe<u64>{empty()}',
    MAYBE + 'fn f()->Maybe<u64>{return empty();}',
    MAYBE + 'fn f(){let x:Maybe<u64>=empty();}',
    MAYBE + 'fn take(x:Maybe<u64>){} fn f(){take(empty());}',
    MAYBE + 'fn outer<U>()->Maybe<U>{empty()}',
    'fn forever<T>()->T{forever()} fn head<T>(x:(T,))->T{x.0} fn f()->u64{head(forever())}',
    'fn forever<T>()->T{forever()} fn head<T>(x:(T,))->T{x.0} fn outer<U>()->U{head(forever())}',
    'fn empty<T>()->[T;0]{[]} fn f()->[u64;0]{empty()}',
    'fn empty<T>()->[T;0]{[]} fn f()->([i32;0],[bool;0]){(empty(),empty())}',
    MAYBE + 'fn f(){let mut x=empty();x=Maybe::<u64>::Some(1);}',
])
def test_annotations_returns_and_argument_contexts_constrain_same_call(source):
    compile_source(source)


def test_inferred_and_explicit_results_share_existing_registry():
    source = MAYBE + 'fn f(){let a:Maybe<u64>=empty();let b=empty::<u64>();let c:Maybe<i32>=empty();}'
    parser = Parser(source)
    checker = Checker(parser.parse(), parser.records, parser.enums, parser.implementations)
    checker.check()
    entries = tuple(checker.function_generics.instances.entries.values())
    assert len(entries) == 2
    assert {entry.value.instance_arguments for entry in entries} == {('u64',), ('i32',)}
    assert all(entry.value is entry.result for entry in entries)


@pytest.mark.parametrize('source,message', [
    ('fn unused<T>(){} fn f(){unused();}', 'cannot infer generic parameter'),
    (MAYBE + 'fn f(){let x=empty();}', 'cannot infer generic parameter'),
    ('fn empty<T>()->[T;0]{[]} fn f(){let x=empty();}', 'cannot infer generic parameter'),
    (MAYBE + 'fn take(x:Maybe<i32>){} fn f(){let x:Maybe<u64>=empty();take(x);}', 'type mismatch'),
    ('fn empty<T>()->[T;0]{[]} fn f()->[u64;1]{empty()}', 'type mismatch'),
    ('fn bad<T>(x:T)->(T,T){(x,x)}', 'moved'),
    ('fn forever<T>()->T{forever()} fn same<T>(a:T,b:T){} fn f(){let a=forever();same(a,(a,));}', 'infinite inferred'),
])
def test_ambiguous_inconsistent_or_recursive_constraints_fail_before_ir(source, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid inference reached IR'))
    with pytest.raises(RustError, match=message):
        compile_source(source)


def test_occurs_check_retains_shared_variable_identity():
    token = Parser('fn f(){}').token
    variable = InferredType(token, description='generic parameter T')
    with pytest.raises(RustError, match='infinite inferred generic parameter T'):
        unify(variable, TupleType((variable,)), token)
    assert variable.bound is None


def test_result_context_does_not_turn_reference_flag_into_certification(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    source = Path(__file__).with_name('programs').joinpath(PROGRAMS[-1] + '.rs').read_text()
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('uncertified reference reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_source(source)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_unchanged_result_context_originals_match_native_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference, 'PROGRAMS', PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path, monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=darwin_check_prerequisite())
def test_reference_context_cross_object_requires_matching_target_check(tmp_path, monkeypatch):
    from tests.rust.test_rust_targets import test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite
    test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite(
        PROGRAMS[-1], monkeypatch, tmp_path=tmp_path)
