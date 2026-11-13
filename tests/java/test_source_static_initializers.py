"""Static initializers and blank static finals in the class model.

JLS21 8.3.1.2, 8.3.3, 8.7, 12.4.2 and chapter 16 (16.8):
https://docs.oracle.com/javase/specs/jls/se21/html/jls-8.html#jls-8.7
https://docs.oracle.com/javase/specs/jls/se21/html/jls-16.html
A `static { ... }` block is checked in a static context and runs, in textual
order with the class variable initializers, as a JLS 12.4.2 step-9 step. A
blank static final of primitive or array type must be definitely assigned
by the static initializers; this model admits its assignment as a top-level
`name = value;` statement of a static initializer, where definite
assignment and unassignment are exact. Reads before it (by simple name, in a
static initializer or class variable initializer), a second assignment, a
missing assignment, assignment elsewhere, forward references, `return`, a
static initializer that cannot complete normally and `this` are errors;
nested assignments stay an explicit gap. The class model checks these
sources and emits nothing. Commons Numbers Primes' `SmallPrimes`
(projects/commons-numbers-1.2, Apache-2.0) declares
`PRIME_NUMBERS_AND_COPRIME_EQUIVALENCE_CLASSES` this way; the sources below
are independently authored originals.
"""
import os
from pathlib import Path

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_source
from pcc.frontends.java.source_class_initialization import (
    CLASS_INITIALIZATION_REQUIREMENTS,
    ClassInitializationObligation,
)
from tests.java.test_source_array_model import check_originals_reference
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner


def original(members, body="return 42;"):
    return "class ArrayExample{" + members + "static int run(){" + body + "}}"


PROGRAMS = [
    ("blank-final-assigned-by-block", original("static final int X;static{X=42;}", "return X;")),
    ("array-and-derived-blank-finals", original(
        "static final int[] P;static final int L;static{int[] t={2,3,5};P=t;L=P[P.length-1];}",
        "return L*8+P[0];")),
    ("textual-order-with-field-initializers", original(
        "static final int A;static final int B=f();static{A=40;}static final int C=A+B;"
        "static int f(){return 2;}", "return C;")),
    ("block-with-loop-and-locals", original(
        "static final int S;static{int s=0;for(int i=0;i<=8;i++){s+=i;}S=s+6;}", "return S;")),
    ("later-block-reads-earlier-assignment", original(
        "static final int X;static final int[] Y;static{X=21;}static{Y=new int[]{X,X};}", "return Y[0]+Y[1];")),
    ("local-shadows-blank-final", original(
        "static final int X;static{int X=1;X=2;}static{X=42;}", "return X;")),
    ("qualified-read-is-not-checked", original(
        "static final int X;static{int y=ArrayExample.X;X=42+y;}", "return X;")),
    # Former gap rows of test_source_class_initialization_model, moved verbatim.
    ("former-blank-array-row", original("static final int[] V;static{V=new int[]{42};}", "return V[0];")),
    ("former-empty-block-row", original("static final int V=f();static{}static int f(){return 42;}", "return V;")),
]

INVALID = [
    ("never-assigned", original("static final int X;", "return X;"),
     "blank static final field X is not definitely assigned by a static initializer"),
    ("assigned-twice", original("static final int X;static{X=1;X=2;}"), "may already have been assigned"),
    ("assigned-in-two-blocks", original("static final int X;static{X=1;}static{X=2;}"), "may already have been assigned"),
    ("read-before-assignment", original("static final int X;static{int y=X;X=1;}"),
     "not definitely assigned before this read"),
    ("self-read-in-assignment", original("static final int X;static{X=X+1;}"), "not definitely assigned before this read"),
    ("initializer-reads-before-block", original("static final int X;static final int Y=X+1;static{X=1;}"),
     "not definitely assigned before this read"),
    ("compound-assignment", original("static final int X;static{X+=1;}"), "not definitely assigned before this read"),
    ("assigned-in-method", original("static final int X;static{X=1;}", "X=2;return X;"), "cannot assign a final"),
    ("forward-reference", original("static{int y=Z;}static final int Z=f();static int f(){return 1;}"),
     "illegal forward reference in a static initializer"),
    ("return", original("static{return;}"), "return is not permitted in a static initializer"),
    ("cannot-complete-normally", original("static{while(true){}}"), "must be able to complete normally"),
    ("this-in-static-context", original("static{int y=this.hashCode();}"), "static context"),
]

