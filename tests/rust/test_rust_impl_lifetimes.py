"""Lifetime parameters of impl blocks, erased behind the actual rustc check.

Rust Reference: https://doc.rust-lang.org/reference/items/implementations.html#generic-implementations
and https://doc.rust-lang.org/reference/items/generics.html. An impl's
lifetime parameters only constrain regions, which the matching actual rustc
check proves; PCC records and erases them. Without that record they are an
explicit gap, as are lifetime parameters of struct and enum declarations.
tests/rust/programs/certified_impl_lifetimes.rs executes through
test_rust_certified_references.py against the native rustc reference.
"""
import pytest

from pcc.frontends.rust import RustError
from pcc.frontends.rust.syntax import Parser
from tests.rust.analysis_only import analyze_source


@pytest.mark.parametrize('text,lifetimes,parameters', [
    ("struct T{n:u64} impl<'a> T{fn get(&self)->u64{self.n}}", ("'a",), ()),
    ("struct T{n:u64} trait G{fn get(&self)->u64;} impl<'a,'b> G for T{fn get(&self)->u64{self.n}}",
     ("'a", "'b"), ()),
    ("struct W<T>{v:T} impl<'a,T> W<T>{fn get(&self)->u64{0}}", ("'a",), ('T',)),
])
def test_impl_lifetime_parameters_are_recorded_apart_from_type_parameters(text, lifetimes, parameters):
    parser = Parser(text)
    parser.parse()
    implementation = parser.implementations[-1]
    assert tuple(token.text for token in implementation.lifetime_parameters) == lifetimes
    assert tuple(token.text for token in implementation.type_parameters) == parameters


def test_uncertified_impl_lifetimes_are_an_explicit_gap():
    with pytest.raises(RustError, match="1:22: explicit impl lifetimes require a matching external rustc check"):
        analyze_source("struct T{n:u64} impl<'a> T{fn get(&self)->u64{self.n}}")


@pytest.mark.parametrize('text', ["struct T<'a>{n:&'a u64}", "enum E<'a>{A(&'a u64)}"])
def test_data_declaration_lifetimes_stay_an_explicit_gap(text):
    # They now parse (tests/rust/test_rust_lifetime_arguments.py); without an
    # actual rustc check they remain an explicit semantic boundary.
    Parser(text).parse()
    with pytest.raises(RustError, match='lifetime parameters on data declarations require a matching external rustc check'):
        analyze_source(text)
