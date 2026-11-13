"""Free const items: `const NAME: Type = expr;`.

https://doc.rust-lang.org/reference/items/constant-items.html
https://doc.rust-lang.org/reference/const_eval.html
Integer and bool const items are evaluated once, at their declaration, from
literals, unary/binary operators, casts and other const items (in any order);
each use denotes the value. They live in the value namespace, resolve through
modules, `use` and paths with ordinary privacy, may give array lengths, and
are gated by cfg like other items. Every rejected source below was checked
against local rustc 1.92.0, whose diagnostic code is recorded beside it.
"""
import platform
import shutil
import subprocess
import sys

import pytest

from pcc.frontends.rust import CfgOptions, RustError, compile_rust
from pcc.frontends.rust.syntax import ConstItem, Parser
from tests.rust.test_rust_frontend import test_development_only_rustc_checks_the_same_source as _rustc
from tests.rust.test_rust_patterns import test_original_match_fixture_full_width_owned_execution as _execute
from tests.rust.test_rust_targets import test_original_scalar_programs_cross_emit_arm64_macho_without_source_rewrite as _cross


def compile_source(source, **options):
    return compile_rust(source, experimental_rustc_checked=True, **options)


def test_const_items_parse_into_the_value_namespace():
    parser = Parser('/// Lock format.\npub const VERSION: u32 = 1;\nmod m{const H: bool = true;}\nfn f(){}')
    functions = parser.parse()
    const, = parser.consts
    assert isinstance(const, ConstItem) and const.public is True
    assert (const.token.text, const.ty, const.expression.kind) == ('VERSION', 'u32', 'integer')
    assert const.documentation[0].text == ' Lock format.'
    assert parser.modules[0].body.consts[0].token.text == 'H'
    assert [f.token.text for f in functions] == ['f']


ACCEPTED = (
    ('const VERSION: u32 = 1; pub fn f()->u32{VERSION + 1}', None),
    ('const A: u64 = B * 2; const B: u64 = 0x10; pub fn f()->u64{A}', None),
    ('const T: bool = A > 3 && !false; const A: i32 = -5 + 10; pub fn f()->bool{T}', None),
    ('const A: i64 = -9223372036854775808; pub fn f()->i64{A}', None),
    ('const A: u32 = 4; const B: u32 = A / 2 - 1 % 3; pub fn f()->u32{B}', None),
    ('const A: usize = 3; pub fn f()->[u32; A]{ [0; A] }', None),
    ('const A: usize = 1; pub fn f()->u32{ let a = [5u32, 6, 7]; a[A] }', None),
    ('mod m{ pub const X: u64 = 7; } use m::X; pub fn f()->u64{ X + m::X + crate::m::X }', None),
    ('mod m { pub const N: usize = 2; } pub fn f()->[u64; m::N]{ [1; crate::m::N] }', None),
    ('const A: u32 = 1; pub fn f(x: u32)->u32{ x + self::A + crate::A }', None),
)


@pytest.mark.parametrize('source,_', ACCEPTED)
def test_const_items_fold_to_their_values_at_each_use(source, _):
    assert 'define' in compile_source(source).ir_text


def test_the_folded_value_reaches_the_ir():
    ir = compile_source('const A: u64 = 0x10 * 3 + B; const B: u64 = 2; pub fn f()->u64{A}').ir_text
    assert 'ret i64 50' in ir


