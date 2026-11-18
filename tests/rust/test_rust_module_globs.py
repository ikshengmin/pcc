"""Closed-source module globs, not a Cargo/core/std or macro namespace provider.

Rust Reference: https://doc.rust-lang.org/reference/items/use-declarations.html#glob-imports
and https://doc.rust-lang.org/reference/visibility-and-privacy.html
"""
import shutil

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.crate_context import parse_crate_sources
from pcc.frontends.rust.glob_imports import ModuleGlobImports
from pcc.frontends.rust.module_resolution import ModuleNamespaces
from pcc.frontends.rust.syntax import Parser
from tests.rust.str_execution import UNAVAILABLE, run_owned_u64_function, run_rustc_str_function


def scopes(source, edition=2021, files=None):
    return ModuleNamespaces(parse_crate_sources(
        'src/lib.rs', {'src/lib.rs': source, **(files or {})}, crate_name='module_globs', edition=edition))


def lookup(r, path, ns='value', requester=None):
    return r.resolve(path, ns, requester or r.root, r.crate.root.body.modules[0].token,
                     _optional_final=True)


def test_ast_retains_whole_use_item_and_each_star_from_unmodified_group():
    source = 'mod a{pub mod b{}}\n/// unchanged group\npub use a::{b::{*}, *};'
    p = Parser(source, source_path='src/lib.rs')
    p.parse()
    assert [tuple(t.text for t in i.path) for i in p.uses] == [('a', 'b'), ('a',)]
    for i in p.uses:
        assert (i.binding, i.glob.text, i.glob.source_path) == (None, '*', 'src/lib.rs')
        assert i.alias is None and i.group_self is None and i.public is True
        assert source[slice(*i.source_span)] == 'use a::{b::{*}, *};'
        assert i.documentation[0].text == ' unchanged group'
        assert source.splitlines()[i.glob.line - 1][i.glob.column - 1] == '*'
    assert p.uses[0].glob.column < p.uses[1].glob.column


P = 'mod a{pub fn x()->u64{17}}mod b{pub fn x()->u64{25}}'
G = 'pub use crate::a::*;pub use crate::b::*;'


@pytest.mark.parametrize('body', [G, 'pub use crate::b::*;pub use crate::a::*;',
                                  'use crate::a::*;pub use crate::b::*;',
                                  'pub use crate::a::*;use crate::b::*;'])
def test_unused_vs_used_ambiguity_is_determined_per_namespace_not_by_order(body):
    text = P + 'mod h{' + body + '}'
    r = scopes(text)
    assert lookup(r, 'h::x', 'type') is None
    with pytest.raises(RustError, match='ambiguous glob'):
        lookup(r, 'h::x')
    assert compile_rust(text).emit_object().startswith(b'\x7fELF')
    with pytest.raises(RustError, match='ambiguous glob'):
        compile_rust(text + 'pub fn answer()->u64{h::x()}')


@pytest.mark.parametrize('order', [0, 1])
def test_explicit_alias_precedes_weak_candidate_in_only_its_actual_namespace(order):
    uses = ['use crate::a::*;', 'use crate::b::x;']
    if order:
        uses.reverse()
    r = scopes('mod a{pub mod x{pub fn v(){}}pub fn x(){}}mod b{pub fn x(){}}'
               'mod h{' + ''.join(uses) + ' fn marker(){} }')
    h = r.root.child('h')
    assert lookup(r, 'x', 'value', h).identity.module.components == ('b',)
    assert lookup(r, 'x', 'type', h).identity.module.components == ('a',)
    with pytest.raises(RustError, match='private use'):
        lookup(r, 'h::x')


def test_private_candidate_must_not_turn_into_public_when_forwarded_through_ambiguous_module():
    for inside in ('pub use crate::a::*;use crate::b::*;', 'use crate::a::*;pub use crate::b::*;'):
        text = P + 'mod h{' + inside + '}mod out{pub use crate::h::*;}'
        with pytest.raises(RustError, match='ambiguous upstream'):
            compile_rust(text + 'fn answer()->u64{out::x()}')


