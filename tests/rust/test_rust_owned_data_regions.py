"""Owned data regions: structs, enums and impls with one lifetime parameter.

Stage 6 of pcc/frontends/rust/README.md, "Replacing the rustc admission gate
with owned checking" (second part): a struct or enum with one lifetime
parameter and no type parameters may store references (`&'a T`, `&'a mut T`,
`&'static T` with a region-free T) and other such data types written with
'a, and `impl<'a> W<'a>` methods may name 'a, with no rustc and no caller
attestation. A value has up to two region slots (pcc/frontends/rust/regions.py):
`W<'a>` and `&'a T` one, `&'s W<'a>` two. The move checker keeps the inner
slot's entries beside the outer ones: a field read yields its data type's slot,
a reference to a `W<'a>` place carries that slot inside, a reborrow holds the
last dereferenced reference's slots (only the outer one for region-free data,
as rustc's reborrow constraints do), a call result maps the callee's region
slots, and a write through a `&mut` adds the written loans to every local the
reference may point to (into caller memory they must satisfy the parameter's
region; a local's loan there is E0597). Well-formed `&'s W<'a>` parameters
imply 'a: 's. rustc's declaration errors are reported: E0106 for an elided
field lifetime, E0392 for an unused lifetime, E0107 for a lifetime argument
count, E0637 for `'_` in a field, E0621 for an anonymous parameter region
where a named one is required. Every corpus row is original; its verdict was
compared with a local rustc 1.92.0 build (-O), which stays a labeled reference.
"""
import re
import shutil
import subprocess

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.str_execution import UNAVAILABLE, run_owned_u64_function, run_rustc_str_function

W = "pub struct W<'a> { pub r: &'a u64, pub n: u64 } impl<'a> W<'a> { pub fn get(&self) -> &'a u64 { self.r } pub fn near(&self) -> &u64 { &self.n } pub fn bump(&mut self) { self.n += 1; } } "
O = "pub enum O<'a> { S(&'a u64), N } "
W2 = "pub struct W<'a> { pub r: &'a u64, pub n: u64 } impl<'a> W<'a> { pub fn get(&self) -> &'a u64 { self.r } } "
M = "pub struct M<'a> { pub r: &'a mut u64 } impl<'a> M<'a> { pub fn inc(&mut self) { *self.r += 1; } pub fn peek(&self) -> u64 { *self.r } } "
I = "pub struct It<'a> { pub items: &'a [u64; 3], pub i: usize } "

