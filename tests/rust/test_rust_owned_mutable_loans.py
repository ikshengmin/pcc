"""Owned mutable loans: `&mut` in bodies, parameters and `&mut self` receivers.

Stage 5 of pcc/frontends/rust/README.md, "Replacing the rustc admission gate
with owned checking": `&mut` is admitted by PCC's own analysis, with no rustc
and no caller attestation. Loans have kinds and places carry explicit
dereferences; an access conflicts with a live loan by rustc's rules
(https://rust-lang.github.io/rfcs/2094-nll.html): a read or shared borrow
with a mutable loan (E0503, E0502), a mutable borrow with any loan (E0499,
E0502), a write (E0506), a move (E0505) or a scope end (E0597) with any loan,
where a write to or the end of a reference does not reach behind its
dereference. `&mut` values move on use; at coercion sites (arguments,
annotated lets, assignments) a `&mut` place is implicitly reborrowed, and
method autoref receivers and argument reborrows are two-phase: reserved while
the arguments are evaluated (reads and shared borrows allowed, mutable loans
conflict, E0499), activated by the call. Reassigning a reference releases
loans of its old referent. Every corpus row is original; its verdict was
compared with a local rustc 1.92.0 build (-O), which stays a labeled reference.
"""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.str_execution import UNAVAILABLE, run_owned_u64_function, run_rustc_str_function

P = 'pub struct S { pub a: u64, pub b: u64 } impl S { pub fn get(&self) -> u64 { self.a } pub fn set(&mut self, n: u64) { self.a = n; } pub fn both(&mut self, o: &S) -> u64 { o.a } } pub fn inc(x: &mut u64) { *x += 1; } pub fn add(x: &mut u64, n: u64) { *x += n; } pub fn two(x: &mut u64, y: &mut u64) { *x += *y; } pub fn read(x: &u64) -> u64 { *x } '
S = 'pub struct S { pub a: u64, pub b: u64 } impl S { pub fn get(&self) -> u64 { self.a } pub fn set(&mut self, n: u64) { self.a = n; } } pub fn inc(x: &mut u64) { *x += 1; } '
E = 'pub enum E { A(u64), B } '

