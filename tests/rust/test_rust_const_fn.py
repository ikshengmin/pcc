"""`const fn` items and inherent methods, called at run time.

https://doc.rust-lang.org/reference/items/functions.html#const-functions
A const function is an ordinary function at run time. Its body's
const-context restrictions are checked by rustc, which compilation already
requires (a CheckedRustSource or the value-only attestation); PCC never
evaluates a const fn at compile time, so a call in a const initializer or
array length stays an explicit gap. Each rejected source was checked with
local rustc 1.92.0.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.syntax import Parser
from tests.rust.str_execution import UNAVAILABLE, run_owned_str_function, run_rustc_str_function

FIXTURE = (Path(__file__).parent / 'programs' / 'const_fn.rs').read_text()


def compile_source(source, **options):
    return compile_rust(source, experimental_rustc_checked=True, **options)


def test_const_functions_and_methods_are_marked_and_kept():
    parser = Parser('struct T{x:u64} impl T{pub const fn get(&self)->u64{self.x} fn plain(&self){}} '
                    'const fn a()->u64{1} const unsafe fn b(){} pub const extern "C" fn c()->u64{2} fn d(){}')
    functions = parser.parse()
    assert [(f.token.text, f.const_fn, f.unsafe, f.abi) for f in functions] == [
        ('get', True, False, 'Rust'), ('plain', False, False, 'Rust'), ('a', True, False, 'Rust'),
        ('b', True, True, 'Rust'), ('c', True, False, 'C'), ('d', False, False, 'Rust')]


@pytest.mark.parametrize('source', [
    # Former gap row of test_rust_const_items, moved verbatim.
    'const fn g()->u32{1}',
    'const fn g(x: u64)->u64{x * 2} pub fn f()->u64{g(4)}',
])
def test_const_functions_compile_as_ordinary_functions(source):
    compile_source(source)


REJECTED = (
    # E0379: functions in trait impls cannot be declared const
    ('pub struct T{pub x:u64} pub trait K{fn k(&self)->u64;} impl K for T{const fn k(&self)->u64{1}}',
     'cannot be declared const', 'E0379'),
)


@pytest.mark.parametrize('source,message,code', REJECTED)
def test_const_trait_impl_methods_are_rejected_like_rustc(source, message, code):
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
        assert run.returncode != 0 and f'error[{code}]' in run.stderr, run.stderr


@pytest.mark.parametrize('source,message', [
    # Valid Rust, explicit PCC gaps.
    ('const fn f()->u64{1} const X: u64 = f();', 'calls, blocks and other expressions'),
    ('struct T{x:u64} impl T{const N: u64 = 1;}', 'associated constants in impls'),
    ('const async fn f(){}', 'async functions'),
])
def test_compile_time_uses_and_other_forms_stay_explicit(source, message):
    with pytest.raises(RustError, match=message):
        compile_source(source)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
def test_fixture_runs_natively_with_owned_compile_assemble_and_link(tmp_path, monkeypatch):
    assert run_owned_str_function(FIXTURE, 'answer', tmp_path, monkeypatch) == b'const fn agrees'


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_build_of_the_fixture_agrees(tmp_path):
    assert run_rustc_str_function(FIXTURE, 'answer', tmp_path) == b'const fn agrees'


@pytest.mark.parametrize('target', ['x86_64-unknown-linux-gnu', 'arm64-apple-darwin'])
def test_fixture_cross_emits_both_targets(target):
    assert compile_source(FIXTURE, target_triple=target).emit_object()
