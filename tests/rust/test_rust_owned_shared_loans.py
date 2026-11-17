"""Owned shared loans: references in bodies, parameters and `&self` receivers.

Stage 4 of pcc/frontends/rust/README.md, "Replacing the rustc admission gate
with owned checking": shared references are admitted by PCC's own analysis,
with no rustc and no caller attestation. `&place` creates a loan; a
reference-typed local or temporary holds the loans of its value (copies,
reborrows and if/match/block values carry them). Moving (E0505) or assigning
(E0506) an overlapping place, or ending the borrowed local's scope (E0597),
invalidates the loan in every holder, and the error is reported when an
invalidated holder is used afterwards: rustc's non-lexical liveness, solved by
the move checker's fixed point. Any two indices of one array overlap, as in
rustc. Matching a place through a reference or an array index moves only its
non-Copy by-value bindings (E0507/E0508).
https://doc.rust-lang.org/reference/types/pointer.html#shared-references-
https://doc.rust-lang.org/reference/expressions/operator-expr.html#borrow-operators
https://rust-lang.github.io/rfcs/2094-nll.html
`&mut`, explicit lifetimes, reference returns and references stored in
aggregates keep explicit owned boundaries (stages 5 and 6). Every corpus row
is original; its verdict was compared with a local rustc 1.92.0 build (-O),
which stays a labeled reference.
"""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.str_execution import UNAVAILABLE, run_owned_u64_function, run_rustc_str_function

S = 'pub struct S { pub a: u64, pub b: u64 } pub fn take(s: S) -> u64 { s.a } pub fn get(x: &u64) -> u64 { *x } '
M = S + 'impl S { pub fn sum(&self) -> u64 { self.a + self.b } pub fn eat(&self, other: S) -> u64 { self.a + other.b } } '
H = S + 'pub fn first(s: &S, t: S) -> u64 { s.a + t.b } '
N = S + 'pub struct N { pub a: u64, pub c: S } '
G = 'pub fn pair(x: &u64, n: u64) -> u64 { *x + n } '

