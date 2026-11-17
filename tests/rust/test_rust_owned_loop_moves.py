"""Owned move checking through loop, while, while let, if let and array for.

Stage 3 of pcc/frontends/rust/README.md, "Replacing the rustc admission gate
with owned checking": reference-free programs with these forms are admitted by
PCC's own checks, with no rustc and no caller attestation. Moves are a may-move
dataflow over places (pcc/analysis/ownership/flow.py); loop headers join the
entry with every backedge until a fixed point, so a value moved in one
iteration is moved at the next (rustc E0382 "value moved here, in previous
iteration of loop"), and loop exits join every break with the header's exit.
https://doc.rust-lang.org/reference/expressions/loop-expr.html
https://doc.rust-lang.org/reference/expressions/if-expr.html#if-let-patterns
https://doc.rust-lang.org/reference/expressions.html#moved-and-copied-types
Every corpus row is original; its verdict was compared with a local rustc
1.92.0 build (-O), which stays a labeled reference.
"""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.certification_support import source_has_references
from tests.rust.str_execution import UNAVAILABLE, run_owned_u64_function, run_rustc_str_function

R = 'pub struct R { pub v: u64 } pub fn take(r: R) -> u64 { r.v } '
O = R + 'pub enum O { S(R), N } pub fn next(n: u64) -> O { if n < 3 { O::S(R { v: n }) } else { O::N } } '
C = 'pub enum C { V(u64), N } '
P = R + 'pub struct P { pub a: R, pub b: R } '
H = R + 'pub struct H { pub a: [R; 2], pub b: R } '
RC = R + 'pub fn check(r: R) -> bool { r.v > 0 } '
OC = RC + 'pub enum O { S(R), N } pub fn next(n: u64) -> O { if n < 3 { O::S(R { v: n }) } else { O::N } } '

