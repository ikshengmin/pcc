"""Restricted visibility: `pub(crate)`, `pub(self)`, `pub(super)`, `pub(in path)`.

https://doc.rust-lang.org/reference/visibility-and-privacy.html#pubin-path-pubcrate-pubsuper-and-pubself
A restriction names the module whose subtree may see an item, field or use
binding; it must be the owner or an ancestor, and re-exports cannot widen it.
Every accepted/rejected source below was checked against rustc 1.92.0
(edition 2021 unless noted); the rustc diagnostic is recorded beside it.
"""
import platform
import shutil
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser
from pcc.frontends.rust.visibility import VisibilityRestriction
from tests.rust.test_rust_frontend import test_development_only_rustc_checks_the_same_source as _rustc
from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution as _execute
from tests.rust.test_rust_targets import test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite as _cross


def compile_source(source, **options):
    return compile_rust(source, experimental_rustc_checked=True, **options)


ACCEPTED = (
    'mod a{pub(crate) fn f()->u64{1}} pub fn answer()->u64{a::f()}',
    'mod a{pub mod b{pub(super) fn f()->u64{1}} pub fn g()->u64{b::f()}} pub fn answer()->u64{a::g()}',
    'mod a{pub mod b{pub(in crate::a) fn f()->u64{1}} pub fn g()->u64{b::f()}} pub fn answer()->u64{a::g()}',
    'pub(crate) fn f()->u64{1} pub fn answer()->u64{f()}',
    'mod a{pub(crate) fn f()->u64{1}} pub(crate) use a::f; pub fn answer()->u64{f()}',
    'mod a{pub struct P{pub(crate) x:u64, pub(super) y:u64} pub fn mk()->P{P{x:1,y:2}}} pub fn answer()->u64{let p=a::mk(); p.x+p.y}',
    'mod a{pub(crate) mod b{pub fn f()->u64{1}}} pub fn answer()->u64{a::b::f()}',
    'mod a{pub(crate) use self::inner::f; mod inner{pub fn f()->u64{1}}} pub fn answer()->u64{a::f()}',
)


@pytest.mark.parametrize('source', ACCEPTED)
def test_restricted_items_fields_and_uses_resolve_inside_their_scope(source):
    assert 'define' in compile_source(source).ir_text


REJECTED = (
    # E0603: function `f` is private
    ('mod a{pub mod b{pub(super) fn f()->u64{1}}} pub fn answer()->u64{a::b::f()}', "private item 'f'"),
    ('mod a{pub mod b{pub(in crate::a) fn f()->u64{1}}} pub fn answer()->u64{a::b::f()}', "private item 'f'"),
    ('mod a{pub(self) fn f()->u64{1}} pub fn answer()->u64{a::f()}', "private item 'f'"),
    # E0433 (c is not an ancestor of a::b; rustc fails to resolve it there)
    ('mod a{pub mod b{pub(in crate::c) fn f()->u64{1}}} mod c{} pub fn answer()->u64{42}',
     r'restricted to ancestor modules \(pub\(in crate::c\)\)'),
    # error: relative paths are not supported in visibilities in 2018 edition or later
    ('mod a{pub(in a) fn f()->u64{1}} pub fn answer()->u64{42}', 'relative paths are not supported'),
    # E0433: there are too many leading `super` keywords
    ('pub(super) fn f()->u64{1} pub fn answer()->u64{42}', 'too many leading super'),
    # E0364: `f` is only public within the crate, and cannot be re-exported outside
    ('mod a{pub(crate) fn f()->u64{1}} pub use a::f; pub fn answer()->u64{42}',
     'only public within the crate, and cannot be re-exported outside'),
    # E0364: `f` is private, and cannot be re-exported
    ('mod a{pub mod b{pub(super) fn f()->u64{1}} pub(crate) use self::b::f;} pub fn answer()->u64{42}',
     'is private, and cannot be re-exported'),
    # E0616: field `y` of struct `P` is private
    ('mod a{pub mod b{pub struct P{pub(super) y:u64} pub fn mk()->P{P{y:2}}}} pub fn answer()->u64{a::b::mk().y}',
     "private field 'y'"),
)


@pytest.mark.parametrize('source,message', REJECTED)
def test_restricted_visibility_violations_are_rejected_like_rustc(source, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid visibility reached IR'))
    with pytest.raises(RustError, match=message):
        compile_source(source)


def test_edition_2015_restriction_paths_start_at_the_crate_root():
    source = 'mod a{pub mod b{pub(in a) fn f()->u64{1}} pub fn g()->u64{b::f()}} pub fn answer()->u64{a::g()}'
    assert 'define' in compile_source(source, edition=2015).ir_text
    with pytest.raises(RustError, match='relative paths are not supported'):
        compile_source(source, edition=2018)


def test_parser_retains_restriction_tokens_and_leaves_plain_pub_alone():
    parser = Parser('pub(crate) fn a(){} pub(in self) fn b(){} pub(super) fn c(){} pub fn d(){} fn e(){}')
    with pytest.raises(RustError, match='too many leading super'):
        parser.parse()
    restrictions = parser.visibility_restrictions
    assert [r.spelling for r in restrictions] == ['pub(crate)', 'pub(in self)', 'pub(super)']
    assert all(isinstance(r, VisibilityRestriction) for r in restrictions)
    assert (restrictions[0].token.text, restrictions[0].token.column) == ('pub', 1)
    parser = Parser('mod m{pub(crate) fn a(){} pub fn d(){} fn e(){}}')
    parser.parse()
    functions = parser.modules[0].body.functions
    assert [type(f.public).__name__ for f in functions] == ['VisibilityRestriction', 'bool', 'bool']
    assert [f.public for f in functions[1:]] == [True, False]


PROGRAMS = ['visibility_restricted']


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith("linux") and platform.machine() in {"x86_64", "AMD64"} else "requires Linux x86-64 owned execution")
@pytest.mark.parametrize('name', PROGRAMS)
def test_restricted_visibility_fixture_owned_execution(name, tmp_path, monkeypatch):
    _execute(name, tmp_path, monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.parametrize('name', PROGRAMS)
def test_restricted_visibility_fixture_cross_emits_darwin(name, monkeypatch):
    _cross(name, monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
@pytest.mark.parametrize('name', PROGRAMS)
def test_development_only_rustc_checks_restricted_visibility_fixture(name, tmp_path):
    _rustc(name, tmp_path)
