"""Lexical missing_docs in inner modules, checked against the real rustc owner.

Only the existing missing_docs and inactive recognized tool names are admitted.
Owning an error verdict does not claim that PCC emits rustc warning text.
"""
import pytest

from pcc.frontends.rust import (
    CfgOptions, RustError, TargetPlatformFacts, compile_rust, compile_rust_crate,
)
from pcc.frontends.rust.syntax import Parser
from tests.rust.str_execution import HOST_TARGET, UNAVAILABLE, run_owned_u64_function

ROOT='//! the crate.\n'
D='#![deny(missing_docs)]\n';A='#![allow(missing_docs)]\n';W='#![warn(missing_docs)]\n';F='#![forbid(missing_docs)]\n'
CASES={
 'allow-on-mod-decl':(ROOT+D+'pub mod m{'+A+'pub fn item(){}}','ok'),
 'deny-on-mod-decl':(ROOT+'pub mod m{'+D+'/// member\npub fn item(){}}','missing'),
 'documented-allow':(ROOT+D+'/// docs\npub mod m{'+A+'pub fn item(){}}','ok'),
 'allow-no-cross-sibling':(ROOT+D+'pub mod m{'+A+'pub fn item(){}} pub fn sister(){}','missing'),
 'deny-in-child-only':(ROOT+'/// docs\npub mod m{'+D+'pub fn item(){}} pub fn sister(){}','missing'),
 'private-not-exported':(ROOT+'mod m{'+D+'pub fn item(){}}','ok'),
 'private-export-definition':(ROOT+'mod m{'+D+'pub fn item(){}} pub use m::item;','missing'),
 'strict-reexport-of-exempt':(ROOT+D+'mod m{'+A+'pub fn item(){}} pub use m::item;','ok'),
 'warning-under-deny':(ROOT+D+'/// docs\npub mod m{'+W+'pub fn no_docs(){}}','warn'),
 'forbid-then-deny':(ROOT+F+D+'/// docs\npub fn item(){}','ok'),
 'forbid-deny-keeps-forbid':(ROOT+F+D+A+'/// docs\npub fn item(){}','E0453'),
 'forbid-in-unexported':(ROOT+F+'mod m{'+A+'fn private(){}}','E0453'),
 'forbid-in-test':(ROOT+F+'mod m{'+A+'fn private(){}}','E0453'),
 'forbid-within-child':(ROOT+'mod m{'+F+'mod n{'+A+'}}','E0453'),
 'allow-sibling-of-forbid':(ROOT+'mod m{'+F+'} mod n{'+A+'}','ok'),
 'inherit-one-child':(ROOT+D+'pub mod m{'+A+'pub mod n{pub fn inner(){}}}','ok'),
 'descendant-reenables':(ROOT+D+'pub mod m{'+A+'/// n\npub mod n{'+D+'pub fn inner(){}}}','missing'),
 'hidden-definition':(ROOT+D+'#[doc(hidden)] pub mod m{pub fn f(){}}pub use m::f;','ok'),
 'strong-glob-reexport-definition':(ROOT+'mod m{'+D+'pub fn item(){}}pub use m::*;','missing'),
 'public-owner-exempt-method':(ROOT+D+'/// s\npub struct S{private:u64}mod methods{'+A+'impl crate::S{pub fn get(&self)->u64{0}}}','ok'),
 'public-owner-strict-method':(ROOT+'pub struct S{private:u64}mod methods{'+D+'impl crate::S{pub fn get(&self)->u64{0}}}','missing'),
 'tool-on-module':(ROOT+'mod m{#![allow(clippy::upper_case_acronyms)]pub fn f(){}}','ok'),
 'real-find-prefix':(ROOT+'mod m{//! internal tools.\n'+A+'#![allow(clippy::upper_case_acronyms)]pub fn value()->u64{42}}pub fn value()->u64{m::value()}','ok'),
 'reexport-not-inherited':(ROOT+'mod def{'+D+'pub fn x(){}}pub mod sink{'+A+'pub use crate::def::x;}','missing'),
 'field-in-strict':(ROOT+'/// m\npub mod m{'+D+'/// S\npub struct S{pub n:u64}}','missing'),
 'enum-in-exempt':(ROOT+D+'pub mod m{'+A+'pub enum E{A,B(u64)}}','ok'),
}
CASES.update({
 'documented-with-inactive-tool':(ROOT+D+'/// docs\npub mod m{#![allow(clippy::upper_case_acronyms, missing_docs)]pub fn no_docs(){}}','ok'),
 'plain-rust-inactive-tool-scope':(ROOT+'#![forbid(clippy::upper_case_acronyms)]mod m{#![allow(clippy::upper_case_acronyms)]}','ok'),
 'missing-root-not-excused':(D+'pub mod m{'+A+'pub fn f(){}}','missing'),
 'test-lints-the-active-items':(D+'/// docs\npub mod m{'+D+'pub fn f(){}}','missing'),
})

