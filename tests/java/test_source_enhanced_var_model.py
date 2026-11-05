"""Primitive-array enhanced-for var inference, with no managed execution.

JLS21 14.14.2, 6.3, 4.12.4 and 16.2.12:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-14.html#jls-14.14.2
Exact originals feed the nonlowering checker and a development-only JDK lane.
All execution is UNRUN; inferred bindings do not supply native array storage.
"""
from pathlib import Path

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_source
from pcc.frontends.java.source_sema import Binding
from pcc.frontends.java.source_types import ArrayType
from tests.java.test_source_array_model import check_originals_reference
from tests.java.test_source_enhanced_for_model import LITERALS, original, walk
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_local_inference import NATIVE_GAPS
from tests.java.test_source_unicode_escapes import JDK_REASON


PROGRAMS = [
    ("component-" + component, original(
        "int count=0;for(var value:new " + component + "[]{" + literal
        + "}){if(value!=" + literal + ")return 0;count++;}return count+41;"))
    for component, literal in LITERALS.items()
] + [
    ("mutable-iteration-copy", original(
        "int[] values={7,35};for(var value:values){value=0;}return values[0]+values[1];")),
    ("final-sum", original(
        "int sum=0;for(final var value:new int[]{7,35})sum+=value;return sum;")),
    ("empty-array-zero-iteration", original(
        "for(var value:new int[0])return 0;return 42;")),
    ("variable-named-var", original(
        "int sum=0;for(var var:new int[]{7,35})sum+=var;return sum;")),
    ("source-call-once", original(
        "int[] log={0};int sum=0;for(var value:values(log))sum+=value;return log[0]==1?sum:0;",
        "static int[] values(int[] log){log[0]++;return new int[]{7,35};}")),
    ("source-effects-survive-zero-iteration", original(
        "int size;for(var value:new int[size=0]){}return size+42;")),
    ("labeled-continue", original(
        "int sum=0;outer:for(var value:new int[]{1,2,3}){sum+=value;"
        "for(var other:new int[]{10}){continue outer;}}return sum+36;")),
    ("byte-overload-keeps-component-type", original(
        "for(var value:new byte[]{1})return pick(value);return 0;",
        "static int pick(byte value){return 42;}static int pick(int value){return 0;}")),
]

VALID_ONLY = [
    ("typed-null", original("int[] values=null;for(var value:values){}return 42;")),
    ("field-shadowed-only-in-body", "class ArrayExample{int[] values;int read(){int sum=0;"
     "for(var values:values)sum+=values;return sum;}}"),
    ("constructors-and-initializers", "class ArrayExample{int total;"
     "{for(var value:new int[]{7,35})total+=value;}"
     "ArrayExample(int[] values){for(final var value:values)total+=value;}}"),
]

INVALID = [
    ("final-write", original("for(final var value:new int[]{42})value=0;return 42;"), "final local"),
    ("final-not-constant", original("for(final var value:new int[]{42}){switch(42){case value:break;}}return 42;"), "constant expression"),
    ("nonconstant-component-cannot-narrow", original("for(final var value:new int[]{42}){byte copy=value;}return 42;"), "assignment conversion"),
    ("binder-not-in-expression", original("for(var value:new int[]{value}){}return 42;"), "undefined local"),
    ("binder-does-not-escape", original("for(var value:new int[]{42}){}return value;"), "undefined local"),
    ("outer-local-collision", original("int value=0;for(var value:new int[]{42}){}return 42;"), "already in scope"),
    ("body-assignment-not-da", original("int result;for(var value:new int[]{42})result=value;return result;"), "not definitely assigned"),
    ("null-not-an-array-type", original("for(var value:null){}return 42;"), "array or Iterable"),
    ("scalar-not-iterable", original("for(var value:42){}return 42;"), "array or Iterable"),
    ("declarator-brackets", original("for(var value[]:new int[]{42}){}return 42;"), "cannot add array dimensions"),
]


def checked(text):
    return analyze_class_sources({"ArrayExample.java": text})


