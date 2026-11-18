"""Owned lints and attributes: missing_docs, `#[inline]` and explicit receivers.

Stage 8 of pcc/frontends/rust/README.md, "Replacing the rustc admission gate
with owned checking" (first part). pcc/frontends/rust/missing_docs.py owns the
crate's `#![allow|warn|deny|forbid(missing_docs)]` level, following rustc's
MissingDoc pass: the crate root and every exported item (reachable through
`pub` modules or a `pub use` re-export) needs documentation, with its named
`pub` fields, variants and struct-variant fields, trait items and `pub`
inherent-impl items of an exported type; a `#[doc(hidden)]` item or anything
defined inside a hidden module, item or impl type needs none; overriding a
`forbid` is E0453. `#[inline]` attributes are validated as rustc does
(E0539/E0805 and "cannot be used on" targets) and stay advisory: owned
codegen applies none. Explicit receiver types (`self: &Self`, `self: &mut
Self`, `self: Self`) are the implicit receivers. No rustc and no attestation.
Every corpus row is original; its verdict was compared with a local rustc
1.92.0 build (-O), which stays a labeled reference.
"""
import re
import shutil
import subprocess

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.str_execution import UNAVAILABLE, run_owned_u64_function, run_rustc_str_function

H = '//! Crate docs.\n#![deny(missing_docs)]\n'