# (name, original source, verdict of a rustc 1.92.0 build)
CORPUS = (
    ('loop-move-backedge', R + 'pub fn f(r: R) -> u64 { let mut t = 0; loop { t = t + take(r); if t > 9 { return t; } } }', 'E0382'),
    ('loop-move-break', R + 'pub fn f(r: R) -> u64 { let mut t = 0; loop { t = take(r); break; } t }', 'ok'),
    ('loop-move-break-use-after', R + 'pub fn f(r: R) -> u64 { let mut t = 0; loop { t = take(r); break; } t + take(r) }', 'E0382'),
    ('loop-reinit', R + 'pub fn f(mut r: R) -> u64 { let mut n = 0; loop { n = n + take(r); r = R { v: n }; if n > 10 { break; } } n + take(r) }', 'ok'),
    ('loop-continue-before-reinit', R + 'pub fn f(mut r: R) -> u64 { let mut t = 0; loop { t = take(r); if t > 3 { continue; } r = R { v: t + 1 }; if t > 2 { break; } } t }', 'E0382'),
    ('loop-break-before-reinit', R + 'pub fn f(mut r: R) -> u64 { let mut t = 0; loop { t = take(r); if t > 3 { break; } r = R { v: t + 1 }; } t + take(r) }', 'E0382'),
    ('loop-return-ends-path', R + 'pub fn f(r: R) -> u64 { loop { let t = take(r); return t; } }', 'ok'),
    ('loop-nested-inner-break', R + 'pub fn f(r: R) -> u64 { let mut t = 0; loop { loop { t = take(r); break; } break; } t }', 'ok'),
    ('loop-nested-outer-backedge', R + 'pub fn f(r: R) -> u64 { let mut t = 0; let mut i = 0; loop { loop { t = take(r); break; } i += 1; if i > 2 { break; } } t }', 'E0382'),
    ('loop-local-each-iteration', R + 'pub fn f() -> u64 { let mut t = 0; loop { let r = R { v: 1 }; t = t + take(r); if t > 4 { break; } } t }', 'ok'),
    ('loop-partial-field-reinit', P + 'pub fn f(mut p: P) -> u64 { let mut t = 0; loop { t = t + take(p.a); p.a = R { v: t }; if t > 5 { break; } } t + take(p.a) + take(p.b) }', 'ok'),
    ('loop-partial-sibling', P + 'pub fn f(p: P) -> u64 { let mut t = 0; loop { t = take(p.a); break; } t + take(p.b) }', 'ok'),
    ('loop-partial-moved-field', P + 'pub fn f(p: P) -> u64 { let mut t = 0; loop { t = take(p.a); break; } t + take(p.a) }', 'E0382'),
    ('loop-partial-whole-use', P + 'pub fn f(p: P) -> u64 { let mut t = 0; loop { t = take(p.a); break; } let q = p; t + q.b.v }', 'E0382'),
    ('loop-copy-ok', 'pub fn f(x: u64) -> u64 { let mut t = 0; let mut i = 0; loop { t = t + x; i += 1; if i > 3 { break; } } t }', 'ok'),
    ('loop-immutable-assign', 'pub fn f() -> u64 { let x = 1; loop { x = 2; break; } x }', 'E0384'),
    ('while-cond-moves', RC + 'pub fn f(r: R) { while check(r) {} }', 'E0382'),
    ('while-body-move', R + 'pub fn f(r: R) -> u64 { let mut i = 0; let mut t = 0; while i < 3 { t = take(r); i += 1; } t }', 'E0382'),
    ('while-true-body-move', R + 'pub fn f(r: R) -> u64 { let mut t = 0; while true { t = take(r); if t > 1 { break; } } t }', 'E0382'),
    ('while-body-reinit', R + 'pub fn f(mut r: R) -> u64 { let mut i = 0; let mut t = 0; while i < 3 { t = t + take(r); r = R { v: i }; i += 1; } t + take(r) }', 'ok'),
    ('while-let-place-payload', O + 'pub fn f(o: O) -> u64 { let mut t = 0; while let O::S(r) = o { t = t + take(r); } t }', 'E0382'),
    ('while-let-reassign', O + 'pub fn f(mut o: O) -> u64 { let mut t = 0; while let O::S(r) = o { t = t + take(r); o = O::N; } t }', 'ok'),
    ('while-let-reassign-next', O + 'pub fn f(mut o: O) -> u64 { let mut t = 0; while let O::S(r) = o { t = t + take(r); o = next(t); } t }', 'ok'),
    ('while-let-temporary', O + 'pub fn f() -> u64 { let mut i = 0; while let O::S(r) = next(i) { i = i + 1 + take(r); } i }', 'ok'),
    ('while-let-moves-outer', O + 'pub fn f(r: R) -> u64 { let mut i = 0; while let O::S(s) = next(i) { i = i + take(s) + take(r); } i }', 'E0382'),
    ('while-let-copy-payload', C + 'pub fn f(mut c: C) -> u64 { let mut t = 0; while let C::V(x) = c { t = t + x; if t > 5 { c = C::N; } } t }', 'ok'),
    ('while-let-wildcard', O + 'pub fn f(mut o: O) -> u64 { let mut t = 0; while let O::S(_) = o { t = t + 1; o = O::N; } t }', 'ok'),
    ('while-let-break-then-inspect', O + 'pub fn f(o: O) -> u64 { let mut t = 0; while let O::S(r) = o { t = take(r); break; } match o { O::N => t, O::S(_) => 0 } }', 'E0382'),
    ('while-let-exit-keeps-place', O + 'pub fn f(mut o: O) -> u64 { let mut t = 0; while let O::S(r) = o { t = t + take(r); o = next(t); } match o { O::N => t, O::S(r) => take(r) } }', 'ok'),
    ('if-let-then-inspect', O + 'pub fn f(o: O) -> u64 { let t = if let O::S(r) = o { take(r) } else { 0 }; let u = if let O::N = o { 1 } else { 2 }; t + u }', 'E0382'),
    ('if-let-else-uses', O + 'pub fn f(o: O) -> u64 { if let O::S(r) = o { take(r) } else { match o { O::N => 0, O::S(r) => take(r) } } }', 'ok'),
    ('if-let-in-loop', O + 'pub fn f(o: O) -> u64 { let mut t = 0; loop { if let O::S(r) = o { t = t + take(r); } else { break; } } t }', 'E0382'),
    ('if-let-in-loop-reinit', O + 'pub fn f(mut o: O) -> u64 { let mut t = 0; loop { if let O::S(r) = o { t = t + take(r); o = O::N; } else { break; } } t }', 'ok'),
    ('if-let-copy-binding', C + 'pub fn f(c: C) -> u64 { let x = if let C::V(x) = c { x } else { 0 }; match c { C::V(y) => x + y, C::N => x } }', 'ok'),
    ('if-let-tuple-sibling', R + 'pub fn f() -> u64 { let p = (R { v: 1 }, R { v: 2 }); let mut t = 0; if let (a, _) = p { t = take(a); } t + take(p.1) }', 'ok'),
    ('if-let-tuple-moved', R + 'pub fn f() -> u64 { let p = (R { v: 1 }, R { v: 2 }); let mut t = 0; if let (a, _) = p { t = take(a); } t + take(p.0) }', 'E0382'),
    ('if-let-moves-outer-twice', O + 'pub fn f(r: R, o: O) -> u64 { let a = if let O::N = o { take(r) } else { 0 }; a + take(r) }', 'E0382'),
    ('for-array-moved', R + 'pub fn f(a: [R; 2]) -> u64 { let mut t = 0; for r in a { t = t + take(r); } let b = a; t }', 'E0382'),
    ('for-empty-array-moved', R + 'pub fn f() -> u64 { let a: [R; 0] = []; for r in a { take(r); } let b = a; 0 }', 'E0382'),
    ('for-copy-array-reused', 'pub fn f(a: [u64; 2]) -> u64 { let mut t = 0; for x in a { t = t + x; } for x in a { t = t + x; } t }', 'ok'),
    ('for-body-moves-outer', R + 'pub fn f(r: R) -> u64 { let mut t = 0; for x in [1u64, 2] { t = t + x + take(r); } t }', 'E0382'),
    ('for-body-move-break', R + 'pub fn f(r: R) -> u64 { let mut t = 0; for x in [1u64, 2] { t = take(r) + x; break; } t }', 'ok'),
    ('for-body-move-break-use-after', R + 'pub fn f(r: R) -> u64 { let mut t = 0; for x in [1u64] { t = take(r) + x; break; } t + take(r) }', 'E0382'),
    ('for-body-reinit', R + 'pub fn f(mut r: R) -> u64 { let mut t = 0; for x in [1u64, 2] { t = t + take(r); r = R { v: x }; } t + take(r) }', 'ok'),
    ('for-continue-carries-move', R + 'pub fn f(mut r: R) -> u64 { let mut t = 0; for x in [1u64, 2] { t = take(r); if x > 1 { continue; } r = R { v: x }; } t }', 'E0382'),
    ('for-element-moved-twice', R + 'pub fn f() -> u64 { let mut t = 0; for r in [R { v: 1 }] { t = take(r) + take(r); } t }', 'E0382'),
    ('for-element-each-iteration', R + 'pub fn f(a: [R; 3]) -> u64 { let mut t = 0; for r in a { t = t + take(r); } t }', 'ok'),
    ('for-in-loop-break', R + 'pub fn f(a: [R; 1]) -> u64 { let mut t = 0; loop { for r in a { t = take(r); } break; } t }', 'ok'),
    ('for-in-loop-backedge', R + 'pub fn f(a: [R; 1]) -> u64 { let mut t = 0; let mut i = 0; loop { for r in a { t = take(r); } i += 1; if i > 1 { break; } } t }', 'E0382'),
    ('for-field-array-sibling', H + 'pub fn f(h: H) -> u64 { let mut t = 0; for r in h.a { t = t + take(r); } t + take(h.b) }', 'ok'),
    ('for-field-array-whole', H + 'pub fn f(h: H) -> u64 { let mut t = 0; for r in h.a { t = t + take(r); } let k = h; t }', 'E0382'),
    ('for-binding-immutable', 'pub fn f() -> u64 { let mut t = 0; for x in [1u64, 2] { x = 3; t = t + x; } t }', 'E0384'),
    ('for-mut-binding', 'pub fn f() -> u64 { let mut t = 0; for mut x in [1u64, 2] { x = x + 3; t = t + x; } t }', 'ok'),
    ('loop-match-arm-move', OC + 'pub fn f(o: O) -> u64 { let mut t = 0; loop { match o { O::S(r) => { t = t + take(r); } O::N => break } } t }', 'E0382'),
    ('loop-match-arm-move-break', OC + 'pub fn f(o: O) -> u64 { let mut t = 0; loop { match o { O::S(r) => { t = t + take(r); break; } O::N => break } } t }', 'ok'),
    ('loop-if-move-else-return', RC + 'pub fn f(r: R, c: bool) -> u64 { let mut t = 0; loop { if c { t = take(r); } else { return t; } } }', 'E0382'),
    ('loop-if-move-break', RC + 'pub fn f(r: R, c: bool) -> u64 { let mut t = 0; loop { if c { t = take(r); break; } t = t + 1; if t > 5 { break; } } t }', 'ok'),
    ('while-and-cond-move', RC + 'pub fn f(r: R) { let mut i = 0; while i < 3 && check(r) { i += 1; } }', 'E0382'),
    ('while-or-cond-move-once', RC + 'pub fn f(r: R) -> u64 { let mut i = 0; if i < 3 || check(r) { i += 1; } i }', 'ok'),
    ('generic-loop-move', 'pub fn f<T>(t: T) { loop { let u = t; } }', 'E0382'),
    ('generic-for-move', 'pub fn f<T>(t: T) -> u64 { let mut n = 0; for x in [1u64, 2] { let u = t; n = n + x; } n }', 'E0382'),
    ('generic-while-let-ok', 'pub enum G<T> { S(T), N } pub fn f<T>(mut g: G<T>) -> u64 { let mut n = 0; while let G::S(x) = g { let y = x; n = n + 1; g = G::N; } n }', 'ok'),
    ('guard-in-loop-move-arm', OC + 'pub fn f(o: O) -> u64 { let mut t = 0; loop { match o { O::S(r) if r.v > 1 => { t = take(r); break; } _ => break } } t }', 'ok'),
    ('guard-in-loop-backedge', OC + 'pub fn f(o: O) -> u64 { let mut t = 0; let mut i = 0; loop { match o { O::S(r) if r.v > 1 => { t = take(r); } _ => {} } i += 1; if i > 2 { break; } } t }', 'E0382'),
    ('shadow-in-for', RC + 'pub fn f(a: [R; 2]) -> u64 { let mut t = 0; for r in a { let r = R { v: r.v + 1 }; t = t + take(r); } t }', 'ok'),
    ('nested-if-let-in-while-let', OC + 'pub fn f() -> u64 { let mut i = 0; while let O::S(r) = next(i) { let o = next(r.v); if let O::S(s) = o { i = i + take(s); } i = i + 1 + take(r); } i }', 'ok'),
    ('nested-for-moves-inner-outer', RC + 'pub fn f(a: [R; 2], b: [u64; 2]) -> u64 { let mut t = 0; for r in a { for x in b { t = t + x; } t = t + take(r); } t }', 'ok'),
    ('nested-for-moves-element-inner', RC + 'pub fn f(a: [R; 2], b: [u64; 2]) -> u64 { let mut t = 0; for r in a { for x in b { t = t + x + take(r); } } t }', 'E0382'),
    ('for-element-field-moves', 'pub struct R { pub v: u64 } pub struct Q { pub a: R, pub b: R } pub fn take(r: R) -> u64 { r.v } pub fn f(a: [Q; 2]) -> u64 { let mut t = 0; for q in a { t = t + take(q.a) + take(q.b); } t }', 'ok'),
    ('if-let-else-if-let', OC + 'pub fn f(o: O, p: O) -> u64 { if let O::S(r) = o { take(r) } else if let O::S(s) = p { take(s) } else { 0 } }', 'ok'),
    ('if-let-else-if-let-reuse', OC + 'pub fn f(o: O) -> u64 { if let O::S(r) = o { take(r) } else if let O::S(s) = o { take(s) } else { 0 } }', 'ok'),
    ('continue-in-while-let-carries-move', OC + 'pub fn f(r: R) -> u64 { let mut i = 0; while let O::S(s) = next(i) { i = i + 1 + take(s); if i > 1 { continue; } i = i + take(r); } i }', 'E0382'),
    ('loop-move-after-diverging-if', RC + 'pub fn f(r: R) -> u64 { loop { if r.v > 1 { return take(r); } return 0; } }', 'ok'),
)
MESSAGES = {'E0382': 'use of moved or possibly moved value', 'E0384': 'cannot assign to immutable binding'}