CASES.update({
 'inactive-private-override':(ROOT+F+'#[cfg(any())]mod dead{'+A+'fn f(){}}','ok'),
 'active-private-override':(ROOT+F+'#[cfg(all())]mod live{'+A+'fn f(){}}','E0453'),
 'test-selects-export':(ROOT+D+'#[cfg(test)]pub fn f(){}','missing'),
 'ordinary-omits-test-export':(ROOT+D+'#[cfg(test)]pub fn f(){}','ok'),
 'hidden-ordinary-impl':(ROOT+D+'/// The owner\npub struct S{private:u64}#[doc(hidden)]mod helper{impl crate::S{pub fn f(&self)->u64{0}}}','ok'),
})
CASES.update({
 'review-hidden-strict-impl':('//! crate\n/// S\npub struct S{value:u64}\n#[doc(hidden)] mod internals{#![deny(missing_docs)] impl crate::S{pub fn get(&self)->u64{0}}}','ok'),
 'review-visible-strict-impl':('//! crate\n/// S\npub struct S{value:u64}\nmod internals{#![deny(missing_docs)] impl crate::S{pub fn get(&self)->u64{0}}}','missing'),
})
# Actual 1.91.1 rejects; actual 1.99.1 accepts. Standalone PCC does not
# choose a version from unrelated target cfg. A same-source check can resolve it.
CASES['review-hidden-strict-impl']=(CASES['review-hidden-strict-impl'][0],'needs-check')
CASES.update({
 'hidden-explicit-forbid-reexport': (ROOT+'#[doc(hidden)]mod h{'+F+'pub fn f(){}}pub use h::f;','needs-check'),
 'hidden-with-doc-strict-field': (ROOT+'#[doc(hidden)]mod h{'+D+'/// U\npub struct U{pub x:u64}}pub use h::U;','needs-check'),
 'hidden-resets-forbid': (ROOT+F+'#[doc(hidden)]mod h{'+A+'pub fn f(){}}pub use h::f;','ok'),
 'hidden-then-visible-nested-deny': (ROOT+'#[doc(hidden)]mod h{pub mod sub{'+D+'pub fn f(){}}}pub use h::sub::f;','missing'),
 'hidden-then-visible-nested-allow': (ROOT+F+'#[doc(hidden)]mod h{pub mod sub{'+A+'pub fn f(){}}}pub use h::sub::f;','ok'),
 'hidden-last-inner-overrides-level': (ROOT+'pub mod h{'+D+'#![doc(hidden)]pub fn f(){}}','ok'),
 'hidden-inner-is-not-different-file': (ROOT+'pub mod h{#![doc(hidden)]'+D+'pub fn f(){}}','needs-check'),
 'hidden-preserves-explicit-conflict': (ROOT+'#[doc(hidden)]pub mod h{'+F+A+'pub fn f(){}}','E0453'),
 'hidden-type-owner-not-module': (ROOT+D+'#[doc(hidden)]mod h{/// S\npub struct S{private:u64}}pub use h::S;impl S{pub fn f(&self){}}','missing'),
 'hidden-impl-direct': (ROOT+D+'/// S\npub struct S{private:u64}#[doc(hidden)]impl S{pub fn f(&self){}}','ok'),
 'hidden-impl-already-documented': (ROOT+'/// S\npub struct S{private:u64}#[doc(hidden)]mod h{'+D+'impl crate::S{/// F\npub fn f(&self){}}}','ok'),
})


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_defining_scope_not_the_importing_scope_sets_the_lint(name):
    source, expected = CASES[name]
    cfg = CfgOptions(test=name in ('forbid-in-test', 'test-lints-the-active-items', 'test-selects-export'))
    if expected == 'needs-check':
        with pytest.raises(RustError, match=r'doc\(hidden\) override order requires a same-source rustc check'):
            compile_rust(source, cfg=cfg)
    elif expected in ('missing', 'E0453'):
        with pytest.raises(RustError, match='missing documentation' if expected=='missing' else 'E0453'):
            compile_rust(source, cfg=cfg)
    else:
        result = compile_rust(source, cfg=cfg)
        assert result.emit_object() and not result.externally_checked
        assert result.external_rustc_lints is None


