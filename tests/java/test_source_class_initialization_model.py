"""Non-constant and array static final fields as class-initialization steps.

JLS21 4.12.4, 8.3.1.2, 8.3.2, 8.3.3, 12.4.1 and 12.4.2:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-4.html#jls-4.12.4
https://docs.oracle.com/javase/specs/jls/se21/html/jls-8.html#jls-8.3.3
https://docs.oracle.com/javase/specs/jls/se21/html/jls-12.html#jls-12.4.2
A static final primitive field with a constant initializer is a constant
variable and folds; every other initialized static final field, including
each primitive-array field, runs in textual order during class
initialization and is never folded. The class model checks these sources;
it emits nothing. Commons Numbers Primes' `SmallPrimes.PRIMES` and
`PRIMES_LAST` (projects/commons-numbers-1.2, Apache-2.0) have this shape;
the sources below are independently authored originals.
"""
import os
from pathlib import Path

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_source
from pcc.frontends.java.source_class_initialization import (
    CLASS_INITIALIZATION_REQUIREMENTS,
    ClassInitializationObligation,
    StaticFieldSlot,
)
from pcc.frontends.java.source_types import ArrayType
from tests.java.test_source_array_model import check_originals_reference
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner


def original(members, body="return 42;"):
    return "class ArrayExample{" + members + "static int run(){" + body + "}}"


PROGRAMS = [
    ("static-array-initializer", original("static final int[] PRIMES={2,3,5,7};",
                                          "return PRIMES[0]+PRIMES[3]*5+PRIMES.length+1;")),
    ("last-component", original("static final int[] PRIMES={2,3,5,7,11,13,17,19,23,29,31,37,41};"
                                "static final int LAST=PRIMES[PRIMES.length-1];", "return LAST+1;")),
    ("textual-order", original("static final int A=f();static final int[] B={A,A+1};static int f(){return 20;}",
                               "return B[0]+B[1]+1;")),
    ("constant-still-folds", original("static final int K=6*7;static final int[] V={K};",
                                      "switch(42){case K:return V[0];default:return 0;}")),
    ("enhanced-for-over-static-array", original("static final int[] V={7,14,21};",
                                                "int s=0;for(final int p:V){s+=p;}return s;")),
    ("components-stay-mutable", original("static final int[] V={41};", "V[0]++;return V[0];")),
    ("creation-expression", original("static final int[] V=new int[]{40,2};", "return V[0]+V[1];")),
    ("declarator-brackets", original("static final int V[]={42},W=V[0];", "return W;")),
    ("qualified-read", original("static final int[] V={42};", "return ArrayExample.V[0];")),
]

INVALID = [
    ("forward-reference", original("static final int[] A=B;static final int[] B={42};"), "illegal forward"),
    ("component-type", original("static final int[] A={1,true};"), "integer value required"),
    ("array-type", original("static final long[] A={1};static final int[] B=A;"), "incompatible array assignment"),
    ("static-context-receiver", original("static final int[] A={this.hashCode()};"), "static context"),
    ("final-rebind", original("static final int[] A={1};", "A=new int[1];return 42;"), "cannot assign a final"),
    ("nonconstant-case-label", original("static final int X=f();static int f(){return 42;}",
                                        "switch(42){case X:return 42;}return 0;"), "constant expression"),
]


def model(text):
    return analyze_class_sources({"ArrayExample.java": text}).models["ArrayExample"]


