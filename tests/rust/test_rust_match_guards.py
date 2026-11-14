"""Match guards.

https://doc.rust-lang.org/reference/expressions/match-expr.html#match-guards
`pattern if guard => arm`: the guard is a bool expression evaluated after the
pattern matches, with the arm's bindings in scope. A false guard continues
with the next arm, so a guarded arm covers nothing for exhaustiveness
(E0004); a non-bool guard is E0308. Bindings are seen by shared reference in
the guard and the scrutinee moves into by-value bindings only once the guard
holds; moving out of a binding (E0507), assigning a borrowed scrutinee
(E0506) and mutating a binding (E0594) in a guard are borrow-check rules, so
guards are admitted only after an actual rustc check. `matches!(v, p if g)`
expands to the same guarded arm. fallible-streaming-iterator's
`Some(x) if x < self.n => Some(x)` (projects/fallible-streaming-iterator-0.1.9,
MIT/Apache-2.0, lib.rs:841) has this shape; the programs below are original
and were compared with local rustc 1.92.0.
"""
import shutil
import subprocess
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.semantics import Checker
from pcc.frontends.rust.syntax import Parser
from tests.rust.certification_support import certify_source, darwin_check_prerequisite
from tests.rust.str_execution import run_owned_u64_function, run_rustc_str_function

PROGRAM = '''pub enum Shape { Dot(u64), Pair(u64, u64), Empty }
pub struct Inner { v: u64 }
pub enum Holder { Full(Inner), Vacant }
fn small(x: u64, limit: u64) -> bool { x < limit }
fn classify(s: Shape, limit: u64) -> u64 {
    match s {
        Shape::Dot(x) if small(x, limit) => x,
        Shape::Dot(x) => limit + x * 2,
        Shape::Pair(a, b) if a == b && a != 0 => a * 100,
        Shape::Pair(a, b) if { let s = a + b; s > limit } => a + b + 1000,
        Shape::Pair(_, b) => b,
        Shape::Empty => 7,
    }
}
fn take(h: Holder, limit: u64) -> u64 {
    match h {
        Holder::Full(n) if n.v > limit => n.v,
        Holder::Full(n) => n.v + 1,
        Holder::Vacant => 0,
    }
}
fn tuple(v: (u64, bool), n: u64) -> u64 {
    match v { (x, true) if x > n => 1, (x, _) if x == n => 2, _ => 3 }
}
pub fn answer() -> u64 {
    let mut total = classify(Shape::Dot(5), 10);
    total = total * 100 + classify(Shape::Dot(12), 10);
    total = total * 1000 + classify(Shape::Pair(4, 4), 10);
    total = total * 10000 + classify(Shape::Pair(6, 9), 10);
    total = total * 10 + classify(Shape::Pair(1, 2), 10);
    total = total * 10 + classify(Shape::Empty, 10);
    total = total * 1000 + take(Holder::Full(Inner { v: 500 }), 100);
    total = total * 100 + take(Holder::Full(Inner { v: 50 }), 100);
    total = total * 10 + take(Holder::Vacant, 1);
    total = total * 10 + tuple((9, true), 3) + tuple((3, true), 3) * 10 / 10;
    total
}
'''
EXPECTED = 5344001015275005103


def model(source):
    """Isolated semantic model, never public certification authorization."""
    parser = Parser(source)
    functions = parser.parse()
    Checker(functions, parser.records, parser.enums, parser.implementations, parser.traits,
            externally_checked=True).check()
    return functions


def test_guarded_arm_keeps_guard_and_body():
    function, = Parser('fn f(o: Option<u64>, n: u64) -> Option<u64> { match o { Some(x) if x < n => Some(x), _ => Some(n) } }').parse()
    match = function.body.children[0]
    guarded, plain = match.children[1:]
    assert (guarded.kind, guarded.token.text, guarded.token.line, guarded.token.column) == ('match_guard', 'if', 1, 65)
    guard, body = guarded.children
    assert (guard.kind, guard.value, body.kind) == ('binary', '<', 'call')
    assert plain.kind == 'call'


def test_checked_model_types_the_guard_with_the_arm_bindings():
    model(PROGRAM)
    matches, = model('pub fn f(x: u64) -> bool { matches!(x, 1 | 2 if x > 1) }')
    selected = matches.body.children[0].children[1]
    assert (selected.kind, selected.children[1].value) == ('match_guard', True)


