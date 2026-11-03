"""Module source/identity prerequisites, not a successful core compiler.

Spec: https://doc.rust-lang.org/reference/items/modules.html
      https://doc.rust-lang.org/reference/visibility-and-privacy.html
The parser graph has no lowering permission and never discards core obligations.
"""
from hashlib import sha256
from pathlib import Path

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.crate_context import ModuleId, parse_crate_sources
from pcc.frontends.rust.syntax import Parser


def load(sources, root='src/lib.rs', edition=2021):
    return parse_crate_sources(root, sources, crate_name="example", edition=edition)


def test_inline_and_outlined_scopes_have_distinct_canonical_item_identities():
    sources = {
        'src/lib.rs': '//! root\npub mod a; mod b { pub fn same()->u64{2} } fn same()->u64{0}',
        'src/a.rs': '//! a\npub fn same()->u64{1} mod child;',
        'src/a/child.rs': 'fn same()->u64{3}',
    }
    graph = load(sources)
    root, (a, b) = graph.root, graph.root.children
    assert root.identity == ModuleId(root.identity.crate, ())
    assert a.identity == ModuleId(root.identity.crate, ('a',)) and b.identity == ModuleId(root.identity.crate, ('b',))
    ids = {module.items['value', 'same'].identity for module in (root, a, b, a.children[0])}
    assert len(ids) == 4
    assert root.body.documentation[0].text == ' root'
    assert a.body.documentation[0].text == ' a'
    assert a.public and not b.public
    assert a.source.text == sources['src/a.rs']
    assert a.source.sha256 == sha256(sources['src/a.rs'].encode()).hexdigest()
    assert not graph.semantic_context_complete


def test_source_map_is_snapshotted_and_both_namespaces_and_visibility_are_retained():
    sources = {'src/lib.rs': 'pub struct R{pub a:u64,b:u64} fn R(){} pub enum E{A} pub trait T{fn f();}'}
    graph = load(sources)
    sources['src/lib.rs'] = 'changed after parse'
    assert graph.root.source.text != sources['src/lib.rs']
    assert graph.root.items['type', 'R'].public
    assert not graph.root.items['value', 'R'].public
    assert graph.root.items['type', 'R'].syntax.field_public == (True, False)
    assert graph.root.items['type', 'E'].public and graph.root.items['type', 'T'].public
    with pytest.raises(TypeError):
        graph.sources['new.rs'] = None
    with pytest.raises(TypeError):
        graph.root.items['type', 'X'] = None


def test_file_and_directory_module_forms_share_the_same_logical_children():
    root = 'mod a;'
    for path in ('src/a.rs', 'src/a/mod.rs'):
        graph = load({'src/lib.rs': root, path: 'mod child;', 'src/a/child.rs': 'fn f(){}'})
        assert graph.root.children[0].children[0].identity == ModuleId(graph.root.identity.crate, ('a', 'child'))


def test_inline_child_uses_module_directory_without_rewriting_source_coordinates():
    source = '\nmod a {\n /// child\n pub mod b;\n fn f(){}\n}'
    graph = load({'src/lib.rs': source, 'src/a/b.rs': 'fn child(){}'})
    a = graph.root.children[0]
    assert a.body.functions[0].token.line == 5
    doc = a.body.modules[0].documentation[0]
    assert source[doc.source_span[0]:doc.source_span[1]] == '/// child'
    assert a.children[0].identity == ModuleId(graph.root.identity.crate, ('a', 'b'))


def test_literal_path_uses_source_directory_and_preserves_distinct_file_alias_scopes():
    sources = {'src/lib.rs': '#[path="shared.rs"] mod a; #[path=r#"shared.rs"#]mod b;',
               'src/shared.rs': 'mod child;', 'src/child.rs': 'fn f(){}'}
    graph = load(sources)
    a, b = graph.root.children
    assert a.source is b.source
    assert a.identity != b.identity
    assert a.children[0].identity != b.children[0].identity
    assert a.children[0].source.path == 'src/child.rs'


