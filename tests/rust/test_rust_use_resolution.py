"""Private single-path use obligations; all new execution is CI-only and UNRUN.

Rust Reference items.use (including all namespaces and 2015 path roots),
names.preludes.extern and visibility-and-privacy. Imported names retain their
actual supplied declaration identity. No use item fabricates core/std sources,
registers a lang item, bypasses rustc checking or qualifies a real project.
https://doc.rust-lang.org/reference/items/use-declarations.html
https://doc.rust-lang.org/reference/names/preludes.html
"""
from pathlib import Path
import hashlib
import platform
import shutil
import subprocess
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.crate_context import parse_crate_sources
from pcc.frontends.rust.generics import PRIMITIVES
from pcc.frontends.rust.module_resolution import (
    ModuleNamespaces, ModuleResolution, analyze_crate_syntax,
)
from pcc.frontends.rust.syntax import CallableSignature, Parser
from pcc.frontends.rust.types import SharedReference
from tests.rust.certification_support import actual_check_prerequisite, certify_source


PROGRAMS = ('use_function_alias', 'use_module_alias', 'use_type_alias',
            'use_forward_alias', 'use_parent_scope', 'use_crate_self_alias',
            'use_value_primitive_alias', 'use_grouped_tree', 'use_public_reexport',
            'use_reference_alias')
PROJECT = 'projects/fallible-streaming-iterator-0.1.9/src/lib.rs'
PROJECT_SHA256 = '7fb7428997bdd89c3ee0818ecee642f8d93882c9c66171c9c6194b732f337089'


def source(name):
    return Path(__file__).with_name('programs').joinpath(name + '.rs').read_text()


def graph(text, edition=2021):
    return parse_crate_sources('src/lib.rs', {'src/lib.rs': text},
                               crate_name='use_test', edition=edition)


def compile_source(text, **options):
    return compile_rust(text, experimental_rustc_checked=True, **options)


def parse_retaining_associated_types(parser, monkeypatch, gap=None):
    """Parse with the real parser, retaining every associated type it returns.

    ``gap`` is ``(line, column, message, token)`` for the next unsupported
    parser boundary; without it the whole source must parse. ``token`` is the
    current token's text at that location, or ``(text, line, column)`` when
    the boundary is reported at an earlier token of the same item.
    """
    associated_types = []
    parse_associated_type = parser.associated_type

    def retain_associated_type(**options):
        item = parse_associated_type(**options)
        associated_types.append(item)
        return item

    monkeypatch.setattr(parser, 'associated_type', retain_associated_type)
    if gap is None:
        parser.parse()
    else:
        line, column, message, token = gap
        with pytest.raises(RustError, match=rf'^{line}:{column}: {message}$'):
            parser.parse()
        expected = token if isinstance(token, tuple) else (token, line, column)
        assert (parser.token.text, parser.token.line, parser.token.column) == expected
    return [(item.token.text, item.token.line, item.token.column, item.value,
             [(bound.path, bound.relaxed) for bound in item.bounds])
            for item in associated_types]


def test_use_syntax_retains_tokens_docs_and_original_codepoint_span():
    text = 'mod target{pub fn value(){}}\n/// original import\nuse self::target::value as read;'
    parser = Parser(text, source_path='src/lib.rs')
    parser.parse()
    item, = parser.uses
    assert tuple(token.text for token in item.path) == ('self', 'target', 'value')
    assert item.binding is item.alias and item.alias.text == 'read'
    assert item.documentation[0].text == ' original import'
    assert (item.token.line, item.token.column, item.token.source_path) == (3, 1, 'src/lib.rs')
    assert text[slice(*item.source_span)] == 'use self::target::value as read;'
    assert parser.modules[0].body.uses == []