@pytest.mark.parametrize("name,text", PROGRAMS + VALID_ONLY, ids=[row[0] for row in PROGRAMS + VALID_ONLY])
def test_inferred_array_loops_have_owned_nonlowering_models(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("inferred loop reached lowering"))
    model = checked(text)
    assert not model.models["ArrayExample"].runtime_registered
    program = model.classes["ArrayExample"]
    bodies = [method.body for method in (*program.methods, *program.constructors)]
    bodies.extend(program.instance_initializers)
    loops = [node for body in bodies for node in walk(body) if node.kind == "enhanced_for"]
    assert loops
    for loop in loops:
        variable, expression, _ = loop.children
        assert isinstance(expression.dtype, ArrayType)
        component = expression.dtype.component
        assert variable.value == variable.symbol.dtype == component
        assert isinstance(variable.symbol, Binding) and variable.inference_token.text == "var"
        assert variable.children == [] and variable.constant is None
        assert loop.value.variable_type == component and loop.value.array_type == expression.dtype
        assert loop.value.captured_array is expression.managed_value
        assert not loop.value.runtime_registered


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_inferred_loops_are_ordinary_errors(name, text, message):
    with pytest.raises(JavaError, match=message) as caught:
        checked(text)
    assert type(caught.value) is JavaError and caught.value.code is None


def test_inferred_iterable_is_checked_once_before_binder_scope(monkeypatch):
    from pcc.frontends.java.source_class_model import ClassAnalyzer
    original_expression = ClassAnalyzer.expression
    visits = []

    def visit(analyzer, node, assigned):
        if node.kind == "call" and node.value == "values":
            visits.append((node, {name for scope in analyzer.scopes for name in scope}))
        return original_expression(analyzer, node, assigned)

    monkeypatch.setattr(ClassAnalyzer, "expression", visit)
    program = checked(dict(PROGRAMS)["source-call-once"]).classes["ArrayExample"]
    loop, = [node for node in walk(program.methods[0].body) if node.kind == "enhanced_for"]
    assert len(visits) == 1 and visits[0][0] is loop.children[1]
    assert {"log", "sum"} <= visits[0][1] and "value" not in visits[0][1]


def test_inferred_field_shadowing_keeps_distinct_binding_identity():
    program = checked(dict(VALID_ONLY)["field-shadowed-only-in-body"]).classes["ArrayExample"]
    loop, = [node for node in walk(program.methods[0].body) if node.kind == "enhanced_for"]
    variable, expression, body = loop.children
    assert expression.symbol is program.fields[0] and expression.dtype == ArrayType("int")
    reads = [node for node in walk(body) if node.kind == "name" and node.value == "values"]
    assert reads and all(node.symbol is variable.symbol and node.dtype == "int" for node in reads)
    assert variable.symbol is not expression.symbol


def test_inferred_component_type_controls_overload_selection():
    program = checked(dict(PROGRAMS)["byte-overload-keeps-component-type"]).classes["ArrayExample"]
    call, = [node for node in walk(program.methods[0].body) if node.kind == "call"]
    assert call.symbol.parameter_types == ("byte",)
    assert call.children[0].dtype == "byte" and call.children[0].constant is None


@pytest.mark.parametrize("component", ("float", "double"))
def test_inferred_loop_does_not_expand_array_component_domain(component):
    text = "class ArrayExample{static void use(" + component + "[] values){for(var value:values){}}}"
    with pytest.raises(UnsupportedJava) as caught:
        checked(text)
    assert caught.value.code == "java.unsupported.array-model-shape"


def test_inferred_loop_does_not_supply_iterable_hierarchy_or_native_storage(monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("inferred loop reached lowering"))
    text = "class ArrayExample{static void use(ArrayExample values){for(var value:values){}}}"
    with pytest.raises(UnsupportedJava) as caught:
        checked(text)
    assert caught.value.code == "java.unsupported.enhanced-for-iterable"
    with pytest.raises(UnsupportedJava) as caught:
        compile_source(original("for(var value:new int[]{42}){}return 42;"))
    assert caught.value.code == "java.unsupported.local-inference-domain"


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-enhanced-var-model-reference")
def test_exact_inferred_loop_originals_against_selected_jdk_only(tmp_path, request):
    check_originals_reference(tmp_path, request, PROGRAMS, VALID_ONLY, INVALID,
                              source_input=Path(__file__))
    for name, _, _ in INVALID:
        assert (tmp_path / name / "compiler-status.txt").read_text() == "1"
        assert b"ArrayExample.java:" in (tmp_path / name / "compiler.stderr.bin").read_bytes()


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-enhanced-var-model-reference")
def test_exact_former_inference_model_gaps_against_jdk_only(tmp_path, request):
    originals = [(name, text) for name, text, _ in NATIVE_GAPS[:2]]
    check_originals_reference(tmp_path, request, originals, [], [], invocation="Inferred.run()",
                              source_input=Path(__file__))
