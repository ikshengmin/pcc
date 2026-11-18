"""Match guards.

https://doc.rust-lang.org/reference/expressions/match-expr.html#match-guards
`pattern if guard => arm`: the guard is a bool expression evaluated after the
pattern matches, with the arm's bindings in scope. A false guard continues
with the next arm, so a guarded arm covers nothing for exhaustiveness
(E0004); a non-bool guard is E0308. Bindings are seen by shared reference in
the guard and the scrutinee moves into by-value bindings only once the guard
holds. PCC owns the guard borrow rules (pcc/frontends/rust/moves.py,
GuardScope) and needs no rustc to admit or reject a guard: moving out of a
binding (E0507), assigning (E0594) or mutably borrowing (E0596) one, moving
(E0505) or assigning (E0506) a place a binding borrows, and assigning the
matched or a tested place or a prefix of one (E0510) are errors; moving a
matched place no binding covers, which rustc accepts, is an explicit gap.
`matches!(v, p if g)` expands to the same guarded arm. fallible-streaming-iterator's
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
from tests.rust.str_execution import UNAVAILABLE, run_owned_u64_function, run_rustc_str_function

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


N_E = 'pub struct N { v: u64 } pub enum E { A(N), B } '
EAT = 'fn eat(_e: E) -> bool { true } '
# (name, original source, PCC's owned verdict, rustc 1.92.0's verdict): an
# error code, 'ok', or 'gap' where rustc accepts and PCC stays explicit.
GUARD_BORROWS = (
    ('move-binding', N_E + 'pub fn f(e: E) -> u64 { match e { E::A(n) if { let m = n; m.v > 1 } => 1, _ => 0 } }',
     'E0507', 'E0507'),
    ('assign-binding', 'pub fn f(v: u64) -> u64 { match v { mut x if { x = 1; x > 0 } => x, _ => 0 } }', 'E0594', 'E0594'),
    ('compound-binding', 'pub fn f(v: u64) -> u64 { match v { mut x if { x += 1; x > 0 } => x, _ => 0 } }',
     'E0594', 'E0594'),
    ('assign-bound-scrutinee', 'pub fn f(mut v: u64) -> u64 { match v { x if { v = 1; x > 0 } => 1, _ => 0 } }',
     'E0506', 'E0506'),
    ('compound-bound-scrutinee', 'pub fn f(mut v: u64) -> u64 { match v { x if { v += 1; x > 0 } => 1, _ => 0 } }',
     'E0506', 'E0506'),
    ('move-bound-scrutinee', N_E + EAT + 'pub fn f(e: E) -> u64 { match e { E::A(n) if eat(e) => n.v, _ => 0 } }',
     'E0505', 'E0505'),
    ('move-bound-scrutinee-after-last-use', N_E + EAT
     + 'pub fn f(e: E) -> u64 { match e { E::A(n) if { let ok = n.v > 1; eat(e) && ok } => 1, _ => 0 } }',
     'E0505', 'E0505'),
    ('assign-matched', 'pub fn f(mut v: u64) -> u64 { match v { 0 if { v = 1; true } => 1, _ => 0 } }', 'E0510', 'E0510'),
    ('assign-matched-prefix', 'pub struct P { a: u64, b: u64 } '
     'pub fn f(mut p: P) -> u64 { match p.a { 0 if { p = P { a: 1, b: 1 }; true } => 1, _ => 0 } }', 'E0510', 'E0510'),
    ('assign-tested', 'pub fn f(mut t: ((u64, u64), u64)) -> u64 { match t.0 { (0, _) if { t.0.0 = 2; true } => 1, _ => 0 } }',
     'E0510', 'E0510'),
    ('move-outer-in-two-guards', 'pub struct N { v: u64 } fn eat(n: N) -> bool { n.v > 0 } '
     'pub fn f(v: u64) -> u64 { let s = N { v: 1 }; match v { 1 if eat(s) => 1, 2 if eat(s) => 2, _ => 0 } }',
     'E0382', 'E0382'),
    ('assign-untested', 'pub fn f(mut t: ((u64, u64), u64)) -> u64 { match t.0 { (0, _) if { t.0.1 = 2; true } => 1, _ => t.0.1 } }',
     'ok', 'ok'),
    ('assign-sibling', 'pub struct P { a: u64, b: u64 } '
     'pub fn f(mut p: P) -> u64 { match p.a { 0 if { p.b = 2; true } => 1, _ => p.b } }', 'ok', 'ok'),
    ('assign-outer', 'pub fn f(v: u64) -> u64 { let mut c = 0; let r = match v { x if { c = 5; x > 3 } => 1, _ => 2 }; r + c }',
     'ok', 'ok'),
    ('move-outer', 'pub struct N { v: u64 } fn eat(n: N) -> bool { n.v > 0 } '
     'pub fn f(v: u64) -> u64 { let s = N { v: 1 }; match v { x if eat(s) && x > 0 => 1, _ => 0 } }', 'ok', 'ok'),
    ('copy-binding', 'pub fn f(v: u64) -> u64 { match v { x if { let y = x; y > 0 } => 1, _ => 0 } }', 'ok', 'ok'),
    ('read-matched', 'pub fn f(v: u64) -> u64 { match v { x if v == x => 1, _ => 0 } }', 'ok', 'ok'),
    ('move-binding-in-arm', N_E + 'pub fn f(e: E) -> u64 { match e { E::A(n) if n.v > 1 => { let m = n; m.v } _ => 0 } }',
     'ok', 'ok'),
    ('assign-matched-in-arm', 'pub fn f(mut v: u64) -> u64 { match v { x if x > 0 => { v = 1; v } _ => 0 } }', 'ok', 'ok'),
    ('move-temporary-scrutinee', N_E + EAT
     + 'fn mk() -> E { E::B } pub fn f() -> u64 { match mk() { E::A(_) if eat(mk()) => 1, _ => 0 } }', 'ok', 'ok'),
    ('move-unbound-matched', N_E + EAT + 'pub fn f(e: E) -> u64 { match e { E::A(_) if eat(e) => 1, _ => 0 } }',
     'gap', 'ok'),
    ('move-unbound-matched-then-test', N_E + EAT
     + 'pub fn f(e: E) -> u64 { match e { E::A(_) if eat(e) => 1, E::B => 2, _ => 0 } }', 'gap', 'ok'),
)


@pytest.mark.parametrize('name,source,owned,_', GUARD_BORROWS, ids=[row[0] for row in GUARD_BORROWS])
def test_guard_borrow_rules_are_decided_without_rustc(name, source, owned, _, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('guard admission ran an external tool'))
    if owned == 'ok':
        compile_rust(source, experimental_rustc_checked=True)
        return
    expected = {'gap': 'requires decision-tree match lowering, which is not implemented',
                'E0382': 'use of moved or possibly moved value'}.get(owned, rf'\({owned}\)$')
    with pytest.raises(RustError, match=expected):
        compile_rust(source, experimental_rustc_checked=True)


def test_closures_in_guards_stay_inside_the_closure_boundary():
    # Closure captures in a guard would need guard-aware capture rules; every
    # closure is still rejected before the move checker runs.
    with pytest.raises(RustError, match='closure expressions require closure types, captures'):
        compile_rust('pub fn f(v: u64) -> u64 { match v { x if { let c = |y: u64| y > 1; c(x) } => 1, _ => 0 } }',
                     experimental_rustc_checked=True)


# Former boundary rows of test_rust_matches_macro ('matches! guards require
# match guards') and test_rust_patterns ('match guards'), moved verbatim.
MOVED_MATCHES_GUARD = 'pub fn f(x: u64) -> bool { matches!(x, 0 if x > 1) }'
MOVED_PATTERNS_GUARD = "fn f(x:bool){match x{true if true=>(),_=>()}}"


@pytest.mark.parametrize('source', [MOVED_MATCHES_GUARD, MOVED_PATTERNS_GUARD])
def test_former_guard_rows_compile_with_owned_guard_checking(source):
    compile_rust(source, experimental_rustc_checked=True)
    model(source)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
def test_program_runs_without_an_actual_rustc_check(tmp_path, monkeypatch):
    assert run_owned_u64_function(PROGRAM, 'answer', tmp_path, monkeypatch) == EXPECTED


DARWIN_LANE = darwin_check_prerequisite() or (
    None if sys.platform == 'darwin' else 'owned execution here is the arm64 Darwin lane')


@pytest.mark.pcc_source_group(legacy='rust-native-reference', name='rust-darwin-owned')
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=DARWIN_LANE)
def test_checked_program_runs_with_owned_compile_assemble_and_link(tmp_path, monkeypatch):
    check = certify_source(PROGRAM, tmp_path / 'certifier', target_triple='aarch64-apple-darwin')
    assert run_owned_u64_function(PROGRAM, 'answer', tmp_path, monkeypatch,
                                  target_triple='aarch64-apple-darwin', rustc_check=check) == EXPECTED


# Borrow-check rules of guards, enforced by the actual check.
# This target-matched metadata check does not execute a Darwin binary. Keep
# its audited compiler/target libraries mandatory on every supported host.
BORROW_ERRORS = (
    ('pub struct N { v: u64 } pub enum E { A(N), B } '
     'pub fn f(e: E) -> u64 { match e { E::A(n) if { let m = n; m.v > 1 } => 1, _ => 0 } }', 'E0507'),
    ('pub fn f(mut v: u64) -> u64 { match v { x if { v = 1; x > 0 } => 1, _ => 0 } }', 'E0506'),
    ('pub fn f(v: u64) -> u64 { match v { mut x if { x += 1; x > 0 } => 1, _ => 0 } }', 'E0594'),
)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=darwin_check_prerequisite())
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
        (source, None if verdict == 'ok' else verdict) for _, source, _, verdict in GUARD_BORROWS] + [
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
