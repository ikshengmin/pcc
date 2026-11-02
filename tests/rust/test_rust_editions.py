"""Edition identity propagation, not complete edition semantics.

Spec: https://doc.rust-lang.org/edition-guide/editions/
https://doc.rust-lang.org/rustc/command-line-arguments.html#--edition-specify-the-edition-to-use
https://doc.rust-lang.org/reference/items/modules.html
Actual token creation/execution obligations are in test_rust_editions_reference.
All newly authored obligations are UNRUN until CI executes them.
"""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import subprocess

import pytest

from pcc.frontends.rust import (
    CheckedRustSource,
    ExternalRustcCheck,
    RustError,
    RustcToolchain,
    check_rust_source,
    compile_rust,
    compile_rust_checked,
)
from pcc.frontends.rust.crate_context import parse_crate_sources
from pcc.frontends.rust.module_resolution import inline_crate
from pcc.frontends.rust.syntax import Parser


EDITIONS = (2015, 2018, 2021, 2024)
TARGET = 'x86_64-unknown-linux-gnu'
ORIGINAL = 'mod inner{pub fn value()->u64{42}} pub fn answer()->u64{inner::value()}'


def forbidden(*args, **kwargs):
    pytest.fail('edition selection must not invoke an external compiler')


@pytest.mark.parametrize('edition', EDITIONS)
def test_public_compile_passes_edition_to_real_parser_and_module_context(edition, monkeypatch):
    import pcc.frontends.rust as frontend
    from pcc.frontends.rust import module_resolution

    parsers, graphs = [], []

    class ObservedParser(Parser):
        def __init__(self, source, **options):
            assert source == ORIGINAL
            assert options['edition'] == edition
            super().__init__(source, **options)
            parsers.append(self)

    original_inline = module_resolution.inline_crate

    def observed_inline(parser, functions):
        graph = original_inline(parser, functions)
        graphs.append(graph)
        return graph

    monkeypatch.setattr(frontend, 'Parser', ObservedParser)
    monkeypatch.setattr(module_resolution, 'inline_crate', observed_inline)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    result = compile_rust(ORIGINAL, edition=edition, experimental_rustc_checked=True)
    assert len(parsers) == len(graphs) == 1
    graph = graphs[0]
    assert graph.edition == graph.policy.edition == edition
    assert graph.root.identity.crate.edition == edition
    child = graph.root.children[0]
    assert child.identity.crate.edition == child.body.crate_policy.edition == edition
    assert graph.root.source.text == ORIGINAL
    assert graph.policy.standard_prelude_path == ('std', 'prelude', 'rust_' + str(edition))
    assert result.edition == edition and 'define' in result.ir_text
    assert not result.externally_checked and not result.borrow_checked_by_pcc


