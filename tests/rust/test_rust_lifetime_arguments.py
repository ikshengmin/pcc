"""Lifetime generic arguments and lifetime parameters on data declarations.

https://doc.rust-lang.org/reference/paths.html#paths-in-expressions (GenericArgs)
https://doc.rust-lang.org/reference/items/generics.html
`Type<'a, T>`, `Type<'_>` and `struct S<'a>` parse and are retained. PCC has
no owned lifetime analysis: without an actual rustc check every explicit
lifetime is an explicit boundary; after one, lifetime arguments are erased
from type identity and only their arity is rechecked (E0107). Reference
fields in data remain a separate explicit gap. Each verdict below was
compared with local rustc 1.92.0.
"""
import shutil
import subprocess

import pytest

from pcc.frontends.rust import RustError, compile_rust
from pcc.frontends.rust.semantics import Checker
from pcc.frontends.rust.syntax import Parser
from pcc.frontends.rust.types import TypeApplication


def parse(source):
    parser = Parser(source)
    return parser, parser.parse()


def checked_model(source):
    """Isolated semantic model, never public certification authorization."""
    parser, functions = parse(source)
    checker = Checker(functions, parser.records, parser.enums, parser.implementations, parser.traits,
                      externally_checked=True)
    checker.check()
    return checker


def test_lifetime_arguments_are_retained_and_erased_from_type_identity():
    _, functions = parse("fn f<'a>(x: Wrapper<'a, u64>, y: Wrapper<'_, u64>, z: Wrapper<'static, u64>) {}")
    types = [ty for _, ty, _ in functions[0].params]
    assert [ty.lifetimes for ty in types] == [("'a",), ("'_",), ("'static",)]
    assert types[0] == types[1] == types[2] == TypeApplication('Wrapper', ('u64',))
    assert str(types[0]) == "Wrapper<'a, u64>"


def test_data_declarations_retain_their_lifetime_parameters():
    parser, _ = parse("struct View<'a, T>{n:T} enum Choice<'a>{A(u64)}")
    assert [[token.text for token in item.lifetime_parameters] for item in parser.records + parser.enums] == [
        ["'a"], ["'a"]]
    assert [token.text for token in parser.records[0].type_parameters] == ['T']


@pytest.mark.parametrize('source,message', [
    # rustc E0747: type provided when a lifetime was expected
    ("fn f(x: W<u64, 'a>) {}", 'lifetime arguments must be provided before type arguments'),
])
def test_misordered_lifetime_arguments_are_rejected_at_parse_time(source, message):
    with pytest.raises(RustError, match=message):
        parse(source)


@pytest.mark.parametrize('source,message', [
    ("struct S{n:u64} pub fn f(s: S<'a>)->u64{1}", 'explicit lifetime arguments require a matching external rustc check'),
    ("struct View<'a>{n:u64} pub fn f()->u64{1}", 'lifetime parameters on data declarations require a matching external rustc check'),
    ("enum E<'a>{A(u64)} pub fn f()->u64{1}", 'lifetime parameters on data declarations require a matching external rustc check'),
])
def test_without_an_actual_check_every_explicit_lifetime_is_a_boundary(source, message):
    with pytest.raises(RustError, match=message):
        compile_rust(source, experimental_rustc_checked=True)


@pytest.mark.parametrize('source,message', [
    # rustc E0107: struct takes 0 lifetime arguments but 1 lifetime argument was supplied
    ("struct S{n:u64} fn f<'a>(s: S<'a>)->u64{s.n}", "S takes 0 lifetime arguments but 1 were supplied"),
    # rustc E0107: struct takes 1 lifetime argument but 2 lifetime arguments were supplied
    ("struct S<'a>{n:u64} fn f<'a>(s: S<'a, 'a>)->u64{s.n}", "S takes 1 lifetime arguments but 2 were supplied"),
    # Reference storage in data stays its own explicit gap even after a check.
    ("struct S<'a>{n:&'a u64} fn f()->u64{1}", 'aggregate reference storage requires lifetime analysis'),
])
def test_checked_semantic_model_rechecks_arity_and_keeps_reference_storage_explicit(source, message):
    with pytest.raises(RustError, match=message):
        checked_model(source)


def test_checked_semantic_model_erases_lifetime_only_arguments():
    # Normalization only: rustc would also require the lifetime to be used
    # (E0392), which needs reference fields, still an explicit gap here.
    checker = checked_model("struct S<'a>{n:u64} fn f<'a>(s: S<'a>)->u64{s.n} fn g(s: S<'_>)->u64{f(s)}")
    assert [ty for _, ty, _ in checker.names['f'].params] == ['S']
    assert [ty for _, ty, _ in checker.names['g'].params] == ['S']


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=None if shutil.which('rustc') else 'development-only rustc is not installed')
def test_rustc_gives_the_same_verdicts(tmp_path):
    cases = (
        ("pub struct W<'a, T>(&'a T); pub fn f<'a>(_x: W<u64, 'a>) {}", 'E0747'),
        ("pub struct S{pub n:u64} pub fn f<'a>(s: S<'a>)->u64{s.n}", 'E0107'),
        ("pub struct S<'a>{pub n:&'a u64} pub fn f<'a>(s: S<'a, 'a>)->u64{*s.n}", 'E0107'),
        ("pub struct S<'a>{pub n:&'a u64} pub fn f<'a>(s: S<'a>)->u64{*s.n}", None),
        ("pub struct S<'a>{pub n:&'a u64} pub fn f(s: S<'_>)->u64{*s.n}", None),
    )
    for index, (source, code) in enumerate(cases):
        path = tmp_path / f'case{index}.rs'
        path.write_text(source + '\n')
        run = subprocess.run([shutil.which('rustc'), '--crate-type=lib', '--edition=2021', '--emit=metadata',
                              str(path), '-o', str(tmp_path / f'case{index}.rmeta')],
                             capture_output=True, text=True, timeout=60)
        if code is None:
            assert run.returncode == 0, run.stderr
        else:
            assert run.returncode != 0 and f'error[{code}]' in run.stderr, run.stderr