def test_grouped_use_tree_declares_one_simple_use_per_leaf():
    # Former gap row ('group', 'use a::{value};') is now this positive form.
    text = ('mod a{pub fn value(){} pub mod b{pub fn v(){} pub fn w(){}}}\n'
            '/// grouped\nuse self::a::{value, b::{v as read, w,},};\nuse {a::b::v};')
    parser = Parser(text, source_path='src/lib.rs')
    parser.parse()
    assert [('::'.join(token.text for token in item.path), item.binding.text)
            for item in parser.uses] == [
        ('self::a::value', 'value'), ('self::a::b::v', 'read'),
        ('self::a::b::w', 'w'), ('a::b::v', 'v')]
    last = parser.uses[-1]
    assert {(item.token.line, item.token.column) for item in parser.uses[:3]} == {(3, 1)}
    assert all(item.documentation[0].text == ' grouped' for item in parser.uses[:3])
    assert {text[slice(*item.source_span)] for item in parser.uses[:3]} == {
        'use self::a::{value, b::{v as read, w,},};'}
    assert text[slice(*last.source_span)] == 'use {a::b::v};'


def test_public_use_marks_every_leaf_as_a_reexport():
    # Former gap row ('reexport', 'pub use a::value;') is now this positive form.
    text = 'mod a{pub fn value(){} pub fn other(){}} pub use a::{value, other as o}; use a::value as v;'
    parser = Parser(text)
    parser.parse()
    assert [(item.binding.text, item.public) for item in parser.uses] == [
        ('value', True), ('o', True), ('v', False)]
    resolver = ModuleNamespaces(graph('mod a{mod inner{pub fn v(){}} pub use self::inner::v;}'))
    owner = next(module for module in resolver.modules.values() if module.identity.components == ('a',))
    declaration = resolver.resolve('a::v', 'value', resolver.root, owner.body.uses[0].token)
    assert declaration.identity.module.components == ('a', 'inner')


def test_use_tree_nesting_has_an_explicit_bound():
    text = 'mod a{pub fn value(){}} use ' + 'a::{' * 33 + 'value' + '}' * 33 + ';'
    with pytest.raises(RustError, match='use tree nesting exceeds implementation limit'):
        Parser(text).parse()


def test_use_parser_advances_associated_types_but_keeps_fn_bounds_explicit(monkeypatch):
    # Independent parser controls, not a rewritten or certified upstream crate.
    prefix = ('mod owner{pub fn value(){}}\nuse owner::value;\n'
              'trait Iter{type Item:?Sized;type Error;')
    parser = Parser(prefix + '}')
    parser.parse()
    trait, = parser.traits
    assert trait.token.text == 'Iter'
    assert [(item.token.text, item.value,
             [(bound.path, bound.relaxed) for bound in item.bounds])
            for item in trait.associated_types] == [
        ('Item', None, [('Sized', True)]), ('Error', None, []),
    ]
    assert ['::'.join(token.text for token in item.path) for item in parser.uses] == [
        'owner::value']
    text = prefix + '\nfn all<F>(&mut self,f:F) where F:FnMut(&Self::Item)->bool;}'
    parser = Parser(text)
    assert parse_retaining_associated_types(parser, monkeypatch) == [
        ('Item', 3, 17, None, [('Sized', True)]),
        ('Error', 3, 34, None, []),
    ]
    # The parser retains the Fn-family signature; its closure-trait meaning
    # remains an explicit semantic gap (tests/rust/test_rust_closure_syntax.py).
    predicate, = parser.traits[0].methods[0].where_predicates
    bound, = predicate.bounds
    assert (predicate.parameter.text, bound.path, bound.token.line, bound.token.column) == ('F', 'FnMut', 4, 34)
    assert bound.callable == CallableSignature((SharedReference('Self::Item', False, None),), 'bool')


def test_single_use_binds_both_modeled_namespaces_to_the_canonical_owner():
    text = ('mod provider{pub struct Name{pub value:u64} pub fn Name()->u64{42}}'
            'use crate::provider::Name as Imported;')
    crate = graph(text)
    resolver = ModuleNamespaces(crate)
    token = crate.root.body.uses[0].token
    for namespace in ('type', 'value'):
        target = resolver.resolve('crate::provider::Name', namespace, resolver.root, token)
        alias = resolver.resolve('Imported', namespace, resolver.root, token)
        assert alias is target
        assert alias.identity.module.components == ('provider',)
        assert alias.identity.name == 'Name'
    assert ('type', 'Imported') not in crate.root.items
    assert ('value', 'Imported') not in crate.root.items