GAPS = [
    # Valid Java whose definite-unassignment proof needs the flow graph.
    original("static final int X;static{if(f()){X=1;}else{X=2;}}static boolean f(){return true;}"),
    original("static final int X;static{int y=(X=1);}"),
    original("static final int X;static{{X=1;}}"),
]


def model(text):
    return analyze_class_sources({"ArrayExample.java": text}).models["ArrayExample"]


@pytest.mark.parametrize("name,text", PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_static_initializer_originals_are_checked_in_the_class_model(name, text):
    result = model(text)
    assert result.class_initialization_steps and result.runtime_registered is False


def test_blocks_and_initializers_are_steps_in_textual_order_and_blank_finals_are_slots():
    result = model(dict(PROGRAMS)["textual-order-with-field-initializers"])
    steps = result.class_initialization_steps
    assert [(step.token.text, getattr(step, "kind", "field")) for step in steps] == [
        ("B", "field"), ("{", "block"), ("C", "field")]
    assert [(slot.field.token.text, slot.reference) for slot in result.static_fields] == [
        ("A", False), ("B", False), ("C", False)]
    fields = {field.token.text: field for field in result.source.fields}
    assert fields["A"].initializer is None and fields["A"].constant is None and fields["C"].constant is None
    assignment = steps[1].children[0]
    assert assignment.kind == "static_field_assign" and assignment.symbol is fields["A"]


def test_obligation_names_each_step_and_the_static_reference_root():
    result = model(dict(PROGRAMS)["array-and-derived-blank-finals"])
    obligation, = [item for item in result.compiler_context_obligations
                   if isinstance(item, ClassInitializationObligation)]
    assert [token.text for token in obligation.tokens] == ["{"]
    assert obligation.requirements == CLASS_INITIALIZATION_REQUIREMENTS + ("static-reference-root",)
    assert [(slot.field.token.text, slot.reference) for slot in result.static_fields] == [("P", True), ("L", False)]


# Former gap rows of test_source_class_model and test_source_diagnostics
# (both pinned "static initializer execution"), moved verbatim.
MOVED_EMPTY_BLOCKS = {"Record.java": "class Record{static {}}", "Original.java": "class Diagnostic{static {}}"}


@pytest.mark.parametrize("name", sorted(MOVED_EMPTY_BLOCKS))
def test_former_empty_static_block_rows_are_checked_steps(name):
    project = analyze_class_sources({name: MOVED_EMPTY_BLOCKS[name]})
    result, = project.models.values()
    assert [step.kind for step in result.class_initialization_steps] == ["block"]
    assert not project.class_initialization_context_complete


def test_a_block_without_fields_is_still_a_step():
    result = model(original("static{int s=0;for(int i=0;i<3;i++){s+=i;}}"))
    assert [step.kind for step in result.class_initialization_steps] == ["block"]
    assert result.static_fields == ()


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_static_initializer_originals_fail_as_java_errors(name, text, message):
    with pytest.raises(JavaError, match=message) as caught:
        model(text)
    assert not isinstance(caught.value, UnsupportedJava)


@pytest.mark.parametrize("text", GAPS)
def test_nested_blank_final_assignments_stay_explicit(text):
    with pytest.raises(UnsupportedJava, match="definite-unassignment flow analysis") as caught:
        model(text)
    assert caught.value.code == "java.unsupported.static-initialization"


def test_reference_typed_blank_finals_keep_the_static_reference_boundary():
    # Source-class (or other reference) statics need the managed static roots.
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"ArrayExample.java": original("static final Box B;static{B=null;}"),
                               "Box.java": "class Box{}"})
    assert caught.value.code == "java.unsupported.static-initialization"


@pytest.mark.parametrize("text", [PROGRAMS[0][1], original("static{int y=1;}")])
def test_native_domain_still_rejects_static_initialization(text, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    with pytest.raises(UnsupportedJava) as caught:
        compile_source(text, filename="ArrayExample.java")
    assert caught.value.code == "java.unsupported.static-initialization"


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else
                     "select an existing development JDK with PCC_JAVA_RAW_REFERENCE")
@pytest.mark.xdist_group(name="java-class-initialization-reference")
def test_static_initializer_originals_against_selected_jdk_only(tmp_path, request):
    check_originals_reference(tmp_path, request, PROGRAMS, [], INVALID, source_input=Path(__file__))