# (name, original source, verdict of a rustc 1.92.0 build)
CORPUS = (
    ('local-borrow-read', S + 'pub fn f() -> u64 { let a = 5u64; let r = &a; *r }', 'ok'),
    ('dead-before-move', S + 'pub fn f() -> u64 { let s = S { a: 1, b: 2 }; let r = &s; let v = r.a; take(s) + v }', 'ok'),
    ('dead-before-assign', 'pub fn f() -> u64 { let mut a = 5u64; let r = &a; let v = *r; a = 6; v + a }', 'ok'),
    ('disjoint-field-assign', S + 'pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; let r = &s.a; s.b = 3; *r + s.b }', 'ok'),
    ('reborrow-read', 'pub fn f() -> u64 { let a = 5u64; let r = &a; let s = &*r; *s }', 'ok'),
    ('holder-reassigned', 'pub fn f() -> u64 { let mut a = 1u64; let b = 2u64; let mut r = &a; r = &b; a = 3; *r + a }', 'ok'),
    ('self-method-then-move', M + 'pub fn f() -> u64 { let s = S { a: 1, b: 2 }; let v = s.sum() + s.sum(); v + take(s) }', 'ok'),
    ('call-borrows-then-move', S + 'pub fn f() -> u64 { let s = S { a: 1, b: 2 }; let v = get(&s.a) + get(&s.b); v + take(s) }', 'ok'),
    ('if-ref', 'pub fn f(c: bool) -> u64 { let a = 5u64; let b = 6u64; let r = if c { &a } else { &b }; *r }', 'ok'),
    ('loop-fresh-borrow', 'pub fn f() -> u64 { let mut x = 0u64; let mut t = 0; let mut i = 0; while i < 3 { let r = &x; t += *r; x += 1; i += 1; } t + x }', 'ok'),
    ('two-shared', 'pub fn f() -> u64 { let a = 5u64; let r1 = &a; let r2 = &a; *r1 + *r2 + a }', 'ok'),
    ('param-ref-fields', S + 'pub fn sum(s: &S) -> u64 { s.a + (*s).b } pub fn f() -> u64 { let s = S { a: 1, b: 2 }; let v = sum(&s); v + take(s) }', 'ok'),
    ('index-read', 'pub fn f() -> u64 { let arr = [1u64, 2]; let r = &arr[0]; *r + arr[1] }', 'ok'),
    ('generic-ref-param', 'pub fn count<T>(x: &T, n: u64) -> u64 { n } pub fn f() -> u64 { let a = 5u64; count(&a, 1) + a }', 'ok'),
    ('generic-ref-param-move-after', S + 'pub fn count<T>(x: &T, n: u64) -> u64 { n } pub fn f() -> u64 { let s = S { a: 1, b: 2 }; count(&s, 1) + take(s) }', 'ok'),
    ('block-value-ref', 'pub fn f() -> u64 { let a = 5u64; let r = { let y = &a; y }; *r }', 'ok'),
    ('copy-chain', 'pub fn f() -> u64 { let a = 5u64; let r = &a; let s = r; let t = s; *t }', 'ok'),
    ('shadow-referent', 'pub fn f() -> u64 { let a = 5u64; let r = &a; let a = 7u64; *r + a }', 'ok'),
    ('loop-borrow-unused-after', 'pub fn f() -> u64 { let mut x = 0u64; let mut r = &x; let mut t = 0; for v in [1u64, 2] { t += *r + v; } x = 3; t + x }', 'ok'),
    ('dead-iteration-local', 'pub fn f() -> u64 { let a = 1u64; let mut r = &a; let mut t = 0; for v in [1u64, 2] { r = &v; t += *r; } t }', 'ok'),
    ('dangling-reassigned-before-use', 'pub fn f() -> u64 { let a = 1u64; let mut r = &a; { let x = 5u64; r = &x; } r = &a; *r }', 'ok'),
    ('discarded-block-ref', 'pub fn f() { { let x = 5u64; &x }; }', 'ok'),
    ('param-ref-local-holder', 'pub fn f(r: &u64) -> u64 { let mut s = r; let x = 5u64; s = &x; *s }', 'ok'),
    ('while-cond-ref', 'pub fn f() -> u64 { let mut x = 0u64; let r = &x; let mut i = 0; while *r < 3 && i < 3 { i += 1; } x = 1; x + i }', 'ok'),
    ('method-on-ref-param', M + 'pub fn g(s: &S) -> u64 { s.sum() + s.a }', 'ok'),
    ('index-borrow-other-array', 'pub fn f() -> u64 { let mut a = [1u64, 2]; let b = [3u64, 4]; let r = &b[0]; a[0] = 5; *r + a[0] }', 'ok'),
    ('conditional-move-after-dead', S + 'pub fn f(c: bool) -> u64 { let s = S { a: 1, b: 2 }; let r = &s; let v = r.b; if c { take(s) } else { v } }', 'ok'),
    ('move-while-borrowed', S + 'pub fn f() -> u64 { let s = S { a: 1, b: 2 }; let r = &s; let t = take(s); r.a + t }', 'E0505'),
    ('assign-while-borrowed', 'pub fn f() -> u64 { let mut a = 5u64; let r = &a; a = 6; *r }', 'E0506'),
    ('compound-while-borrowed', 'pub fn f() -> u64 { let mut a = 5u64; let r = &a; a += 1; *r }', 'E0506'),
    ('move-whole-field-borrowed', S + 'pub fn f() -> u64 { let s = S { a: 1, b: 2 }; let r = &s.a; let t = take(s); *r + t }', 'E0505'),
    ('assign-whole-field-borrowed', S + 'pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; let r = &s.a; s = S { a: 3, b: 4 }; *r }', 'E0506'),
    ('dangling-block-local', 'pub fn f() -> u64 { let a = 1u64; let mut r = &a; { let x = 5u64; r = &x; } *r }', 'E0597'),
    ('dangling-for-binding', 'pub fn f() -> u64 { let a = 1u64; let mut r = &a; for v in [1u64, 2] { r = &v; } *r }', 'E0597'),
    ('dangling-block-value', 'pub fn f() -> u64 { let r = { let x = 5u64; &x }; *r }', 'E0597'),
    ('dangling-block-value-unused', 'pub fn f() -> u64 { let r = { let x = 5u64; &x }; 0 }', 'E0597'),
    ('dangling-match-arm', 'pub fn f() -> u64 { let t = (1u64, 2u64); let r = match t { (a, _) => &a }; *r }', 'E0597'),
    ('arg-borrow-then-move', H + 'pub fn f() -> u64 { let s = S { a: 1, b: 2 }; first(&s, s) }', 'E0505'),
    ('receiver-then-move', M + 'pub fn f() -> u64 { let s = S { a: 1, b: 2 }; s.eat(s) }', 'E0505'),
    ('loop-backedge-use', 'pub fn f() -> u64 { let mut x = 0u64; let r = &x; let mut t = 0; loop { t += *r; x += 1; if t > 3 { break; } } t }', 'E0506'),
    ('borrow-after-move', S + 'pub fn f() -> u64 { let s = S { a: 1, b: 2 }; let t = take(s); let r = &s; r.a + t }', 'E0382'),
    ('reborrow-keeps-loan', 'pub fn f() -> u64 { let mut a = 5u64; let r = &a; let s = &*r; a = 6; *s }', 'E0506'),
    ('copy-keeps-loan', 'pub fn f() -> u64 { let mut a = 5u64; let r = &a; let s = r; a = 6; *s }', 'E0506'),
    ('index-overlap', 'pub fn f() -> u64 { let mut arr = [1u64, 2]; let r = &arr[0]; arr[1] = 5; *r }', 'E0506'),
    ('if-ref-loan', 'pub fn f(c: bool) -> u64 { let a = 5u64; let mut b = 6u64; let r = if c { &a } else { &b }; b = 3; *r }', 'E0506'),
    ('param-assign-while-borrowed', 'pub fn f(mut a: u64) -> u64 { let r = &a; a = 1; *r }', 'E0506'),
    ('let-underscore-use', 'pub fn f() -> u64 { let mut a = 5u64; let r = &a; a = 6; let _ = r; a }', 'E0506'),
    ('statement-use', 'pub fn f() -> u64 { let mut a = 5u64; let r = &a; a = 6; r; a }', 'E0506'),
    ('conditional-move-then-use', S + 'pub fn f(c: bool) -> u64 { let s = S { a: 1, b: 2 }; let r = &s; let t = if c { take(s) } else { 0 }; t + r.a }', 'E0505'),
    ('while-let-binding-loan', 'pub enum O { S(u64), N } pub fn f(mut o: O) -> u64 { let r = &o; let mut t = 0; while let O::S(v) = o { t += v; o = O::N; } match *r { O::S(v) => v + t, O::N => t } }', 'E0506'),
    ('move-out-of-reference', S + 'pub fn f(r: &S) -> u64 { let s = *r; s.a }', 'E0507'),
    ('assign-through-reference', S + 'pub fn f(r: &S) { r.a = 1; }', 'E0594'),
    ('match-deref-copy', 'pub enum E { A(u64), B } pub fn f(e: &E) -> u64 { match *e { E::A(x) => x, E::B => 0 } }', 'ok'),
    ('match-deref-wildcard', S + 'pub enum F { A(S), B } pub fn f(e: &F) -> u64 { match *e { F::A(_) => 1, F::B => 0 } }', 'ok'),
    ('match-deref-noncopy-binding', S + 'pub enum F { A(S), B } pub fn f(e: &F) -> u64 { match *e { F::A(s) => s.a, F::B => 0 } }', 'E0507'),
    ('match-index-wildcard', S + 'pub enum F { A(S), B } pub fn f(a: [F; 2]) -> u64 { match a[0] { F::A(_) => 1, F::B => 0 } }', 'ok'),
    ('match-index-noncopy-binding', S + 'pub enum F { A(S), B } pub fn f(a: [F; 2]) -> u64 { match a[0] { F::A(s) => s.a, F::B => 0 } }', 'E0508'),
    ('if-let-deref-copy', 'pub enum E { A(u64), B } pub fn f(e: &E) -> u64 { if let E::A(x) = *e { x } else { 0 } }', 'ok'),
    ('while-let-deref-copy', 'pub enum E { A(u64), B } pub fn f(e: &E) -> u64 { let mut t = 0; while let E::A(x) = *e { t += x; if t > 3 { break; } } t }', 'ok'),
    ('match-deref-then-assign-referent', 'pub enum E { A(u64), B } pub fn f() -> u64 { let mut e = E::A(1); let r = &e; e = E::B; match *r { E::A(x) => x, E::B => 0 } }', 'E0506'),
    ('generic-body-loan', 'pub fn f<T>(t: T) -> T { let r = &t; let u = t; let _ = r; u }', 'E0505'),
    ('pending-arg-assign', G + 'pub fn f() -> u64 { let mut a = 1u64; let r = &a; pair(r, { a = 2; 0 }) }', 'E0506'),
    ('pending-arg-assign-dead-holder', G + 'pub fn f() -> u64 { let mut a = 1u64; pair(&a, 0) + { a = 2; a } }', 'ok'),
    ('if-condition-dead-after', 'pub fn f() -> u64 { let mut a = 1u64; let r = &a; if *r > 0 { a = 2; } a }', 'ok'),
    ('if-condition-used-after', 'pub fn f() -> u64 { let mut a = 1u64; let r = &a; if *r > 0 { a = 2; } *r }', 'E0506'),
    ('while-condition-holder', 'pub fn f() -> u64 { let mut a = 1u64; let r = &a; while *r < 3 { a += 1; } a }', 'E0506'),
    ('loop-local-holder-dead', 'pub fn f(c: bool) -> u64 { let mut x = 0u64; loop { let r = &x; if *r > 2 || c { break; } x += 1; } x }', 'ok'),
    ('disjoint-field-move', N + 'pub fn f() -> u64 { let n = N { a: 1, c: S { a: 2, b: 3 } }; let r = &n.a; let t = take(n.c); *r + t }', 'ok'),
    ('overlap-subfield-move', N + 'pub fn f() -> u64 { let n = N { a: 1, c: S { a: 2, b: 3 } }; let r = &n.c.a; let t = take(n.c); *r + t }', 'E0505'),
    ('reborrow-escapes-block', 'pub fn f() -> u64 { let a = 1u64; let mut r = &a; { let x = 5u64; let y = &x; r = &*y; } *r }', 'E0597'),
    ('reborrow-of-outer-escapes-block', 'pub fn f() -> u64 { let a = 1u64; let mut r = &a; { let y = &a; r = &*y; } *r }', 'ok'),
    ('result-not-a-reference', 'pub fn f() -> u64 { let mut x = 1u64; let v = { let r = &x; *r + 1 }; x = 2; v + x }', 'ok'),
    ('guard-reads-holder', 'pub enum O { S(u64), N } pub fn f(o: O) -> u64 { let a = 5u64; let r = &a; match o { O::S(v) if v < *r => v, _ => *r } }', 'ok'),
    ('guard-then-assign-used', 'pub enum O { S(u64), N } pub fn f(o: O) -> u64 { let mut a = 5u64; let r = &a; let v = match o { O::S(v) if v < *r => v, _ => 0 }; a = 1; v + *r }', 'E0506'),
    ('self-method-reads-fields', 'pub struct S { pub a: u64, pub b: u64 } impl S { pub fn f(&self) -> u64 { let r = &self.a; let t = self.b; *r + t } }', 'ok'),
    ('param-move-after-holder-dead', S + 'pub fn f(s: S) -> u64 { let r = &s.b; let v = *r; take(s) + v }', 'ok'),
    ('param-reassign-while-borrowed', S + 'pub fn f(mut s: S) -> u64 { let r = &s.b; s = S { a: 0, b: 0 }; *r }', 'E0506'),
    ('nested-loop-holder', 'pub fn f() -> u64 { let mut x = 0u64; let mut t = 0; let mut i = 0; while i < 2 { let r = &x; let mut j = 0; while j < 2 { t += *r; j += 1; } x += 1; i += 1; } t }', 'ok'),
    ('nested-loop-holder-outer', 'pub fn f() -> u64 { let mut x = 0u64; let r = &x; let mut t = 0; let mut i = 0; while i < 2 { let mut j = 0; while j < 2 { t += *r; j += 1; } x += 1; i += 1; } t }', 'E0506'),
    ('borrow-moved-field', S + 'pub fn f() -> u64 { let s = S { a: 1, b: 2 }; let t = s; let r = &s.a; *r + t.a }', 'E0382'),
    ('holder-copied-into-param-call', 'pub fn f() -> u64 { let mut a = 1u64; let r = &a; let v = get2(r); a = 3; v + a } pub fn get2(x: &u64) -> u64 { *x }', 'ok'),
    ('array-element-borrow-then-move-array', S + 'pub fn f(a: [S; 2]) -> u64 { let r = &a[0].b; let v = *r; let b = a; v + b[1].a }', 'ok'),
    ('array-element-borrow-live-move-array', S + 'pub fn f(a: [S; 2]) -> u64 { let r = &a[0].b; let b = a; *r + b[1].a }', 'E0505'),
    ('shadowed-holder', 'pub fn f() -> u64 { let mut a = 1u64; let r = &a; let r = 7u64; a = 2; r + a }', 'ok'),
    ('block-tail-holder-copy', 'pub fn f(c: bool) -> u64 { let mut a = 1u64; let b = 2u64; let r = { if c { &a } else { &b } }; a = 5; *r }', 'E0506'),
    ('match-arm-ref-value', 'pub fn f(c: u64) -> u64 { let a = 1u64; let b = 2u64; let r = match c { 0 => &a, _ => &b }; *r }', 'ok'),
    ('match-arm-ref-value-loan', 'pub fn f(c: u64) -> u64 { let a = 1u64; let mut b = 2u64; let r = match c { 0 => &a, _ => &b }; b = 3; *r }', 'E0506'),
    ('for-over-copy-with-holder', 'pub fn f(arr: [u64; 3]) -> u64 { let r = &arr; let mut t = 0; for v in arr { t += v; } t + r[0] }', 'ok'),
    ('for-moves-borrowed-array', S + 'pub fn f(arr: [S; 2]) -> u64 { let r = &arr; let mut t = 0; for v in arr { t += take(v); } t + r[0].a }', 'E0505'),
)
MESSAGES = {
    'E0505': r'because it is borrowed \(E0505\)',
    'E0506': r'because it is borrowed \(E0506\)',
    'E0597': r'does not live long enough: it is dropped while still borrowed \(E0597\)',
    'E0382': 'use of moved or possibly moved value',
    'E0507': 'cannot move a non-Copy value out of a shared reference',
    'E0508': 'cannot move a non-Copy value out of an array index',
    'E0594': 'cannot assign through a shared reference',
}


