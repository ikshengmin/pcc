"""Owned fixed-array for loops, not general Iterator/Drop support.

Rust Reference (retrieved 2026-10-31), feature rust.loops.owned-array-for:
https://doc.rust-lang.org/reference/expressions/loop-expr.html#iterator-loops
https://doc.rust-lang.org/reference/destructors.html#drop-scopes
https://doc.rust-lang.org/reference/expressions.html#moved-and-copied-types
https://doc.rust-lang.org/reference/expressions/operator-expr.html#overflow
Source models below are not external checking; only the explicit adapter tests
and unchanged-original native/reference lane establish actual checked inputs.
"""
import os
from pathlib import Path
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.iteration import OwnedArrayIteration, StandardIterator
from pcc.frontends.rust.semantics import Checker
from pcc.frontends.rust.syntax import Parser

PROGRAMS = (
    'for_array_order', 'for_array_snapshot', 'for_array_noncopy',
    'for_array_source_once', 'for_array_zero_effect', 'for_array_control',
    'for_array_nested_control', 'for_array_return', 'for_array_standard_names',
    'for_array_scope_and_shapes', 'for_array_safe_division', 'for_array_binding_reborrow',
    'for_array_generic_function', 'for_array_iterable_outer_exit',
    'for_array_statement_values',
)


def model(source):
    """Isolated semantic model, never public certification authorization."""
    parser = Parser(source)
    functions = parser.parse()
    checker = Checker(functions, parser.records, parser.enums, parser.implementations,
                      externally_checked=True)
    checker.check()
    return checker


def nodes(expression):
    yield expression
    for child in expression.children:
        yield from nodes(child)


def test_standard_array_identity_and_binding_scopes_do_not_use_source_names():
    checker = model('fn f(){let IntoIterator=[1u64,2];let Iterator=3;let Option=4;'
                    'for Option in IntoIterator {let Iterator=Option;}'
                    'let after=Option;for mut Option in IntoIterator {Option+=1;}}')
    expressions = list(nodes(checker.functions[0].body))
    loops = [expr for expr in expressions if expr.kind == 'for']
    assert len(loops) == 2
    assert all(isinstance(expr.value, OwnedArrayIteration) for expr in loops)
    assert all(expr.value.identity is StandardIterator.OWNED_ARRAY for expr in loops)
    assert all(expr.value.array_type.length == 2 for expr in loops)
    outer = next(expr.binding for expr in expressions if expr.kind == 'let' and expr.token.text == 'Option')
    after = next(expr for expr in expressions if expr.kind == 'let' and expr.token.text == 'after')
    assert after.children[0].binding is outer
    assert len({outer.serial, loops[0].binding.serial, loops[1].binding.serial}) == 3
    assert not loops[0].binding.mutable and loops[1].binding.mutable


def test_noncopy_nominal_element_can_have_proven_empty_destruction():
    checker = model('struct R{n:u64}fn f(a:[R;2]){for r in a{r.n;}}')
    loop = next(expr for expr in nodes(checker.functions[0].body) if expr.kind == 'for')
    assert loop.value.array_type.element == 'R'
    assert not checker.copy_type('R')


@pytest.mark.parametrize('source,message', [
    ('fn f<T>(a:[T;2]){for _ in a{}}', 'unknown generic element destruction'),
    ('fn f<T>(a:[T;0]){for _ in a{}}', 'unknown generic element destruction'),
    ('struct R<T>{n:T}fn f<T>(a:[R<T>;2]){for _ in a{}}', 'unknown generic element destruction'),
    ('fn f(a:&[u64;2]){for _ in a{}}', 'concrete owned arrays only'),
    ('fn f(a:&mut [u64;2]){for _ in a{}}', 'concrete owned arrays only'),
    ('fn f(){for x in 0..3{}}', 'range iterators'),
    ('fn f(){for x in 0..=3{}}', 'range iterators'),
    ('fn f(){for (x,y) in [(1u64,2u64)]{}}', 'destructuring/refutable patterns'),
    ('fn f(){for () in [();1]{}}', 'destructuring/refutable patterns'),
    ('fn f(){for x in [1u64]{x=2;}}', 'immutable'),
    ('fn f(){for x in [1u64]{}x;}', 'unknown local'),
    ('fn f(){for x in [1u64]{1}}', 'type mismatch'),
    ('fn f(){for _ in [1u64]{}break;}', 'outside a loop'),
    ('fn f(){for _ in [1u64]{}continue;}', 'outside a loop'),
    ('fn f(){(for _ in [1u64]{}) let x=1;}', "expected ';'"),
    ('fn f(d:u64){for x in [10u64]{x/d;}}', 'panic-capable division'),
    ('fn f(d:u64){for x in [10u64]{x%d;}}', 'panic-capable division'),
    ('fn f(x:i64){for _ in [1]{x/-1;}}', 'panic-capable division'),
    ('fn f(x:i64){for _ in [1]{x%-1;}}', 'panic-capable division'),
    ('fn f(i:usize){for a in [[1u64,2]]{a[i];}}', 'closed scalar constant'),
    ('struct R{n:u64}impl Drop for R{fn drop(&mut self){}}', 'trait impls'),
])
def test_for_model_preserves_type_scope_destruction_and_panic_capability_gaps(source, message):
    with pytest.raises(RustError, match=message):
        model(source)