@pytest.mark.parametrize('sources,message', [
    ({'src/lib.rs': 'mod a;'}, 'source missing'),
    ({'src/lib.rs': 'mod a;', 'src/a.rs': '', 'src/a/mod.rs': ''}, 'ambiguous'),
    ({'src/lib.rs': 'mod a{} mod a{}'}, 'duplicate type-namespace'),
    ({'src/lib.rs': 'struct a{} mod a{}'}, 'duplicate type-namespace'),
    ({'src/lib.rs': 'fn a(){} fn a(){}'}, 'duplicate value-namespace'),
    ({'src/lib.rs': '#[path="lib.rs"]mod again;'}, 'circular'),
    ({'src/lib.rs': '#[path="../../outside.rs"]mod a;'}, 'escapes'),
    ({'src/lib.rs': '#[path="/outside.rs"]mod a;'}, 'relative POSIX'),
    ({'src/lib.rs': '#[path="a.rs"]mod a{}'}, 'inline modules'),
    ({'src/lib.rs': 'mod a {#[path="b.rs"]mod b;}'}, 'directory ownership'),
    ({'src/lib.rs': '#[path="a.rs"]fn f(){}'}, 'only for outlined modules'),
    ({'src/lib.rs': '#[path=include_str!("a")]mod a;'}, 'literal string'),
    ({'src/lib.rs': 'mod a{#![no_std]fn f(){}}'}, 'semantic inner module'),
    ({'src/lib.rs': 'mod a{#![doc(html_no_source)]fn f(){}}'}, 'requires crate placement'),
    ({'src/lib.rs': '#[cfg(any())]mod a;'}, r'^src/lib\.rs: 1:3: cfg requires an actual source/options-bound CheckedRustSource configuration$'),
])
def test_loader_rejects_incomplete_or_unimplemented_context_instead_of_flattening(sources,message):
    with pytest.raises(RustError, match=message):
        load(sources)


def test_inline_depth_limit_is_diagnostic_before_python_recursion_failure():
    with pytest.raises(RustError, match='inline module nesting'):
        Parser('mod a{' * 33 + '}' * 33).parse()


@pytest.mark.parametrize('edition', [2015, 2018, 2021, 2024])
def test_edition_is_explicit_source_context_not_semantic_acceptance(edition):
    graph = load({'src/lib.rs': 'fn f(){}'}, edition=edition)
    assert graph.edition == edition and not graph.semantic_context_complete


@pytest.mark.parametrize('sources', [{'./lib.rs': ''}, {'../lib.rs': ''}, {'/lib.rs': ''}, {'lib.rs': b''}])
def test_source_map_does_not_guess_paths_or_decode_bytes(sources):
    with pytest.raises(RustError):
        parse_crate_sources('lib.rs', sources, crate_name='example', edition=2021)


def test_module_parser_graph_does_not_create_an_unchecked_public_lowering_escape(monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('unresolved module graph reached IR'))
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust('mod a{pub fn f(x:&u64)->u64{*x}}', experimental_rustc_checked=True)


def test_original_core_root_retains_no_core_and_staging_failure():
    root = Path(__file__).parent/'upstream/rust-1.90.0/library/core/src/lib.rs'
    original = root.read_bytes()
    assert sha256(original).hexdigest() == 'c22a6133d900f42b3d447840e84013b26b7ec3757274dfb31b9d514b901edd50'
    with pytest.raises(RustError, match='no_core requires an owned compiler/core context'):
        load({'src/lib.rs': original.decode()}, edition=2024)
    assert root.read_bytes() == original


def test_item_ids_do_not_alias_across_crates_or_changed_outlined_sources():
    sources = {'src/lib.rs': 'mod a;', 'src/a.rs': 'fn f(){}'}
    first = parse_crate_sources('src/lib.rs', sources, crate_name='first', edition=2021)
    second = parse_crate_sources('src/lib.rs', sources, crate_name='second', edition=2021)
    reordered = parse_crate_sources('src/lib.rs', dict(reversed(list(sources.items()))), crate_name='first', edition=2021)
    assert first.root.children[0].items['value', 'f'].identity != second.root.children[0].items['value', 'f'].identity
    assert first.root.identity == reordered.root.identity
    changed = load({**sources, 'src/a.rs': 'fn f(){let x=1;}'})
    unchanged = load(sources)
    assert changed.root.identity != unchanged.root.identity


