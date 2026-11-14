"""Tuple patterns in let statements.

https://doc.rust-lang.org/reference/statements.html#let-statements
https://doc.rust-lang.org/reference/patterns.html#tuple-patterns
`let (a, (b, mut c)): T = init;` binds through an irrefutable pattern of
tuple, identifier and wildcard sub-patterns. The initializer is evaluated
once; the pattern is checked like a match arm (shape, E0416 duplicates)
but its bindings join the enclosing block scope and may shadow earlier
locals. `let mut (a, b)` is a syntax error; other (possibly refutable)
pattern forms and let-else stay explicit. fallible-streaming-iterator's
`let (lower, upper) = self.it.size_hint();` (lib.rs:836) has this shape;
the program below is original and was compared with local rustc 1.92.0.
"""
import shutil

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser
from pcc.frontends.rust.types import TupleType
from tests.rust.str_execution import UNAVAILABLE, run_owned_u64_function, run_rustc_str_function

PROGRAM = '''fn split(v: u64) -> (u64, (u32, u64)) { (v & 0xffff_ffff_0000_0000, ((v / 256) as u32 & 0xff, v & 0xff)) }
pub fn answer() -> u64 {
    let x = 0xfedcba9876543210u64;
    let (high, (mid, mut low)) = split(x);
    let (_, y): (u64, u64) = (1, 2);
    low += y;
    let (x, ()) = (high + mid as u64 + low, ());
    x
}
'''
EXPECTED = 0xfedcba9800000044


def test_pattern_let_keeps_pattern_annotation_and_initializer():
    function, = Parser('fn f() { let (a, (_, mut b)): (u8, (u8, u8)) = (1, (2, 3)); }').parse()
    statement = function.body.children[0]
    pattern, annotation = statement.value
    assert statement.kind == 'pattern_let' and pattern.kind == 'tuple'
    assert annotation == TupleType(('u8', TupleType(('u8', 'u8'))))
    assert [child.kind for child in pattern.children[1].children] == ['wildcard', 'binding']


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
def test_program_runs_with_owned_compile_assemble_and_link(tmp_path, monkeypatch):
    assert run_owned_u64_function(PROGRAM, 'answer', tmp_path, monkeypatch) == EXPECTED


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_returns_the_same_value(tmp_path):
    output = run_rustc_str_function(PROGRAM, 'answer', tmp_path, output='&{call}.to_le_bytes()')
    assert int.from_bytes(output, 'little') == EXPECTED


# Former boundary row of test_rust_wildcard_let ('expected an identifier').
MOVED_TUPLE_LET = 'fn f(){let (x,y)=(1,2);}'


def test_former_tuple_let_row_now_checks_and_lowers():
    compile_rust(MOVED_TUPLE_LET, experimental_rustc_checked=True)


def test_bindings_join_the_block_scope_and_shadow():
    compile_rust('pub fn f()->u64{let x = 5; let (x, _) = (x + 37, 9); x}', experimental_rustc_checked=True)


@pytest.mark.parametrize('source,message', [
    ('pub fn f()->u64{let (a, b) = (1, 2, 3); a}', 'tuple pattern shape does not match'),
    ('pub fn f()->u64{let (a, a) = (1, 2); a}', 'duplicate binding in pattern'),
    ('pub fn f()->u64{let mut (a, b) = (1, 2); a}', '`mut` must be attached to each individual binding'),
    ('pub fn f()->u64{let (a, b) = (1u64, 2u64); a = 3; a+b}', "cannot assign to immutable binding 'a'"),
    # Possibly refutable forms and let-else are not modeled.
    ('pub fn f()->u64{let (1, b) = (1u64, 2u64); b}', 'require irrefutability checking'),
    ('pub fn f()->u64{let (a, b) = (1u64, 2u64) else { return 0 }; a}', 'let-else statements are not implemented'),
])
def test_invalid_and_unmodeled_let_patterns(source, message):
    with pytest.raises(RustError, match=message):
        compile_rust(source, experimental_rustc_checked=True)
