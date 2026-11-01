"""Blocklike statements terminate before ambiguous postfix/infix expressions.

Rust Reference (retrieved 2026-11-01):
https://doc.rust-lang.org/reference/statements.html#expression-statements
https://doc.rust-lang.org/reference/expressions/match-expr.html
https://doc.rust-lang.org/reference/expressions/if-expr.html
The original for_array_iterable_outer_exit.rs remains byte-identical and is
the actual runtime differential regression; no source separator is inserted.
"""
import os

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser


@pytest.mark.parametrize('block,kind', [
    ('if true{}', 'if'),
    ('if true{}else if false{}else{}', 'if'),
    ('{}', 'block'),
    ('unsafe{}', 'unsafe'),
    ('while false{}', 'while'),
    ('for _ in [1u64]{}', 'for'),
    ('match true{true=>{},false=>{}}', 'match'),
])
def test_array_after_complete_blocklike_is_a_separate_statement_or_tail(block, kind):
    for suffix,statements in [('[4u64,5]', 1), ('[4u64,5];', 2)]:
        source = 'fn f(){' + block + suffix + '}'
        body = Parser(source).parse()[0].body
        assert body.value == statements
        assert body.children[0].kind == 'statement'
        assert body.children[0].children[0].kind == kind
        array = body.children[1] if statements == 1 else body.children[1].children[0]
        assert array.kind == 'array' and len(array.children) == 2


VALUE_FORMS = [
    ('fn f()->u64{(if true{[1u64,2]}else{[3,4]})[0]}', 'index'),
    ('fn f()->u64{({[1u64,2]})[0]}', 'index'),
    ('fn f()->u64{(match true{true=>[1u64,2],false=>[3,4]})[0]}', 'index'),
    ('fn f()->usize{{[1u64,2]}.len()}', 'call'),
    ('fn f()->usize{if true{[1u64,2]}else if false{[3,4]}else{[5,6]}.len()}', 'call'),
    ('fn f()->[u64;2]{if true{[1u64,2]}else{[3,4]}}', 'if'),
    ('fn f()->u64{let a={ [1u64,2] }[0];a}', 'name'),
    ('fn f()->u64{let a=if true{[1u64,2]}else{[3,4]}[0];a}', 'name'),
    ('fn f()->u64{let a=if true{[1u64,2]}else if false{[3,4]}else{[5,6]}[0];a}', 'name'),
    ('fn f(x:i32){match x{0=>{} -1=>{} _=>{}}}', 'match'),
]


@pytest.mark.parametrize('source,kind', VALUE_FORMS)
def test_grouped_value_tail_dot_and_match_arm_contexts_are_preserved(source, kind):
    body = Parser(source).parse()[0].body
    assert body.children[-1].kind == kind
    assert 'define' in compile_rust(source, experimental_rustc_checked=True).ir_text


def test_omitted_semicolon_requires_unit_but_explicit_discard_does_not():
    original = 'fn f()->[u64;2]{if true{[1u64,2]}else{[3,4]} [5,6]}'
    with pytest.raises(RustError, match='type mismatch'):
        compile_rust(original, experimental_rustc_checked=True)
    explicit = 'fn f()->[u64;2]{if true{[1u64,2]}else{[3,4]};[5,6]}'
    assert 'define' in compile_rust(explicit, experimental_rustc_checked=True).ir_text


@pytest.mark.pcc_gate(unavailable=None if os.environ.get('PCC_RUSTC_INSTALL_RECEIPT') else 'explicit audited rustc installation receipt required')
@pytest.mark.parametrize('source,kind', VALUE_FORMS)
def test_value_forms_have_actual_same_input_external_check_and_owned_object(source, kind, tmp_path):
    from tests.rust.test_rust_certified_references import certify
    path = tmp_path / 'original.rs'
    path.write_text(source)
    checked = certify(path, tmp_path / 'certifier')
    assert compile_rust(source, rustc_check=checked).emit_object().startswith(b'\x7fELF')
    assert path.read_text() == source
