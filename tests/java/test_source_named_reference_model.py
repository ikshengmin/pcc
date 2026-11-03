"""Source-owned nominal references only; no String/managed native execution.

JLS21 4.3, 4.12.5, 5.1.5, 6.5.5, 8.3, 8.4, 15.21.3 and 16:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-4.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-6.html#jls-6.5.5
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.21.3
The JDK observes the exact complete originals separately. PCC only checks their
source models; neither source descriptors nor null defaults supply a GC ABI.
All new obligations are authored for CI and execution is UNRUN.
"""
from pathlib import Path

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_source, compile_sources
from pcc.frontends.java.source_class_model import ClassReferenceFieldSlot, ReceiverType
from pcc.frontends.java.source_syntax import SourceParser
from pcc.frontends.java.source_types import ClassType, NamedType
from scripts.ci_java_primes_project import PRODUCTION, load_inputs, original_sources
from tests.java.test_source_array_model import check_originals_reference
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_unicode_escapes import JDK_REASON


def original(members, body):
    return "class ArrayExample{" + members + "int run(){" + body + "}}"


PROGRAMS = [
    ("default-null", original("ArrayExample value;", "return value==null?42:0;")),
    ("explicit-null", original("ArrayExample value=null;", "return this.value==null?42:0;")),
    ("self-initializer", original("ArrayExample value=this;", "return this==value?42:0;")),
    ("constructor-reference", original("ArrayExample value;ArrayExample(){this(null);}ArrayExample(ArrayExample value){this.value=value;}", "return value==null?42:0;")),
    ("field-rebind", original("ArrayExample value;", "value=this;return value==this?42:0;")),
    ("explicit-field-rebind", original("ArrayExample value;", "this.value=this;return value==this?42:0;")),
    ("grouped-field-rebind", original("ArrayExample value;", "((this.value))=this;return value==this?42:0;")),
    ("local-reference", original("", "ArrayExample first=this,second=first;return second==this?42:0;")),
    ("final-local-reference", original("", "final ArrayExample first=this;return first==this?42:0;")),
    ("reference-return", original("ArrayExample self(){return this;}", "return self()==this?42:0;")),
    ("reference-parameter", original("static ArrayExample id(ArrayExample value){return value;}", "return id(this)==this?42:0;")),
    ("null-parameter", original("static ArrayExample id(ArrayExample value){return value;}", "return id(null)==null?42:0;")),
    ("null-conditional", original("", "ArrayExample value=true?this:null;return value==this?42:0;")),
    ("null-reverse-conditional", original("", "ArrayExample value=false?null:this;return value==this?42:0;")),
    ("qualified-forward-default", original("ArrayExample first=this.later;ArrayExample later=this;", "return first==null&&later==this?42:0;")),
    ("reference-initializer-block", original("ArrayExample value;{value=this;}", "return value==this?42:0;")),
    ("forward-simple-write", original("{value=this;}ArrayExample value;", "return value==this?42:0;")),
    ("reference-result-null", original("ArrayExample self(){return null;}", "return self()==null?42:0;")),
]

INVALID = [
    ("integer-to-reference", original("ArrayExample value=42;", "return 42;"), "incompatible source reference assignment"),
    ("reference-to-integer", original("", "int value=this;return value;"), "integer value required"),
    ("wrong-return", original("ArrayExample value(){return 42;}", "return 42;"), "incompatible source reference assignment"),
    ("wrong-argument", original("void accept(ArrayExample value){}", "accept(42);return 42;"), "unresolved"),
    ("unassigned-reference", original("", "ArrayExample value;return value==null?42:0;"), "not definitely assigned"),
    ("final-reference-rebind", original("", "final ArrayExample value=this;value=null;return 42;"), "cannot assign"),
    ("final-parameter-rebind", original("void accept(final ArrayExample value){value=null;}", "return 42;"), "cannot assign"),
    ("forward-reference-read", original("ArrayExample first=later;ArrayExample later;", "return 42;"), "forward or self reference"),
    ("static-this", original("static ArrayExample self(){return this;}", "return 42;"), "static context"),
    ("duplicate-reference-signature", original("void accept(ArrayExample x){}void accept(ArrayExample y){}", "return 42;"), "duplicate method signature"),
    ("reference-arithmetic", original("", "return this+1;"), "integer value required"),
    ("reference-compound", original("", "ArrayExample value=this;value+=1;return 42;"), "compound arithmetic"),
    ("null-is-not-constant-flow", original("", "ArrayExample value=null;int answer;if(value==null){answer=42;}return answer;"), "not definitely assigned"),
]


