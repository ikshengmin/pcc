"""Core's `matches!` macro, expanded to the match it is defined as.

https://doc.rust-lang.org/core/macro.matches.html
`matches!(value, pattern $(if guard)? $(,)?)` is
`match value { pattern => true, _ => false }`. PCC expands exactly that match
at parse time, so typing, exhaustiveness, moves and lowering are the match
machinery's. Guards need match guards and stay an explicit gap; a crate that
defines core itself (lang-item context inspection) has no `matches!`.
"""
import shutil
from pathlib import Path

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser
from tests.rust.str_execution import UNAVAILABLE, run_owned_str_function, run_rustc_str_function

FIXTURE = (Path(__file__).parent / 'programs' / 'matches.rs').read_text()


def compile_source(source, **options):
    return compile_rust(source, experimental_rustc_checked=True, **options)


def test_matches_expands_to_the_core_definition():
    parser = Parser('fn f(x: u64) -> bool { matches!(x, 1 | 2,) }')
    function, = parser.parse()
    expansion = function.body.children[0]
    assert expansion.kind == 'match'
    pattern, fallback = expansion.value
    assert (pattern.kind, [option.kind for option in pattern.children], fallback.kind) == ('or', ['literal', 'literal'], 'wildcard')
    scrutinee, yes, no = expansion.children
    assert (scrutinee.kind, scrutinee.value, yes.value, no.value) == ('name', 'x', True, False)
    assert [item.name for item in parser.macro_expansions] == ['matches']


@pytest.mark.parametrize('source', [
    'pub fn f(x: u64) -> bool { matches!(x, 0) }',
    'pub fn f(x: u64) -> bool { matches![x, 0 | 1,] }',
    'pub fn f(x: (u64, bool)) -> bool { matches!{x, (1, true)} }',
    # A place scrutinee is inspected, not moved: the non-Copy `e` moves later.
    'enum E{A, B} pub fn f() -> bool { let e = E::A; let m = matches!(e, E::A); let g = e; m }',
])
def test_matches_forms_compile(source):
    compile_source(source)


@pytest.mark.parametrize('source,message', [
    ('pub fn f(x: u64) -> bool { matches!(x, 0 if x > 1) }', 'matches! guards require match guards'),
    ('pub fn f(x: u64) -> bool { matches!(x) }', "expected ','"),
    ('pub fn f(x: u64) -> bool { matches!() }', 'requires a value and a pattern'),
    ('pub fn f(x: u64) -> bool { matches!(x, 0, 1) }', "expected '\\)'"),
])
def test_unowned_or_malformed_matches_are_explicit(source, message):
    with pytest.raises(RustError, match=message):
        compile_source(source)


def test_lang_item_context_has_no_core_macros():
    from pcc.frontends.rust.lang_item_context import inspect_lang_item_context
    with pytest.raises(RustError, match='macro invocations require macro-by-example expansion'):
        inspect_lang_item_context('src/lib.rs', {'src/lib.rs': 'fn f(x: u64) -> bool { matches!(x, 0) }'},
                                  crate_name='example', edition=2021)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
def test_fixture_runs_natively_with_owned_compile_assemble_and_link(tmp_path, monkeypatch):
    assert run_owned_str_function(FIXTURE, 'answer', tmp_path, monkeypatch) == b'matches agrees'


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_build_of_the_fixture_agrees(tmp_path):
    assert run_rustc_str_function(FIXTURE, 'answer', tmp_path) == b'matches agrees'


@pytest.mark.parametrize('target', ['x86_64-unknown-linux-gnu', 'arm64-apple-darwin'])
def test_fixture_cross_emits_both_targets(target):
    assert compile_source(FIXTURE, target_triple=target).emit_object()
