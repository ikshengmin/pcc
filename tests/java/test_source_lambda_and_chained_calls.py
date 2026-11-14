"""Lambda expressions and method calls on expression results, as syntax.

JLS21 15.12 and 15.27:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.27
In the class model `Primary . Identifier ( args )` (for example
`set.stream().reduce(...)`) and lambda expressions (`x -> e`,
`(a, b) -> e`, `(int a, long b) -> { ... }`, `() -> e`) parse. A method on a
value's reference type needs the managed receiver domain and a lambda needs
functional-interface typing and managed function objects, so the checker
reports both at java.unsupported.object-expressions (after checking the
receiver); mixing inferred and declared lambda parameters is an error. The
native domain keeps its earlier boundary. Commons Numbers Primes' SmallPrimes
static block (`primeNumbers.stream().reduce(1, (a, b) -> a * b)`,
projects/commons-numbers-1.2, Apache-2.0) now parses completely; the sources
below are independently authored originals.
"""
import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_source
from pcc.frontends.java.source_syntax import SourceParser
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner


def initializer(expression):
    program = SourceParser("class A{static int run(){int r=" + expression + ";return r;}}", "A.java",
                           class_model=True).parse()
    return program.methods[0].body.children[0].children[0]


def test_chained_calls_keep_receiver_and_arguments():
    node = initializer("f().g(1, 2).h()")
    assert node.kind == "method_call" and node.value == "h"
    receiver = node.children[0]
    assert receiver.kind == "method_call" and receiver.value == "g" and len(receiver.children) == 3
    assert receiver.children[0].kind == "call" and receiver.children[0].value == "f"


@pytest.mark.parametrize("text,parameters,body", [
    ("x -> x + 1", [("x", None)], "binary"),
    ("(a, b) -> a * b", [("a", None), ("b", None)], "binary"),
    ("(int a, final long b) -> { return 1; }", [("a", "int"), ("b", "long")], "block"),
    ("() -> 42", [], "integer"),
])
def test_lambdas_keep_parameters_and_body(text, parameters, body):
    node = initializer("g(" + text + ")").children[0]
    assert node.kind == "lambda" and [(name.text, dtype) for name, dtype in node.value] == parameters
    assert node.children[0].kind == body


def test_groups_and_casts_are_not_lambdas():
    assert initializer("(1 + 2) * 3").kind == "binary"
    assert initializer("(int) 4L").kind == "cast"


@pytest.mark.parametrize("text", [
    "class A{static int f(){return 1;}static int run(){return f().hashCode();}}",
    "class A{static int run(){return g(x -> x);}static int g(int x){return x;}}",
])
def test_checker_reports_the_object_expression_boundary(text):
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"A.java": text})
    assert caught.value.code == "java.unsupported.object-expressions"


def test_receiver_is_checked_before_the_boundary():
    with pytest.raises(JavaError, match="missing") as caught:
        analyze_class_sources({"A.java": "class A{static int run(){return missing().hashCode();}}"})
    assert not isinstance(caught.value, UnsupportedJava)


def test_mixed_lambda_parameters_are_an_error():
    with pytest.raises(JavaError, match="all inferred or all declared") as caught:
        initializer("g((a, int b) -> a)")
    assert not isinstance(caught.value, UnsupportedJava)


def test_native_domain_keeps_its_boundary(monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    with pytest.raises(UnsupportedJava):
        compile_source("class A{static int f(){return 1;}static int run(){return f().hashCode();}}", filename="A.java")