# (name, original source, verdict of a rustc 1.92.0 build): 'missing_docs' is
# its missing_docs lint error, 'inline-target' / 'inline-form' its code-less
# `#[inline]` placement and form errors.
CORPUS = (
    ('pub-fn', H + 'pub fn f() {}', 'missing_docs'),
    ('pub-fn-documented', H + '/// Docs.\npub fn f() {}', 'ok'),
    ('private-fn', H + 'fn f() {}', 'ok'),
    ('pub-fn-private-mod', H + 'mod m { pub fn f() {} }', 'ok'),
    ('pub-fn-reexported', H + 'mod m { pub fn f() {} }\npub use m::f;', 'missing_docs'),
    ('pub-crate-fn', H + 'pub(crate) fn f() {}', 'ok'),
    ('struct-pub-field', H + '/// S.\npub struct S { pub a: u64 }', 'missing_docs'),
    ('struct-private-field', H + '/// S.\npub struct S { a: u64 }', 'ok'),
    ('tuple-struct-field', H + '/// S.\npub struct S(pub u64);', 'ok'),
    ('enum-variant', H + '/// E.\npub enum E { A }', 'missing_docs'),
    ('enum-struct-variant-field', H + '/// E.\npub enum E {\n    /// A.\n    A { x: u64 },\n}', 'missing_docs'),
    ('trait-method', H + '/// T.\npub trait T { fn f(&self); }', 'missing_docs'),
    ('trait-assoc-type', H + '/// T.\npub trait T { type Out; }', 'missing_docs'),
    ('trait-impl-method', H + '/// T.\npub trait T {\n    /// F.\n    fn f(&self);\n}\n/// S.\npub struct S { a: u64 }\nimpl T for S { fn f(&self) {} }', 'ok'),
    ('inherent-pub-method', H + '/// S.\npub struct S { a: u64 }\nimpl S { pub fn f(&self) {} }', 'missing_docs'),
    ('inherent-private-method', H + '/// S.\npub struct S { a: u64 }\nimpl S { fn f(&self) {} }', 'ok'),
    ('inherent-pub-method-private-struct', H + 'struct S { a: u64 }\nimpl S { pub fn f(&self) {} }', 'ok'),
    ('doc-hidden-fn', H + '#[doc(hidden)]\npub fn f() {}', 'ok'),
    ('doc-hidden-struct-field', H + '#[doc(hidden)]\npub struct S { pub a: u64 }', 'ok'),
    ('doc-hidden-struct-method', H + '#[doc(hidden)]\npub struct S { a: u64 }\nimpl S { pub fn f(&self) {} }', 'ok'),
    ('doc-attribute', H + '#[doc = "Docs."]\npub fn f() {}', 'ok'),
    ('empty-doc-comment', H + '///\npub fn f() {}', 'ok'),
    ('no-crate-docs', '#![deny(missing_docs)]\n/// F.\npub fn f() {}', 'missing_docs'),
    ('crate-doc-attribute', '#![doc = "Crate."]\n#![deny(missing_docs)]\n/// F.\npub fn f() {}', 'ok'),
    ('pub-mod-undocumented', H + 'pub mod m {\n    /// F.\n    pub fn f() {}\n}', 'missing_docs'),
    ('pub-mod-inner-docs', H + 'pub mod m {\n    //! M.\n    /// F.\n    pub fn f() {}\n}', 'ok'),
    ('pub-mod-outer-docs', H + '/// M.\npub mod m {\n    /// F.\n    pub fn f() {}\n}', 'ok'),
    ('warn-level', '//! Crate docs.\n#![warn(missing_docs)]\npub fn f() {}', 'ok'),
    ('allow-level', '//! Crate docs.\n#![allow(missing_docs)]\npub fn f() {}', 'ok'),
    ('deny-then-allow', H + '#![allow(missing_docs)]\npub fn f() {}', 'ok'),
    ('allow-then-deny', '//! Crate docs.\n#![allow(missing_docs)]\n#![deny(missing_docs)]\npub fn f() {}', 'missing_docs'),
    ('forbid-then-allow', '//! Crate docs.\n#![forbid(missing_docs)]\n#![allow(missing_docs)]\n/// F.\npub fn f() {}', 'E0453'),
    ('pub-const', H + 'pub const C: u64 = 1;', 'missing_docs'),
    ('pub-static', H + 'pub static S: u64 = 1;', 'missing_docs'),
    ('pub-mod-in-private-mod', H + 'mod p {\n    pub mod q {\n        pub fn f() {}\n    }\n}', 'ok'),
    ('reexport-module', H + 'mod p {\n    /// Q.\n    pub mod q {\n        pub fn f() {}\n    }\n}\npub use p::q;', 'missing_docs'),
    ('reexport-alias', H + 'mod m { pub fn f() {} }\npub use m::f as g;', 'missing_docs'),
    ('reexport-documented-target', H + 'mod m {\n    /// F.\n    pub fn f() {}\n}\npub use m::f;', 'ok'),
    ('pub-struct-in-private-mod-used-in-sig', H + 'mod m { pub struct S { pub a: u64 } }\n/// F.\npub fn f(s: m::S) -> u64 { s.a }', 'ok'),
    ('trait-default-method', H + '/// T.\npub trait T { fn f(&self) -> u64 { 1 } }', 'missing_docs'),
    ('enum-variant-documented-tuple-payload', H + '/// E.\npub enum E {\n    /// A.\n    A(u64),\n}', 'ok'),
    ('inherent-assoc-fn', H + '/// S.\npub struct S { a: u64 }\nimpl S { pub fn new() -> Self { S { a: 0 } } }', 'missing_docs'),
    ('doc-hidden-module-items', H + '#[doc(hidden)]\npub mod m {\n    pub fn f() {}\n}', 'ok'),
    ('doc-hidden-enum-variants', H + '#[doc(hidden)]\npub enum E { A }', 'ok'),
    ('doc-hidden-trait-items', H + '#[doc(hidden)]\npub trait T { fn f(&self); }', 'ok'),
    ('doc-hidden-variant-fields', H + '/// E.\npub enum E {\n    #[doc(hidden)]\n    A { x: u64 },\n}', 'ok'),
    ('reexport-from-hidden-module', H + '#[doc(hidden)]\npub mod m {\n    pub fn f() {}\n}\npub use m::f;', 'ok'),
    ('doc-hidden-trait-impl-for-hidden', H + '/// T.\npub trait T {\n    /// F.\n    fn f(&self);\n}\n#[doc(hidden)]\npub struct S { a: u64 }\nimpl T for S { fn f(&self) {} }', 'ok'),
    ('struct-documented-fields-documented', H + '/// S.\npub struct S {\n    /// A.\n    pub a: u64,\n}', 'ok'),
    ('inline-fn', '#[inline]\npub fn f() -> u64 { 1 }', 'ok'),
    ('inline-always', '#[inline(always)]\npub fn f() -> u64 { 1 }', 'ok'),
    ('inline-never', '#[inline(never)]\npub fn f() -> u64 { 1 }', 'ok'),
    ('inline-bad-arg', '#[inline(sometimes)]\npub fn f() -> u64 { 1 }', 'E0539'),
    ('inline-two-args', '#[inline(always, never)]\npub fn f() -> u64 { 1 }', 'E0805'),
    ('inline-on-struct', '#[inline]\npub struct S { pub a: u64 }', 'inline-target'),
    ('inline-on-const', '#[inline]\npub const C: u64 = 1;', 'inline-target'),
    ('inline-duplicate', '#[inline]\n#[inline]\npub fn f() -> u64 { 1 }', 'ok'),
    ('inline-conflicting', '#[inline(always)]\n#[inline(never)]\npub fn f() -> u64 { 1 }', 'ok'),
    ('inline-method', 'pub struct S { pub a: u64 }\nimpl S {\n    #[inline]\n    pub fn get(&self) -> u64 { self.a }\n}', 'ok'),
    ('inline-trait-default', 'pub trait T {\n    #[inline]\n    fn f(&self) -> u64 { 1 }\n}', 'ok'),
    ('inline-trait-required', 'pub trait T {\n    #[inline]\n    fn f(&self) -> u64;\n}', 'ok'),
    ('inline-generic', '#[inline]\npub fn id<T>(t: T) -> T { t }', 'ok'),
    ('inline-value', '#[inline = "always"]\npub fn f() -> u64 { 1 }', 'inline-form'),
    ('inline-extern-decl', 'extern "C" {\n    #[inline]\n    fn g();\n}', 'ok'),
)
MESSAGES = {
    'missing_docs': r'missing documentation for .*\(missing_docs, (deny|forbid) at the crate root\)',
    'E0453': r'incompatible with previous forbid \(rustc E0453\)',
    'E0539': r'malformed `inline` attribute input.*\(rustc E0539\)',
    'E0805': r'malformed `inline` attribute input.*\(rustc E0805\)',
    'inline-target': 'attribute cannot be used on',
    'inline-form': 'valid forms for the attribute are',
}