@pytest.mark.parametrize('source,message', [
    # E0308: the guard is a bool expression.
    ('pub fn f(v: u64) -> u64 { match v { x if x => 1, _ => 0 } }', 'type mismatch: expected bool, found u64'),
    # E0004: a guarded arm covers nothing.
    ('pub fn f(v: u64) -> u64 { match v { x if x > 3 => 1, 0 => 2 } }', 'non-exhaustive match patterns'),
    ('pub fn f(v: bool) -> u64 { match v { true => 1, false if v => 0 } }', 'non-exhaustive match patterns'),
    ('pub fn f(v: u64) -> u64 { match v { _ if y > 1 => 1, _ => 0 } }', "unknown local 'y'"),
])
def test_invalid_guards_are_errors_in_the_checked_model(source, message):
    with pytest.raises(RustError, match=message):
        model(source)


def test_without_an_actual_check_guards_are_an_explicit_boundary():
    with pytest.raises(RustError, match='^1:39: match guard ownership/borrowing admission requires an actual external rustc check$'):
        compile_rust('pub fn f(v: u64) -> u64 { match v { x if x > 3 => 1, _ => 0 } }', experimental_rustc_checked=True)


# Former boundary rows of test_rust_matches_macro ('matches! guards require
# match guards') and test_rust_patterns ('match guards'), moved verbatim.
MOVED_MATCHES_GUARD = 'pub fn f(x: u64) -> bool { matches!(x, 0 if x > 1) }'
MOVED_PATTERNS_GUARD = "fn f(x:bool){match x{true if true=>(),_=>()}}"


@pytest.mark.parametrize('source', [MOVED_MATCHES_GUARD, MOVED_PATTERNS_GUARD])
def test_former_guard_rows_reach_the_guard_boundary_and_check(source):
    with pytest.raises(RustError, match='match guard ownership/borrowing admission requires an actual external rustc check'):
        compile_rust(source, experimental_rustc_checked=True)
    model(source)


DARWIN_LANE = darwin_check_prerequisite() or (
    None if sys.platform == 'darwin' else 'owned execution here is the arm64 Darwin lane')


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=DARWIN_LANE)
def test_checked_program_runs_with_owned_compile_assemble_and_link(tmp_path, monkeypatch):
    check = certify_source(PROGRAM, tmp_path / 'certifier', target_triple='aarch64-apple-darwin')
    assert run_owned_u64_function(PROGRAM, 'answer', tmp_path, monkeypatch,
                                  target_triple='aarch64-apple-darwin', rustc_check=check) == EXPECTED


# Borrow-check rules of guards, enforced by the actual check.
BORROW_ERRORS = (
    ('pub struct N { v: u64 } pub enum E { A(N), B } '
     'pub fn f(e: E) -> u64 { match e { E::A(n) if { let m = n; m.v > 1 } => 1, _ => 0 } }', 'E0507'),
    ('pub fn f(mut v: u64) -> u64 { match v { x if { v = 1; x > 0 } => 1, _ => 0 } }', 'E0506'),
    ('pub fn f(v: u64) -> u64 { match v { mut x if { x += 1; x > 0 } => 1, _ => 0 } }', 'E0594'),
)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=DARWIN_LANE)
@pytest.mark.parametrize('source,code', BORROW_ERRORS, ids=[row[1] for row in BORROW_ERRORS])
def test_actual_check_rejects_guard_borrow_errors(source, code, tmp_path):
    from pcc.frontends.rust import RustExternalCheckError
    with pytest.raises(RustExternalCheckError, match=f'error\\[{code}\\]'):
        certify_source(source, tmp_path / 'certifier', target_triple='aarch64-apple-darwin')


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_gives_the_same_value_and_verdicts(tmp_path):
    output = run_rustc_str_function(PROGRAM, 'answer', tmp_path, output='&{call}.to_le_bytes()')
    assert int.from_bytes(output, 'little') == EXPECTED
    cases = [(source, code) for source, code in BORROW_ERRORS] + [
        ('pub fn f(v: u64) -> u64 { match v { x if x => 1, _ => 0 } }', 'E0308'),
        ('pub fn f(v: u64) -> u64 { match v { x if x > 3 => 1, 0 => 2 } }', 'E0004'),
        (MOVED_MATCHES_GUARD, None),
        (MOVED_PATTERNS_GUARD, None),
    ]
    for index, (source, code) in enumerate(cases):
        path = tmp_path / f'case{index}.rs'
        path.write_text(source + '\n')
        run = subprocess.run([shutil.which('rustc'), '--crate-type=lib', '--edition=2021', '--emit=metadata',
                              str(path), '-o', str(tmp_path / f'case{index}.rmeta')],
                             capture_output=True, text=True, timeout=60)
        if code is None:
            assert run.returncode == 0, run.stderr
        else:
            assert run.returncode != 0 and f'error[{code}]' in run.stderr, run.stderr