def test_cycle_and_canonical_diamond_are_not_conflicts():
    text = ('mod root_provider{pub fn v()->u64{42}}'
            'mod a{pub use crate::root_provider::*;pub use crate::b::*;}'
            'mod b{pub use crate::a::*;pub use crate::root_provider::*;}'
            'mod c{pub use crate::a::*;pub use crate::b::*;}'
            'fn answer()->u64{c::v()}')
    r = scopes(text)
    assert lookup(r, 'c::v').identity == lookup(r, 'root_provider::v').identity
    assert compile_rust(text).emit_object().startswith(b'\x7fELF')
    assert lookup(scopes('mod a{pub use crate::b::*;}mod b{pub use crate::a::*;}'), 'b::v') is None


def test_same_entity_via_two_routes_keeps_wider_actual_visibility():
    src = ('mod a{pub fn x(){}}mod h{mod side{pub use crate::a::*;}'
           'use crate::a::*;pub use self::side::*;}pub use h::x;')
    r = scopes(src)
    assert lookup(r, 'x').identity == lookup(r, 'a::x').identity


def test_private_and_restricted_names_never_leak_through_reexports_or_constructors():
    text = ('mod p{pub struct Pair(pub u64,u64);pub(crate) fn c(){}'
            'fn private(){} pub mod child{pub use super::*; pub fn call(){private();}}}'
            'pub use p::*;')
    r = scopes(text)
    assert lookup(r, 'Pair', 'type').identity.name == 'Pair'
    assert lookup(r, 'Pair') is None and lookup(r, 'private') is None
    assert lookup(r, 'c').identity.name == 'c'
    with pytest.raises(RustError, match='private'):
        lookup(r, 'p::child::private')
    with pytest.raises(RustError, match='cannot be re-exported'):
        scopes(text + 'pub use self::c as outside;')
    assert compile_rust(text).emit_object().startswith(b'\x7fELF')
    with pytest.raises(RustError):
        compile_rust(text + 'fn build(){let p=Pair(1,2);}')


def test_explicit_alias_used_in_glob_target_must_bind_both_namespaces_after_complete_graph():
    src = ('mod origin{pub fn both()->u64{40}}'
           'mod h{pub mod both{pub fn n()->u64{2}}pub use crate::origin::*;}'
           'use h::both as dual;use dual::*;pub fn answer()->u64{dual()+n()}')
    r = scopes(src)
    assert lookup(r, 'dual').identity.module.components == ('origin',)
    assert lookup(r, 'n').identity.module.components == ('h','both')
    assert compile_rust(src).emit_object().startswith(b'\x7fELF')


def test_file_backed_glob_target_uses_its_own_scope_and_edition():
    files = {'src/lib.rs': 'mod p;mod c;', 'src/p.rs':'pub fn x(){}',
             'src/c.rs':'mod p{pub fn x(){}}use p::*;pub fn f(){x()}'}
    for edition, owner in ((2015,('p',)),(2021,('c','p'))):
        r = scopes(files['src/lib.rs'], edition, files)
        assert lookup(r, 'x', requester=r.root.child('c')).identity.module.components == owner


@pytest.mark.parametrize('text,gap', [
    ('use self::*;', 'into itself'),
    ('mod h{use crate::h::*;}', 'into itself'),
    ('use super::*;', 'too many leading super'),
    ('mod a{mod p{pub fn f(){}}} use a::p::*;', 'private item'),
    ('enum E{A,B}use E::*;', 'non-module or enum'),
    ('use std::collections::*;', 'library/prelude source dependency'),
    ('mod a{} use a::* as renamed;', 'terminal'),
    ('mod a{} use a::*::x;', 'terminal'),
    ('use *;', 'explicit module'),
    ('mod a{pub struct u32{pub n:u64}}use a::*;', 'primitive-shadowing'),
    ('mod p{pub mod a{}}use p::*;use a::*;', 'unknown type-namespace'),
])
def test_unsupported_or_inaccessible_sources_do_not_invent_bindings(text, gap):
    with pytest.raises(RustError, match=gap):
        scopes(text)