# (name, original source, verdict of a rustc 1.92.0 build; 'lifetime' is its
# code-less "lifetime may not live long enough")
CORPUS = (
    ('construct-and-read', W + 'pub fn f() -> u64 { let x = 5u64; let w = W { r: &x, n: 1 }; *w.r + w.n }', 'ok'),
    ('struct-outlives-referent', W + 'pub fn f() -> u64 { let z = 0u64; let mut w = W { r: &z, n: 0 }; { let x = 5u64; w = W { r: &x, n: 1 }; } *w.r }', 'E0597'),
    ('struct-referent-scope', W + 'pub fn f() -> u64 { let z = 0u64; let mut w = W { r: &z, n: 0 }; let mut v = 0; { let x = 5u64; w.r = &x; v = *w.r; } v }', 'ok'),
    ('struct-field-write-outlives', W + 'pub fn f() -> u64 { let z = 0u64; let mut w = W { r: &z, n: 0 }; { let x = 5u64; w.r = &x; } *w.r }', 'E0597'),
    ('via-ref-implied-bound', W + 'pub fn f() -> u64 { let mut x = 5u64; let w = W { r: &x, n: 1 }; let p = &w; let n = &p.n; x = 2; *n }', 'ok'),
    ('direct-field-borrow-no-bound', W + 'pub fn f() -> u64 { let mut x = 5u64; let w = W { r: &x, n: 1 }; let n = &w.n; x = 2; *n }', 'ok'),
    ('write-through-mut-ref-local', W + 'pub fn f() -> u64 { let z = 0u64; let mut w = W { r: &z, n: 0 }; { let x = 5u64; let p = &mut w; p.r = &x; } *w.r }', 'E0597'),
    ('write-through-mut-ref-ok', W + 'pub fn f() -> u64 { let z = 0u64; let x = 5u64; let mut w = W { r: &z, n: 0 }; { let p = &mut w; p.r = &x; } *w.r }', 'ok'),
    ('setter-call-local', W + "impl<'a> W<'a> { pub fn set(&mut self, r: &'a u64) { self.r = r; } } pub fn f() -> u64 { let z = 0u64; let mut w = W { r: &z, n: 0 }; { let x = 5u64; w.set(&x); } *w.r }", 'E0597'),
    ('setter-call-ok', W + "impl<'a> W<'a> { pub fn set(&mut self, r: &'a u64) { self.r = r; } } pub fn f() -> u64 { let z = 0u64; let x = 5u64; let mut w = W { r: &z, n: 0 }; w.set(&x); *w.r }", 'ok'),
    ('setter-param-local', W + "impl<'a> W<'a> { pub fn bad(&mut self) { let x = 1u64; self.r = &x; } }", 'E0597'),
    ('setter-param-other-region', W + "impl<'a> W<'a> { pub fn bad2(&mut self, r: &u64) { self.r = r; } }", 'E0621'),
    ('getter-copy-out', W + 'pub fn f() -> u64 { let x = 5u64; let z = 0u64; let mut r = &z; { let w = W { r: &x, n: 1 }; r = w.get(); } *r }', 'ok'),
    ('struct-param-returns-field', W + "pub fn first<'a>(w: W<'a>) -> &'a u64 { w.r }", 'ok'),
    ('struct-ref-param-returns-own', W + "pub fn own<'a>(w: &W<'a>) -> &'a u64 { &w.n }", 'E0621'),
    ('struct-holds-loan', W + 'pub fn f() -> u64 { let mut x = 5u64; let w = W { r: &x, n: 1 }; x = 6; *w.r }', 'E0506'),
    ('struct-dead-before-write', W + 'pub fn f() -> u64 { let mut x = 5u64; let w = W { r: &x, n: 1 }; let v = *w.r; x = 6; v + x }', 'ok'),
    ('getter-independent-of-self', W + 'pub fn f() -> u64 { let x = 5u64; let mut w = W { r: &x, n: 1 }; let g = w.get(); w.bump(); *g }', 'ok'),
    ('near-tied-to-self', W + 'pub fn f() -> u64 { let x = 5u64; let mut w = W { r: &x, n: 1 }; let g = w.near(); w.bump(); *g }', 'E0502'),
    ('getter-keeps-referent', W + 'pub fn f() -> u64 { let mut x = 5u64; let w = W { r: &x, n: 1 }; let g = w.get(); x = 7; *g }', 'E0506'),
    ('field-through-ref', W + "pub fn read<'a>(p: &W<'a>) -> &'a u64 { p.r }", 'ok'),
    ('field-through-ref-elided-two', W + 'pub fn read(p: &W) -> &u64 { p.r }', 'E0106'),
    ('param-struct-elided', W + 'pub fn read(w: W) -> &u64 { w.r }', 'ok'),
    ('param-struct-elided-explicit-anon', W + "pub fn read(w: W<'_>) -> &u64 { w.r }", 'ok'),
    ('return-struct', W + "pub fn make<'a>(x: &'a u64) -> W<'a> { W { r: x, n: 0 } }", 'ok'),
    ('return-struct-local', W + "pub fn make<'a>(x: &'a u64) -> W<'a> { let y = 1u64; W { r: &y, n: 0 } }", 'E0515'),
    ('return-struct-elided', W + 'pub fn make(x: &u64) -> W { W { r: x, n: 0 } }', 'ok'),
    ('caller-make-keeps-loan', W + "pub fn make<'a>(x: &'a u64) -> W<'a> { W { r: x, n: 0 } } pub fn f() -> u64 { let mut a = 1u64; let w = make(&a); a = 2; *w.r }", 'E0506'),
    ('enum-payload', O + 'pub fn f() -> u64 { let x = 5u64; let o = O::S(&x); match o { O::S(r) => *r, O::N => 0 } }', 'ok'),
    ('enum-payload-holds-loan', O + 'pub fn f() -> u64 { let mut x = 5u64; let o = O::S(&x); x = 6; match o { O::S(r) => *r, O::N => 0 } }', 'E0506'),
    ('enum-payload-binding-outlives', O + 'pub fn f() -> u64 { let mut x = 5u64; let o = O::S(&x); let z = 0u64; let v = match o { O::S(r) => r, O::N => &z }; x = 6; *v }', 'E0506'),
    ('nested-struct', W + "pub struct V<'a> { pub w: W<'a> } pub fn f() -> u64 { let x = 5u64; let v = V { w: W { r: &x, n: 1 } }; *v.w.r }", 'ok'),
    ('mut-field-struct', "pub struct M<'a> { pub r: &'a mut u64 } pub fn f() -> u64 { let mut x = 5u64; let m = M { r: &mut x }; *m.r += 1; x }", 'ok'),
    ('mut-field-struct-conflict', "pub struct M<'a> { pub r: &'a mut u64 } pub fn f() -> u64 { let mut x = 5u64; let m = M { r: &mut x }; x += 1; *m.r }", 'E0503'),
    ('copy-struct-ref', "#[derive(Clone, Copy)] pub struct C<'a> { pub r: &'a u64 } pub fn f() -> u64 { let x = 5u64; let c = C { r: &x }; let d = c; *c.r + *d.r }", 'ok'),
    ('impl-method-uses-a', W + "impl<'a> W<'a> { pub fn first(&self, other: &'a u64) -> &'a u64 { if *self.r > *other { self.r } else { other } } }", 'ok'),
    ('impl-method-wrong-region', W + "impl<'a> W<'a> { pub fn bad(&self, other: &u64) -> &'a u64 { other } }", 'E0621'),
    ('mut-self-setter', W + "impl<'a> W<'a> { pub fn set(&mut self, r: &'a u64) { self.r = r; } }", 'ok'),
    ('static-field', "pub struct T<'a> { pub r: &'a u64, pub s: &'static u64 } pub fn f(t: T) -> u64 { *t.r + *t.s }", 'ok'),
    ('if-let-enum', O + 'pub fn f() -> u64 { let x = 5u64; let o = O::S(&x); if let O::S(r) = o { *r } else { 0 } }', 'ok'),
    ('while-let-enum-reassign', O + 'pub fn f() -> u64 { let x = 5u64; let mut o = O::S(&x); let mut t = 0; while let O::S(r) = o { t += *r; o = O::N; } t }', 'ok'),
    ('enum-returned-from-method', W2 + O + "impl<'a> W<'a> { pub fn opt(&self) -> O<'a> { if self.n > 0 { O::S(self.r) } else { O::N } } } pub fn f() -> u64 { let x = 5u64; let w = W { r: &x, n: 1 }; match w.opt() { O::S(r) => *r, O::N => 0 } }", 'ok'),
    ('enum-returned-keeps-referent', W2 + O + "impl<'a> W<'a> { pub fn opt(&self) -> O<'a> { O::S(self.r) } } pub fn f() -> u64 { let mut x = 5u64; let w = W { r: &x, n: 1 }; let o = w.opt(); x = 2; match o { O::S(r) => *r, O::N => 0 } }", 'E0506'),
    ('mut-field-methods', M + 'pub fn f() -> u64 { let mut x = 5u64; { let mut m = M { r: &mut x }; m.inc(); m.inc(); } x }', 'ok'),
    ('mut-field-methods-conflict', M + 'pub fn f() -> u64 { let mut x = 5u64; let mut m = M { r: &mut x }; m.inc(); let y = x; m.inc(); y }', 'E0503'),
    ('mut-field-peek-then-read', M + 'pub fn f() -> u64 { let mut x = 5u64; let m = M { r: &mut x }; let v = m.peek(); v + x }', 'ok'),
    ('struct-in-loop-dangling', W2 + 'pub fn f() -> u64 { let z = 0u64; let mut w = W { r: &z, n: 0 }; for v in [1u64, 2] { w = W { r: &v, n: v }; } *w.r }', 'E0597'),
    ('struct-in-loop-ok', W2 + 'pub fn f() -> u64 { let z = 0u64; let mut t = 0; let mut w = W { r: &z, n: 0 }; for v in [1u64, 2] { w = W { r: &v, n: v }; t += *w.r; } t }', 'ok'),
    ('dup-self', W2 + "impl<'a> W<'a> { pub fn dup(&self) -> W<'a> { W { r: self.r, n: 0 } } } pub fn f() -> u64 { let x = 5u64; let w = W { r: &x, n: 1 }; let d = w.dup(); *d.r }", 'ok'),
    ('dup-self-type', W2 + "impl<'a> W<'a> { pub fn dup(&self) -> Self { W { r: self.r, n: 0 } } }", 'ok'),
    ('elide-self-with-struct-param', W2 + 'pub struct S { pub a: u64 } impl S { pub fn pick(&self, w: W) -> &u64 { &self.a } }', 'ok'),
    ('elide-self-returns-struct-field', W2 + 'pub struct S { pub a: u64 } impl S { pub fn pick(&self, w: W) -> &u64 { w.r } }', 'lifetime'),
    ('static-struct', W2 + "pub fn f(w: W<'static>) -> &'static u64 { w.r }", 'ok'),
    ('static-struct-wrong', W2 + "pub fn f<'a>(w: W<'a>) -> &'static u64 { w.r }", 'lifetime'),
    ('struct-moved-then-field', W2 + 'pub fn f() -> u64 { let x = 5u64; let w = W { r: &x, n: 1 }; let v = w; *w.r + *v.r }', 'E0382'),
    ('getter-after-referent-move', "pub struct B { pub v: u64 } pub struct W<'a> { pub r: &'a B } pub fn take(b: B) -> u64 { b.v } pub fn f() -> u64 { let b = B { v: 1 }; let w = W { r: &b }; let t = take(b); w.r.v + t }", 'E0505'),
    ('struct-field-reassign-param', W2 + "pub fn f<'a>(mut w: W<'a>, o: &'a u64) -> u64 { w.r = o; *w.r }", 'ok'),
    ('struct-field-reassign-param-local', W2 + 'pub fn f(mut w: W) -> u64 { let x = 1u64; w.r = &x; *w.r }', 'E0597'),
)
MESSAGES = {
    'lifetime': 'lifetime may not live long enough',
    'E0106': r'missing lifetime specifier.*\(E0106\)',
    'E0382': 'use of moved or possibly moved value',
    'E0502': r'because it is also borrowed as (im)?mutable \(E0502\)',
    'E0503': r'because it was mutably borrowed \(E0503\)',
    'E0505': r'because it is borrowed \(E0505\)',
    'E0506': r'because it is borrowed \(E0506\)',
    'E0515': r'data owned by the current function \(E0515\)',
    'E0597': r'does not live long enough.*\(E0597\)',
    'E0621': r'explicit lifetime required in the type of .*\(E0621\)',
}


