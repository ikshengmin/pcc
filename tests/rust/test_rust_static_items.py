"""Static items: `static [mut] NAME: Type = expr;`.

https://doc.rust-lang.org/reference/items/static-items.html
A static is one global place, unlike a const. Integer, bool and `&str`
statics are evaluated once at their declaration (initializers may read
consts and immutable statics), lowered to globals, read by loading and
written by storing. Every use of a `static mut` needs an unsafe context.
Each rejected source was checked with local rustc 1.92.0.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser, StaticItem
from tests.rust.str_execution import UNAVAILABLE, run_owned_str_function, run_rustc_str_function

FIXTURE = (Path(__file__).parent / 'programs' / 'statics.rs').read_text()


def compile_source(source, **options):
    return compile_rust(source, experimental_rustc_checked=True, **options)


def test_static_items_parse_with_mutability_and_visibility():
    parser = Parser('pub static A: u64 = 1; static mut B: u32 = 2; mod m{ pub static C: bool = true; }')
    parser.parse()
    assert [(item.token.text, item.mutable, item.public) for item in parser.statics] == [('A', False, True), ('B', True, False)]
    assert isinstance(parser.modules[0].body.statics[0], StaticItem)


def test_statics_are_globals_read_by_loading():
    ir = compile_source('static mut C: u32 = 5; static K: u64 = 2 * 3; '
                        'pub fn f()->u32{ unsafe { C += 1; C } } pub fn g()->u64{ K }').ir_text
    assert '@__pcc_rust_static_0 = internal global i32 5' in ir
    assert '@__pcc_rust_static_1 = internal constant i64 6' in ir
    assert 'load i64, ptr @__pcc_rust_static_1' in ir


@pytest.mark.parametrize('source', [
    'static A: u32 = 1; static B: u32 = A; pub fn f()->u32{B}',
    'static A: u32 = 1; const B: u32 = A + 1; pub fn f()->u32{B}',
    'static S: &str = "x"; static T: &str = S; pub fn f()->&\'static str{T}',
    'mod m { pub static X: u64 = 7; } pub fn f()->u64{ m::X + crate::m::X }',
])
def test_initializers_may_read_consts_and_immutable_statics(source):
    assert 'define' in compile_source(source).ir_text


REJECTED = (
    ('pub static mut C: u32 = 0; pub fn f()->u32{ C }', 'requires an unsafe block', 'E0133'),
    ('pub static mut C: u32 = 0; pub fn f(){ C = 1; }', 'requires an unsafe block', 'E0133'),
    ('pub static C: u32 = 0; pub fn f(){ unsafe { C = 1; } }', 'cannot assign to immutable static item', 'E0594'),
    ('pub static A: u32 = 1; pub fn f(){ let A = 2; }', 'let bindings cannot shadow statics', 'E0530'),
    ('pub static A: u32 = 1; pub fn f(){ A(); }', 'expected function, found static', 'E0618'),
    ('pub static A: u32 = 1; pub fn A(){}', 'defined multiple times', 'E0428'),
    ('pub static A: u32 = 4294967295 + 1;', 'constant integer overflow', 'E0080'),
)


@pytest.mark.parametrize('source,message,code', REJECTED, ids=[row[2] + ':' + row[1] for row in REJECTED])
def test_invalid_statics_are_rejected_like_rustc(source, message, code):
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
    ('static mut M: u32 = 1; const B: u32 = M;', 'requires an unsafe block'),
    ('static A: (u32, u32) = (1, 2);', 'static items of type'),
    ('pub fn f(){ static N: u32 = 1; }', 'block-scoped const/static items'),
])
def test_unowned_static_forms_are_explicit(source, message):
    with pytest.raises(RustError, match=message):
        compile_source(source)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
def test_fixture_runs_natively_with_owned_compile_assemble_and_link(tmp_path, monkeypatch):
    assert run_owned_str_function(FIXTURE, 'answer', tmp_path, monkeypatch) == b'statics agree'


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_build_of_the_fixture_agrees(tmp_path):
    assert run_rustc_str_function(FIXTURE, 'answer', tmp_path) == b'statics agree'


@pytest.mark.parametrize('target', ['x86_64-unknown-linux-gnu', 'arm64-apple-darwin'])
def test_fixture_cross_emits_both_targets(target):
    assert compile_source(FIXTURE, target_triple=target).emit_object()