# (name, original source, verdict of a rustc 1.92.0 build)
CORPUS = (
    ('call-reborrow-twice', P + 'pub fn f() -> u64 { let mut a = 1; let r = &mut a; inc(r); inc(r); *r }', 'ok'),
    ('let-move-then-use', P + 'pub fn f() -> u64 { let mut a = 1; let r = &mut a; let s = r; *s + *r }', 'E0382'),
    ('let-annotated-reborrow', P + 'pub fn f() -> u64 { let mut a = 1; let r = &mut a; let s: &mut u64 = r; *s += 1; *r }', 'ok'),
    ('assign-reborrow', P + 'pub fn f() -> u64 { let mut a = 1; let mut b = 2; let r = &mut a; let mut s = &mut b; s = r; *s += 1; *r }', 'ok'),
    ('assign-reborrow-overlap', P + 'pub fn f() -> u64 { let mut a = 1; let mut b = 2; let r = &mut a; let mut s = &mut b; s = r; *r += 1; *s }', 'E0503'),
    ('two-phase-method', P + 'pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; s.set(s.get()); s.a }', 'ok'),
    ('two-phase-method-field', P + 'pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; s.set(s.b); s.a }', 'ok'),
    ('two-phase-shared-arg-live', P + 'pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; s.both(&s) }', 'E0502'),
    ('two-phase-arg-reborrow', P + 'pub fn f() -> u64 { let mut a = 1; let r = &mut a; add(r, *r); *r }', 'ok'),
    ('explicit-mut-then-read-arg', P + 'pub fn f() -> u64 { let mut a = 1; add(&mut a, a); a }', 'E0503'),
    ('two-mut-args', P + 'pub fn f() -> u64 { let mut a = 1; let r = &mut a; two(r, r); *r }', 'E0499'),
    ('coerce-mut-to-shared-arg', P + 'pub fn f() -> u64 { let mut a = 1; let r = &mut a; let v = read(r); *r += 1; v + *r }', 'ok'),
    ('coerce-fresh-mut-read-arg', P + 'pub fn g(x: &u64, n: u64) -> u64 { *x + n } pub fn f() -> u64 { let mut a = 1; g(&mut a, a) }', 'E0503'),
    ('read-while-mut-live', P + 'pub fn f() -> u64 { let mut a = 1; let r = &mut a; let v = a; *r += 1; v }', 'E0503'),
    ('shared-while-mut-live', P + 'pub fn f() -> u64 { let mut a = 1; let r = &mut a; let s = &a; *r += 1; *s }', 'E0502'),
    ('mut-while-shared-live', P + 'pub fn f() -> u64 { let mut a = 1; let s = &a; let r = &mut a; *r += 1; *s }', 'E0502'),
    ('two-mut-live', P + 'pub fn f() -> u64 { let mut a = 1; let r = &mut a; let s = &mut a; *r += 1; *s }', 'E0499'),
    ('reassign-ref-while-deref-borrowed', P + 'pub fn f() -> u64 { let mut a = 1; let mut b = 2; let mut r = &mut a; let s = &mut *r; r = &mut b; *s += 1; *r + *s }', 'ok'),
    ('use-ref-while-deref-borrowed', P + 'pub fn f() -> u64 { let mut a = 1; let r = &mut a; let s = &mut *r; *r += 1; *s }', 'E0503'),
    ('deref-borrow-outlives-ref-scope', P + 'pub fn f() -> u64 { let mut a = 1u64; let mut b = 2u64; let mut s = &mut b; { let r = &mut a; s = &mut *r; } *s }', 'ok'),
    ('mut-borrow-immutable', P + 'pub fn f() -> u64 { let a = 1; let r = &mut a; *r }', 'E0596'),
    ('assign-through-shared', P + 'pub fn f() -> u64 { let a = 1; let r = &a; *r = 2; a }', 'E0594'),
    ('move-out-of-mut-ref', P + 'pub fn f(r: &mut S) -> S { *r }', 'E0507'),
    ('swap-fields-disjoint', P + 'pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; let x = &mut s.a; let y = &mut s.b; *x += *y; s.a }', 'ok'),
    ('self-method-mut-field', 'pub struct S { pub a: u64 } impl S { pub fn bump(&mut self) -> u64 { let r = &mut self.a; *r += 1; self.a } }', 'ok'),
    ('ref-param-reborrow-twice', P + 'pub fn f(x: &mut u64) -> u64 { inc(x); inc(x); *x }', 'ok'),
    ('move-param-ref-twice', P + 'pub fn g(x: &mut u64) -> u64 { let y = x; let z = x; *y + *z }', 'E0382'),
    ('mut-ref-param-method', P + 'pub fn f(s: &mut S) -> u64 { s.set(3); s.set(s.get()); s.get() }', 'ok'),
    ('deref-in-loop', P + 'pub fn f() -> u64 { let mut a = 0; let r = &mut a; let mut i = 0; while i < 3 { *r += 1; i += 1; } a }', 'ok'),
    ('loop-mut-borrow-each-iteration', P + 'pub fn f() -> u64 { let mut a = 0; let mut i = 0; while i < 3 { let r = &mut a; *r += i; i += 1; } a }', 'ok'),
    ('loop-two-phase-holder', P + 'pub fn f() -> u64 { let mut a = 0; let r = &mut a; let mut i = 0; while i < 3 { a += 1; *r += 1; i += 1; } 0 }', 'E0503'),
    ('compound-through-ref-field', P + 'pub fn f(s: &mut S) { s.a += s.b; }', 'ok'),
    ('shared-reborrow-of-mut-then-write', P + 'pub fn f() -> u64 { let mut a = 1; let r = &mut a; let s = &*r; *r = 2; *s }', 'E0506'),
    ('shared-reborrow-of-mut-read-both', P + 'pub fn f() -> u64 { let mut a = 1; let r = &mut a; let s = &*r; *s + *r }', 'ok'),
    ('loop-reborrow-arg', S + 'pub fn f() -> u64 { let mut a = 0u64; let r = &mut a; for i in [1u64, 2] { inc(r); *r += i; } a }', 'ok'),
    ('while-let-through-mut', E + 'pub fn f(r: &mut E) -> u64 { let mut t = 0; while let E::A(x) = *r { t += x; *r = E::B; } t }', 'ok'),
    ('if-let-through-mut-write', E + 'pub fn f(r: &mut E) -> u64 { if let E::A(x) = *r { *r = E::A(x + 1); x } else { 0 } }', 'ok'),
    ('disjoint-fields-through-mut', S + 'pub fn f(s: &mut S) -> u64 { let t = &mut s.a; s.b = 1; *t += 1; s.a }', 'ok'),
    ('same-field-through-mut', S + 'pub fn f(s: &mut S) -> u64 { let t = &mut s.a; s.a = 1; *t }', 'E0506'),
    ('shared-field-then-mut-reborrow', S + 'pub fn f(s: &mut S) -> u64 { let t = &s.a; let u = &mut *s; u.b = 2; *t }', 'E0502'),
    ('move-ref-while-reborrowed', 'pub fn f() -> u64 { let mut a = 1u64; let r = &mut a; let s = &mut *r; let t = r; *s + *t }', 'E0505'),
    ('write-through-param', 'pub fn f(x: &mut u64) -> u64 { *x = 5; *x }', 'ok'),
    ('moved-param-then-use', S + 'pub fn f(x: &mut S) { let y = x; y.a = 1; x.a = 2; }', 'E0382'),
    ('annotated-reborrow-dead', S + 'pub fn f(x: &mut S) { let y: &mut S = x; y.a = 1; x.a = 2; }', 'ok'),
    ('annotated-reborrow-live', S + 'pub fn f(x: &mut S) { let y: &mut S = x; x.a = 2; y.a = 1; }', 'E0506'),
    ('method-while-shared-live', S + 'pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; s.set(1); let r = &s; s.set(2); r.a }', 'E0502'),
    ('method-while-mut-live', S + 'pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; let r = &mut s; s.set(1); r.a }', 'E0499'),
    ('compound-reads-borrowed-rhs', 'pub fn f() -> u64 { let mut a = 1u64; let mut b = 2u64; let r = &mut a; *r += a; b += 1; *r + b }', 'E0503'),
    ('loop-holder-deref', 'pub fn f() -> u64 { let mut a = 0u64; let r = &mut a; loop { *r += 1; if *r > 3 { break; } } a }', 'ok'),
    ('second-mut-after-first-dead', 'pub fn f() -> u64 { let mut v = 0u64; let r = &mut v; *r += 1; let s = &mut v; *s += 1; v }', 'ok'),
    ('dangling-mut-block', 'pub fn f() -> u64 { let mut a = 1u64; let mut r = &mut a; { let mut x = 5u64; r = &mut x; } *r }', 'E0597'),
    ('mut-self-calls-own-method', S + 'impl S { pub fn bump(&mut self) -> u64 { self.set(self.get() + 1); self.a } }', 'ok'),
    ('mut-self-two-phase-field-arg', S + 'impl S { pub fn copy_b(&mut self) { self.set(self.b); } }', 'ok'),
    ('mut-self-shared-arg-live', 'pub struct S { pub a: u64 } impl S { pub fn take(&mut self, o: &u64) -> u64 { *o } pub fn g(&mut self) -> u64 { self.take(&self.a) } }', 'E0502'),
    ('reborrow-chain', 'pub fn f() -> u64 { let mut a = 1u64; let r = &mut a; let s = &mut *r; let t = &mut *s; *t += 1; *s += 1; *r += 1; a }', 'ok'),
    ('reborrow-chain-violation', 'pub fn f() -> u64 { let mut a = 1u64; let r = &mut a; let s = &mut *r; let t = &mut *s; *s += 1; *t += 1; a }', 'E0503'),
    ('index-through-mut', 'pub fn f(arr: &mut [u64; 3]) -> u64 { arr[0] = 5; let x = &mut arr[1]; *x += arr[2]; arr[0] }', 'E0503'),
    ('index-mut-overlap', 'pub fn f(arr: &mut [u64; 3]) -> u64 { let x = &mut arr[1]; arr[2] = 5; *x }', 'E0506'),
    ('guard-reads-through-mut', E + 'pub fn f(r: &mut E, n: u64) -> u64 { match *r { E::A(x) if x > n => x, _ => 0 } }', 'ok'),
    ('swap-locals-via-refs', 'pub fn swap(x: &mut u64, y: &mut u64) { let t = *x; *x = *y; *y = t; } pub fn f() -> u64 { let mut a = 1u64; let mut b = 2u64; swap(&mut a, &mut b); a * 10 + b }', 'ok'),
    ('swap-same-twice', 'pub fn swap(x: &mut u64, y: &mut u64) { let t = *x; *x = *y; *y = t; } pub fn f() -> u64 { let mut a = 1u64; swap(&mut a, &mut a); a }', 'E0499'),
    ('struct-field-mut-arg-pair', S + 'pub fn two(x: &mut u64, y: &mut u64) { *x += *y; } pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; two(&mut s.a, &mut s.b); s.a }', 'ok'),
    ('if-mut-ref-value', 'pub fn f(c: bool) -> u64 { let mut a = 1u64; let mut b = 2u64; let r = if c { &mut a } else { &mut b }; *r += 1; a + b }', 'ok'),
    ('if-mut-ref-value-live', 'pub fn f(c: bool) -> u64 { let mut a = 1u64; let mut b = 2u64; let r = if c { &mut a } else { &mut b }; b += 1; *r }', 'E0503'),
    ('generic-mut-param', 'pub fn put<T>(x: &mut T, v: T) { *x = v; } pub fn f() -> u64 { let mut a = 1u64; put(&mut a, 5); a }', 'ok'),
    ('generic-body-mut-loan', 'pub fn g<T>(mut t: T, u: T) -> T { let r = &mut t; let w = t; *r = u; w }', 'E0505'),
)
MESSAGES = {
    'E0382': 'use of moved or possibly moved value',
    'E0499': r'as mutable more than once at a time \(E0499\)',
    'E0502': r'because it is also borrowed as (im)?mutable \(E0502\)',
    'E0503': r'because it was mutably borrowed \(E0503\)',
    'E0505': r'because it is borrowed \(E0505\)',
    'E0506': r'because it is borrowed \(E0506\)',
    'E0507': 'cannot move a non-Copy value out of a shared reference',
    'E0594': 'cannot assign through a shared reference',
    'E0596': 'cannot borrow an immutable place as mutable',
    'E0597': r'does not live long enough: it is dropped while still borrowed \(E0597\)',
}