@pytest.mark.parametrize('name', PROGRAMS)
def test_legacy_attestation_does_not_authorize_new_iterator_ownership(name, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unchecked for reached IR'))
    source = Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()
    from tests.rust.certification_support import source_has_references
    # The public reference-presence gate precedes even the iterator type model.
    message = ('reference-bearing Rust source requires an actual'
               if source_has_references(source) else
               'for ownership/borrowing admission requires an actual external rustc check')
    with pytest.raises(RustError, match=message):
        compile_rust(source, experimental_rustc_checked=True)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if os.environ.get('PCC_RUSTC_INSTALL_RECEIPT') else 'explicit audited rustc installation receipt required')
@pytest.mark.parametrize('source,code', [
    ('struct R{n:u64}fn f(){let a=[R{n:1}];for _ in a{}let b=a;}', 'E0382'),
    ('struct R{n:u64}fn f(){let a:[R;0]=[];for _ in a{}let b=a;}', 'E0382'),
    ('struct R{n:u64}fn take(r:R){}fn f(){for r in [R{n:1}]{take(r);take(r);}}', 'E0382'),
    ('fn f(){let mut p=&0u64;for x in [1u64,2]{p=&x;}let y=*p;}', 'E0597'),
    ('fn f(){for x in [1u64,2]{x=3;}}', 'E0384'),
])
def test_original_iterator_ownership_rejections_belong_to_actual_checker(source, code, tmp_path, monkeypatch):
    from pcc.frontends.rust import RustExternalCheckError, compile_rust_checked
    from pcc.frontends.rust.lowering import Lowerer
    from tests.rust.test_rust_certified_references import toolchain
    path = tmp_path / 'original.rs'
    path.write_text(source)
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('external rejection reached owned IR'))
    with pytest.raises(RustExternalCheckError, match=code):
        compile_rust_checked(source, toolchain=toolchain(), evidence_directory=tmp_path / 'certifier')
    assert path.read_text() == source
    assert not (tmp_path / 'certifier/checked.rmeta').exists()


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if os.environ.get('PCC_RUSTC_INSTALL_RECEIPT') else 'explicit audited rustc installation receipt required')
@pytest.mark.parametrize('source,message', [
    ('fn f<T>(a:[T;2]){for _ in a{}}', 'unknown generic element destruction'),
    ('fn f(a:&[u64;2]){for _ in a{}}', 'concrete owned arrays only'),
    ('fn f(){for x in 0..3{}}', 'range iterators'),
    ('fn f(){for (x,y) in [(1u64,2u64)]{}}', 'destructuring/refutable patterns'),
    ('fn f(d:u64){for x in [10u64]{x/d;}}', 'panic-capable division'),
    ('fn f(x:i64){for _ in [1]{x%-1;}}', 'panic-capable division'),
    ('fn f(i:usize){for a in [[1u64,2]]{a[i];}}', 'closed scalar constant'),
])
def test_actual_check_does_not_replace_owned_capability_and_panic_checks(source, message, tmp_path, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    from tests.rust.test_rust_certified_references import certify
    path = tmp_path / 'original.rs'
    path.write_text(source)
    checked = certify(path, tmp_path / 'certifier')
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unsupported checked source reached IR'))
    with pytest.raises(RustError, match=message):
        compile_rust(source, rustc_check=checked)
    assert path.read_text() == source


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'} and shutil.which('rustc') and shutil.which('cc') and os.environ.get('PCC_RUSTC_INSTALL_RECEIPT') else 'requires audited actual rustc installation and Linux native reference linker')
def test_original_for_programs_have_actual_checked_full_width_native_pairs(tmp_path, monkeypatch):
    from tests.rust import test_rust_certified_references as certification
    from tests.rust import test_rust_method_reference as reference
    from tests.rust import test_rust_patterns as owned
    from pcc.frontends.rust.moves import MoveChecker
    for name in PROGRAMS:
        directory = tmp_path / name
        directory.mkdir()
        path = Path(__file__).with_name('programs') / (name + '.rs')
        checked = certification.certify(path, directory / 'certifier')
        with monkeypatch.context() as patch:
            patch.setattr(MoveChecker, 'check', lambda *a: pytest.fail('new for used legacy move admission'))
            patch.setattr(MoveChecker, 'check_function', lambda *a: pytest.fail('new for used legacy abstract admission'))
            patch.setattr(reference, 'PROGRAMS', (name,))
            patch.setattr(owned, 'compile_source', lambda source, **options: compile_rust(source, rustc_check=checked))
            reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(directory, patch)
