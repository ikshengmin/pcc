"""Built-in `#[derive(..)]` on structs and enums.

https://doc.rust-lang.org/reference/attributes/derive.html
Copy, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Debug and Default are
declared and validated as rustc validates them. Copy makes a type copied
instead of moved; `.clone()` and `==`/`!=` borrow their operands; derived
equality is structural. Uses of the other derives remain explicit gaps.
Every rejected source below was checked with local rustc 1.92.0, whose
diagnostic code is recorded beside it.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser
from tests.rust.str_execution import UNAVAILABLE, run_owned_str_function, run_rustc_str_function

FIXTURE = (Path(__file__).parent / 'programs' / 'derives.rs').read_text()


def compile_source(source, **options):
    return compile_rust(source, experimental_rustc_checked=True, **options)


def test_derive_paths_are_retained_on_the_item():
    parser = Parser('#[derive(Debug, Copy)]\n#[doc(hidden)]\n#[derive(Clone, thiserror::Error)] pub enum G{A}')
    parser.parse()
    enum, = parser.enums
    assert [derive.name for derive in enum.derives] == ['Debug', 'Copy', 'Clone', 'thiserror::Error']
    assert (enum.derives[3].token.text, enum.derives[3].token.line) == ('thiserror', 3)


ACCEPTED = (
    '#[derive(Debug, Copy, Clone, Eq, PartialEq)] pub enum G{Yes, No} pub fn f()->bool{ let a = G::Yes; let b = a; a == b }',
    '#[derive(Hash, Debug, Default, PartialOrd, Ord, PartialEq, Eq, Clone, Copy)] '
    'pub struct S{x:u64, s:&\'static str, t:(u32, bool), a:[u32; 4]} pub fn f(){}',
    '#[derive()] pub struct S{x:u64} pub fn f(){}',
    # Clone and == borrow: `p` is still owned for the later move.
    '#[derive(Clone, PartialEq)] struct P{x:u64} pub fn f()->u64{ let p = P{x:1}; let q = p.clone(); let e = p == q; let r = p; r.x + q.x }',
    '#[derive(Clone, Copy)] struct P{x:u64} pub fn f()->u64{ let p = P{x:1}; let q = p; p.x + q.x }',
    'pub fn f(x: u64)->u64{ x.clone() + (true, 3u32).clone().1 as u64 }',
)


@pytest.mark.parametrize('source', [
    # Former boundary rows, moved verbatim: test_rust_aggregates pinned the
    # derive as an unsupported attribute, test_rust_arrays pinned array `==`.
    '#[derive(Copy,Clone)] struct P{x:i32}',
    'fn f(a:[u64;2],b:[u64;2])->bool{a==b}',
])
def test_former_boundary_rows_now_compile(source):
    compile_source(source)


@pytest.mark.parametrize('source', ACCEPTED)
def test_valid_derives_and_their_uses_compile(source):
    assert 'define' in compile_source(source).ir_text


REJECTED = (
    # E0277: the trait bound `S: Clone` is not satisfied
    ('#[derive(Copy)] pub struct S{pub x:u64}', 'deriving `Copy` requires `S: Clone`', 'E0277'),
    # E0204: the trait `Copy` cannot be implemented for this type (reported first)
    ('pub struct N{pub x:u64} #[derive(Clone, Copy)] pub struct S{pub n:N}', 'does not implement `Copy`', 'E0204'),
    # E0277: the trait bound `N: Clone` is not satisfied
    ('pub struct N{pub x:u64} #[derive(Clone)] pub struct S{pub n:N}', 'does not implement `Clone`', 'E0277'),
    ('#[derive(Clone)] pub struct N{pub x:u64} #[derive(Clone, Copy)] pub struct S{pub n:N}', 'does not implement `Copy`', 'E0204'),
    # E0277: can't compare `S` with `S`
    ('#[derive(Eq)] pub struct S{pub x:u64}', 'deriving `Eq` requires `S: PartialEq`', 'E0277'),
    ('#[derive(Ord)] pub struct S{pub x:u64}', 'deriving `Ord` requires', 'E0277'),
    # E0369: binary operation `==` cannot be applied to type `N`
    ('pub struct N{pub x:u64} #[derive(PartialEq)] pub struct S{pub n:N}', 'does not implement `PartialEq`', 'E0369'),
    # E0277: `N` doesn't implement `Debug`
    ('pub struct N{pub x:u64} #[derive(Debug)] pub struct S{pub n:N}', 'does not implement `Debug`', 'E0277'),
    # E0119: conflicting implementations of trait `Clone`
    ('#[derive(Clone, Clone)] pub struct S{pub x:u64}', 'conflicting implementations', 'E0119'),
    # E0774: `derive` may only be applied to `struct`s, `enum`s and `union`s
    ('#[derive(Debug)] pub fn f(){}', 'may only be applied to', 'E0774'),
    # E0369 at the use: no PartialEq
    ('pub struct P{pub x:u64} pub fn f(p: P, q: P)->bool{ p == q }', 'does not implement PartialEq', 'E0369'),
    # E0382: use of moved value (no Copy derive)
    ('pub struct P{pub x:u64} pub fn f()->u64{ let p = P{x:1}; let q = p; p.x }', 'moved', 'E0382'),
)


@pytest.mark.parametrize('source,message,code', REJECTED, ids=[row[2] + ':' + row[1] for row in REJECTED])
def test_invalid_derives_and_uses_are_rejected_like_rustc(source, message, code, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('invalid derive reached IR'))
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


@pytest.mark.parametrize('source,message', [
    # Accepted by rustc (given the crates or variants), but explicit gaps here.
    ('#[derive(Debug, thiserror::Error)] pub struct S{x:u64}', 'procedural-macro expansion is not implemented'),
    ('#[derive(Serialize)] pub struct S{x:u64}', 'cannot find derive macro `Serialize`'),
    ('#[derive(Clone)] pub struct S<T>{x:T}', 'derives on generic data declarations'),
    ('#[derive(Default)] pub enum E{A}', r'needs a `#\[default\]` variant'),
    ('#[derive(PartialOrd, PartialEq)] struct P{x:u64} pub fn f()->bool{ P{x:1} < P{x:2} }', "operator '<' does not accept P"),
    ('#[derive(PartialEq)] struct P{s:&\'static str} pub fn f(p: P)->bool{ p == p }', 'core str equality'),
])
def test_unowned_derive_forms_and_uses_are_explicit(source, message):
    with pytest.raises(RustError, match=message):
        compile_source(source)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
def test_fixture_runs_natively_with_owned_compile_assemble_and_link(tmp_path, monkeypatch):
    assert run_owned_str_function(FIXTURE, 'answer', tmp_path, monkeypatch) == b'derives agree'


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_build_of_the_fixture_agrees(tmp_path):
    assert run_rustc_str_function(FIXTURE, 'answer', tmp_path) == b'derives agree'


@pytest.mark.parametrize('target', ['x86_64-unknown-linux-gnu', 'arm64-apple-darwin'])
def test_fixture_cross_emits_both_targets(target):
    assert compile_source(FIXTURE, target_triple=target).emit_object()


THISERROR_SHAPE = """#[derive(Debug, thiserror::Error)]
#[error("uv was installed through {source}")]
struct Installed { #[source] inner: u64, source: u64 }
#[derive(Debug, thiserror::Error)]
enum Failure { #[error("a")] A, #[error(transparent)] B(#[from] u64) }
pub fn f(){}"""


def test_procedural_derive_helper_attributes_are_retained_not_interpreted():
    parser = Parser(THISERROR_SHAPE)
    parser.parse()
    record, = parser.records
    enum, = parser.enums
    assert [(h.name, h.token.line) for h in record.helper_attributes] == [('error', 2), ('source', 3)]
    assert [(h.name, h.token.line) for h in enum.helper_attributes] == [('error', 5), ('error', 5), ('from', 5)]
    assert ''.join(token.text for token in record.helper_attributes[0].tokens) == 'error("uv was installed through {source}")]'
    # The owning derive is still the explicit semantic gap.
    with pytest.raises(RustError, match='procedural-macro expansion is not implemented'):
        compile_source(THISERROR_SHAPE)


@pytest.mark.parametrize('source,message', [
    # Without a procedural derive to own them, unknown attributes stay errors.
    ('#[error("x")] struct S{x:u64} pub fn f(){}', 'unsupported Rust attribute'),
    ('#[derive(Debug)] #[error("x")] struct S{x:u64}', 'unsupported Rust attribute'),
    ('struct R{#[allow(missing_docs)]pub x:u64}', 'expected an identifier'),
])
def test_unowned_attributes_keep_their_errors(source, message):
    with pytest.raises(RustError, match=message):
        compile_source(source)