def test_strong_type_marker_vs_value_only_glob_names():
    r = scopes('mod p{pub struct Copy{pub f:u64} pub fn Sized(){}}use p::*;')
    assert r.has_local_type_binding('Copy',r.root)
    assert not r.has_local_type_binding('Sized',r.root)


def test_lint_follows_only_effective_public_reexports():
    head = '//! documented crate\n#![deny(missing_docs)]\n'
    with pytest.raises(RustError, match='missing documentation for a function'):
        compile_rust(head + 'mod h{pub fn x(){}}pub use h::*;')
    assert compile_rust(head + 'mod h{pub fn x(){}}pub use h::*;fn x(){}').emit_object().startswith(b'\x7fELF')
    with pytest.raises(RustError, match='missing_docs on ambiguous'):
        compile_rust(head + P + '/// documented module\npub mod h{' + G + '}')


def test_scoped_macro_precondition_keeps_no_false_builtin_with_an_unresolved_macro_namespace():
    with pytest.raises(RustError, match='glob import.*macro namespace'):
        Parser('mod p{}use p::*;fn f(){println!();}').parse()
    with pytest.raises(RustError, match='glob import.*macro namespace'):
        Parser('mod p{}use p::*;fn f()->bool{matches!(1u64, 1u64)}').parse()
    parser = Parser('mod p{}use p::*;mod sibling{pub fn f(){println!();}}')
    parser.parse()  # root imports do not bind a child module's output macro
    assert len(parser.macro_expansions) == 1


def test_glob_edge_and_work_bounds_report_real_rejections(monkeypatch):
    monkeypatch.setattr(ModuleGlobImports, 'MAX_EDGES', 2)
    scopes('mod a{}use a::*;use a::*;')
    with pytest.raises(RustError, match='count exceeds'):
        scopes('mod a{}use a::*;use a::*;use a::*;')
    monkeypatch.setattr(ModuleGlobImports, 'MAX_WORK', 1)
    with pytest.raises(RustError, match='exceeds work limit'):
        scopes('mod a{pub fn f(){}}use a::*;')


def test_export_enumeration_charges_each_outgoing_edge_even_with_no_exported_names():
    text = 'mod p{}mod h{' + 'use crate::p::*;' * 24 + '}use h::*;'
    r = scopes(text)
    r.globs.remaining = 15
    with pytest.raises(RustError, match='exceeds work limit'):
        list(r.globs.public_declarations(r.root))


def test_tuple_constructor_visibility_charges_each_actual_field():
    text = 'mod p{pub struct Pair(' + ','.join(['pub u64'] * 24) + ');}use p::*;'
    r = scopes(text)
    r.globs.remaining = 15
    with pytest.raises(RustError, match='exceeds work limit'):
        lookup(r, 'Pair')


NATIVE = ('mod a{pub fn shared()->u64{4294967300}pub fn extra()->u64{14}}'
          'mod b{pub fn choose()->u64{28}}'
          'mod h{pub use crate::a::*;pub use crate::b::choose;}'
          'use h::{*};pub fn answer()->u64{shared()+extra()+choose()}')


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
def test_true_owned_object_with_weak_and_explicit_imports_executes_full_u64(tmp_path,monkeypatch):
    assert run_owned_u64_function(NATIVE,'answer',tmp_path,monkeypatch,
                                  experimental_rustc_checked=False) == 4294967342


@pytest.mark.integration
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE or (None if shutil.which('rustc') else 'development-only rustc unavailable'))
def test_same_source_with_real_rustc_has_the_same_full_u64(tmp_path):
    result = run_rustc_str_function(NATIVE, 'answer', tmp_path, edition=2021, output='&{call}.to_le_bytes()')
    assert result == (4294967342).to_bytes(8,'little')
