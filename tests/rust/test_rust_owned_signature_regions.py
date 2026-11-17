"""Owned signature regions: reference results, explicit lifetimes, parameter regions.

Stage 6 of pcc/frontends/rust/README.md, "Replacing the rustc admission gate
with owned checking" (first part): functions and methods may return a
top-level reference and name lifetimes in their signatures, with no rustc and
no caller attestation. pcc/frontends/rust/regions.py applies rustc's
elision: a reference parameter has its written region or a fresh anonymous
one; an elided output takes `self`'s region, else the region of the only input
lifetime position, else E0106
(https://doc.rust-lang.org/reference/lifetime-elision.html). The move checker
gives reference parameters an origin (their universal region): a returned
value may hold no loan of the function's own storage (E0515) and only the
output region or 'static as origins ("lifetime may not live long enough",
which rustc reports without a code); assigning into a reference parameter
needs the same of its region; parameter holders are used at every exit, so
storing a reference to a local into one fails when the local dies (E0597). A
call returning a reference holds the values of the arguments whose region is
the output region. A function's result is a coercion site (`&mut T` to `&T`),
and an assignment may write through a reference a call returns. Every corpus
row is original; its verdict was compared with a local rustc 1.92.0 build
(-O), which stays a labeled reference.
"""
import re
import shutil
import subprocess

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.str_execution import UNAVAILABLE, run_owned_u64_function, run_rustc_str_function

S = 'pub struct S { pub a: u64, pub b: u64 } impl S { pub fn get(&self) -> &u64 { &self.a } pub fn set(&mut self, n: u64) { self.a = n; } pub fn slot(&mut self) -> &mut u64 { &mut self.b } } pub fn take(s: S) -> u64 { s.a } pub fn geta(s: &S) -> &u64 { &s.a } '
T = 'pub struct S { pub a: u64, pub b: u64 } impl S { pub fn get(&self) -> &u64 { &self.a } pub fn set(&mut self, n: u64) { self.a = n; } pub fn slot(&mut self) -> &mut u64 { &mut self.b } } pub fn g(x: &mut u64, n: u64) { *x += n; } pub fn ma(s: &mut S) -> &mut u64 { &mut s.a } '

