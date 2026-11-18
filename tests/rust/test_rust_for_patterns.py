"""Destructuring patterns in `for` loops.

Rust Reference expressions/loop-expr.html#iterator-loops: `for PATTERN in
iter` binds each element to an irrefutable pattern. The parser binds the
element to a name no source can spell (syntax.FOR_ELEMENT) and destructures
it with `let PATTERN = element;` around the body, so tuple, nested tuple,
unit, `mut`-binding and wildcard patterns are checked, moved, lowered and
linted exactly like `let` patterns: shape mismatches are E0308, a repeated
name E0416, assigning an immutable pattern binding E0384, moving a part twice
E0382. Patterns that need irrefutability checking (struct, enum, literal)
keep explicit boundaries; reference patterns parse and check
(test_rust_reference_patterns.py), but borrowed iteration (`for &x in &a`)
keeps its own boundary. Every corpus row and program is
original; verdicts and results were compared with a local rustc 1.92.0 build
(-O), which stays a labeled reference.
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
    ('tuple', 'pub fn f() -> u32 { let mut s = 0; for (a, b) in [(1u32, 2u32), (3, 4)] { s += a * b; } s }', 'ok'),
    ('nested-tuple', 'pub fn f() -> u64 { let mut s = 0; for (a, (b, c)) in [(1u64, (2u64, 3u64))] { s += a + b * c; } s }',
     'ok'),
    ('mut-binding-in-tuple', 'pub fn f() -> u32 { let mut s = 0; for (mut a, b) in [(1u32, 2u32)] { a += b; s += a; } s }',
     'ok'),
    ('wildcard-in-tuple', 'pub fn f() -> u32 { let mut s = 0; for (_, b) in [(1u32, 2u32), (5, 6)] { s += b; } s }', 'ok'),
    ('unit-elements', 'pub fn f() -> u32 { let mut n = 0; for () in [(), ()] { n += 1; } n }', 'ok'),
    ('one-tuple', 'pub fn f() -> u32 { let mut s = 0; for (a,) in [(7u32,)] { s += a; } s }', 'ok'),
    ('parenthesized-binding', 'pub fn f() -> u32 { let mut s = 0; for (a) in [1u32, 2] { s += a; } s }', 'ok'),
    ('tuple-over-integers', 'pub fn f() -> u32 { let mut s = 0; for (a, b) in [1u32, 2] { s += a; } s }', 'E0308'),
    ('tuple-arity', 'pub fn f() -> u32 { let mut s = 0; for (a, b) in [(1u32, 2u32, 3u32)] { s += a; } s }', 'E0308'),
    ('tuple-over-range', 'pub fn f() -> u32 { let mut s = 0; for (a, b) in 0..3u32 { s += a; } s }', 'E0308'),
    ('immutable-pattern-binding',
     'pub fn f() -> u32 { let mut s = 0; for (a, b) in [(1u32, 2u32)] { a += b; s += a; } s }', 'E0384'),
    ('duplicate-binding', 'pub fn f() -> u32 { let mut s = 0; for (a, a) in [(1u32, 2u32)] { s += a; } s }', 'E0416'),
    ('moves-non-copy-parts',
     S + 'pub fn f() -> u32 { let mut s = 0; for (x, y) in [(S { v: 1 }, S { v: 2 })] { let z = x; s += z.v + y.v; } s }',
     'ok'),
    ('moved-part-twice',
     S + 'pub fn f() -> u32 { let mut s = 0; for (x, _y) in [(S { v: 1 }, S { v: 2 })] { let z = x; let w = x; '
         's += z.v + w.v; } s }', 'E0382'),
    ('shadow-in-body', 'pub fn f() -> u32 { let mut s = 0; for (a, b) in [(1u32, 2u32)] { let a = a + b; s += a; } s }',
     'ok'),
    ('break-continue', 'pub fn f() -> u32 { let mut s = 0; for (a, b) in [(1u32, 2u32), (3, 4), (5, 6)] '
                       '{ if a == 3 { continue; } if b == 6 { break; } s += a + b; } s }', 'ok'),
    ('overflow-in-body', 'pub fn f() -> u32 { let mut s = 0; for (a, _b) in [(1u32, 2u32)] { s += a; '
                         'let _c = 4294967295u32 + 1; } s }', 'overflow'),
)
MESSAGES = {
    'E0308': r'tuple pattern shape does not match scrutinee: mismatched types \(rustc E0308\)',
    'E0416': r'duplicate binding in pattern: .*\(rustc E0416\)',
    'E0384': "cannot assign to immutable binding 'a'",
    'E0382': "use of moved or possibly moved value 'x'",
    'overflow': 'constant integer overflow|this arithmetic operation will overflow',
}


@pytest.mark.parametrize('name,source,verdict', CORPUS, ids=[row[0] for row in CORPUS])
def test_for_pattern_verdicts_match_a_rustc_build_without_running_rustc(name, source, verdict, monkeypatch):
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


def test_the_element_name_cannot_be_written_in_source():
    from pcc.frontends.rust.syntax import FOR_ELEMENT, Parser
    loop, = Parser('fn f() { for (a, b) in [(1u32, 2u32)] { a; b; } }').parse()[0].body.children
    assert loop.kind == 'for' and loop.value.kind == 'binding' and loop.value.token.text == FOR_ELEMENT
    bind, body = loop.children[1].children
    assert bind.kind == 'pattern_let' and bind.value[0].kind == 'tuple' and bind.children[0].value == FOR_ELEMENT
    assert body.kind == 'block'
    with pytest.raises(RustError):
        Parser(f'fn f() {{ let {FOR_ELEMENT} = 1; }}').parse()


# Each check sets one failure bit.
PROGRAM = '''\
pub struct Point { x: u64, y: u64 }

fn dot(pairs: [(u64, u64); 3]) -> u64 {
    let mut s = 0;
    for (a, b) in pairs { s += a * b; }
    s
}

fn nested(rows: [(u64, (u64, bool)); 3]) -> u64 {
    let mut s = 0;
    for (i, (v, keep)) in rows {
        if !keep { continue; }
        s += i << v;
    }
    s
}

fn points(items: [(Point, u64); 2]) -> u64 {
    let mut s = 0;
    for (p, w) in items {
        let q = p;
        s += (q.x * w) ^ q.y;
    }
    s
}

pub fn answer() -> u64 {
    let mut bad = 0u64;
    if dot([(1, 2), (3, 4), (5, 6)]) != 44 { bad |= 1 << 0; }
    if nested([(1, (4, true)), (2, (1, false)), (3, (2, true))]) != 28 { bad |= 1 << 1; }
    if points([(Point { x: 2, y: 1 }, 5), (Point { x: 3, y: 8 }, 7)]) != 11 + 29 { bad |= 1 << 2; }
    let mut last = 0u64;
    for (mut a, _) in [(1u64, 9u64), (2, 9), (3, 9)] {
        a *= 10;
        if a == 30 { break; }
        last = a;
    }
    if last != 20 { bad |= 1 << 3; }
    let mut n = 0u64;
    for () in [(), (), ()] { n += 1; }
    for (k,) in [(4u64,)] { n += k; }
    if n != 7 { bad |= 1 << 4; }
    if bad == 0 { 0xfedcba9876543210 } else { bad }
}
'''


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE or (None if shutil.which('rustc') else
                                                  'development-only rustc is not installed'))
def test_for_pattern_program_runs_with_owned_admission_and_matches_rustc(tmp_path, monkeypatch):
    owned = run_owned_u64_function(PROGRAM, 'answer', tmp_path, monkeypatch, experimental_rustc_checked=False)
    reference = run_rustc_str_function(PROGRAM, 'answer', tmp_path, output='&{call}.to_le_bytes()')
    assert owned == int.from_bytes(reference, 'little') == 0xfedcba9876543210


@pytest.mark.parametrize('source,boundary', [
    ('pub enum E { A(u32) }\npub fn f(a: [E; 1]) -> u32 { let mut s = 0; for E::A(x) in a { s += x; } s }',
     'for patterns other than tuple, reference, binding and wildcard patterns require irrefutability checking'),
    ('pub fn f(a: [(u32, u32); 1]) -> u32 { let mut s = 0; for (1, b) in a { s += b; } s }',
     'for patterns other than tuple, reference, binding and wildcard patterns require irrefutability checking'),
    ('pub fn f(a: [u32; 2]) -> u32 { let mut s = 0; for &x in &a { s += x; } s }',
     'for supports concrete owned arrays only; borrowed/custom iterators'),
])
def test_refutable_and_reference_for_patterns_keep_explicit_boundaries(source, boundary):
    with pytest.raises(RustError, match=boundary):
        compile_rust(source)
