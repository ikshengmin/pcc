"""Instance primitive-array field semantics; managed native storage is NOT enabled.

JLS21 4.12.4/5, 8.3.1.2, 8.3.2/3, 10.2/6/7, 15.8.5, 15.21.3 and 15.26:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-4.html#jls-4.12.5
https://docs.oracle.com/javase/specs/jls/se21/html/jls-8.html#jls-8.3.2
https://docs.oracle.com/javase/specs/jls/se21/html/jls-10.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.8.5
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.26

These independently authored, complete sources exercise a prerequisite visible
in ZXing's Reed-Solomon fields, not an adapted/simplified ZXing acceptance input.
The JDK lane executes the exact originals through a separate allocation observer.
PCC checks their source semantics and must reject native lowering, not emulate it.
"""
import os
from pathlib import Path

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_source
from pcc.frontends.java.source_class_model import ArrayReferenceFieldSlot
from pcc.frontends.java.source_types import ArrayType
from tests.java.test_source_array_model import check_originals_reference
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner


def original(members, body):
    return "class ArrayExample{" + members + "int run(){" + body + "}}"


PROGRAMS = [
    ("default-null", original("int[] values;", "return values==null?42:0;")),
    ("explicit-null", original("int[] values=null;", "return this.values==null?42:0;")),
    ("array-initializer", original("int[] values={7,35,};", "return values[0]+this.values[1];")),
    ("zeroed-creation", original("int[] values=new int[3];", "return values[0]+values.length+39;")),
    ("declarator-brackets", original("int first[]={7},answer=35,last[]={42};", "return first[0]+answer+last[0]-42;")),
    ("common-brackets", original("int[] first={42},second=first;", "return first==second?second[0]:0;")),
    ("constructor-assignment", original("int[] values;ArrayExample(){this.values=new int[]{42};}", "return values[0];")),
    ("constructor-parameter-shadow", original("int[] values;ArrayExample(){this(new int[]{42});}ArrayExample(int[] values){this.values=values;}", "return values[0];")),
    ("field-rebind", original("int[] values={7};", "values=new int[]{42};return this.values[0];")),
    ("explicit-field-rebind", original("int[] values={7};", "this.values=new int[]{42};return values[0];")),
    # Exact original rejected by the grouped receiver-field classification.
    ("grouped-explicit-field-rebind", "class ArrayExample{int[] values={7};int run(){(this.values)=new int[]{42};return values[0];}}"),
    ("nested-grouped-explicit-field-rebind", original("int[] values={7};", "((this.values))=new int[]{42};return values[0];")),
    ("grouped-field-rebind", original("int[] values={7};", "((values))=new int[]{42};return values[0];")),
    ("grouped-receiver-local-shadow", original("int[] values={7};", "int[] values={35};((this.values))=values;return this.values[0]+7;")),
    ("grouped-receiver-constructor-shadow", original("int[] values;ArrayExample(){int[] values={42};((this.values))=values;}", "return values[0];")),
    ("grouped-receiver-initializer-shadow", original("int[] values;{int[] values={42};((this.values))=values;}", "return values[0];")),
    ("grouped-receiver-forward-write", original("{((this.values))=new int[]{42};}int[] values;", "return values[0];")),
    ("grouped-final-field-local-shadow", original("final int[] values={7};", "int[] values={0};((values))=new int[]{35};return this.values[0]+values[0];")),
    ("local-alias-is-reference", original("int[] values={7};", "int[] other=values;other[0]=42;return other==values?values[0]:0;")),
    ("fresh-array-is-not-equal", original("int[] first={42},second={42};", "return first==second?0:42;")),
    ("local-shadow", original("int[] values={35};", "int[] values={7};return values[0]+this.values[0];")),
    ("method-shadow", original("int[] values={7};int read(int[] values){return values[0]+this.values[0];}", "return read(new int[]{35});")),
    ("final-components-mutable", original("final int[] values={41};", "values[0]++;return values[0];")),
    ("grouped-final-component-mutable", original("final int[] values={41};", "((this.values[0]))++;return values[0];")),
    ("grouped-final-reference-component-mutable", original("final int[] values={7};", "((this.values))[0]+=35;return values[0];")),
    ("final-alias-components-mutable", original("final int[] values={7};", "int[] alias=values;alias[0]+=35;return values[0];")),
    ("final-null", original("final int[] values=null;", "return values==null?42:0;")),
    ("field-length", original("int[] values={7,35};", "return this.values.length+values.length+38;")),
    ("narrow-components", original("byte[] a={127};short b[]={-1};char[] c={'*'};", "a[0]+=171;return a[0]+b[0]+c[0]-41;")),
    ("boolean-and-long", original("boolean[] flags={false};long[] values={41L};", "flags[0]|=true;values[0]++;return flags[0]?(int)values[0]:0;")),
    ("ordered-initializers", original("int[] first={7};{first[0]+=1;}int[] second={first[0]+34};", "return second[0];")),
    ("forward-write-exception", original("{values=new int[]{42};}int[] values;", "return values[0];")),
    ("qualified-forward-read-default", original("int[] first=this.later;int[] later={42};", "return first==null?later[0]:0;")),
    ("return-reference", original("int[] values={7};int[] get(){return values;}", "get()[0]=42;return values[0];")),
    ("array-overload", original("int[] values={42};int read(int[] x){return x[0];}int read(long[] x){return 0;}", "return read(values);")),
    ("simple-store-effects", original("int[] values={0};", "int x;values[0]=(x=42);return x;")),
]