# (name, original source, verdict of a rustc 1.92.0 build; 'lifetime' is its
# code-less "lifetime may not live long enough")
CORPUS = (
    ('elide-single-param', S + 'pub fn f(s: &S) -> &u64 { &s.b }', 'ok'),
    ('return-local', "pub fn f() -> &'static u64 { let x = 1u64; &x }", 'E0515'),
    ('return-local-elided', 'pub fn f(y: &u64) -> &u64 { let x = 1u64; &x }', 'E0515'),
    ('elide-two-params', 'pub fn f(a: &u64, b: &u64) -> &u64 { a }', 'E0106'),
    ('elide-no-params', 'pub fn f() -> &u64 { &1 }', 'E0106'),
    ('elide-self', S + 'impl S { pub fn pick(&self, o: &u64) -> &u64 { &self.b } }', 'ok'),
    ('elide-self-returns-other', S + 'impl S { pub fn pick(&self, o: &u64) -> &u64 { o } }', 'lifetime'),
    ('explicit-wrong-param', "pub fn f<'a, 'b>(x: &'a u64, y: &'b u64) -> &'a u64 { y }", 'lifetime'),
    ('explicit-right-param', "pub fn f<'a>(x: &'a u64, y: &u64) -> &'a u64 { x }", 'ok'),
    ('static-return-of-param', "pub fn f(x: &u64) -> &'static u64 { x }", 'lifetime'),
    ('caller-move-while-result-live', S + 'pub fn f() -> u64 { let s = S { a: 1, b: 2 }; let r = geta(&s); let t = take(s); *r + t }', 'E0505'),
    ('caller-unrelated-arg-free', "pub fn first<'a>(x: &'a u64, y: &u64) -> &'a u64 { x } pub fn f() -> u64 { let a = 1u64; let mut b = 2u64; let r = first(&a, &b); b = 5; *r + b }", 'ok'),
    ('caller-related-arg-live', "pub fn first<'a>(x: &'a u64, y: &u64) -> &'a u64 { x } pub fn f() -> u64 { let mut a = 1u64; let b = 2u64; let r = first(&a, &b); a = 5; *r + a }", 'E0506'),
    ('reassign-param-local', 'pub fn f(mut p: &u64) { let x = 1u64; p = &x; }', 'E0597'),
    ('reassign-param-local-used', 'pub fn f(mut p: &u64) -> u64 { let x = 5u64; p = &x; *p }', 'E0597'),
    ('reassign-param-other-elided', 'pub fn f(mut p: &u64, q: &u64) { p = q; }', 'lifetime'),
    ('reassign-param-same-region', "pub fn f<'a>(mut p: &'a u64, q: &'a u64) -> u64 { p = q; *p }", 'ok'),
    ('local-copy-of-param-reassigned-local', 'pub fn f(p: &u64) -> u64 { let mut q = p; let x = 5u64; q = &x; *q }', 'ok'),
    ('mut-return-field', S + 'pub fn f(x: &mut S) -> &mut u64 { &mut x.a }', 'ok'),
    ('caller-mut-result-live', S + 'pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; let r = s.slot(); s.a = 1; *r += 1; s.b }', 'E0506'),
    ('callee-reborrow-then-write', 'pub fn f(p: &mut u64) -> &mut u64 { let r = &mut *p; *p = 1; r }', 'E0506'),
    ('method-result-then-mutate', S + 'pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; let r = s.get(); s.set(1); *r }', 'E0502'),
    ('method-result-dead-then-mutate', S + 'pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; let v = *s.get(); s.set(1); v + s.a }', 'ok'),
    ('pick-if', "pub fn pick<'a>(c: bool, x: &'a u64, y: &'a u64) -> &'a u64 { if c { x } else { y } }", 'ok'),
    ('pick-if-elided', 'pub fn pick(c: bool, x: &u64) -> &u64 { if c { x } else { x } }', 'ok'),
    ('return-copy-of-param', 'pub fn f(x: &u64) -> &u64 { let y = x; y }', 'ok'),
    ('return-local-on-branch', 'pub fn f(x: &u64) -> &u64 { let a = 5u64; let r = &a; if *x > 0 { x } else { r } }', 'E0515'),
    ('early-return-param', 'pub fn f(x: &u64, c: bool) -> &u64 { if c { return x; } x }', 'ok'),
    ('early-return-local', 'pub fn f(x: &u64, c: bool) -> &u64 { let a = 1u64; if c { return &a; } x }', 'E0515'),
    ('return-mut-param-whole', 'pub fn f(x: &mut u64) -> &mut u64 { x }', 'ok'),
    ('return-shared-from-mut', 'pub fn f(x: &mut u64) -> &u64 { x }', 'ok'),
    ('chain-calls', S + 'pub fn f() -> u64 { let s = S { a: 1, b: 2 }; let r = geta(&s); let q = geta2(r); *q } pub fn geta2(x: &u64) -> &u64 { x }', 'ok'),
    ('chain-calls-move', S + 'pub fn f() -> u64 { let s = S { a: 1, b: 2 }; let q = geta2(geta(&s)); let t = take(s); *q + t } pub fn geta2(x: &u64) -> &u64 { x }', 'E0505'),
    ('generic-with-lifetime', "pub fn f<'a, T>(x: &'a T, n: u64) -> &'a T { x }", 'ok'),
    ('static-lifetime-param-local', "pub fn f(x: &'static u64) -> u64 { *x }", 'ok'),
    ('loop-param-holder', 'pub fn f(mut p: &u64, arr: [u64; 3]) -> u64 { let mut t = 0; for v in arr { p = &v; t += *p; } t }', 'E0597'),
    ('write-through-call', T + 'pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; *s.slot() = 5; *s.slot() += 1; s.b }', 'ok'),
    ('write-through-call-while-shared', T + 'pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; let r = s.get(); *s.slot() = 5; *r }', 'E0502'),
    ('write-through-free-call', T + 'pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; *ma(&mut s) += 4; s.a }', 'ok'),
    ('activated-result-then-read-arg', T + 'pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; g(s.slot(), s.a); s.b }', 'E0503'),
    ('activated-result-then-read-arg-ok', T + 'pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; let n = s.a; g(s.slot(), n); s.b }', 'ok'),
    ('result-held-then-moved', T + 'pub fn take(s: S) -> u64 { s.a } pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; let r = s.slot(); let t = take(s); *r + t }', 'E0505'),
    ('result-through-two-calls', T + 'pub fn id(x: &mut u64) -> &mut u64 { x } pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; let r = id(s.slot()); *r += 1; s.b }', 'ok'),
    ('result-through-two-calls-live', T + 'pub fn id(x: &mut u64) -> &mut u64 { x } pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; let r = id(s.slot()); s.a = 3; *r }', 'E0506'),
    ('explicit-lifetime-two-params-same', "pub fn f<'a>(x: &'a u64, y: &'a u64) -> &u64 { x }", 'E0106'),
    ('return-reborrow-of-local-ref', 'pub fn f(p: &u64) -> &u64 { let x = 5u64; let r = &x; let s = &*r; if *p > 0 { p } else { s } }', 'E0515'),
    ('static-param-to-static-out', "pub fn f(x: &'static u64) -> &'static u64 { x }", 'ok'),
    ('undeclared-lifetime', "pub fn f(x: &'a u64) -> u64 { *x }", 'E0261'),
    ('return-mut-from-shared-param', 'pub fn f(x: &u64) -> &mut u64 { x }', 'E0308'),
    ('return-param-storage', 'pub fn f(x: u64, y: &u64) -> &u64 { &x }', 'E0515'),
    ('assign-param-from-local-holder-of-param', 'pub fn f(mut p: &u64, q: &u64) -> u64 { let r = p; p = r; *p + *q }', 'ok'),
    ('mut-param-reassign-local', 'pub fn f(mut p: &mut u64) { let mut x = 1u64; p = &mut x; *p += 1; }', 'E0597'),
    ('method-explicit-self-lifetime', "pub struct S { pub a: u64 } impl S { pub fn get<'a>(&'a self, o: &u64) -> &'a u64 { &self.a } }", 'ok'),
    ('nested-call-result-coerced', T + 'pub fn read(x: &u64) -> u64 { *x } pub fn f() -> u64 { let mut s = S { a: 1, b: 2 }; let v = read(s.slot()); s.a = 5; v + s.a }', 'ok'),
)
MESSAGES = {
    'lifetime': 'lifetime may not live long enough',
    'E0106': r'missing lifetime specifier.*\(E0106\)',
    'E0261': r'use of undeclared lifetime name .*\(rustc E0261\)',
    'E0308': 'type mismatch',
    'E0502': r'because it is also borrowed as (im)?mutable \(E0502\)',
    'E0503': r'because it was mutably borrowed \(E0503\)',
    'E0505': r'because it is borrowed \(E0505\)',
    'E0506': r'because it is borrowed \(E0506\)',
    'E0515': r'data owned by the current function \(E0515\)',
    'E0597': r'does not live long enough: it is dropped while still borrowed \(E0597\)',
}