REJECTED = (
    # E0080: attempt to compute `u32::MAX + 1_u32`, which would overflow
    ('const A: u32 = 4294967295 + 1;', 'constant integer overflow', 'E0080'),
    ('const A: u32 = 1 - 2;', 'constant integer overflow', 'E0080'),
    # E0080: attempt to divide by zero
    ('const A: i32 = 5 / 0;', 'divisor must be a nonzero constant', 'E0080'),
    # E0391: cycle detected when simplifying constant
    ('const A: u32 = B; const B: u32 = A;', 'cycle detected', 'E0391'),
    # E0005: refutable pattern in local binding / function argument
    ('const A: u32 = 1; pub fn f(){ let A = 2; }', 'names a constant', 'E0005'),
    ('const A: u32 = 1; pub fn f(A: u32){}', 'names a constant', 'E0005'),
    # E0618: expected function, found `u32`
    ('const A: u32 = 1; pub fn f(){ A(); }', 'expected function, found constant', 'E0618'),
    ('mod m{ pub const A: u32 = 1; } pub fn f(){ m::A(); }', 'expected function, found constant', 'E0618'),
    # E0428: the name `A` is defined multiple times
    ('const A: u32 = 1; fn A(){}', 'defined multiple times', 'E0428'),
    ('mod m{ const A: u32 = 1; fn A(){} }', "duplicate value-namespace item 'A'", 'E0428'),
    # E0603: constant `H` is private
    ('mod m{ const H: u64 = 1; } pub fn f()->u64{ m::H }', "private item 'H'", 'E0603'),
    # E0070: invalid left-hand side of assignment
    ('const A: u32 = 1; pub fn f(){ A = 2; }', 'assignment target', 'E0070'),
    # E0425: cannot find value `B` in module `m`
    ('mod m{} pub fn f()->u32{ m::B }', "cannot find value 'B' in module 'm'", 'E0425'),
)


@pytest.mark.parametrize('source,message,code', REJECTED)
def test_invalid_const_items_and_uses_are_rejected_like_rustc(source, message, code, monkeypatch):
    from pcc.frontends.rust.lowering import Lowerer
    monkeypatch.setattr(Lowerer, 'lower', lambda *a: pytest.fail('invalid const reached IR'))
    with pytest.raises(RustError, match=message):
        compile_source(source)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_rejects_the_same_const_sources_with_the_recorded_codes(tmp_path):
    for index, (source, _, code) in enumerate(REJECTED):
        path = tmp_path / f'case{index}.rs'
        path.write_text(source + '\n')
        run = subprocess.run([shutil.which('rustc'), '--crate-type=lib', '--edition=2021', '--emit=metadata',
                              str(path), '-o', str(tmp_path / f'case{index}.rmeta')],
                             capture_output=True, text=True, timeout=30)
        assert run.returncode != 0 and f'error[{code}]' in run.stderr, (source, run.stderr)


@pytest.mark.parametrize('source,message', [
    # rustc accepts these; they remain explicit PCC gaps.
    ('const A: u32 = 1; pub fn f(x: u32)->u32{ match x { A => 1, _ => 2 } }', 'constant patterns'),
    ('const A: (u32, u32) = (1, 2);', 'const items of type'),
    ('const A: u32 = f(); fn f()->u32{1}', 'calls, blocks and other expressions'),
    ('const _: u32 = 1;', 'unnamed const items'),
    ('pub fn f(){ const N: u32 = 1; }', 'block-scoped const items'),
    ('pub fn f()->u32{ const { 1 } }', 'inline const blocks'),
])
def test_unowned_const_forms_are_explicit_gaps(source, message):
    with pytest.raises(RustError, match=message):
        compile_source(source)


def test_const_items_follow_cfg_like_other_items():
    source = ('#[cfg(feature="fast")] const MODE: u32 = 1;\n'
              '#[cfg(not(feature="fast"))] const MODE: u32 = 2;\n'
              '#[cfg(feature="slow")] const BROKEN: u32 = 4294967295 + 1;\n'
              'pub fn f()->u32{MODE}')
    for features, value in ((('feature="fast"',), 1), ((), 2)):
        options = CfgOptions(cfg=features, debug_assertions=False, panic='abort')
        assert f'ret i32 {value}' in compile_source(source, cfg=options).ir_text


PROGRAMS = ['const_items']


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if sys.platform.startswith('linux') and platform.machine() in {'x86_64', 'AMD64'} else 'requires Linux x86-64 owned execution')
@pytest.mark.parametrize('name', PROGRAMS)
def test_const_item_fixture_owned_execution(name, tmp_path, monkeypatch):
    _execute(name, tmp_path, monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.parametrize('name', PROGRAMS)
def test_const_item_fixture_cross_emits_darwin(name, monkeypatch):
    _cross(name, monkeypatch)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
@pytest.mark.parametrize('name', PROGRAMS)
def test_development_only_rustc_checks_const_item_fixture(name, tmp_path):
    _rustc(name, tmp_path)
