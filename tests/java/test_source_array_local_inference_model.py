"""Local var inference for existing primitive-array source models only.

JLS21 14.4.1, 6.3, 4.12.4 and 16:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-14.html#jls-14.4.1
Original source bytes are shared with a development-only JDK observer.
All execution is UNRUN; no native reference storage or allocation is supplied.
"""
from pathlib import Path

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_source, compile_sources
from pcc.frontends.java.source_sema import Binding
from pcc.frontends.java.source_types import ArrayType
from pcc.support.managed_objects import unmet_requirements
from tests.java.test_source_array_model import check_originals_reference
from tests.java.test_source_enhanced_for_model import LITERALS, original, walk
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_local_inference import MODEL_ARRAY_ORIGINAL
from tests.java.test_source_unicode_escapes import JDK_REASON


PROGRAMS = [
    ("component-" + component, original(
        "var values=new " + component + "[]{" + literal
        + "};return values[0]==" + literal + "?42:0;"))
    for component, literal in LITERALS.items()
] + [
    ("alias-retains-reference", original(
        "var values=new int[]{7,35};var copy=values;copy[0]=0;return values[0]+values[1]+7;")),
    ("final-reference-allows-component-write", original(
        "final var values=new int[]{0};values[0]=42;return values[0];")),
    ("call-initializer-once", original(
        "int[] log={0};var values=make(log);return log[0]==1?values[0]:0;",
        "static int[] make(int[] log){log[0]++;return new int[]{42};}")),
    ("conditional-array-initializer", original(
        "var values=true?new int[]{42}:new int[0];return values[0];")),
    ("enhanced-for-inferred-array", original(
        "var values=new int[]{7,35};int sum=0;for(var value:values)sum+=value;return sum;")),
    ("basic-for-local-array", original(
        "int sum=0;for(var values=new int[]{7,35};sum==0;){sum=values[0]+values[1];}return sum;")),
    ("typed-null-result", original(
        "var values=missing();return values==null?42:0;", "static int[] missing(){return null;}")),
    ("initializer-effect-da", original(
        "int size;var values=new int[size=42];return size==values.length?42:0;")),
]

VALID_ONLY = [
    ("parameter-initializer", "class ArrayExample{static int use(int[] input){var values=input;return values.length;}}"),
    ("constructors-and-initializers", "class ArrayExample{int total;"
     "{var values=new int[]{42};total=values[0];}ArrayExample(int[] input){var values=input;total=values.length;}}"),
]

INVALID = [
    ("self-array-read", original("var values=new int[]{values[0]};return 42;"), "own inferred initializer"),
    ("self-array-size", original("var values=new int[values.length];return 42;"), "own inferred initializer"),
    ("duplicate-local", original("int values=0;var values=new int[]{42};return 42;"), "already in scope"),
    ("bare-initializer", original("var values={42};return 42;"), "bare array initializer"),
    ("missing-initializer", original("var values;return 42;"), "requires an initializer"),
    ("null-type", original("var values=null;return 42;"), "cannot infer"),
    ("extra-array-brackets", original("var values[]=new int[]{42};return 42;"), "cannot add array dimensions"),
    ("final-rebind", original("final var values=new int[]{42};values=null;return 42;"), "final local"),
    ("different-component-rebind", original("var values=new int[]{42};values=new long[]{42L};return 42;"), "array assignment conversion"),
    ("unassigned-array-initializer", original("int[] input;var values=input;return 42;"), "not definitely assigned"),
    ("boolean-component-not-numeric", original("var values=new boolean[]{true};return values[0]+1;"), "numeric|integer"),
]


def checked(text):
    return analyze_class_sources({"ArrayExample.java": text})


@pytest.mark.parametrize("name,text", PROGRAMS + VALID_ONLY, ids=[row[0] for row in PROGRAMS + VALID_ONLY])
def test_array_var_originals_have_nonlowering_binding_and_managed_plans(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("array var reached native lowering"))
    model = checked(text)
    program = model.classes["ArrayExample"]
    bodies = [method.body for method in (*program.methods, *program.constructors)]
    bodies.extend(program.instance_initializers)
    variables = [node for body in bodies for node in walk(body)
                 if node.kind == "var" and node.inference_token is not None]
    assert variables and not model.models["ArrayExample"].runtime_registered
    for variable in variables:
        initializer, = variable.children
        assert isinstance(variable.value, ArrayType) and isinstance(variable.symbol, Binding)
        assert variable.value == variable.symbol.dtype == initializer.dtype
        assert variable.inference_token.text == "var" and variable.constant is None
        assert initializer.managed_value is not None and unmet_requirements(initializer.managed_value)


