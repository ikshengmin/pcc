"""`&'static str` values: string literals as runtime data.

https://doc.rust-lang.org/reference/tokens.html#string-literals
https://doc.rust-lang.org/reference/types/textual.html
A string literal (and env!) is a `&'static str`: PCC stores its UTF-8 bytes
once in private readonly data and passes a (pointer, length) pair, the pair
rustc returns in the first two integer registers. Literals are the only
source of such a value and no borrow of a local can produce one, so this
exact type needs no lifetime analysis; `&str` with an elided or named
lifetime, `&mut str` and borrows stay behind the checked-source boundary.
Operations needing core (`==`, `len`, slicing) remain explicit gaps.
"""
import shutil
from pathlib import Path

import pytest

from pcc.frontends.rust import RustError, compile_rust
from tests.rust.str_execution import UNAVAILABLE, run_owned_str_function, run_rustc_str_function

FIXTURE = (Path(__file__).parent / 'programs' / 'static_str.rs').read_text()


def compile_source(source, **options):
    return compile_rust(source, experimental_rustc_checked=True, **options)


def test_literal_bytes_are_stored_once_with_a_non_null_empty_string():
    ir = compile_source('pub fn a()->&\'static str{"\\u{e9}x"} pub fn b()->&\'static str{"\\u{e9}x"} '
                        'pub fn c()->&\'static str{""}').ir_text
    globals_ = [line for line in ir.splitlines() if line.startswith('@__pcc_rust_str')]
    assert globals_ == [
        '@__pcc_rust_str_0 = private constant [3 x i8] c"\\C3\\A9\\78"',
        '@__pcc_rust_str_value_0 = private constant { ptr, i64 } '
        '{ ptr getelementptr inbounds ([3 x i8], ptr @__pcc_rust_str_0, i32 0, i32 0), i64 3 }',
        '@__pcc_rust_str_1 = private constant [1 x i8] c"\\00"',
        '@__pcc_rust_str_value_1 = private constant { ptr, i64 } '
        '{ ptr getelementptr inbounds ([1 x i8], ptr @__pcc_rust_str_1, i32 0, i32 0), i64 0 }',
    ]


@pytest.mark.parametrize('source', [
    'pub fn f() -> &\'static str { "0.13.7" }',
    'fn pick(f: bool) -> &\'static str { if f { "yes" } else { "no" } } pub fn g() -> &\'static str { (pick(true), pick(false)).1 }',
    'struct Named { name: &\'static str } pub fn g() -> &\'static str { Named { name: "uv" }.name }',
    'const NAME: &str = "uv"; const ALIAS: &\'static str = NAME; pub fn g() -> &\'static str { ALIAS }',
    'pub fn g(s: &\'static str) -> &\'static str { s }',
    'pub fn g() -> [&\'static str; 2] { ["a", "b"] }',
])
def test_static_str_values_are_copy_data_without_a_checked_source(source):
    assert 'define' in compile_source(source).ir_text


@pytest.mark.parametrize('source', [
    # Former rows of test_rust_doc_attributes, moved verbatim: a string
    # literal statement is now an ordinary `&'static str` value.
    'fn f(){"hello";}',
    'fn f(){r#"hello"#;}',
])
def test_former_runtime_string_rows_now_compile(source):
    assert 'define' in compile_source(source).ir_text


@pytest.mark.parametrize('source,message', [
    # Every other reference form keeps the checked-source boundary.
    ('pub fn g(s: &str) -> u64 { 1 }', 'reference-bearing Rust source requires an actual'),
    ('pub fn g<\'a>(s: &\'a str) -> u64 { 1 }', 'reference-bearing Rust source requires an actual'),
    ('pub fn g() -> u64 { let s = &"x"; 1 }', 'reference-bearing Rust source requires an actual'),
    # Operations that need core's str/PartialEq model.
    ('pub fn g() -> bool { "a" == "a" }', "operator '==' does not accept &'static str"),
    ('pub fn g() -> u64 { "abc".len() as u64 }', 'unknown inherent or concrete trait method'),
    ('pub fn g() -> u64 { "abc" as u64 }', 'supported casts'),
    ('pub fn g() -> u64 { match "a" { "a" => 1, _ => 2 } }', 'reference, range'),
])
def test_other_references_and_str_operations_stay_explicit(source, message):
    with pytest.raises(RustError, match=message):
        compile_source(source)


@pytest.mark.parametrize('function,expected', [
    ('answer', 'tab\tquote"é\U0001F600\\é'.encode()),
    ('short', b'uv'),
    ('empty', b''),
])
@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=UNAVAILABLE)
def test_fixture_runs_natively_with_owned_compile_assemble_and_link(function, expected, tmp_path, monkeypatch):
    assert run_owned_str_function(FIXTURE, function, tmp_path, monkeypatch) == expected


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
@pytest.mark.parametrize('function', ['answer', 'short', 'empty'])
def test_rustc_prints_the_same_bytes(function, tmp_path):
    expected = {'answer': 'tab\tquote"é\U0001F600\\é'.encode(), 'short': b'uv', 'empty': b''}[function]
    assert run_rustc_str_function(FIXTURE, function, tmp_path) == expected


@pytest.mark.parametrize('target', ['x86_64-unknown-linux-gnu', 'arm64-apple-darwin'])
def test_fixture_cross_emits_both_targets(target):
    assert compile_source(FIXTURE, target_triple=target).emit_object()