@pytest.mark.parametrize("name,text", PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_class_initialization_originals_are_checked_in_the_class_model(name, text):
    result = model(text)
    assert result.class_initialization_steps
    assert all(isinstance(slot, StaticFieldSlot) and slot.offset is None for slot in result.static_fields)
    assert result.runtime_registered is False


def test_steps_are_textual_order_and_only_non_constant_fields():
    result = model(original("static final int[] P={2,3,5};static final int K=6*7;"
                            "static final int L=P[P.length-1];static final int F=f();static int f(){return 1;}"))
    assert [field.token.text for field in result.class_initialization_steps] == ["P", "L", "F"]
    assert [(slot.field.token.text, slot.reference) for slot in result.static_fields] == [
        ("P", True), ("L", False), ("F", False)]
    fields = {field.token.text: field for field in result.source.fields}
    assert fields["K"].constant == 42 and fields["L"].constant is None and fields["F"].constant is None
    assert isinstance(fields["P"].dtype, ArrayType) and fields["P"].initializer.runtime_steps[0] == "allocate-zeroed-array"


def test_obligation_names_the_unmet_initialization_runtime():
    result = model(original("static final int[] P={2};static final int F=f();static int f(){return 1;}"))
    obligation, = [item for item in result.compiler_context_obligations
                   if isinstance(item, ClassInitializationObligation)]
    assert obligation.owner == "ArrayExample" and [token.text for token in obligation.tokens] == ["P", "F"]
    assert obligation.requirements == CLASS_INITIALIZATION_REQUIREMENTS + ("static-reference-root",)
    assert set(obligation.requirements) <= set(result.managed_requirements)
    scalar = model(original("static final int F=f();static int f(){return 1;}"))
    obligation, = [item for item in scalar.compiler_context_obligations
                   if isinstance(item, ClassInitializationObligation)]
    assert "static-reference-root" not in obligation.requirements


def test_constant_only_classes_have_no_initialization_steps():
    project = analyze_class_sources({"ArrayExample.java": original("static final int K=42;", "return K;")})
    result = project.models["ArrayExample"]
    assert result.class_initialization_steps == () and result.static_fields == ()
    assert project.class_initialization_context_complete
    assert not any(isinstance(item, ClassInitializationObligation) for item in result.compiler_context_obligations)


def test_another_class_reads_the_static_array_without_folding():
    project = analyze_class_sources({
        "Table.java": "class Table{static final int[] PRIMES={2,3,5};static final int LAST=PRIMES[PRIMES.length-1];}",
        "User.java": "class User{static int run(){int s=0;for(final int p:Table.PRIMES){s+=p;}return s+Table.LAST;}}",
    })
    assert not project.class_initialization_context_complete
    loop, result = project.models["User"].source.methods[0].body.children[1:]
    read = result.children[0].children[1]
    assert read.kind == "static_field" and read.constant is None and read.symbol.token.text == "LAST"
    assert project.models["User"].class_initialization_steps == ()


# Former boundary rows, moved verbatim: test_source_array_fields pinned the
# static array field as array storage, test_source_integral_arithmetic_model
# pinned the zero-divisor initializer as static initialization.
MOVED_ARRAY_FIELD = "class ArrayExample{static final int[] values={42};int run(){return 42;}}"
MOVED_ZERO_DIVISOR = "class ArrayExample{static final int VALUE=1/0;}"


def test_former_static_array_field_row_is_a_class_initialization_step():
    result = model(MOVED_ARRAY_FIELD)
    assert [(slot.field.token.text, slot.reference) for slot in result.static_fields] == [("values", True)]


def test_former_zero_divisor_row_initializes_at_class_initialization():
    # JLS 15.29: 1/0 is not a constant expression, so VALUE is no constant
    # variable; its ArithmeticException belongs to class initialization.
    from pcc.frontends.java.source_integral_arithmetic_model import IntegralArithmeticContextObligation
    result = model(MOVED_ZERO_DIVISOR)
    assert [field.token.text for field in result.class_initialization_steps] == ["VALUE"]
    assert result.source.fields[0].constant is None
    assert any(isinstance(item, IntegralArithmeticContextObligation) for item in result.compiler_context_obligations)


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_class_initialization_originals_fail_as_java_errors(name, text, message):
    with pytest.raises(JavaError, match=message) as caught:
        model(text)
    assert not isinstance(caught.value, UnsupportedJava)


@pytest.mark.parametrize("members,code", [
    ("static int[] V={42};", "java.unsupported.static-initialization"),
    ("static final int[] V;static{V=new int[]{42};}", "java.unsupported.static-initialization"),
    ("static final int V=f();static{}static int f(){return 42;}", "java.unsupported.static-initialization"),
    # Legal Java (both read null), but cyclic lazy evaluation stays a gap.
    ("static final int[] A=ArrayExample.B;static final int[] B=ArrayExample.A;", "java.unsupported.cyclic-initialization"),
])
def test_remaining_class_initialization_shapes_stay_explicit(members, code):
    with pytest.raises(UnsupportedJava) as caught:
        model(original(members))
    assert caught.value.code == code


@pytest.mark.parametrize("name,text", PROGRAMS[:2], ids=[row[0] for row in PROGRAMS[:2]])
def test_native_domain_still_rejects_static_array_fields(name, text, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    with pytest.raises(UnsupportedJava) as caught:
        compile_source(text, filename="ArrayExample.java")
    assert caught.value.code == "java.unsupported.array-storage"


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else
                     "select an existing development JDK with PCC_JAVA_RAW_REFERENCE")
@pytest.mark.xdist_group(name="java-class-initialization-reference")
def test_class_initialization_originals_against_selected_jdk_only(tmp_path, request):
    check_originals_reference(tmp_path, request, PROGRAMS, [], INVALID, source_input=Path(__file__))