@pytest.mark.parametrize('name,source,verdict', CORPUS, ids=[row[0] for row in CORPUS])
def test_mutable_loans_match_a_rustc_build_without_running_rustc(name, source, verdict, monkeypatch):
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


# `&mut`-bearing originals (formerly admitted only after an actual rustc check)
# that owned loans now admit; each defines answer() -> u64. The shared-only ones
# run in tests/rust/test_rust_owned_shared_loans.py.
PROGRAMS = ('arrays_argument_snapshot', 'arrays_index_temporary', 'arrays_len_effects', 'arrays_noncopy_repeats', 'arrays_repeat_effects', 'call_placeholder_order', 'call_placeholder_references', 'certified_disjoint_mutable_fields', 'certified_generic_mut', 'certified_generic_mut_receiver', 'certified_inferred_mut', 'certified_local_reborrow', 'certified_local_slot_rebinding', 'certified_mut_methods', 'certified_mut_record', 'certified_mut_scalar', 'certified_nonlexical_reborrow', 'empty_call_references', 'for_array_binding_reborrow', 'for_array_return', 'for_array_source_once', 'for_array_zero_effect', 'if_let_effects', 'inferred_receiver_references', 'loop_reference', 'method_generic_order', 'modules_mutable_reference', 'result_context_mutable', 'supertrait_shared_mutable', 'trait_static_local_drop_name', 'trait_static_order', 'trait_static_receivers', 'while_let_payload_flow')


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE or (None if shutil.which('rustc') else
                                                  'development-only rustc is not installed'))