@pytest.mark.parametrize('name,source,verdict', CORPUS, ids=[row[0] for row in CORPUS])
def test_data_regions_match_a_rustc_build_without_running_rustc(name, source, verdict, monkeypatch):
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
        if not codes and 'lifetime may not live long enough' in run.stderr:
            codes = ['lifetime']
        assert (run.returncode == 0, codes) == ((True, []) if verdict == 'ok' else (False, [verdict])), \
            (name, run.stderr)


# An original program: a struct holding a reference with getters, a setter,
# `Self` results, an enum of references returned through a method and a
# struct holding a `&mut` written through its methods.
PROGRAMS = {
    'struct-references': "pub struct W<'a> { pub r: &'a u64, pub n: u64 }\nimpl<'a> W<'a> {\n    pub fn get(&self) -> &'a u64 { self.r }\n    pub fn near(&self) -> &u64 { &self.n }\n    pub fn dup(&self) -> Self { W { r: self.r, n: self.n + 1 } }\n    pub fn set(&mut self, r: &'a u64) { self.r = r; }\n}\npub enum O<'a> { S(&'a u64), N }\npub fn pick<'a>(w: &W<'a>, c: bool) -> O<'a> { if c { O::S(w.r) } else { O::N } }\npub struct M<'a> { pub r: &'a mut u64 }\nimpl<'a> M<'a> { pub fn inc(&mut self) { *self.r += 1; } pub fn peek(&self) -> u64 { *self.r } }\npub fn answer() -> u64 {\n    let x = 5u64;\n    let y = 7u64;\n    let mut w = W { r: &x, n: 1 };\n    let a = *w.get() + *w.near();\n    let d = w.dup();\n    w.set(&y);\n    let b = *w.r + d.n + *d.r;\n    let c = match pick(&w, true) { O::S(r) => *r, O::N => 0 } + match pick(&w, false) { O::S(r) => *r, O::N => 100 };\n    let mut z = 10u64;\n    { let mut m = M { r: &mut z }; m.inc(); m.inc(); let p = m.peek(); z += 0; let _ = p; }\n    if a == 6 && b == 14 && c == 107 && z == 12 { 0xfedcba9876543210 } else { a * 1000000 + b * 1000 + c }\n}\n",
}


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE or (None if shutil.which('rustc') else
                                                  'development-only rustc is not installed'))