GROUPED_SCALAR_PROGRAMS = [
    ("grouped-scalar-receiver-store", original("int value;", "(this.value)=42;return value;")),
    ("grouped-scalar-receiver-compound", original("byte value=7;", "((this.value))+=35;return value;")),
    ("grouped-scalar-receiver-increment", original("int value=41;", "((this.value))++;return value;")),
    ("grouped-scalar-receiver-shadow", original("int value=7;", "int value=35;((this.value))=value;return this.value+7;")),
    ("grouped-scalar-qualified-forward-compound", original("{((this.value))+=42;}int value;", "return value;")),
    ("grouped-boolean-receiver-compound", original("boolean value;", "((this.value))|=true;return value?42:0;")),
]


VALID_ONLY = [
    ("null-dereference-not-folded", original("int[] values;", "return values.length;")),
    ("negative-size-not-folded", original("int[] values=new int[-1];", "return 42;")),
    ("out-of-bounds-not-folded", original("int[] values={42};", "return values[1];")),
]

INVALID = [
    ("initializer-component-mismatch", original("int[] values=new long[1];", "return 42;"), "incompatible array assignment"),
    ("initializer-scalar-mismatch", original("int[] values=42;", "return 42;"), "incompatible array assignment"),
    ("initializer-element-narrowing", original("byte[] values={128};", "return 42;"), "incompatible assignment"),
    ("assignment-component-mismatch", original("int[] values;", "values=new long[1];return 42;"), "incompatible array assignment"),
    ("explicit-assignment-mismatch", original("int[] values;", "this.values=new long[1];return 42;"), "incompatible array assignment"),
    ("final-rebind", original("final int[] values={42};", "values=null;return 42;"), "cannot assign a final"),
    ("final-explicit-rebind", original("final int[] values={42};", "this.values=null;return 42;"), "cannot assign a final"),
    ("final-grouped-explicit-rebind", original("final int[] values={42};", "((this.values))=null;return 42;"), "cannot assign a final"),
    ("final-grouped-constructor-rebind", original("final int[] values={42};ArrayExample(){((this.values))=null;}", "return 42;"), "cannot assign a final"),
    ("final-constructor-rebind", original("final int[] values={42};ArrayExample(){values=null;}", "return 42;"), "cannot assign a final"),
    ("length-is-final", original("int[] values={42};", "values.length=1;return 42;"), "length is final"),
    ("explicit-length-is-final", original("int[] values={42};", "this.values.length=1;return 42;"), "length is final"),
    ("grouped-length-is-final", original("int[] values={42};", "((this.values.length))=1;return 42;"), "length is final"),
    ("grouped-receiver-assignment-mismatch", original("int[] values;", "((this.values))=new long[1];return 42;"), "incompatible array assignment"),
    ("grouped-receiver-static-context", original("int[] values;static void clear(){((this.values))=null;}", "return 42;"), "static context"),
    ("static-context-read", original("int[] values;static int size(){return values.length;}", "return 42;"), "static context"),
    ("static-context-write", original("int[] values;static void clear(){values=null;}", "return 42;"), "static context"),
    ("forward-simple-read", original("int[] first=later;int[] later={42};", "return 42;"), "forward or self reference"),
    ("self-simple-read", original("int[] values=values;", "return 42;"), "forward or self reference"),
    ("forward-length-read", original("int size=values.length;int[] values={42};", "return 42;"), "forward or self reference"),
    ("forward-component-write-reads-reference", original("{values[0]=42;}int[] values={0};", "return 42;"), "forward reference"),
    ("grouped-forward-component-reads-reference", original("{((values)[0])=42;}int[] values={0};", "return 42;"), "forward reference"),
    ("grouped-scalar-forward-compound", original("{((value))+=42;}int value;", "return 42;"), "forward reference"),
    ("grouped-scalar-static-final-write", original("static final int K=42;", "((this.K))=7;return 42;"), "cannot assign a final"),
    ("scalar-local-shadows-array", original("int[] values={42};", "int values=0;return values.length;"), "array reference"),
    ("unassigned-local-shadows-array", original("int[] values={42};", "int[] values;return values[0];"), "not definitely assigned"),
    ("incompatible-reference-equality", original("int[] values;long[] other;", "return values==other?42:0;"), "incomparable array"),
    ("duplicate-field", original("int[] values,values;", "return 42;"), "duplicate field"),
    ("long-index", original("int[] values={42};", "return values[0L];"), "promote to int"),
    ("array-compound-assignment", original("int[] values={42};", "values+=1;return 42;"), "instance compound arithmetic"),
]