@pytest.mark.parametrize('name', PROGRAMS)
def test_mutable_reference_programs_run_with_owned_admission_and_match_rustc(name, tmp_path, monkeypatch):
    source = Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()
    owned = run_owned_u64_function(source, 'answer', tmp_path, monkeypatch, experimental_rustc_checked=False)
    reference = run_rustc_str_function(source, 'answer', tmp_path, output='&{call}.to_le_bytes()')
    assert owned == int.from_bytes(reference, 'little')


@pytest.mark.parametrize('source,boundary', [
    ("pub fn f<'a>(x: &'a mut u64) {}", 'explicit function lifetimes require'),
    ('pub fn f(x: &mut u64) -> &mut u64 { x }', 'reference returns and aggregate reference storage require'),
    ('pub struct S { pub a: u64 } impl S { pub fn f(self: &mut Self) {} }', 'explicit receiver types require'),
    ('pub fn f(mut p: &mut u64) { let mut x = 1u64; p = &mut x; }', 'reference parameter reassignment requires'),
    ('pub fn id<T>(t: T) -> T { t } pub fn f() -> u64 { let mut a = 1u64; *id(&mut a) }',
     'generic function reference arguments require'),
])
def test_mutable_reference_shapes_beyond_owned_loans_keep_explicit_boundaries(source, boundary):
    with pytest.raises(RustError, match=boundary):
        compile_rust(source)