@pytest.mark.parametrize('name,source,verdict', CORPUS, ids=[row[0] for row in CORPUS])
def test_signature_regions_match_a_rustc_build_without_running_rustc(name, source, verdict, monkeypatch):
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


# Original programs: references returned from functions and methods, `&mut`
# results written through, explicit lifetimes and a reassigned parameter.
PROGRAMS = {
    'returns': "pub struct S { pub a: u64, pub b: u64 }\nimpl S {\n    pub fn get(&self) -> &u64 { &self.a }\n    pub fn slot(&mut self) -> &mut u64 { &mut self.b }\n    pub fn larger(&self, other: &u64) -> u64 { if self.a > *other { self.a } else { *other } }\n}\npub fn geta(s: &S) -> &u64 { &s.a }\npub fn pick<'a>(c: bool, x: &'a u64, y: &'a u64) -> &'a u64 { if c { x } else { y } }\npub fn first<'a>(x: &'a u64, y: &u64) -> &'a u64 { if *y > 100 { return x; } x }\npub fn bump(p: &mut u64) -> &mut u64 { *p += 1; p }\npub fn shared(p: &mut u64) -> &u64 { *p += 2; p }\npub fn answer() -> u64 {\n    let mut s = S { a: 3, b: 4 };\n    let v = *s.get() + *geta(&s);\n    *s.slot() += 10;\n    let a = 7u64;\n    let b = 9u64;\n    let p = *pick(true, &a, &b) * 100 + *pick(false, &a, &b);\n    let f = *first(&a, &b);\n    let mut c = 1u64;\n    *bump(&mut c) += 5;\n    let d = *shared(&mut c);\n    let e = s.larger(&a);\n    if v == 6 && s.b == 14 && p == 709 && f == 7 && c == 9 && d == 9 && e == 7 { 0xfedcba9876543210 } else { v * 1000 + s.b }\n}\n",
    'param-loop': "pub fn last<'a>(mut p: &'a u64, items: &'a [u64; 3]) -> &'a u64 {\n    if items[0] > *p { p = &items[0]; }\n    if items[1] > *p { p = &items[1]; }\n    if items[2] > *p { p = &items[2]; }\n    p\n}\npub fn answer() -> u64 { let x = 2u64; let arr = [1u64, 5, 3]; if *last(&x, &arr) == 5 { 0xfedcba9876543210 } else { 1 } }\n",
}


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE or (None if shutil.which('rustc') else
                                                  'development-only rustc is not installed'))
@pytest.mark.parametrize('name', sorted(PROGRAMS))
def test_reference_result_programs_run_with_owned_admission_and_match_rustc(name, tmp_path, monkeypatch):
    source = PROGRAMS[name]
    owned = run_owned_u64_function(source, 'answer', tmp_path, monkeypatch, experimental_rustc_checked=False)
    reference = run_rustc_str_function(source, 'answer', tmp_path, output='&{call}.to_le_bytes()')
    assert owned == int.from_bytes(reference, 'little') == 0xfedcba9876543210


@pytest.mark.parametrize('source,boundary', [
    ("pub struct W<'a> { pub r: &'a u64 }", 'lifetime parameters on data declarations require'),
    ('pub fn f(x: &u64) -> (&u64, u64) { (x, 1) }', 'reference returns and aggregate reference storage require'),
    ("pub struct S { pub a: u64 } impl<'a> S { pub fn f(&self) -> u64 { self.a } }", 'explicit impl lifetimes require'),
    ("pub fn f<'a, 'b: 'a>(x: &'b u64) -> &'a u64 { x }", 'generic bounds and default arguments require'),
    ("pub fn f<'a>(x: &'a u64) -> u64 { let y: &'a u64 = x; *y }", 'explicit reference lifetimes outside a function signature'),
    ('pub fn id<T>(t: T) -> T { t } pub fn f() -> u64 { let a = 1u64; *id(&a) }', 'generic function reference arguments require'),
])
def test_region_shapes_beyond_signatures_keep_explicit_boundaries(source, boundary):
    with pytest.raises(RustError, match=boundary):
        compile_rust(source)
