"""Parameterized reference types in the nonlowering class model.

JLS21 4.5 and 4.5.1 (type arguments and wildcards), 3.2 (a `>>` or `>>>`
token closing nested type arguments is split) and 4.3:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-4.html#jls-4.5
https://docs.oracle.com/javase/specs/jls/se21/html/jls-3.html#jls-3.2
Type arguments are parsed and retained unresolved. No erasure, capture,
parameterized-type identity or generic class is modeled; resolving the
named class still requires its supplied source, so a JDK collection type
stays a source-dependency boundary. Array type arguments, type annotations
and member types of parameterized types are explicit gaps.
"""
import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources
from pcc.frontends.java.source_syntax import SourceParser
from pcc.frontends.java.source_types import NamedType, Wildcard


def result_type(declaration):
    program = SourceParser("class A{" + declaration + "}", "A.java", class_model=True).parse()
    return program.methods[0].result


@pytest.mark.parametrize("declaration,text", [
    ("List<Integer> f(){return null;}", "List<Integer>"),
    ("Map<String,List<Integer>> f(){return null;}", "Map<String,List<Integer>>"),
    ("Map<String,Map<String,List<Integer>>> f(){return null;}", "Map<String,Map<String,List<Integer>>>"),
    ("List<?> f(){return null;}", "List<?>"),
    ("List<? extends Number> f(){return null;}", "List<? extends Number>"),
    ("Comparable<? super A> f(){return null;}", "Comparable<? super A>"),
    ("java.util.List<Integer> f(){return null;}", "java.util.List<Integer>"),
])
def test_type_arguments_are_retained_and_nested_closers_split(declaration, text):
    dtype = result_type(declaration)
    assert isinstance(dtype, NamedType) and str(dtype) == text


def test_wildcards_keep_their_bound_kind_and_bound():
    dtype = result_type("Map<? extends Number,?> f(){return null;}")
    bounded, unbounded = dtype.arguments
    assert isinstance(bounded, Wildcard) and bounded.kind == "extends" and str(bounded.bound) == "Number"
    assert isinstance(unbounded, Wildcard) and unbounded.kind is None and unbounded.bound is None


def test_split_closer_keeps_the_following_expression_tokens():
    parser = SourceParser("class A{List<List<Integer>> f(){return null;} int g(){return 8>>1;}}", "A.java",
                          class_model=True)
    program = parser.parse()
    assert [method.token.text for method in program.methods] == ["f", "g"]


@pytest.mark.parametrize("declaration,message", [
    ("List<int> f(){return null;}", "a type argument must be a reference type"),
    ("List<> f(){return null;}", "the diamond form is not a declaration type"),
    ("List<Integer f(){return null;}", "expected '>'"),
])
def test_invalid_type_arguments_are_ordinary_errors(declaration, message):
    with pytest.raises(JavaError, match=message) as caught:
        result_type(declaration)
    assert not isinstance(caught.value, UnsupportedJava)


@pytest.mark.parametrize("declaration", [
    "List<int[]> f(){return null;}",
    "List<String[]> f(){return null;}",
    "Outer<String>.Inner f(){return null;}",
])
def test_unmodeled_type_argument_shapes_are_explicit_gaps(declaration):
    with pytest.raises(UnsupportedJava) as caught:
        result_type(declaration)
    assert caught.value.code == "java.unsupported.generic-declarations"


def test_resolving_a_parameterized_jdk_type_still_requires_its_source():
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"A.java": "import java.util.List;class A{List<Integer> f(){return null;}}"})
    assert caught.value.code in {"java.unsupported.source-dependency", "java.unsupported.implicit-type-metadata"}