@pytest.mark.parametrize('name,source,verdict', CORPUS, ids=[row[0] for row in CORPUS])
def test_shared_loans_match_a_rustc_build_without_running_rustc(name, source, verdict, monkeypatch):
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
        codes = sorted(set(re.findall(r'error\[(E\d+)\]', run.stderr)))
        assert (run.returncode == 0, codes) == ((True, []) if verdict == 'ok' else (False, [verdict])), \
            (name, run.stderr)


# Reference-bearing originals (formerly admitted only after an actual rustc
# check) that owned shared loans now admit; each defines answer() -> u64.
PROGRAMS = ('arrays_indexed_borrows', 'core_self_alias_consumer', 'inferred_shared', 'method_generic_shared', 'modules_shared_reference', 'shared_explicit', 'shared_generic', 'shared_local_rebinding', 'shared_methods', 'shared_order', 'shared_partial', 'shared_reborrow', 'shared_scalar', 'use_reference_alias')


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE or (None if shutil.which('rustc') else
                                                  'development-only rustc is not installed'))
@pytest.mark.parametrize('name', PROGRAMS)
def test_reference_programs_run_with_owned_admission_and_match_rustc(name, tmp_path, monkeypatch):
    source = Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()
    owned = run_owned_u64_function(source, 'answer', tmp_path, monkeypatch, experimental_rustc_checked=False)
    reference = run_rustc_str_function(source, 'answer', tmp_path, output='&{call}.to_le_bytes()')
    assert owned == int.from_bytes(reference, 'little')


@pytest.mark.parametrize('source,boundary', [
    ("pub struct W<'a> { pub r: &'a u64 }", 'lifetime parameters on data declarations require'),
    ('pub fn f(x: &u64) -> (&u64, u64) { (x, 1) }', 'reference returns and aggregate reference storage require'),
    ('pub fn f() -> u64 { let a = 1u64; let t = (&a, 1u64); 0 }', 'aggregate reference storage requires'),
    ('pub fn id<T>(t: T) -> T { t } pub fn f() -> u64 { let a = 1u64; *id(&a) }',
     'generic function reference arguments require'),
    ('pub fn f() -> u64 { let r = &5u64; *r }', 'shared borrowing of temporaries requires'),
    ('pub enum E { A(u64), B } pub fn f(e: &E) -> u64 { match e { E::A(x) => *x, E::B => 0 } }',
     'needs default binding modes, which are not implemented'),
])
def test_reference_shapes_beyond_shared_loans_keep_explicit_boundaries(source, boundary):
    with pytest.raises(RustError, match=boundary):
        compile_rust(source)
