"""Keep a parenthesized current receiver distinct from any arbitrary Primary."""
import os
from pathlib import Path

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_source
from pcc.frontends.java.source_types import ClassType
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner


def checked(source):
    return analyze_class_sources({"Original.java": source}).models["Case"]


def check_receiver(node, model, depth):
    primary = node.current_receiver
    for unused in range(depth):
        assert primary.kind == "group" and isinstance(primary.dtype, ClassType)
        assert primary.dtype.class_name == model.source.name
        primary = primary.children[0]
    assert primary.kind == "this" and primary.symbol is model.source


def test_current_receiver_field_and_overloaded_call_bind_by_identity(monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    source = ("class Case{int x;int f(int n){return n+1;}int f(long n){return 0;}"
              "int m(int x){((this)).x=((this)).f(x);return (this).x;}}")
    model = checked(source)
    narrow, wide, method = model.source.methods
    store, ret = method.body.children
    call, = store.children
    read, = ret.children
    assert store.kind == "field_assign" and read.kind == "instance_field"
    assert store.symbol is read.symbol is model.fields[0].field
    assert call.kind == "receiver_call" and call.symbol is narrow and call.symbol is not wide
    assert len(call.children) == 1 and call.children[0].symbol is method.locals[0]
    for node, depth in ((store, 2), (call, 2), (read, 1)):
        check_receiver(node, model, depth)
    assert not model.runtime_registered
    with pytest.raises(UnsupportedJava):
        compile_source(source, filename="Original.java")


def test_current_object_length_field_does_not_become_the_array_length_pseudo_member():
    source = ("class Case{int length;int[] values={1,2};int m(){(this).length=2;"
              "return (this).length+(this).values.length;}}")
    model = checked(source)
    store, ret = model.source.methods[0].body.children
    total, = ret.children
    scalar, array_length = total.children
    assert store.symbol is scalar.symbol is model.fields[0].field
    assert scalar.kind == "instance_field" and array_length.kind == "array_length"
    array, = array_length.children
    assert array.symbol is model.fields[1].field
    for explicit in (store, scalar, array):
        check_receiver(explicit, model, 1)


@pytest.mark.parametrize("source", [
    "class Case{int first=(this).second;int second=2;}",
    "class Case{int x;Case(int x){(this).x=x;}int m(){return (this).x;}}",
    "class Case{int x;void set(int v){x=v;}int test(){((this)).set(2);return x;}}",
    "class Case{static final int C=42;int f(){return (this).C;}}",
])
def test_original_model_accepts_current_receiver_by_field_or_method_identity(source):
    model = checked(source)
    assert not model.runtime_registered


@pytest.mark.parametrize(("source","message"), [
    ("class Case{int x;static int m(){return (this).x;}}", "static context"),
    ("class Case{int x;Case(){this((this).x);}Case(int v){x=v;}}", "before constructor invocation"),
    ("class Case{int m(){return (this).absent;}}", "undefined instance field"),
    ("class Case{int m(){return (this).absent();}}", "unresolved receiver method"),
    ("class Case{static final int K=42;byte y=(this).K;}", "incompatible assignment"),
])
def test_bad_current_receiver_keeps_language_error_not_unsupported(source, message):
    with pytest.raises(JavaError, match=message) as caught:
        checked(source)
    assert type(caught.value) is JavaError


@pytest.mark.parametrize("source", [
    "class Case{int x;void m(Case other){(other).x=2;}}",
    "class Case{int x;Case other(){return this;}void m(){(other()).x=2;}}",
    "class Case{int x;void m(boolean b,Case other){(b?this:other).x=2;}}",
    "class Case{int x;void m(){((Case)this).x=2;}}",
    "class Case{int f(){return 1;}int m(Case other){return (other).f();}}",
])
def test_alias_computation_cast_or_conditional_not_reclassified_as_this(source):
    with pytest.raises(UnsupportedJava):
        checked(source)


# The observer is a separate source unit and is never inserted into the original.
OBSERVED = ("class Case{int length;int[] v={39};void add(int n){length+=n;}"
            "int run(int length){((this)).length=length;(this).add(1);"
            "return ((this)).length+((this)).v[0];}}")


def test_same_complete_observed_program_checks_without_a_native_fallback(monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    model = checked(OBSERVED)
    assert [field.field.token.text for field in model.fields] == ["length", "v"]
    assert [method.token.text for method in model.source.methods] == ["add", "run"]
    assert not model.runtime_registered
    with pytest.raises(UnsupportedJava):
        compile_source(OBSERVED, filename="ArrayExample.java")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else
                      "select an existing development JDK with PCC_JAVA_RAW_REFERENCE")
@pytest.mark.xdist_group(name="java-grouped-current-receiver-reference")
def test_complete_grouped_receiver_program_executes_on_selected_jdk_only(tmp_path, request):
    from tests.java.test_source_array_model import check_originals_reference
    check_originals_reference(
        tmp_path, request, [("grouped-receiver-evaluates", OBSERVED)], [],
        [("before-ctor", "class Case{int x;Case(){this((this).x);}Case(int v){x=v;}}",
          "receiver cannot be used before constructor invocation"),
         ("not-constant", "class Case{static final int K=42;byte y=(this).K;}",
          "primary-qualified read is not a constant expression")],
        invocation="new Case().run(2)", source_input=Path(__file__))
