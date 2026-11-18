"""Reference patterns `&p` / `&mut p`, and `let` patterns matched in place.

Rust Reference patterns.html#reference-patterns: `(& | &&) mut? Pattern`
matches a reference of the same mutability and checks the subpattern against
the referent (otherwise E0308). Bindings inside copy out of the referent, so
a non-Copy one is E0507 (cannot move out of a shared/mutable reference).
Match coverage treats a reference type as one constructor `&`; `&p`
alternatives check on their own. Lowering loads the referent for the
subpattern's tests and bindings.

statements.html#let-statements: a `let` pattern whose initializer is a place
is matched in place, as a match scrutinee is: only the parts bound by value
move (`let (a, _) = t;` leaves `t.1` usable; `let &mut x = r;` leaves `r`
usable), and moving through a reference or out of an array index is E0507 /
E0508. Default binding modes (a non-reference pattern against a reference),
referents that hold references and nested references keep explicit
boundaries. Every corpus row and program is original; verdicts and results
were compared with a local rustc 1.92.0 build (-O), which stays a labeled
reference.
"""
import shutil
import subprocess

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.str_execution import UNAVAILABLE, run_owned_u64_function, run_rustc_str_function
from tests.rust.test_rust_shift_operators import rustc_verdict

S = 'pub struct S { v: u32 }\n'