@pytest.mark.parametrize('name', sorted(PROGRAMS))
def test_data_region_programs_run_with_owned_admission_and_match_rustc(name, tmp_path, monkeypatch):
    source = PROGRAMS[name]
    owned = run_owned_u64_function(source, 'answer', tmp_path, monkeypatch, experimental_rustc_checked=False)
    reference = run_rustc_str_function(source, 'answer', tmp_path, output='&{call}.to_le_bytes()')
    assert owned == int.from_bytes(reference, 'little') == 0xfedcba9876543210


@pytest.mark.parametrize('source,message', [
    # rustc's own declaration errors
    ("pub struct W<'a> { pub r: &'a u64 } pub struct V<'a> { pub w: W }", r'missing lifetime specifier.*\(E0106\)'),
    ('pub struct R { pub p: &u64 }', r'missing lifetime specifier.*\(E0106\)'),
    ("pub struct R<'a> { pub p: &u64, pub q: &'a u64 }", r'missing lifetime specifier.*\(E0106\)'),
    ("pub struct View<'a> { pub n: u64 }", r"lifetime parameter `'a` is never used \(rustc E0392\)"),
    ("pub struct S { pub n: u64 } pub fn f(s: S<'a>) -> u64 { 1 }", r'S takes 0 lifetime arguments.*\(rustc E0107\)'),
    ("pub struct W<'a> { pub r: &'a u64 } pub struct V<'a> { pub w: W<'_> }", r"`'_` cannot be used in a field type \(rustc E0637\)"),
    ("pub struct R<'a> { pub p: &'b u64 }", r"use of undeclared lifetime name `'b` \(rustc E0261\)"),
    # explicit boundaries
    ("pub struct T<'a, 'b> { pub x: &'a u64, pub y: &'b u64 }", 'several lifetime parameters'),
    ("pub struct G<'a, T> { pub x: &'a T }", 'several lifetime parameters, or lifetimes beside type parameters'),
    ("pub struct R<'a> { pub p: (&'a u64, u64) }", 'references inside tuple or array fields need region inference'),
    ("pub struct R<'a> { pub p: &'a &'a u64 }", 'a reference field to data with regions needs region inference'),
    ("pub struct W<'a> { pub r: &'a u64 } pub struct V<'a> { pub w: &'a W<'a> }", 'a reference field to data with regions needs region inference'),
    ("pub struct W<'a> { pub r: &'a u64 } pub fn f<'a>(w: W<'a>) -> (W<'a>, u64) { (w, 1) }", 'reference returns and aggregate reference storage require'),
])
def test_data_region_declarations_report_rustc_errors_or_explicit_boundaries(source, message):
    with pytest.raises(RustError, match=message):
        compile_rust(source)