@pytest.mark.parametrize("name,text", PROGRAMS + VALID_ONLY, ids=[row[0] for row in PROGRAMS + VALID_ONLY])
def test_array_field_originals_have_checked_source_model_only(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("model invoked native lowering"))
    inputs = {"ArrayExample.java": text}
    model = analyze_class_sources(inputs).models["ArrayExample"]
    assert inputs == {"ArrayExample.java": text}
    assert any(isinstance(slot, ArrayReferenceFieldSlot) for slot in model.fields)
    assert all(slot.offset is None for slot in model.fields)
    assert model.payload_size is None and model.payload_alignment is None
    assert not model.runtime_registered


@pytest.mark.parametrize("name,text", GROUPED_SCALAR_PROGRAMS, ids=[row[0] for row in GROUPED_SCALAR_PROGRAMS])
def test_grouped_scalar_receiver_originals_use_the_same_checked_field_path(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("model invoked native lowering"))
    model = analyze_class_sources({"ArrayExample.java": text}).models["ArrayExample"]
    assert len(model.fields) == 1 and model.fields[0].offset == 0
    assert model.payload_size == model.fields[0].size
    assert not model.runtime_registered


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_array_field_originals_fail_as_java_errors(name, text, message):
    with pytest.raises(JavaError, match=message) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.parametrize("name,text", PROGRAMS + GROUPED_SCALAR_PROGRAMS + VALID_ONLY,
                         ids=[row[0] for row in PROGRAMS + GROUPED_SCALAR_PROGRAMS + VALID_ONLY])
def test_array_field_originals_still_fail_before_native_ir(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *a: pytest.fail("managed field reached scalar IR"))
    with pytest.raises(UnsupportedJava) as caught:
        compile_source(text, filename="ArrayExample.java")
    assert caught.value.code == "java.unsupported.instance-methods"