def test_default_remains_2021_without_inferring_source_or_tool_edition(monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    result = compile_rust(ORIGINAL, experimental_rustc_checked=True)
    assert result.edition == 2021


@pytest.mark.parametrize('edition', EDITIONS)
def test_inline_and_outlined_identities_share_selected_edition(edition):
    parser = Parser(ORIGINAL, edition=edition)
    inline = inline_crate(parser, parser.parse())
    sources = {'src/lib.rs': 'mod inner; pub fn answer()->u64{inner::value()}',
               'src/inner.rs': 'pub fn value()->u64{42}'}
    outlined = parse_crate_sources('src/lib.rs', sources, crate_name='example', edition=edition)
    for graph in (inline, outlined):
        for module in (graph.root, *graph.root.children):
            assert module.identity.crate.edition == module.body.crate_policy.edition == edition
        assert graph.edition == edition
        assert not graph.semantic_context_complete
    assert outlined.sources['src/inner.rs'].text == sources['src/inner.rs']


def test_identical_bytes_in_distinct_editions_have_distinct_module_item_keys():
    items = []
    for edition in (2021, 2024):
        parser = Parser(ORIGINAL, edition=edition)
        graph = inline_crate(parser, parser.parse())
        items.append(graph.root.children[0].items['value', 'value'].identity)
    assert items[0].module.crate.source_closure_sha256 == items[1].module.crate.source_closure_sha256
    assert items[0] != items[1] and items[0].binding_key != items[1].binding_key


@pytest.mark.parametrize('edition', (True, False, None, '2024', 2024.0, 0, 2027))
def test_invalid_editions_fail_before_parser_tool_inspection_or_evidence_write(edition, tmp_path, monkeypatch):
    import pcc.frontends.rust as frontend
    from pcc.frontends.rust import external_check

    monkeypatch.setattr(frontend, 'Parser', forbidden)
    monkeypatch.setattr(external_check, '_file_sha', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    pin = RustcToolchain(tmp_path / 'absent', '0' * 64, 'rustc not executed')
    evidence = tmp_path / 'must-not-exist'
    with pytest.raises(RustError, match='recognized integer edition'):
        compile_rust(ORIGINAL, edition=edition, experimental_rustc_checked=True)
    for entry in (check_rust_source, compile_rust_checked):
        with pytest.raises(RustError, match='recognized integer edition'):
            entry(ORIGINAL, edition=edition, toolchain=pin, evidence_directory=evidence)
    assert not evidence.exists()


@pytest.mark.parametrize('edition', EDITIONS)
def test_edition_is_not_an_admission_waiver(edition, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer

    monkeypatch.setattr(Lowerer, 'lower', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    with pytest.raises(RustError, match='has no borrow checker'):
        compile_rust(ORIGINAL, edition=edition)
    with pytest.raises(RustError, match='actual source/options-bound CheckedRustSource'):
        compile_rust('fn f(x:&mut u64){*x=42;}', edition=edition,
                     experimental_rustc_checked=True)
    with pytest.raises(RustError, match='no_core requires'):
        compile_rust('#![no_core]fn f(){}', edition=edition,
                     experimental_rustc_checked=True)


def shaped_record(source, edition):
    # Deliberately non-certifying provenance consistency control. Never mint a
    # CheckedRustSource, forge the seal, or use a fake process to obtain one.
    return ExternalRustcCheck(
        sha256(source.encode()).hexdigest(), str(edition), TARGET, (), (), False,
        '0' * 64, 'rustc non-certifying shape control',
        ('/not-executed/rustc', '--edition=' + str(edition), '--target=' + TARGET,
         '-Coverflow-checks=off', '--emit=metadata', '--crate-type=lib',
         '--crate-name=certified_input', '/not-executed/source.rs', '-o',
         '/not-executed/output.rmeta'), 0, '0' * 64, '0' * 64)


@pytest.mark.parametrize('edition', EDITIONS)
def test_provenance_shape_binds_edition_but_never_authorizes_compilation(edition, monkeypatch):
    record = shaped_record(ORIGINAL, edition)
    record.validate(ORIGINAL, target_triple=TARGET, overflow_checks=False, edition=edition)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    with pytest.raises(RustError, match='caller-constructed external record'):
        compile_rust(ORIGINAL, rustc_check=record, edition=edition)
    with pytest.raises(RustError, match='only be created by check_rust_source'):
        CheckedRustSource(ORIGINAL, record)
    other = 2021 if edition != 2021 else 2024
    for invalid in (replace(record, edition=str(other)),
                    replace(record, command=(record.command[0], '--edition=' + str(other), *record.command[2:])),
                    replace(record, command=record.command + ('--edition=' + str(other),)),
                    replace(record, cfg=('feature="extra"',)),
                    replace(record, dependencies=(('unseen', '0' * 64),))):
        with pytest.raises(RustError, match='external rustc check'):
            invalid.validate(ORIGINAL, target_triple=TARGET, overflow_checks=False, edition=edition)
    with pytest.raises(RustError, match='external rustc check'):
        record.validate(ORIGINAL, target_triple=TARGET, overflow_checks=False, edition=other)
    with pytest.raises(RustError, match='unchanged Rust source bytes'):
        record.validate(ORIGINAL + '\n', target_triple=TARGET, overflow_checks=False, edition=edition)


def test_value_fixture_is_original_source_not_rewritten_by_edition_option():
    path = Path(__file__).with_name('programs') / 'edition_context_values.rs'
    original = path.read_bytes()
    for edition in EDITIONS:
        parser = Parser(original.decode(), edition=edition)
        graph = inline_crate(parser, parser.parse())
        assert graph.root.source.text.encode() == original
        assert graph.root.source.sha256 == sha256(original).hexdigest()
    assert path.read_bytes() == original