def test_imported_module_uses_target_identity_instead_of_inventing_an_alias_child():
    crate = graph('mod original{pub fn value(){}} use crate::original as alias;')
    resolver = ModuleNamespaces(crate)
    token = crate.root.body.uses[0].token
    assert resolver.resolve('alias::value', 'value', resolver.root, token) is resolver.resolve(
        'crate::original::value', 'value', resolver.root, token)


def test_outlined_use_resolution_preserves_logical_files_and_declaration_scope():
    sources = {'src/lib.rs': 'mod provider; mod reader; fn answer()->u64{reader::read()}',
               'src/provider.rs': 'pub fn value()->u64{42}',
               'src/reader.rs': 'use crate::provider::value as obtain; pub fn read()->u64{obtain()}'}
    crate = parse_crate_sources('src/lib.rs', sources, crate_name='outlined', edition=2021)
    checker = analyze_crate_syntax(crate)
    resolver = checker.module_resolution
    reader = next(module for module in resolver.modules.values() if module.identity.components == ('reader',))
    item, = reader.body.uses
    target = resolver.resolve('obtain', 'value', reader.identity, item.token)
    assert target.identity.module.components == ('provider',)
    assert reader.source.text == sources['src/reader.rs']
    assert item.token.source_path == 'src/reader.rs'


EDITION_ORIGINAL = ('mod values{pub fn read()->u64{42}}'
    'mod inner{mod values{pub fn read()->u64{7}} use values::read; pub fn result()->u64{read()}}')


@pytest.mark.parametrize('edition,owner', [(2015, ('values',)), (2018, ('inner', 'values')),
                                         (2021, ('inner', 'values')), (2024, ('inner', 'values'))])
def test_use_roots_follow_the_explicit_edition_without_changing_privacy_requester(edition, owner):
    crate = graph(EDITION_ORIGINAL, edition)
    resolver = ModuleNamespaces(crate)
    inner = next(module for module in resolver.modules.values() if module.identity.components == ('inner',))
    token = inner.body.uses[0].token
    assert resolver.resolve('read', 'value', inner.identity, token).identity.module.components == owner


INVALID = (
    # Exact independent-review counterexample. Rust's imported Number type
    # shadows the language-prelude u64; it is not the primitive return type.
    ('primitive-shadow-return', 'mod provider{pub struct Number{pub x:u64}} use provider::Number as u64; fn answer()->u64{42}', 'primitive-shadowing type use', 'E0308'),
    ('record-shadows-Copy', 'mod provider{pub struct Number{pub x:u64}} use provider::Number as Copy; fn id<T:Copy>(value:T)->T{value}', 'local/user trait bounds', 'E0404'),
    ('record-shadows-Sized', 'mod provider{pub struct Number{pub x:u64}} use provider::Number as Sized; fn id<T:Sized>(value:T)->T{value}', 'local/user trait bounds', 'E0404'),
    ('module-shadows-core-marker', 'mod provider{pub mod marker{pub struct Copy{pub x:u64}}} use provider as core; fn id<T:core::marker::Copy>(value:T)->T{value}', 'local/user trait bounds', 'E0404'),
    ('unresolved-import', 'mod a{} use a::missing; fn answer()->u64{42}', 'unknown use target', 'E0432'),
    ('unused-private-target', 'mod a{fn hidden(){}} use a::hidden; fn answer()->u64{42}', 'private item', 'E0603'),
    ('private-alias', 'mod a{pub fn value(){}} mod b{use crate::a::value as hidden;} fn answer(){b::hidden();}', 'private use binding', 'E0603'),
    ('binding-conflict', 'mod a{pub fn value(){}} use a::value; fn value(){}', 'duplicate imported value-namespace', 'E0255'),
    ('cycle', 'use self::b as a; use self::a as b;', 'cyclic use bindings', 'E0432'),
    # Every leaf of a grouped use tree is resolved and checked like a simple use.
    ('group-unresolved', 'mod a{} use a::{missing}; fn answer()->u64{42}', 'unknown use target', 'E0432'),
    ('group-private', 'mod a{fn hidden(){}} use a::{hidden}; fn answer()->u64{42}', 'private item', 'E0603'),
    # A `pub use` cannot widen the re-exported binding's visibility, even unused.
    ('reexport-private-item', 'mod a{fn hidden(){} pub use self::hidden as h;} fn answer()->u64{42}',
     'is private, and cannot be re-exported', 'E0364'),
    ('reexport-private-import', 'mod a{pub fn v(){}} mod b{use crate::a::v; pub use self::v as w;} fn answer()->u64{42}',
     'is private, and cannot be re-exported', 'E0364'),
    ('reexport-private-module', 'mod a{mod inner{} pub use self::inner as i;} fn answer()->u64{42}',
     'is private, and cannot be re-exported', 'E0365'),
    ('reexport-crate-private', 'fn top()->u64{1} mod a{pub use crate::top;} fn answer()->u64{42}',
     'is private, and cannot be re-exported', 'E0364'),
    # The re-export does not make its private source path nameable.
    ('reexport-source-path-private', 'mod a{mod inner{pub fn v()->u64{42}} pub use self::inner::v;} pub fn answer()->u64{a::inner::v()}',
     'private item', 'E0603'),
)