@pytest.mark.parametrize('bad_path', ['src/\ud800.rs', 'src/\udfff.rs'])
@pytest.mark.parametrize('position', ['root', 'unreachable-key'])
def test_lone_surrogate_paths_fail_as_rust_errors_before_any_parse(bad_path, position, monkeypatch):
    from pcc.frontends.rust import crate_context
    def forbidden(*args, **kwargs):
        pytest.fail('invalid source-map path reached the parser')
    monkeypatch.setattr(crate_context, 'Parser', forbidden)
    sources = {'src/lib.rs': 'fn f(){}'}
    root = 'src/lib.rs'
    if position == 'root':
        root = bad_path
    else:
        sources[bad_path] = ''
    with pytest.raises(RustError, match='source paths must be valid UTF-8') as caught:
        load(sources, root=root)
    assert isinstance(caught.value.__cause__, UnicodeEncodeError)


def test_root_path_and_edition_are_independent_parts_of_the_crate_identity():
    # Exactly the same frozen closure: selecting another root or edition must
    # not alias nominal identities, even when both root files have equal text.
    sources = {'src/lib.rs': 'fn f(){}', 'src/other.rs': 'fn f(){}'}
    first = load(sources)
    other_root = load(sources, root='src/other.rs')
    other_edition = load(sources, edition=2024)
    identities = [g.root.items['value', 'f'].identity for g in (first, other_root, other_edition)]
    assert len(set(identities)) == 3
    assert len({g.root.identity.crate.source_closure_sha256 for g in (first, other_root, other_edition)}) == 1
    assert all(not g.semantic_context_complete for g in (first, other_root, other_edition))


def test_outlined_file_error_keeps_its_original_path_line_and_column():
    sources = {'src/lib.rs': 'mod child;', 'src/child.rs': '\nfn f(1:u64){}'}
    before = dict(sources)
    with pytest.raises(RustError) as caught:
        load(sources)
    assert str(caught.value) == "src/lib.rs: src/child.rs: 2:6: expected an identifier"
    assert sources == before


@pytest.mark.parametrize('source', [
    'pub(crate) mod child{}',
    'pub(super) fn f(){}',
    'pub(in crate::scope) struct R{n:u64}',
    'pub(crate) enum E{A}',
    'pub(crate) trait T{fn f();}',
    'struct R{pub(crate) n:u64}',
])
def test_restricted_visibility_remains_rejected_before_lowering(source, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('restricted visibility reached IR'))
    with pytest.raises(RustError):
        compile_rust(source, experimental_rustc_checked=True)


def test_source_file_count_limit_counts_unreachable_supplied_files():
    sources = {'src/lib.rs': ''}
    sources.update({f'src/unused{i}.rs': '' for i in range(255)})
    assert len(load(sources).sources) == 256
    sources['src/overflow.rs'] = ''
    with pytest.raises(RustError, match='at most256 files'):
        load(sources)


def test_source_byte_limits_apply_before_parsing_even_to_unreachable_files():
    one_mib = ' ' * (1024 * 1024)
    sources = {'src/lib.rs': '', 'src/unused.rs': one_mib}
    assert len(load(sources).sources) == 2
    with pytest.raises(RustError, match='1MiB file /8MiB total'):
        load({**sources, 'src/unused.rs': one_mib + ' '})
    total_limit = {'src/lib.rs': '', **{f'src/unused{i}.rs': one_mib for i in range(8)}}
    assert len(load(total_limit).sources) == 9
    with pytest.raises(RustError, match='1MiB file /8MiB total'):
        load({**total_limit, 'src/extra.rs': ' '})


def test_module_count_limit_includes_the_root_and_inline_modules():
    source = ''.join(f'mod child{i}{{}}' for i in range(255))
    assert len(load({'src/lib.rs': source}).root.children) == 255
    with pytest.raises(RustError, match='256 modules /32 levels'):
        load({'src/lib.rs': source + 'mod extra{}'})


def test_outlined_depth_limit_is_reported_without_python_recursion_failure():
    sources = {'src/lib.rs': 'mod child;'}
    for depth in range(1, 34):
        path = 'src/' + 'child/' * (depth - 1) + 'child.rs'
        sources[path] = 'mod child;' if depth < 33 else ''
    with pytest.raises(RustError, match='256 modules /32 levels'):
        load(sources)