@pytest.mark.parametrize('name,source,verdict', CORPUS, ids=[row[0] for row in CORPUS])
def test_loop_moves_match_a_rustc_build_without_running_rustc(name, source, verdict, monkeypatch):
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
                              '-A', 'dead_code', '-A', 'unused', '-A', 'irrefutable_let_patterns',
                              '-A', 'while_true', str(path), '-o', str(tmp_path / f'case{index}.o')],
                             capture_output=True, text=True, timeout=60)
        codes = sorted(set(re.findall(r'error\[(E\d+)\]', run.stderr)))
        assert (run.returncode == 0, codes) == ((True, []) if verdict == 'ok' else (False, [verdict])), \
            (name, run.stderr)


PROGRAMS = tuple(sorted(
    path.stem for path in Path(__file__).with_name('programs').glob('*.rs')
    if path.stem.startswith(('loop_', 'while_let_', 'if_let_', 'for_array_'))
    and not source_has_references(path.read_text())))


def test_every_reference_free_loop_program_is_executed():
    assert len(PROGRAMS) == 34 and 'loop_reference' not in PROGRAMS


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE or (None if shutil.which('rustc') else
                                                  'development-only rustc is not installed'))
@pytest.mark.parametrize('name', PROGRAMS)
def test_programs_run_with_owned_admission_and_match_rustc(name, tmp_path, monkeypatch):
    source = Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()
    owned = run_owned_u64_function(source, 'answer', tmp_path, monkeypatch, experimental_rustc_checked=False)
    reference = run_rustc_str_function(source, 'answer', tmp_path, output='&{call}.to_le_bytes()')
    assert owned == int.from_bytes(reference, 'little')