def rustc_verdict(stderr, returncode):
    if returncode == 0:
        return 'ok'
    codes = sorted(set(re.findall(r'error\[(E\d+)\]', stderr)))
    if codes:
        return ','.join(codes)
    if 'missing documentation for' in stderr:
        return 'missing_docs'
    if 'cannot be used on' in stderr:
        return 'inline-target'
    if 'valid forms for the attribute' in stderr:
        return 'inline-form'
    return 'error'


@pytest.mark.parametrize('name,source,verdict', CORPUS, ids=[row[0] for row in CORPUS])
def test_lints_and_attributes_match_a_rustc_build_without_running_rustc(name, source, verdict, monkeypatch):
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


def test_a_test_build_skips_missing_docs():
    from pcc.frontends.rust.configuration import CfgOptions
    source = H + 'pub fn f() {}'
    with pytest.raises(RustError, match=MESSAGES['missing_docs']):
        compile_rust(source, cfg=CfgOptions())
    assert compile_rust(source, cfg=CfgOptions(test=True)).symbols


# Original programs: explicit receivers, and a crate that denies
# missing_docs with inline attributes on documented and private items.
PROGRAMS = {
    'explicit-receivers': "pub struct S { pub a: u64 }\nimpl S {\n    pub fn get(self: &Self) -> u64 { self.a }\n    pub fn bump(self: &mut Self) { self.a += 1; }\n    pub fn take(self: Self) -> u64 { self.a * 10 }\n    pub fn first<'a>(self: &'a Self, other: &u64) -> &'a u64 { if *other > 100 { &self.a } else { &self.a } }\n}\npub fn answer() -> u64 {\n    let mut s = S { a: 4 };\n    s.bump();\n    let seven = 7u64;\n    let r = *s.first(&seven);\n    let v = s.get() + r;\n    if v == 10 && s.take() == 50 { 0xfedcba9876543210 } else { v }\n}\n",
    'inline-and-lints': '//! A crate with every exported item documented.\n#![deny(missing_docs)]\n\n/// A counter.\npub struct Counter {\n    /// The current count.\n    pub n: u64,\n    hidden: u64,\n}\n\nimpl Counter {\n    /// Adds one.\n    #[inline]\n    pub fn bump(&mut self) { self.n += 1; self.hidden += 2; }\n    #[inline(always)]\n    fn private(&self) -> u64 { self.hidden }\n    /// Both counts.\n    #[inline(never)]\n    pub fn total(&self) -> u64 { self.n + self.private() }\n}\n\n/// Kinds of step.\npub enum Step {\n    /// One.\n    One,\n    /// Many.\n    Many {\n        /// How many.\n        count: u64,\n    },\n}\n\n/// Something with a size.\npub trait Sized2 {\n    /// The size.\n    fn size(&self) -> u64;\n}\n\nimpl Sized2 for Counter { fn size(&self) -> u64 { self.total() } }\n\nfn helper() -> u64 { 3 }\n\n/// The answer.\npub fn answer() -> u64 {\n    let mut c = Counter { n: 0, hidden: 0 };\n    c.bump();\n    c.bump();\n    let step = Step::Many { count: helper() };\n    let extra = match step { Step::One => 1, Step::Many { count } => count };\n    if c.size() == 6 && extra == 3 { 0xfedcba9876543210 } else { c.size() }\n}\n',
}


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE or (None if shutil.which('rustc') else
                                                  'development-only rustc is not installed'))
@pytest.mark.parametrize('name', sorted(PROGRAMS))
def test_programs_run_with_owned_admission_and_match_rustc(name, tmp_path, monkeypatch):
    source = PROGRAMS[name]
    owned = run_owned_u64_function(source, 'answer', tmp_path, monkeypatch, experimental_rustc_checked=False)
    reference = run_rustc_str_function(source, 'answer', tmp_path, output='&{call}.to_le_bytes()')
    assert owned == int.from_bytes(reference, 'little') == 0xfedcba9876543210


@pytest.mark.parametrize('source,boundary', [
    (H + '#[doc(alias = "g")]\npub fn f() {}', "unsupported doc option 'alias'"),
    (H + '#[allow(missing_docs)]\npub fn f() {}', 'unsupported Rust attribute'),
    ('#[inline]\npub const fn f() -> u64 { 1 }', 'target or qualifier is not implemented'),
    ('pub struct S { pub a: u64 } impl S { pub fn f(self: &u64) {} }', 'arbitrary explicit receiver types are not implemented'),
])
def test_forms_beyond_the_owned_lint_and_attributes_keep_explicit_boundaries(source, boundary):
    with pytest.raises(RustError, match=boundary):
        compile_rust(source)