# (name, original source, verdict of a rustc 1.92.0 build).
CORPUS = (
    ('let-ref', 'pub fn f(r: &u32) -> u32 { let &x = r; x + 1 }', 'ok'),
    ('let-ref-mut', 'pub fn f(r: &mut u32) -> u32 { let &mut x = r; x }', 'ok'),
    ('let-ref-mut-vs-shared', 'pub fn f(r: &u32) -> u32 { let &mut x = r; x }', 'E0308'),
    ('let-ref-vs-mut', 'pub fn f(r: &mut u32) -> u32 { let &x = r; x }', 'E0308'),
    ('let-ref-vs-value', 'pub fn f(v: u32) -> u32 { let &x = v; x }', 'E0308'),
    ('let-ref-non-copy', S + 'pub fn f(r: &S) -> u32 { let &s = r; s.v }', 'E0507'),
    ('let-ref-mut-non-copy', S + 'pub fn f(r: &mut S) -> u32 { let &mut s = r; s.v }', 'E0507'),
    ('let-ref-tuple', 'pub fn f(r: &(u32, bool)) -> u32 { let &(a, b) = r; if b { a } else { 0 } }', 'ok'),
    ('let-ref-tuple-vs-int', 'pub fn f(r: &u32) -> u32 { let &(a, b) = r; a }', 'E0308'),
    ('let-ref-wildcard', 'pub fn f(r: &u32) -> u32 { let &_ = r; 1 }', 'ok'),
    ('let-ref-mut-tuple', 'pub fn f(r: &mut (u32, u32)) -> u32 { let &mut (a, b) = r; a + b }', 'ok'),
    ('let-ref-mut-then-write', 'pub fn f(r: &mut u32) -> u32 { let &mut x = r; *r = x + 1; *r }', 'ok'),
    ('match-ref-tuple', 'pub fn f(r: &(u32, bool)) -> u32 { match r { &(a, true) => a, &(_, false) => 0 } }', 'ok'),
    ('match-ref-literal', 'pub fn f(r: &u32) -> u32 { match r { &0 => 10, &n => n } }', 'ok'),
    ('match-ref-nonexhaustive', 'pub fn f(r: &u32) -> u32 { match r { &0 => 10 } }', 'E0004'),
    ('match-ref-bool', 'pub fn f(r: &bool) -> u32 { match r { &true => 1, &false => 0 } }', 'ok'),
    ('match-ref-enum', 'pub enum E { A, B(u32) }\npub fn f(r: &E) -> u32 { match r { &E::A => 0, &E::B(n) => n } }',
     'ok'),
    ('match-ref-enum-non-copy', S + 'pub enum E { A(S) }\npub fn f(r: &E) -> u32 { match r { &E::A(s) => s.v } }',
     'E0507'),
    ('match-ref-or', 'pub fn f(r: &u32) -> u32 { match r { &1 | &2 => 1, _ => 0 } }', 'ok'),
    ('match-ref-inner-or', 'pub fn f(r: &u32) -> u32 { match r { &(1 | 2) => 1, _ => 0 } }', 'ok'),
    ('match-ref-guard', 'pub fn f(r: &u32) -> u32 { match r { &n if n > 3 => n, &n => n + 1 } }', 'ok'),
    ('match-binds-reference', 'pub fn f(r: &u32) -> u32 { match r { y => *y } }', 'ok'),
    ('if-let-ref', 'pub fn f(r: &u32) -> u32 { if let &5 = r { 1 } else { 0 } }', 'ok'),
    ('while-let-ref', 'pub fn f(r: &u32) -> u32 { let mut n = 0; while let &3 = r { n += 1; if n > 2 { break; } } n }',
     'ok'),
    ('ref-pattern-keeps-loan', 'pub fn f() -> u32 { let mut a = 5u32; let r = &a; a = 6; let &x = r; x }', 'E0506'),
    ('ref-pattern-after-loan-ends', 'pub fn f() -> u32 { let mut a = 5u32; let r = &a; let &x = r; a = 6; x + a }',
     'ok'),
    ('let-tuple-partial-move', S + 'pub fn f(t: (S, S)) -> u32 { let (a, _) = t; a.v + t.1.v }', 'ok'),
    ('let-tuple-moved-whole', S + 'pub fn f(t: (S, S)) -> u32 { let (a, b) = t; let u = t; a.v + b.v + u.0.v }',
     'E0382'),
    ('let-tuple-moved-part', S + 'pub fn f(t: (S, S)) -> u32 { let (a, _) = t; a.v + t.0.v }', 'E0382'),
    ('let-tuple-wildcards-move-nothing', S + 'pub fn f(t: (S, S)) -> (S, S) { let (_, _) = t; t }', 'ok'),
    ('let-tuple-through-ref-non-copy', S + 'pub fn f(r: &(S, u32)) -> u32 { let (a, b) = *r; a.v + b }', 'E0507'),
    ('let-tuple-through-ref-copy', 'pub fn f(r: &(u32, u32)) -> u32 { let (a, b) = *r; a + b }', 'ok'),
    ('let-tuple-from-index-non-copy', S + 'pub fn f(a: [(S, u32); 2]) -> u32 { let (x, y) = a[0]; x.v + y }', 'E0508'),
    ('let-tuple-field-place', S + 'pub struct P { t: (S, u32) }\npub fn f(p: P) -> u32 { let (a, b) = p.t; a.v + b }',
     'ok'),
)
MESSAGES = {
    'E0308': r'mismatched types.*\(rustc E0308\)',
    'E0507': r'cannot move (out of a (shared|mutable) reference: binding .* is not Copy \(rustc E0507\)'
             r'|a non-Copy value out of a shared reference)',
    'E0508': 'cannot move a non-Copy value out of an array index',
    'E0004': 'non-exhaustive match patterns',
    'E0506': r'because it is borrowed \(E0506\)',
    'E0382': "use of moved or possibly moved value 't'",
}


@pytest.mark.parametrize('name,source,verdict', CORPUS, ids=[row[0] for row in CORPUS])
def test_reference_pattern_verdicts_match_a_rustc_build_without_running_rustc(name, source, verdict, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('owned admission ran a tool'))
    if verdict == 'ok':
        assert not compile_rust(source).externally_checked
    else:
        with pytest.raises(RustError, match=MESSAGES[verdict]):
            compile_rust(source)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_builds_give_the_same_verdicts(tmp_path):
    for index, (name, source, verdict) in enumerate(CORPUS):
        path = tmp_path / f'case{index}.rs'
        path.write_text(source + '\n')
        run = subprocess.run([shutil.which('rustc'), '--crate-type=lib', '--edition=2021', '--emit=obj', '-O',
                              '-A', 'warnings', str(path), '-o', str(tmp_path / f'case{index}.o')],
                             capture_output=True, text=True, timeout=60)
        assert rustc_verdict(run.stderr, run.returncode) == verdict, (name, run.stderr)