@pytest.mark.parametrize("name,text", PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_exact_reference_originals_have_source_models_without_layout(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("model invoked native lowering"))
    inputs = {"ArrayExample.java": text}
    project = analyze_class_sources(inputs)
    model = project.models["ArrayExample"]
    assert inputs == {"ArrayExample.java": text}
    assert not model.runtime_registered
    reference_slots = [slot for slot in model.fields if isinstance(slot, ClassReferenceFieldSlot)]
    if reference_slots:
        assert model.payload_size is model.payload_alignment is None
        assert all(slot.offset is slot.size is slot.alignment is slot.default_value is None for slot in reference_slots)
        assert all(slot.field.dtype == ClassType("ArrayExample") for slot in reference_slots)
    assert all(method.owner == "ArrayExample" for method in model.source.methods)


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_exact_invalid_reference_originals_reject_as_java_errors(name, text, message):
    with pytest.raises(JavaError, match=message) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.parametrize("name,text", PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_source_reference_model_does_not_enable_native_lowering(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("managed reference reached scalar IR"))
    with pytest.raises(UnsupportedJava):
        compile_source(text, filename="ArrayExample.java")


@pytest.mark.parametrize("text,code", [
    ("class A{static A run(){return null;}}", "declared-result-type"),
    ("class A{static int run(A value){return 42;}}", "declared-parameter-type"),
    ("class A{static int run(){A value=null;return 42;}}", "declared-local-type"),
    ("class A{static final A value=null;static int run(){return 42;}}", "declared-result-type"),
])
def test_static_named_declarations_hit_explicit_native_gate_before_any_lowering(text, code, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("managed declaration constructed native lowerer"))
    for invoke in (lambda: compile_source(text, filename="A.java"),
                   lambda: compile_sources({"A.java": text}, entry_class="A")):
        with pytest.raises(UnsupportedJava) as caught:
            invoke()
        assert caught.value.code == "java.unsupported." + code


def test_unbraced_named_local_declarations_remain_invalid_java_not_a_capability_gap():
    text = "class A{static int run(){if(true) A value=null;return 42;}}"
    for invoke in (lambda: compile_source(text, filename="A.java"),
                   lambda: analyze_class_sources({"A.java": text})):
        with pytest.raises(JavaError, match="a local declaration requires a block") as caught:
            invoke()
        assert type(caught.value) is JavaError and caught.value.code is None


def test_named_declaration_tokens_and_canonical_method_descriptors_are_distinct():
    text = "class ArrayExample{ArrayExample copy(ArrayExample value){return value;}}"
    parsed = SourceParser(text, "Original.java", class_model=True).parse()
    assert isinstance(parsed.methods[0].result, NamedType)
    token, = parsed.methods[0].result.tokens
    assert (token.filename, token.line, token.column, token.text) == ("Original.java", 1, 20, "ArrayExample")
    project = analyze_class_sources({"Original.java": text})
    method, = project.classes["ArrayExample"].methods
    assert method.result == ClassType("ArrayExample")
    assert method.parameter_types == (ClassType("ArrayExample"),)
    assert method.descriptor == "(LArrayExample;)LArrayExample;"
    assert ReceiverType("ArrayExample") == method.result


def test_declared_types_follow_import_and_same_package_precedence_not_string_spelling():
    sources = {
        "Main.java": "package app;import provider.String;class Main{String value;String get(String value){return value;}}",
        "String.java": "package provider;public class String{}",
        "OtherString.java": "package app;class String{}",
    }
    project = analyze_class_sources(sources)
    model = project.models["app/Main"]
    assert model.fields[0].field.dtype == ClassType("provider/String")
    assert model.source.methods[0].descriptor == "(Lprovider/String;)Lprovider/String;"
    own = analyze_class_sources({"String.java": "class String{String self(){return this;}}"})
    assert own.classes["String"].methods[0].result == ClassType("String")
    same = analyze_class_sources({"A.java": "package app;class A{String value;}",
                                 "String.java": "package app;class String{}"})
    assert same.models["app/A"].fields[0].field.dtype == ClassType("app/String")


def test_distinct_nominal_types_and_inaccessible_source_dependencies_are_not_merged():
    with pytest.raises(JavaError, match="incompatible source reference assignment"):
        analyze_class_sources({"A.java": "class A{A value;void set(B other){value=other;}}", "B.java": "class B{}"})
    with pytest.raises(JavaError, match="not accessible"):
        analyze_class_sources({"A.java": "package p;import q.B;class A{B value;}",
                               "B.java": "package q;class B{}"})
    with pytest.raises(JavaError, match="ambiguous source-reference"):
        analyze_class_sources({"A.java": "class A{void accept(A a){}void accept(B b){}void call(){accept(null);}}",
                               "B.java": "class B{}"})


def test_any_nominal_reference_keeps_the_entire_mixed_payload_layout_unknown():
    project = analyze_class_sources({"A.java": "class A{int before;A next;long after;A self(){return this;}}"})
    model = project.models["A"]
    assert model.payload_size is model.payload_alignment is None
    assert [slot.offset for slot in model.fields] == [None, None, None]
    assert [slot.size for slot in model.fields] == [4, None, 8]
    assert isinstance(model.fields[1], ClassReferenceFieldSlot)
    assert model.source.methods[0].result == ClassType("A")
    assert not model.runtime_registered


def test_cross_class_static_calls_preserve_nominal_parameter_and_result_identity():
    inputs = {"A.java": "class A{static A call(){return B.id(null);}}",
              "B.java": "class B{static A id(A value){return value;}}"}
    project = analyze_class_sources(inputs)
    caller = project.classes["A"].methods[0]
    target = project.classes["B"].methods[0]
    call = caller.body.children[0].children[0]
    assert call.symbol is target and call.dtype == caller.result == ClassType("A")
    assert target.descriptor == "(LA;)LA;"


def test_even_unused_reference_signatures_require_a_supplied_source_dependency():
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"A.java": "class A{static int run(){return 42;}Missing unused(){return null;}}"})
    assert caught.value.code == "java.unsupported.source-dependency"