def test_grouped_receiver_keeps_field_identity_and_component_store_distinct():
    text = original("final int[] fixed={7};int[] values;",
                    "int[] values={35};((this.values))=values;((this.fixed[0]))+=this.values[0];return fixed[0];")
    model = analyze_class_sources({"ArrayExample.java": text}).models["ArrayExample"]
    local, rebind, component, _ = model.source.methods[0].body.children
    assert rebind.kind == "field_assign" and rebind.symbol is model.fields[1].field
    assert rebind.children[0].symbol is local.symbol
    target = component.children[0]
    while target.kind == "group":
        target = target.children[0]
    assert component.kind == "array_assign" and target.kind == "array_place"
    assert target.children[0].symbol is model.fields[0].field
    assert all(slot.offset is None for slot in model.fields)
    assert model.payload_size is None and not model.runtime_registered


@pytest.mark.parametrize("body,code", [
    ("(this).values=new int[]{42};return values[0];", "java.unsupported.array-model-shape"),
    ("int[] other=(this.values=new int[]{42});return other[0];", "java.unsupported.assignment-expression-target"),
])
def test_grouped_field_statement_fix_does_not_admit_new_receiver_or_expression_forms(body, code):
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"ArrayExample.java": original("int[] values;", body)})
    assert caught.value.code == code


def test_field_types_reference_identity_and_initialization_plan_are_retained():
    text = original("int a[]={7},b=35;{a[0]+=1;}final int[] c=a;", "return c==a?c[0]+b-1:0;")
    model = analyze_class_sources({"ArrayExample.java": text}).models["ArrayExample"]
    a, b, c = [slot.field for slot in model.fields]
    assert [field.dtype for field in (a, b, c)] == [ArrayType("int"), "int", ArrayType("int")]
    assert model.initialization_steps == (a, b, model.source.instance_initializers[0], c)
    assert model.constructors[0].initialization_steps == model.initialization_steps
    assert c.initializer.kind == "instance_field" and c.initializer.symbol is a
    assert c.constant is None and c.initializer.constant is None
    assert isinstance(model.fields[0], ArrayReferenceFieldSlot)
    assert model.fields[0].default_value is None and model.fields[0].size is None
    assert model.fields[1].size == 4 and model.fields[1].offset is None


def test_array_field_length_and_component_store_keep_runtime_check_order():
    text = original("int[] values;", "values[0]=42;values[0]+=1;return this.values.length;")
    model = analyze_class_sources({"ArrayExample.java": text}).models["ArrayExample"]
    simple, compound, result = model.source.methods[0].body.children
    assert simple.children[0].children[0].symbol is model.fields[0].field
    assert simple.runtime_steps == ("array-reference", "index", "rhs", "null-check", "bounds-check", "store-component")
    assert compound.runtime_steps == ("array-reference", "index", "null-check", "bounds-check", "read-component", "rhs", "operation", "narrow", "store-component")
    assert result.children[0].runtime_steps == ("array-reference", "null-check", "read-length")
    assert result.children[0].constant is None


@pytest.mark.parametrize("members,code", [
    ("final int[] values;ArrayExample(){values=new int[]{42};}", "java.unsupported.final-instance-fields"),
    ("static final int[] values={42};", "java.unsupported.array-storage"),
    ("int[][] values;", "java.unsupported.array-model-shape"),
    ("float[] values;", "java.unsupported.array-model-shape"),
])
def test_deferred_array_field_shapes_remain_explicit(members, code):
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"ArrayExample.java": original(members, "return 42;")})
    assert caught.value.code == code


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else
                     "select an existing development JDK with PCC_JAVA_RAW_REFERENCE")
@pytest.mark.xdist_group(name="java-array-field-reference")
def test_exact_array_field_originals_against_selected_jdk_only(tmp_path, request):
    check_originals_reference(tmp_path, request, PROGRAMS + GROUPED_SCALAR_PROGRAMS, VALID_ONLY, INVALID,
                              invocation="new ArrayExample().run()", source_input=Path(__file__))