def test_same_inner_module_from_the_old_negative_now_has_real_scoped_metadata():
    source = 'mod m{#![allow(missing_docs)]fn f(){}}'
    parser = Parser(source)
    parser.parse()
    assert not parser.crate_lint_attributes
    attr, = parser.modules[0].body.inner_lints
    assert source[slice(*attr.source_span)] == '#![allow(missing_docs)]'
    assert attr.level == 'allow' and tuple(x.name for x in attr.builtin_lints) == ('missing_docs',)
    assert compile_rust(source).module_lint_attributes[0][0] == ('m',)


def test_outline_nested_module_keeps_original_path_span_and_independent_identity():
    src = {'src/lib.rs': '//! root\n#![deny(missing_docs)]pub mod part; pub mod second;',
           'src/part.rs': '#![allow(missing_docs)]pub fn f(){}',
           'src/second.rs': '//! second\npub mod sub;',
           'src/second/sub.rs': '#![allow(missing_docs)]#![allow(clippy::upper_case_acronyms)]pub fn g(){}'}
    result = compile_rust_crate('src/lib.rs', src, crate_name='nest')
    assert result.emit_object()
    assert [(m,p) for m,p,_ in result.module_lint_attributes] == [
        (('part',), 'src/part.rs'), (('second','sub'), 'src/second/sub.rs')]
    for _,path,attributes in result.module_lint_attributes:
        assert [src[path][slice(*a.source_span)] for a in attributes] == (
            ['#![allow(missing_docs)]'] if path.endswith('part.rs') else
            ['#![allow(missing_docs)]', '#![allow(clippy::upper_case_acronyms)]'])
    with pytest.raises(RustError, match='missing documentation'):
        compile_rust_crate('src/lib.rs', {**src, 'src/part.rs': 'pub fn f(){}'},crate_name='nest')


@pytest.mark.parametrize('multiple', [False, True])
def test_target_only_facts_do_not_fabricate_a_test_harness(multiple):
    sources={'src/lib.rs':'//! crate\npub mod m;', 'src/m.rs':'#![deny(missing_docs)]pub fn f(){}'}
    opts=TargetPlatformFacts()
    with pytest.raises(RustError,match='missing documentation'):
        if multiple:
            compile_rust_crate('src/lib.rs',sources,crate_name='supplied',cfg=opts)
        else:
            compile_rust('//! crate\npub mod m{#![deny(missing_docs)]pub fn f(){}}',cfg=opts)


@pytest.mark.parametrize('attr,diagnostic', [
    ('allow(no_such_group)', 'only missing_docs'),
    ('allow(other_tool::x)', 'unknown tool'),
    ('allow(missing_docs,reason="not owned")', 'only missing_docs'),
    ('feature(lang_items)', 'semantic inner module attributes'),
])
def test_other_rules_are_not_silently_treated_as_documentation_levels(attr,diagnostic):
    with pytest.raises(RustError,match=diagnostic):
        compile_rust('mod a{#!['+attr+']pub fn f(){}}')


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
def test_owned_execution_after_the_two_real_find_tools_lints(tmp_path, monkeypatch):
    source=CASES['real-find-prefix'][0]
    assert run_owned_u64_function(source,'value',tmp_path,monkeypatch,target_triple=HOST_TARGET)==42