@pytest.mark.parametrize('name,text,message,code', INVALID)
def test_invalid_imports_are_checked_even_when_unused_and_never_enter_ir(name, text, message, code, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid import reached IR'))
    with pytest.raises(RustError, match=message):
        compile_source(text)


# The exact former E0573 negative is accepted by pinned rustc 1.91.1.
# Module names fall back to primitives in bare type positions; nominal types do
# not. See rustc_resolve::late::resolve_qpath_anywhere's primitive fallback:
# https://doc.rust-lang.org/nightly/nightly-rustc/src/rustc_resolve/late.rs.html
PRIMITIVE_MODULE_IMPORTS = (
    ('primitive-module-return', 'mod provider{pub fn value()->u64{42}} use provider as u64; fn answer()->u64{42}'),
    ('module', 'mod provider{pub fn value()->u64{42}} use provider as u64; fn answer(){u64::value();}'),
    ('crate-self-alias', 'extern crate self as original; use original as u64; fn answer(){}'),
    ('crate-self-member', 'extern crate self as original; use original as u64; fn value()->u64{42} fn answer()->u64{u64::value()}'),
)


PRIMITIVE_TYPE_IMPORTS = (
    ('well-typed-record', 'mod provider{pub struct Number{pub x:u64}} use provider::Number as u64; fn answer()->u64{u64{x:42}}'),
    ('enum', 'mod provider{pub enum Number{Value}} use provider::Number as u64; fn answer()->u64{u64::Value}'),
    ('both-namespaces', 'mod provider{pub struct Number{pub x:u64} pub fn Number()->u64{42}} use provider::Number as u64; fn answer(){u64();}'),
    ('forward-chain', 'use self::Intermediate as u64; use provider::Number as Intermediate; mod provider{pub struct Number{pub x:u64}} fn answer(){}'),
)
PRIMITIVE_TARGET_IMPORTS = (
    ('bare-language-type', 'use u64 as Word; fn answer()->Word{42}'),
    ('both-prelude-and-value', 'fn u64()->u64{42} use u64 as i64; fn answer()->bool{let value:i64=!0;value>0}'),
    ('unlowered-primitive-target', 'fn i128()->u64{42} use i128 as u64; fn answer()->u64{42}'),
)


@pytest.mark.parametrize('name,text', PRIMITIVE_TYPE_IMPORTS)
def test_every_primitive_shadowing_type_target_stays_an_explicit_gap(name, text, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('primitive-shadowing import reached IR'))
    with pytest.raises(RustError, match='primitive-shadowing type use'):
        compile_source(text)


@pytest.mark.parametrize('name,text', PRIMITIVE_TARGET_IMPORTS)
def test_bare_primitive_targets_never_drop_the_language_prelude_namespace(name, text, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unresolved language-prelude import reached IR'))
    with pytest.raises(RustError, match='unqualified primitive use targets'):
        compile_source(text)


@pytest.mark.parametrize('name', sorted(PRIMITIVES - {'()'}))
def test_primitive_guard_checks_type_bindings_but_allows_value_only_names(name, monkeypatch):
    record = 'mod provider{pub struct Number{pub x:u64}} use provider::Number as ' + name + ';'
    with pytest.raises(RustError, match='primitive-shadowing type use'):
        ModuleNamespaces(graph(record))
    value = ('mod provider{pub fn value()->u64{42}} use provider::value as ' + name +
             '; pub fn answer()->u64{' + name + '()}')
    resolver = ModuleNamespaces(graph(value))
    alias = resolver.resolve(name, 'value', resolver.root, resolver.crate.root.body.uses[0].token)
    assert alias.identity.name == 'value' and alias.identity.module.components == ('provider',)
    with monkeypatch.context() as denied:
        denied.setattr(subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('owned compile invoked tool'))
        assert compile_source(value).emit_object().startswith(b'\x7fELF')


@pytest.mark.parametrize('name', ('Copy', 'Sized', 'core'))
def test_marker_shadow_query_counts_imported_types_but_not_value_only_names(name):
    types = ModuleNamespaces(graph('mod provider{pub struct Number{pub x:u64}} use provider::Number as ' + name + ';'))
    values = ModuleNamespaces(graph('mod provider{pub fn value(){}} use provider::value as ' + name + ';'))
    assert types.has_local_type_binding(name, types.root)
    assert not values.has_local_type_binding(name, values.root)


GAPS = (
    ('glob', 'mod a{pub fn value(){}} use a::*;', 'glob use declarations'),
    ('group-glob', 'mod a{pub fn value(){}} use a::{*};', 'glob use declarations'),
    ('group-self', 'mod a{pub fn value(){}} use a::{self};', 'nonleading self/crate/super'),
    ('empty-group', 'mod a{pub fn value(){}} use a::{};', 'empty use groups'),
    ('anonymous', 'mod a{pub fn value(){}} use a::value as _;', 'anonymous use'),
    ('absolute', 'mod a{pub fn value(){}} use ::a::value;', 'absolute use paths'),
    ('trailing-self', 'mod a{} use a::self as alias;', 'nonleading self/crate/super'),
    ('duplicate-spelling', 'mod a{pub fn value(){}} use a::value as first; use a::value as first;', 'namespace-merge resolution'),
    ('no-owned-core', '#![no_std] use core::cmp;', 'implicit library/prelude source dependency is unavailable'),
    ('no-owned-phantom', '#![no_std] use core::marker::PhantomData;', 'implicit library/prelude source dependency is unavailable'),
    ('block-use', 'fn value(){} fn answer(){use self::value;}', 'block-scoped use'),
)


@pytest.mark.parametrize('name,text,message', GAPS)
def test_unowned_use_forms_and_library_dependencies_remain_explicit(name, text, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unowned import reached IR'))
    with pytest.raises(RustError, match=message):
        compile_source(text)


@pytest.mark.parametrize('name', PROGRAMS[:-1])
def test_value_use_originals_have_owned_source_ir_obligations(name, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('owned compile invoked tool'))
    assert compile_source(source(name)).emit_object().startswith(b'\x7fELF')


def test_imports_never_turn_the_boolean_into_reference_certification(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('unchecked import reference reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_source(source('use_reference_alias'))


def test_long_forward_import_chain_has_an_explicit_bound_before_ir(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('oversized use chain reached IR'))
    text = ''.join('use self::a' + str(index + 1) + ' as a' + str(index) + ';'
                   for index in range(129)) + 'fn a129(){}'
    with pytest.raises(RustError, match='use binding dependency chain exceeds implementation limit'):
        compile_source(text)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name', PROGRAMS)
def test_actual_same_input_check_precedes_owned_import_lowering(name, tmp_path, monkeypatch):
    original = source(name)
    checked = certify_source(original, tmp_path / 'actual-check')
    with monkeypatch.context() as denied:
        denied.setattr(subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('owned compile invoked tool'))
        result = compile_source(original, rustc_check=checked)
        assert result.emit_object().startswith(b'\x7fELF') and result.externally_checked
    assert (tmp_path / 'actual-check/original.rs').read_bytes() == original.encode('utf-8')
    assert checked.provenance.source_sha256 == hashlib.sha256(original.encode()).hexdigest()


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text', PRIMITIVE_TYPE_IMPORTS + PRIMITIVE_TARGET_IMPORTS)
def test_actual_valid_primitive_type_shadows_retain_the_owned_scope_gap(name, text, tmp_path, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    checked = certify_source(text, tmp_path / 'actual-check')
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('primitive-shadowing type import reached IR'))
    with monkeypatch.context() as denied:
        denied.setattr(subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('owned compile invoked tool'))
        with pytest.raises(RustError, match='primitive-shadowing type use|unqualified primitive use targets'):
            compile_source(text, rustc_check=checked)
    assert checked.provenance.source_sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert (tmp_path / 'actual-check/original.rs').read_bytes() == text.encode('utf-8')


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('alias,bound', [('Copy', 'Copy'), ('Sized', 'Sized'), ('core', 'core::marker::Copy')])
def test_actual_value_alias_keeps_the_canonical_marker_type_namespace(alias, bound, tmp_path, monkeypatch):
    text = ('mod provider{pub fn value()->u64{42}} use provider::value as ' + alias +
            '; fn id<T:' + bound + '>(value:T)->T{value} pub fn answer()->u64{id(' + alias + '())}')
    checked = certify_source(text, tmp_path / 'actual-check')
    with monkeypatch.context() as denied:
        denied.setattr(subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('owned compile invoked tool'))
        result = compile_source(text, rustc_check=checked)
        assert result.externally_checked and result.standard_marker_contract is not None
        assert result.emit_object().startswith(b'\x7fELF')
    assert (tmp_path / 'actual-check/original.rs').read_bytes() == text.encode('utf-8')


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('edition', (2015, 2018, 2021, 2024))
def test_actual_same_input_edition_check_is_not_replaced_by_a_resolution_guess(edition, tmp_path, monkeypatch):
    from pcc.frontends.rust import check_rust_source
    from tests.rust.test_rust_certified_references import toolchain
    checked = check_rust_source(EDITION_ORIGINAL, toolchain=toolchain(),
        evidence_directory=tmp_path / 'actual-check', edition=edition)
    with monkeypatch.context() as denied:
        denied.setattr(subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('owned compile invoked tool'))
        result = compile_source(EDITION_ORIGINAL, rustc_check=checked, edition=edition)
        assert result.emit_object().startswith(b'\x7fELF') and result.externally_checked
    assert (tmp_path / 'actual-check/original.rs').read_bytes() == EDITION_ORIGINAL.encode('utf-8')
    assert checked.provenance.edition == str(edition)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_original_project_keeps_both_use_items_before_the_next_parser_gap(tmp_path, monkeypatch):
    from pcc.frontends.rust import check_rust_source
    from tests.rust.test_rust_certified_references import toolchain
    original = Path(__file__).resolve().parents[2].joinpath(PROJECT).read_bytes()
    assert hashlib.sha256(original).hexdigest() == PROJECT_SHA256
    checked = check_rust_source(original.decode(), toolchain=toolchain(),
        evidence_directory=tmp_path / 'actual-check', edition=2015)
    parser = Parser(original.decode(), edition=2015, rustc_check=checked)
    # Keep the whole authenticated source. Observe the real parser's next
    # boundary without accepting a rewritten prefix or a discarded declaration:
    # every earlier Fn-family bound, `Some(e)` pattern, closure, integer
    # range loop and the impl lifetime parameter is parsed.
    assert parse_retaining_associated_types(parser, monkeypatch, (
        306, 1, 'generic traits and blanket/generic trait impls are not implemented', ('&', 306, 51))) == [
        ('Item', 28, 10, None, [('Sized', True)]),
        ('Error', 31, 10, None, []),
    ]
    assert ['::'.join(token.text for token in item.path) for item in parser.uses] == [
        'core::cmp', 'core::marker::PhantomData']
    assert [original.decode()[slice(*item.source_span)] for item in parser.uses] == [
        'use core::cmp;', 'use core::marker::PhantomData;']
    assert checked.provenance.source_sha256 == PROJECT_SHA256
    assert (tmp_path / 'actual-check/original.rs').read_bytes() == original
    # This bounded parser observation is not the independent strict project
    # gate, an owned core dependency or permission to lower unresolved imports.


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite() or (
    None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'}
    and shutil.which('rustc') and shutil.which('cc') else 'requires Linux native rustc/system-linker reference'))
def test_unchanged_use_originals_match_actual_native_rustc(tmp_path, monkeypatch):
    from tests.rust import test_rust_method_reference as reference
    monkeypatch.setattr(reference, 'PROGRAMS', PROGRAMS)
    reference.test_unchanged_inherent_programs_match_actual_rustc_native_execution(tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
def test_actual_rustc_rejects_the_exact_invalid_use_originals(tmp_path, monkeypatch):
    from pcc.frontends.rust import RustExternalCheckError, compile_rust_checked
    from pcc.frontends.rust.lowering import Lowerer
    from tests.rust.test_rust_certified_references import toolchain
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('rustc-invalid import reached IR'))
    for name, text, message, code in INVALID:
        with pytest.raises(RustExternalCheckError, match=code):
            compile_rust_checked(text, toolchain=toolchain(), evidence_directory=tmp_path / name)
        assert (tmp_path / name / 'original.rs').read_bytes() == text.encode('utf-8')


@pytest.mark.parametrize('name,text', PRIMITIVE_MODULE_IMPORTS)
def test_primitive_module_alias_originals_emit_owned_objects(name, text, monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('owned compile invoked tool'))
    assert compile_source(text).emit_object().startswith(b'\x7fELF')


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('name,text', PRIMITIVE_MODULE_IMPORTS)
def test_actual_valid_primitive_module_originals_certify_then_emit_owned(name, text, tmp_path, monkeypatch):
    checked = certify_source(text, tmp_path / 'actual-check')
    with monkeypatch.context() as denied:
        denied.setattr(subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('owned compile invoked tool'))
        result = compile_source(text, rustc_check=checked)
        assert result.externally_checked and result.emit_object().startswith(b'\x7fELF')
    assert (tmp_path / 'actual-check/original.rs').read_bytes() == text.encode('utf-8')
    assert checked.provenance.source_sha256 == hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize('name', sorted(PRIMITIVES - {'()'}))
def test_primitive_module_members_precede_associated_fallback(name):
    text = ('mod provider{pub fn member()->u64{42}} use provider as ' + name +
            '; fn answer(){}')
    resolver = ModuleResolution(graph(text))
    token = resolver.crate.root.body.uses[0].token
    assert resolver.associated_function(name, 'member', resolver.root, token).token.text == 'member'
    assert resolver.associated_function(name, 'max_value', resolver.root, token) is None
    # Returning None defers to the existing primitive method owner; it is not a
    # claim that primitive associated functions have been implemented.
    absent = ModuleResolution(graph('fn answer(){}'))
    assert absent.associated_function(name, 'max_value', absent.root, token) is None


@pytest.mark.parametrize('text,message', [
    ('mod provider{fn hidden()->u64{42}} use provider as u64; fn answer()->u64{u64::hidden()}', 'private item'),
    ('mod provider{} mod u64{} use provider as u64; fn answer(){}', 'duplicate imported type-namespace'),
])
def test_primitive_module_fallback_preserves_privacy_and_conflicts(text, message, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('invalid module lookup reached IR'))
    with pytest.raises(RustError, match=message):
        compile_source(text)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=actual_check_prerequisite())
@pytest.mark.parametrize('text,code', [
    ('mod provider{fn hidden()->u64{42}} use provider as u64; fn answer()->u64{u64::hidden()}', 'E0603'),
    ('mod provider{} mod u64{} use provider as u64; fn answer(){}', 'E0255'),
])
def test_actual_primitive_module_privacy_and_conflicts_reject_originals(text, code, tmp_path, monkeypatch):
    from pcc.frontends.rust import RustExternalCheckError, compile_rust_checked
    from pcc.frontends.rust.lowering import Lowerer
    from tests.rust.test_rust_certified_references import toolchain
    monkeypatch.setattr(Lowerer, 'lower', lambda *args: pytest.fail('rustc-invalid module reached IR'))
    directory = tmp_path / 'actual-check'
    with pytest.raises(RustExternalCheckError, match=code):
        compile_rust_checked(text, toolchain=toolchain(), evidence_directory=directory)
    assert (directory / 'original.rs').read_bytes() == text.encode('utf-8')