def test_double_ampersand_is_two_reference_patterns_and_mut_binds_the_inner_one():
    from pcc.frontends.rust.syntax import Parser
    parser = Parser('fn f() { let &&mut x = y; }')
    parser.pos = parser.tokens.index(next(t for t in parser.tokens if t.text == '&&'))
    outer = parser.pattern()
    inner = outer.children[0]
    assert (outer.kind, outer.value, inner.kind, inner.value) == ('reference', False, 'reference', True)
    assert inner.children[0].kind == 'binding' and inner.children[0].value is False


# Each check sets one failure bit.
PROGRAM = '''\
pub struct S { v: u64 }
pub enum Shape { Dot, Line(u64), Rect(u64, u64) }

fn deref_pair(r: &(u64, bool)) -> u64 { let &(a, keep) = r; if keep { a } else { 0 } }
fn classify(r: &u64) -> u64 {
    match r { &0 => 100, &(1 | 2) => 200, &n if n > 50 => n * 2, &n => n }
}
fn area(s: &Shape) -> u64 { match s { &Shape::Dot => 1, &Shape::Line(n) => n, &Shape::Rect(w, h) => w * h } }
fn bump(r: &mut u64) -> u64 { let &mut old = r; *r = old + 1; old }
fn flag(b: &bool) -> u64 { if let &true = b { 1 } else { 0 } }
fn first_then_second(t: (S, S)) -> u64 { let (a, _) = t; a.v * 10 + t.1.v }

pub fn answer() -> u64 {
    let mut bad = 0u64;
    let kept = (7u64, true);
    let dropped = (7u64, false);
    if deref_pair(&kept) != 7 || deref_pair(&dropped) != 0 { bad |= 1 << 0; }
    let zero = 0u64;
    let two = 2u64;
    let sixty = 60u64;
    let nine = 9u64;
    if classify(&zero) != 100 || classify(&two) != 200 || classify(&sixty) != 120 || classify(&nine) != 9 { bad |= 1 << 1; }
    let dot = Shape::Dot;
    let line = Shape::Line(4);
    let rect = Shape::Rect(3, 5);
    if area(&dot) != 1 || area(&line) != 4 || area(&rect) != 15 { bad |= 1 << 2; }
    let mut counter = 41u64;
    let old = bump(&mut counter);
    if old != 41 || counter != 42 { bad |= 1 << 3; }
    let yes = true;
    let no = false;
    if flag(&yes) != 1 || flag(&no) != 0 { bad |= 1 << 4; }
    if first_then_second((S { v: 3 }, S { v: 4 })) != 34 { bad |= 1 << 5; }
    let pair = (5u64, 6u64);
    let r = &pair;
    let &(x, y) = r;
    if x + y != 11 { bad |= 1 << 6; }
    if bad == 0 { 0xfedcba9876543210 } else { bad }
}
'''


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE or (None if shutil.which('rustc') else
                                                  'development-only rustc is not installed'))
def test_reference_pattern_program_runs_with_owned_admission_and_matches_rustc(tmp_path, monkeypatch):
    owned = run_owned_u64_function(PROGRAM, 'answer', tmp_path, monkeypatch, experimental_rustc_checked=False)
    reference = run_rustc_str_function(PROGRAM, 'answer', tmp_path, output='&{call}.to_le_bytes()')
    assert owned == int.from_bytes(reference, 'little') == 0xfedcba9876543210


@pytest.mark.parametrize('source,boundary', [
    ('pub fn f(r: &u32) -> u32 { match r { 0 => 1, _ => 2 } }', 'needs default binding modes'),
    ('pub fn f(r: &&u32) -> u32 { let &&x = r; x }', 'nested references require'),
    ('pub fn f(r: &()) -> u32 { let &() = r; 1 }', 'unit and nested references require'),
    ('pub fn f(a: [u32; 2]) -> u32 { let mut s = 0; for &x in &a { s += x; } s }',
     'for supports concrete owned arrays only'),
])
def test_forms_beyond_owned_reference_patterns_keep_explicit_boundaries(source, boundary):
    with pytest.raises(RustError, match=boundary):
        compile_rust(source)
