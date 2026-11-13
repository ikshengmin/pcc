"""Outlives bounds on struct and enum type parameters.

https://doc.rust-lang.org/reference/items/generics.html
https://doc.rust-lang.org/reference/trait-bounds.html#lifetime-bounds
`struct Convert<'a, I, T: 'a>` (fallible-streaming-iterator, lib.rs:373) and
`T: 'static + 'a +` parse and are retained per type parameter. They only
constrain regions: PCC has no owned lifetime analysis, so without an actual
rustc check they are an explicit boundary, and after one they are erased (no
layout or code effect). Undeclared (E0261) and `'_` (E0637) lifetimes are
rejected; trait bounds on data declarations stay an explicit gap. Verdicts
were compared with local rustc 1.92.0; the program below runs on this host
through PCC's own compiler, assembler and linker after the actual check, and
through a development rustc build.
"""
import shutil
import subprocess
import sys

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.semantics import Checker
from pcc.frontends.rust.syntax import Parser
from pcc.frontends.rust.types import TypeApplication
from tests.rust.certification_support import certify_source, darwin_check_prerequisite
from tests.rust.str_execution import run_owned_u64_function, run_rustc_str_function

PROGRAM = '''pub struct Hold<T: 'static> { v: T }
pub enum Slot<T: 'static +> { Full(T), Empty }
fn pick(s: Slot<u64>, d: u64) -> u64 { match s { Slot::Full(v) => v, Slot::Empty => d } }
pub fn answer() -> u64 {
    let h = Hold { v: 0xfedcba9876543200u64 };
    let b: Slot<u64> = Slot::Empty;
    h.v + pick(Slot::Full(0x10u64), 1) + pick(b, 2)
}
'''
EXPECTED = 0xfedcba9876543212


def declared(source):
    parser = Parser(source)
    parser.parse()
    return {item.token.text: [(name.text, [lifetime.text for lifetime in lifetimes])
                              for name, lifetimes in item.outlives_bounds]
            for item in parser.records + parser.enums}


def test_bounds_are_retained_per_type_parameter_in_source_order():
    assert declared("struct Convert<'a, I, T: 'a>{it: I, n: u64} "
                    "enum E<'a, 'b, T: 'a + 'b +, U: 'static>{A(T), B(U)} struct Empty<T:>{v: T}") == {
        'Convert': [('T', ["'a"])],
        'E': [('T', ["'a", "'b"]), ('U', ["'static"])],
        'Empty': [],  # `T:` has no bounds and constrains nothing
    }


@pytest.mark.parametrize('source,message', [
    ("struct S<T: 'a>{v: T}", r"1:13: use of undeclared lifetime name `'a` \(rustc E0261\)"),
    ("struct S<'a, T: '_>{v: &'a T}", r"1:17: `'_` cannot be used in a bound \(rustc E0637\)"),
])
def test_invalid_bounds_are_parse_errors(source, message):
    with pytest.raises(RustError, match=message):
        Parser(source).parse()


@pytest.mark.parametrize('source', [
    "struct S<T: Copy>{v: T}",
    "struct S<'a, T: 'a + Copy>{v: &'a T}",
    "enum E<T: ?Sized>{A(u64)}",
])
def test_trait_bounds_on_data_declarations_stay_explicit(source):
    with pytest.raises(RustError, match='trait bounds on struct and enum type parameters require trait solving'):
        Parser(source).parse()


@pytest.mark.parametrize('source,position', [
    (PROGRAM, '1:20'),
    ("enum E<T: 'static>{A(T)} pub fn f()->u64{1}", '1:11'),
])
def test_without_an_actual_check_bounds_are_an_explicit_boundary(source, position):
    with pytest.raises(RustError, match=f'^{position}: outlives bounds on data declarations require a matching '
                                        'external rustc check record$'):
        compile_rust(source, experimental_rustc_checked=True)


def test_checked_semantic_model_erases_the_bound():
    """Isolated semantic model, never public certification authorization."""
    parser = Parser("struct S<T: 'static>{v: T} fn f(s: S<u64>)->u64{s.v}")
    functions = parser.parse()
    checker = Checker(functions, parser.records, parser.enums, parser.implementations, parser.traits,
                      externally_checked=True)
    checker.check()
    assert [ty for _, ty, _ in checker.names['f'].params] == [TypeApplication('S', ('u64',))]


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=darwin_check_prerequisite() or (
    None if sys.platform == 'darwin' else 'owned execution here is the arm64 Darwin lane'))
def test_checked_program_runs_with_owned_compile_assemble_and_link(tmp_path, monkeypatch):
    check = certify_source(PROGRAM, tmp_path / 'certifier', target_triple='aarch64-apple-darwin')
    assert run_owned_u64_function(PROGRAM, 'answer', tmp_path, monkeypatch,
                                  target_triple='aarch64-apple-darwin', rustc_check=check) == EXPECTED


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_gives_the_same_verdicts_and_value(tmp_path):
    output = run_rustc_str_function(PROGRAM, 'answer', tmp_path, output='&{call}.to_le_bytes()')
    assert int.from_bytes(output, 'little') == EXPECTED
    cases = (
        ("pub struct Convert<'a, I, T: 'a> { pub it: I, pub item: Option<&'a T> }", None),
        ("pub enum E<'a, 'b, T: 'a + 'b +> { A(&'a T, &'b T) }", None),
        ("pub struct S<T:> { pub v: T }", None),
        ("pub struct S<T: Copy> { pub v: T }", None),
        ("pub struct S<T: 'a> { pub v: T }", 'E0261'),
        ("pub struct S<'a, T: '_> { pub v: &'a T }", 'E0637'),
    )
    for index, (source, code) in enumerate(cases):
        path = tmp_path / f'case{index}.rs'
        path.write_text(source + '\n')
        run = subprocess.run([shutil.which('rustc'), '--crate-type=lib', '--edition=2015', '--emit=metadata',
                              str(path), '-o', str(tmp_path / f'case{index}.rmeta')],
                             capture_output=True, text=True, timeout=60)
        if code is None:
            assert run.returncode == 0, run.stderr
        else:
            assert run.returncode != 0 and f'error[{code}]' in run.stderr, run.stderr