@pytest.mark.parametrize("text,code", [
    ("class ArrayExample{Missing value;}", "source-dependency"),
    ("class ArrayExample{ArrayExample[] value;}", "array-model-shape"),
    ("class ArrayExample{final ArrayExample value=this;}", "final-instance-fields"),
    ("class ArrayExample{static final ArrayExample value=null;}", "static-initialization"),
    ("class ArrayExample{ArrayExample value;int read(){return value.hashCode();}}", "object-expressions"),
    ("class ArrayExample{ArrayExample value(){return (ArrayExample)null;}}", "object-expressions"),
    ("class ArrayExample{p.Other value;}", "implicit-type-metadata"),
    ("class ArrayExample{void method(){p.Other value=null;}}", "implicit-type-metadata"),
    ("class ArrayExample{ArrayExample<String> value;}", "generic-declarations"),
    ("class ArrayExample{void method(){ArrayExample<String> value;}}", "generic-declarations"),
    ("package java.lang;class String{}", "string-literals"),
])
def test_missing_managed_and_wider_reference_contracts_remain_explicit(text, code):
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"Original.java": text})
    assert caught.value.code == "java.unsupported." + code


def test_original_primes_string_field_is_retained_without_fabricating_bootstrap_string():
    _, files = load_inputs()
    sources = original_sources(files, PRODUCTION)
    first = PRODUCTION[0]
    seen = []

    class ObserveTypeNames(SourceParser):
        def declaration_type(self, token):
            dtype = super().declaration_type(token)
            if isinstance(dtype, NamedType):
                seen.append(dtype)
            return dtype

    before = dict(sources)
    # Complete, unchanged original reaches a later grammar boundary. This
    # observation is not class-model or whole-project acceptance.
    with pytest.raises(JavaError):
        ObserveTypeNames(sources[first], first, class_model=True).parse()
    assert str(seen[0]) == "String"
    token = seen[0].tokens[0]
    assert (token.filename, token.line, token.column) == (first, 31, 18)
    assert sources == before


def test_string_declaration_retains_literal_data_and_unresolved_bootstrap_obligations():
    text = 'class Declared{static final String MESSAGE="original";}'
    parsed = SourceParser(text, "Declared.java", class_model=True).parse()
    field, = parsed.fields
    assert isinstance(field.dtype, NamedType) and str(field.dtype) == "String"
    assert field.initializer.kind == "string"
    assert field.initializer.value == tuple(map(ord, "original"))
    assert field.initializer.dtype is field.initializer.constant is None
    project = analyze_class_sources({"Declared.java": text})
    assert not project.string_context_complete
    assert project.compiler_context_obligations
    assert project.classes["Declared"].fields[0].string_constant.units == tuple(map(ord, "original"))


def test_original_missing_string_declaration_is_only_a_dependent_model_not_native_acceptance():
    text = "class ArrayExample{String value;}"
    project = analyze_class_sources({"Original.java": text})
    model = project.models["ArrayExample"]
    assert not project.string_context_complete
    assert model.compiler_context_obligations and not model.runtime_registered
    assert model.payload_size is model.payload_alignment is None


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-array-model-reference")
def test_same_named_reference_originals_have_independent_jdk_semantic_obligations(tmp_path, request):
    check_originals_reference(tmp_path, request, PROGRAMS, [], INVALID,
                              invocation="new ArrayExample().run()", source_input=Path(__file__))
