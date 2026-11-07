"""The question-mark operator parses as a postfix operator with an explicit gap.

Rust Reference:
https://doc.rust-lang.org/reference/expressions/operator-expr.html#the-question-mark-operator
https://doc.rust-lang.org/reference/expressions.html#expression-precedence
The operator binds like field, call and index continuations and before any
unary operator. Its meaning needs core Try implementors (Result, Option) and
From conversion on the early return; no core model exists, so every use stays
an explicit semantic gap. The unchanged fallible-streaming-iterator default
method `next` is the first project input that needed the syntax.
"""
from pathlib import Path
import re

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser
from tests.rust.certification_support import actual_check_prerequisite, certify_source

GAP = "the ? operator requires owned core Try/Result/Option and From lowering"
SHAPES = (
    ('fn f(){g()?;}', 'block(discard(try(call(name))))'),
    ('fn f(){-g()?;}', 'block(discard(unary(try(call(name)))))'),
    ('fn f(){&g()?;}', 'block(discard(borrow(try(call(name)))))'),
    ('fn f(){x.m()?.n()?;}', 'block(discard(try(call(field(try(call(field(name))))))))'),
    ('fn f(){g()? + 1;}', 'block(discard(binary(try(call(name)),integer)))'),
    ('fn f(){g()??;}', 'block(discard(try(try(call(name)))))'),
    ('fn f(){a[g()?];}', 'block(discard(index(name,try(call(name)))))'),
)
# Valid Rust: rustc accepts each unchanged original; PCC keeps an explicit gap.
VALID = (
    ('result-early-return', 'fn g()->Result<u64,u8>{Ok(1)}pub fn f()->Result<u64,u8>{let x=g()?;Ok(x+1)}'),
    ('option-early-return', 'fn g()->Option<u64>{Some(1)}pub fn f()->Option<u64>{Some(g()?+1)}'),
    ('default-method-shape',
     'pub trait It{type Item;type Error;fn advance(&mut self)->Result<(),Self::Error>;'
     'fn get(&self)->Option<&Self::Item>;'
     'fn next(&mut self)->Result<Option<&Self::Item>,Self::Error>{self.advance()?;Ok((*self).get())}}'),
)
# Invalid Rust: a non-Try operand. rustc rejects it (E0277); so does PCC.
INVALID = (
    ('integer-operand', 'fn g()->u64{1}pub fn f()->u64{g()?}', GAP, 'E0277'),
    ('local-enum-operand', 'enum R{A(u64)}fn g()->R{R::A(1)}pub fn f()->u64{g()?;1}', GAP, 'E0277'),
)


def shape(expr):
    children = [child for child in (expr.children or []) if hasattr(child, 'kind')]
    return expr.kind + ('(' + ','.join(shape(child) for child in children) + ')' if children else '')


@pytest.mark.parametrize('source,expected', SHAPES, ids=[row[1] for row in SHAPES])
def test_question_mark_is_a_postfix_operator(source, expected):
    assert shape(Parser(source).parse()[0].body) == expected


def test_original_default_method_line_is_unchanged():
    root = Path(__file__).resolve().parents[2]
    lines = (root / 'projects/fallible-streaming-iterator-0.1.9/src/lib.rs').read_text().splitlines()
    assert lines[51] == '        self.advance()?;'


@pytest.mark.parametrize('name,text,message,code', INVALID, ids=[row[0] for row in INVALID])
def test_question_mark_stays_an_explicit_semantic_gap(name, text, message, code, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('try operator reached IR'))
    with pytest.raises(RustError, match=re.escape(message)):
        compile_rust(text, experimental_rustc_checked=True)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_accepts_question_mark_originals_with_explicit_owned_gap(tmp_path):
    for name, text in VALID:
        checked = certify_source(text, tmp_path / name)
        with pytest.raises(RustError, match="unsupported Rust type 'Result'|unsupported Rust type 'Option'|" + re.escape(GAP)):
            checked.compile_owned()


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_non_try_question_mark_operands(tmp_path, monkeypatch):
    from tests.rust import test_rust_supertraits as oracle
    monkeypatch.setattr(oracle, 'INVALID', INVALID)
    oracle.test_genuine_rustc_rejects_the_same_invalid_obligations(tmp_path, monkeypatch)
