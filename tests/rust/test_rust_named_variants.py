"""Struct-like enum variants: `V { a: T, b: U }`.

https://doc.rust-lang.org/reference/items/enumerations.html
https://doc.rust-lang.org/reference/patterns.html#struct-patterns
A struct-like variant is stored like a tuple payload in declaration order,
with its field names. A literal `E::V { b, a: x }` evaluates fields in source
order and stores each at its declaration position; a pattern
`E::V { a, b: p, .. }` becomes the positional payload pattern it denotes.
Each rejected source was checked with local rustc 1.92.0.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser
from tests.rust.str_execution import UNAVAILABLE, run_owned_str_function, run_rustc_str_function

FIXTURE = (Path(__file__).parent / 'programs' / 'named_variants.rs').read_text()


def compile_source(source, **options):
    return compile_rust(source, experimental_rustc_checked=True, **options)


def test_struct_like_variants_keep_positions_and_names():
    parser = Parser('enum E{ V { supported: u32, version: u64 }, U, T(bool) }')
    parser.parse()
    enum, = parser.enums
    assert enum.payloads == {'V': ('u32', 'u64'), 'T': ('bool',)}
    assert {name: [token.text for token in tokens] for name, tokens in enum.named_payloads.items()} == {
        'V': ['supported', 'version']}


def test_former_named_payload_gap_row_now_parses():
    # Former row of test_rust_patterns, moved verbatim: valid Rust.
    compile_source('enum E{A{x:i32}}')


@pytest.mark.parametrize('source', [
    'enum E{ V { a: u32, b: bool }, U } pub fn f()->u32{ let e = E::V { b: true, a: 4 }; match e { E::V { a, b: true } => a, _ => 0 } }',
    'enum E{ V { a: u32 } } pub fn f()->u32{ let a = 3; match (E::V { a }) { E::V { a: x } => x } }',
    'enum E{ V { a: u32 }, U } impl E { fn m() -> Self { Self::V { a: 1 } } } pub fn f()->bool{ matches!(E::m(), E::V { .. }) }',
    'enum E{ A(u32), U } pub fn f(e: E)->bool{ matches!(e, E::A { .. } | E::U { .. }) }',
    'enum E{ V { a: u32 } } pub fn f(e: E)->u32{ match e { E::V { mut a } => { a = a + 1; a } } }',
])
def test_construction_and_patterns_compile(source):
    assert 'define' in compile_source(source).ir_text


REJECTED = (
    ('pub enum E{ V { a: u32 } } pub fn f(){ let _e = E::V { a: 1, a: 2 }; }', 'specified more than once', 'E0062'),
    ('pub enum E{ V { a: u32 } } pub fn f(){ let _e = E::V { b: 1 }; }', 'has no field named `b`', 'E0559'),
    ('pub enum E{ V { a: u32, b: u32 } } pub fn f(){ let _e = E::V { a: 1 }; }', 'missing field `b`', 'E0063'),
    ('pub enum E{ V { a: u32 } } pub fn f(){ let _e = E::V(1); }', 'found struct variant', 'E0533'),
    ('pub enum E{ V { a: u32, b: u32 } } pub fn f(e: E)->u32{ match e { E::V { a } => a } }', 'does not mention field `b`', 'E0027'),
    ('pub enum E{ V { a: u32 } } pub fn f(e: E)->u32{ match e { E::V { c, .. } => c } }', 'does not have a field named `c`', 'E0026'),
    ('pub enum E{ V { a: u32 } } pub fn f(e: E)->u32{ match e { E::V { a, a: _ } => a } }', 'bound multiple times', 'E0025'),
    ('pub enum E{ V { a: u32 } } pub fn f(e: E)->u32{ match e { E::V(x) => x } }', 'found struct variant', 'E0164'),
    ('pub enum E{ V { a: u32, a: u32 } }', 'already declared', 'E0124'),
    ('pub enum E{ V { pub a: u32 } }', 'visibility qualifiers are not permitted', 'E0449'),
)


@pytest.mark.parametrize('source,message,code', REJECTED, ids=[row[2] for row in REJECTED])
def test_invalid_struct_variants_are_rejected_like_rustc(source, message, code):
    with pytest.raises(RustError, match=message):
        compile_source(source)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_rejects_the_same_sources_with_the_recorded_codes(tmp_path):
    for index, (source, _, code) in enumerate(REJECTED):
        path = tmp_path / f'case{index}.rs'
        path.write_text(source + '\n')
        run = subprocess.run([shutil.which('rustc'), '--crate-type=lib', '--edition=2021', '--emit=metadata',
                              str(path), '-o', str(tmp_path / f'case{index}.rmeta')],
                             capture_output=True, text=True, timeout=60)
        assert run.returncode != 0 and f'error[{code}]' in run.stderr, (source, run.stderr)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
def test_fixture_runs_natively_with_owned_compile_assemble_and_link(tmp_path, monkeypatch):
    assert run_owned_str_function(FIXTURE, 'answer', tmp_path, monkeypatch) == b'named variants agree'


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_build_of_the_fixture_agrees(tmp_path):
    assert run_rustc_str_function(FIXTURE, 'answer', tmp_path) == b'named variants agree'


@pytest.mark.parametrize('target', ['x86_64-unknown-linux-gnu', 'arm64-apple-darwin'])
def test_fixture_cross_emits_both_targets(target):
    assert compile_source(FIXTURE, target_triple=target).emit_object()