def test_former_array_inference_original_is_preserved_as_model_success(monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("model reached lowering"))
    model = analyze_class_sources({"Inferred.java": MODEL_ARRAY_ORIGINAL})
    variable = model.programs[0].methods[0].body.children[0]
    assert variable.value == variable.symbol.dtype == ArrayType("int")
    assert variable.children[0].dtype == ArrayType("int")
    assert not model.models["Inferred"].runtime_registered


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_array_var_originals_remain_ordinary_errors(name, text, message):
    with pytest.raises(JavaError, match=message) as caught:
        checked(text)
    assert type(caught.value) is JavaError and caught.value.code is None


def test_array_initializer_checked_once_with_pending_name_not_a_binding(monkeypatch):
    from pcc.frontends.java.source_class_model import ClassAnalyzer
    from pcc.frontends.java.source_local_inference import PendingInferredLocal
    original_expression = ClassAnalyzer.expression
    visits = []

    def visit(analyzer, node, assigned):
        if node.kind == "call" and node.value == "make":
            visits.append((node, analyzer.scopes[-1]["values"]))
        return original_expression(analyzer, node, assigned)

    monkeypatch.setattr(ClassAnalyzer, "expression", visit)
    program = checked(dict(PROGRAMS)["call-initializer-once"]).classes["ArrayExample"]
    variable = program.methods[0].body.children[1]
    assert len(visits) == 1 and visits[0][0] is variable.children[0]
    assert isinstance(visits[0][1], PendingInferredLocal)
    assert visits[0][1] not in program.methods[0].locals
    assert variable.symbol.dtype == ArrayType("int")


def test_array_aliases_keep_distinct_bindings_and_identical_component_type():
    program = checked(dict(PROGRAMS)["alias-retains-reference"]).classes["ArrayExample"]
    first, second = program.methods[0].body.children[:2]
    assert first.symbol is not second.symbol and first.symbol.slot != second.symbol.slot
    assert first.symbol.dtype == second.symbol.dtype == ArrayType("int")
    assert second.children[0].symbol is first.symbol and second.children[0].constant is None


@pytest.mark.parametrize("api", ("source", "sources"))
def test_array_var_model_does_not_grant_native_compilation(api, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("native array inference reached IR"))
    with pytest.raises(UnsupportedJava) as caught:
        if api == "source":
            compile_source(MODEL_ARRAY_ORIGINAL)
        else:
            compile_sources({"Inferred.java": MODEL_ARRAY_ORIGINAL}, entry_class="Inferred")
    assert caught.value.code == "java.unsupported.object-expressions"


@pytest.mark.parametrize("component", ("float", "double"))
def test_array_var_does_not_expand_array_component_domain(component):
    text = original("var values=new " + component + "[]{42};return 42;")
    with pytest.raises(UnsupportedJava) as caught:
        checked(text)
    assert caught.value.code == "java.unsupported.object-expressions"


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-array-var-model-reference")
def test_exact_array_var_originals_against_selected_jdk_only(tmp_path, request):
    check_originals_reference(tmp_path, request, PROGRAMS, VALID_ONLY, INVALID,
                              source_input=Path(__file__))
    for name, _, _ in INVALID:
        assert (tmp_path / name / "compiler-status.txt").read_text() == "1"
        assert b"ArrayExample.java:" in (tmp_path / name / "compiler.stderr.bin").read_bytes()


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-array-var-model-reference")
def test_exact_former_array_inference_original_against_jdk_only(tmp_path, request):
    check_originals_reference(tmp_path, request, [("former-array-inference", MODEL_ARRAY_ORIGINAL)], [], [],
                              invocation="Inferred.run()", source_input=Path(__file__))
