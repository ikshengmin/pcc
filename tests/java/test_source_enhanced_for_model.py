"""Primitive-array enhanced-for semantics, never managed native execution.

JLS21 5.2, 6.3, 6.4, 14.14.2, 14.15, 14.16, 14.22 and 16.2.12:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-5.html#jls-5.2
https://docs.oracle.com/javase/specs/jls/se21/html/jls-6.html#jls-6.3
https://docs.oracle.com/javase/specs/jls/se21/html/jls-14.html#jls-14.14.2
https://docs.oracle.com/javase/specs/jls/se21/html/jls-16.html#jls-16.2.12

Complete original Java units are shared, unchanged, by the nonlowering PCC
checker and a separate selected-JDK observer. Only the JDK lane executes their
run methods. These source-authored obligations are execution UNRUN until CI.
The six admitted array components do not imply float/reference/Iterable array
support; integral components may still widen to float/double loop variables.
"""
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from pcc.frontends.java import (
    JavaError,
    UnsupportedJava,
    analyze_class_sources,
    compile_source,
    compile_sources,
)
from pcc.frontends.java.source_sema import Binding
from pcc.frontends.java.source_types import ArrayType
from pcc.support.managed_objects import unmet_requirements
from tests.java.test_source_array_model import check_originals_reference
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_unicode_escapes import JDK_REASON


def original(body, helpers=""):
    return "class ArrayExample{static int run(){" + body + "}" + helpers + "}"


# Explicit specification matrix, deliberately independent of PCC's widening().
ASSIGNABLE_TARGETS = {
    "boolean": ("boolean",),
    "byte": ("byte", "short", "int", "long", "float", "double"),
    "short": ("short", "int", "long", "float", "double"),
    "char": ("char", "int", "long", "float", "double"),
    "int": ("int", "long", "float", "double"),
    "long": ("long", "float", "double"),
}
LITERALS = {"boolean": "true", "byte": "42", "short": "42",
            "char": "'*'", "int": "42", "long": "42L"}
PRIMITIVES = ("boolean", "byte", "short", "char", "int", "long", "float", "double")

CONVERSION_PROGRAMS = [
    ("element-" + source + "-to-" + target,
     original("int count=0;for(" + target + " value:new " + source + "[]{"
              + LITERALS[source] + "}){if(value!=" + LITERALS[source]
              + ")return 0;count++;}return count+41;"))
    for source, targets in ASSIGNABLE_TARGETS.items() for target in targets
]

PROGRAMS = CONVERSION_PROGRAMS + [
    ("sum-ordered-elements", original(
        "int sum=0;for(int value:new int[]{7,35})sum+=value;return sum;")),
    ("empty-array-skips-body", original(
        "int sum=42;for(int value:new int[]{}){sum=0;}return sum;")),
    ("empty-statement-body", original(
        "for(int value:new int[]{7,35});return 42;")),
    ("final-variable", original(
        "int sum=0;for(final int value:new int[]{7,35}){sum+=value;}return sum;")),
    ("writable-variable-reset-each-iteration", original(
        "int sum=0;for(int value:new int[]{1,2}){sum+=value;value=99;}return sum+39;")),
    ("variable-write-does-not-store-array", original(
        "int[] values={7,35};for(int value:values){value=0;}return values[0]+values[1];")),
    ("variable-increment", original(
        "int sum=0;for(int value:new int[]{6,34}){value++;sum+=value;}return sum;")),
    ("capture-survives-null-rebind", original(
        "int[] values={7,35};int sum=0;for(int value:values){sum+=value;values=null;}return sum;")),
    ("capture-survives-different-length-rebind", original(
        "int[] values={7,35};int sum=0;for(int value:values){sum+=value;values=new int[]{99};}return sum;")),
    ("later-components-read-after-body-writes", original(
        "int[] values={7,1};int sum=0;for(int value:values){sum+=value;values[1]=35;}return sum;")),
    ("current-component-copied-before-body", original(
        "int[] values={7,35};int sum=0;for(int value:values){values[0]=99;sum+=value;}return sum;")),
    ("evaluate-iterable-once", original(
        "int[] log={0};int sum=0;for(int value:values(log)){sum+=value;}return log[0]==1?sum:0;",
        "static int[] values(int[] log){log[0]++;return new int[]{7,35};}")),
    ("empty-iterable-still-evaluated", original(
        "int[] log={0};for(int value:empty(log)){log[0]=0;}return log[0]+41;",
        "static int[] empty(int[] log){log[0]++;return new int[0];}")),
    ("source-side-effects-precede-first-element", original(
        "int[] values={0,0};int sum=0;for(int value:prepare(values)){sum+=value;}return sum;",
        "static int[] prepare(int[] values){values[0]=7;values[1]=35;return values;}")),
    ("initializer-side-effects-left-to-right", original(
        "int n=0;int sum=0;for(int value:new int[]{n++,n++,n++}){sum+=value;}"
        "return n==3?sum+39:0;")),
    ("expression-da-visible-in-body", original(
        "int n;int sum=0;for(int value:new int[]{n=21}){sum=value+n;}return sum;")),
    ("expression-da-survives-zero-iterations", original(
        "int size;for(int value:new int[size=0]){}return size+42;")),
    ("expression-da-survives-abrupt-body", original(
        "int n;for(int value:new int[]{n=42}){break;}return n;")),
    ("body-local-redeclared-after-loop", original(
        "int sum=0;for(int value:new int[]{7,35}){int copy=value;sum+=copy;}"
        "int copy=0;return sum+copy;")),
    ("iteration-variable-scope-ends", original(
        "for(int value:new int[]{1}){}int value=42;return value;")),
    ("successive-loops-reuse-name", original(
        "int sum=0;for(int value:new int[]{7})sum+=value;"
        "for(int value:new int[]{35})sum+=value;return sum;")),
    ("iterable-uses-field-before-shadowing", original(
        "int sum=0;for(int value:new int[]{value+42})sum+=value;return sum;",
        "static final int value=0;")),
    ("break-stops-iteration", original(
        "int sum=0;for(int value:new int[]{7,35,99}){sum+=value;if(sum==42)break;}return sum;")),
    ("continue-advances-index", original(
        "int sum=0;for(int value:new int[]{99,7,35}){if(value==99)continue;sum+=value;}return sum;")),
    ("nested-loop-break-is-inner", original(
        "int sum=0;for(int outer:new int[]{1,2}){for(int inner:new int[]{19,99})"
        "{sum+=inner;break;}sum+=outer;}return sum+1;")),
    ("labeled-break-is-outer", original(
        "int sum=0;outer:for(int value:new int[]{1,2}){for(int other:new int[]{40,99})"
        "{sum=value+other;break outer;}}return sum+1;")),
    ("labeled-continue-is-outer", original(
        "int sum=0;outer:for(int value:new int[]{1,2,3}){sum+=value;"
        "for(int other:new int[]{10,20}){if(other==10)continue outer;sum+=other;}}return sum+36;")),
    ("enhanced-loop-continues-basic-loop", original(
        "int sum=0;outer:for(int index=0;index<2;index++){for(int value:new int[]{1,2})"
        "{sum+=value;continue outer;}}return sum+40;")),
    ("switch-break-and-loop-continue", original(
        "int sum=0;for(int value:new int[]{1,2,3}){switch(value){case 1:continue;"
        "case 2:sum+=39;break;default:sum+=3;break;}}return sum;")),
    ("return-from-body", original(
        "for(int value:new int[]{42})return value;return 0;")),
    ("empty-array-body-return-does-not-hide-tail", original(
        "for(int value:new int[0])return 0;return 42;")),
    ("conditional-array-expression", original(
        "int sum=0;for(int value:true?new int[]{7,35}:new int[0])sum+=value;return sum;")),
    ("parenthesized-array-expression", original(
        "int[] values={7,35};int sum=0;for(int value:((values)))sum+=value;return sum;")),
    ("int-to-float-rounding", original(
        "for(float value:new int[]{16777217})return value==16777216.0f?42:0;return 0;")),
    ("long-to-double-rounding", original(
        "for(double value:new long[]{9007199254740993L})"
        "return value==9007199254740992.0d?42:0;return 0;")),
]

# No run invocation: null and negative dimensions have intentional abrupt paths.
VALID_ONLY = [
    ("typed-null-array-runtime", original(
        "int[] values=null;for(int value:values){return value;}return 42;")),
    ("null-call-runtime-after-source-effect", original(
        "int[] log={0};for(int value:missing(log)){log[0]=0;}return 42;",
        "static int[] missing(int[] log){log[0]++;return null;}")),
    ("negative-dimension-runtime", original(
        "for(int value:new int[-1]){}return 42;")),
    ("instance-field-shadowed-only-in-body",
        "class ArrayExample{int[] values;int read(){int sum=0;"
        "for(int values:values){sum+=values;}return sum;}}"),
    ("constructor-and-instance-initializer",
        "class ArrayExample{int total;{for(int value:new int[]{7,35})total+=value;}"
        "ArrayExample(int[] values){for(int value:values){total+=value;}}}"),
    ("void-method-empty-and-nonempty-bodies",
        "class ArrayExample{static void consume(int[] values){"
        "for(final int value:values){}for(int value:values);}}"),
    ("body-reachable-even-for-empty-array",
        "class ArrayExample{static void work(){for(int value:new int[0]){int copy=value;}}}"),
]

INVALID_CONVERSIONS = [
    ("reject-element-" + source + "-to-" + target,
     original("for(" + target + " value:new " + source + "[]{"
              + LITERALS[source] + "}){}return 42;"),
     "element assignment")
    for source, targets in ASSIGNABLE_TARGETS.items() for target in PRIMITIVES
    if target not in targets
]

INVALID = INVALID_CONVERSIONS + [
    ("source-reference-unassigned", original(
        "int[] values;for(int value:values){}return 42;"), "not definitely assigned"),
    ("body-assignment-not-da-after-loop", original(
        "int result;for(int value:new int[]{42}){result=value;}return result;"), "not definitely assigned"),
    ("break-assignment-not-da-after-loop", original(
        "int result;for(int value:new int[]{42}){result=value;break;}return result;"), "not definitely assigned"),
    ("continue-assignment-not-da-after-loop", original(
        "int result;for(int value:new int[]{42}){result=value;continue;}return result;"), "not definitely assigned"),
    ("iteration-variable-does-not-escape", original(
        "for(int value:new int[]{42}){}return value;"), "undefined local"),
    ("body-local-does-not-escape", original(
        "for(int value:new int[]{42}){int result=value;}return result;"), "undefined local"),
    ("not-in-scope-in-iterable", original(
        "for(int value:new int[]{value}){}return 42;"), "undefined local"),
    ("same-name-as-outer-local", original(
        "int value=42;for(int value:new int[]{42}){}return value;"), "already in scope"),
    ("same-name-as-array-local", original(
        "int[] values={42};for(int values:values){}return 42;"), "already in scope"),
    ("same-name-as-parameter",
        "class ArrayExample{static void use(int value){for(int value:new int[]{42}){}}}", "already in scope"),
    ("duplicate-inside-body", original(
        "for(int value:new int[]{42}){int value=0;}return 42;"), "already in scope"),
    ("duplicate-in-nested-loop", original(
        "for(int value:new int[]{42}){for(int value:new int[]{42}){}}return 42;"), "already in scope"),
    ("assign-final-variable", original(
        "for(final int value:new int[]{42}){value=0;}return 42;"), "final local"),
    ("increment-final-variable", original(
        "for(final int value:new int[]{42}){value++;}return 42;"), "final local"),
    ("compound-final-variable", original(
        "for(final int value:new int[]{42}){value+=1;}return 42;"), "final local"),
    ("final-iteration-variable-is-not-constant", original(
        "for(final int value:new int[]{42}){switch(42){case value:break;}}return 42;"), "constant expression"),
    ("nonconstant-body-read-cannot-narrow", original(
        "for(final int value:new int[]{42}){byte copy=value;}return 42;"), "assignment conversion"),
    ("scalar-iterable", original("for(int value:42){}return 42;"), "array|Iterable"),
    ("boolean-iterable", original("for(int value:true){}return 42;"), "array|Iterable"),
    ("null-type-is-not-array", original("for(int value:null){}return 42;"), "array|Iterable"),
    ("body-return-does-not-prove-method-return",
        "class ArrayExample{static int run(){for(int value:new int[]{42})return value;}}", "missing return"),
    ("statement-after-body-break-unreachable", original(
        "for(int value:new int[]{42}){break;value++;}return 42;"), "unreachable"),
    ("statement-after-body-continue-unreachable", original(
        "for(int value:new int[]{42}){continue;value++;}return 42;"), "unreachable"),
    ("statement-after-body-return-unreachable", original(
        "for(int value:new int[]{42}){return value;value++;}return 42;"), "unreachable"),
    ("loop-after-return-unreachable", original(
        "return 42;for(int value:new int[]{42}){}"), "unreachable"),
    ("undefined-continue-label", original(
        "for(int value:new int[]{42}){continue missing;}return 42;"), "undefined enclosing"),
    ("continue-label-is-block", original(
        "outer:{for(int value:new int[]{42}){continue outer;}}return 42;"), "directly label a loop"),
    ("duplicate-enclosing-label", original(
        "outer:for(int value:new int[]{42}){outer:for(int other:new int[]{42}){}}return 42;"), "duplicate enclosing"),
    ("initializer-forbidden", original(
        "for(int value=0:new int[]{42}){}return 42;"), "expected ';'"),
    ("multiple-declarators-forbidden", original(
        "for(int value,other:new int[]{42}){}return 42;"), "expected ';'"),
    ("duplicate-final-modifier", original(
        "for(final final int value:new int[]{42}){}return 42;"), "duplicate final"),
]

# These are valid Java, but not part of the admitted model domain. They still
# receive independent JDK typechecks without treating UnsupportedJava as an
# invalid-language reference result.
BOUNDARIES = [
    ("inferred-var-loop", original("for(var value:new int[]{42}){}return 42;")),
    ("final-inferred-var-loop", original("for(final var value:new int[]{42}){}return 42;")),
    ("boxed-loop-variable", original("for(Integer value:new int[]{42}){}return 42;")),
    ("raw-iterable-loop",
        "class ArrayExample{static void consume(Iterable values){for(Object value:values){}}}"),
    ("reference-array-loop",
        "class ArrayExample{static void consume(String[] values){for(String value:values){}}}"),
    ("multidimensional-array-loop",
        "class ArrayExample{static void consume(int[][] values){for(int[] value:values){}}}"),
    ("float-array-loop",
        "class ArrayExample{static void consume(float[] values){for(float value:values){}}}"),
    ("double-array-loop",
        "class ArrayExample{static void consume(double[] values){for(double value:values){}}}"),
    ("reference-assignment-expression", original(
        "int[] values;for(int value:(values=new int[]{42})){}return values[0];")),
]
BOUNDARY_CODES = {
    "inferred-var-loop": "java.unsupported.local-inference-domain",
    "final-inferred-var-loop": "java.unsupported.local-inference-domain",
    "boxed-loop-variable": "java.unsupported.source-dependency",
    "raw-iterable-loop": "java.unsupported.source-dependency",
    "reference-array-loop": "java.unsupported.array-model-shape",
    "multidimensional-array-loop": "java.unsupported.array-model-shape",
    "float-array-loop": "java.unsupported.array-model-shape",
    "double-array-loop": "java.unsupported.array-model-shape",
    "reference-assignment-expression": "java.unsupported.assignment-expression-target",
}

# These supplied classes have no JDK wrapper or Iterable supertype. The JDK
# rejects these exact originals; PCC instead reports its incomplete reference
# conversion/hierarchy domain, without pretending either source is supported.
INCOMPLETE_TYPE_BOUNDARIES = [
    ("owned-reference-loop-variable", original(
        "for(ArrayExample value:new int[]{42}){}return 42;"),
        "java.unsupported.enhanced-for-boxing"),
    ("owned-reference-iterable",
        "class ArrayExample{static void consume(ArrayExample values){for(int value:values){}}}",
        "java.unsupported.enhanced-for-iterable"),
]


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def checked_program(name, rows=PROGRAMS):
    text = dict(rows)[name]
    return analyze_class_sources({"ArrayExample.java": text}).classes["ArrayExample"]


@pytest.mark.parametrize("name,text", PROGRAMS + VALID_ONLY,
                         ids=[row[0] for row in PROGRAMS + VALID_ONLY])
def test_complete_originals_have_checked_enhanced_for_models_only(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer

    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__",
                        lambda *args: pytest.fail("array iteration constructed native lowering"))
    sources = {"ArrayExample.java": text}
    project = analyze_class_sources(sources)
    assert sources == {"ArrayExample.java": text}
    assert not project.models["ArrayExample"].runtime_registered
    assert "java/lang/Object" not in project.classes


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_enhanced_for_originals_are_language_errors_not_capability_gaps(name, text, message):
    with pytest.raises(JavaError, match=message) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.parametrize("name,text", PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_checked_array_iteration_never_grants_native_compilation(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer

    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__",
                        lambda *args: pytest.fail("managed iteration reached scalar IR"))
    for invoke in (lambda: compile_source(text, filename="ArrayExample.java"),
                   lambda: compile_sources({"ArrayExample.java": text}, entry_class="ArrayExample")):
        with pytest.raises(UnsupportedJava) as caught:
            invoke()
        assert caught.value.code == "java.unsupported.array-storage"


@pytest.mark.parametrize("name,text", BOUNDARIES, ids=[row[0] for row in BOUNDARIES])
def test_valid_wider_iteration_domains_keep_explicit_capability_boundaries(name, text):
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert caught.value.code == BOUNDARY_CODES[name]


@pytest.mark.parametrize("name,text,code", INCOMPLETE_TYPE_BOUNDARIES,
                         ids=[row[0] for row in INCOMPLETE_TYPE_BOUNDARIES])
def test_incomplete_owned_reference_domains_are_not_guessed(name, text, code):
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert caught.value.code == code


def test_iteration_plan_preserves_original_expression_once_and_managed_requirements():
    program = checked_program("evaluate-iterable-once")
    method = program.methods[0]
    loop, = [node for node in walk(method.body) if node.kind == "enhanced_for"]
    declaration, iterable, body = loop.children
    assert declaration.kind == "enhanced_var" and declaration.children == []
    assert isinstance(declaration.symbol, Binding)
    assert (declaration.symbol.name, declaration.symbol.dtype) == ("value", "int")
    assert iterable.kind == "call" and iterable.symbol is program.methods[1]
    assert iterable.dtype == ArrayType("int") and body.kind == "block"
    assert sum(node is iterable for node in walk(method.body)) == 1
    assert [binding.name for binding in method.locals] == ["log", "sum", "value"]
    plan = loop.value
    assert plan.array_type == ArrayType("int") and plan.variable_type == "int"
    assert plan.captured_array is iterable.managed_value
    assert plan.captured_array is not None
    assert set(unmet_requirements(plan.captured_array)) <= set(plan.managed_requirements)
    assert {
        "enhanced-for-captured-array-root-through-loop-exit",
        "enhanced-for-captured-array-relocation-aware-access",
        "enhanced-for-captured-array-root-retirement-on-every-exit",
    } <= set(plan.managed_requirements)
    assert not plan.runtime_registered
    assert plan.entry_steps == (
        "evaluate-array-expression-once", "retain-captured-array", "initialize-int-index-zero")
    assert plan.condition_steps == (
        "captured-array", "null-check", "read-length", "compare-index-less-than-length")
    assert plan.iteration_steps == (
        "captured-array", "index", "bounds-check", "read-component",
        "assignment-conversion", "initialize-loop-variable", "body")
    assert plan.continue_steps == ("increment-int-index", "test-condition")
    assert plan.normal_completion_steps == plan.continue_steps
    with pytest.raises(FrozenInstanceError):
        plan.variable_type = "long"
    with pytest.raises(FrozenInstanceError):
        declaration.symbol.is_final = True


def test_iterable_expression_is_checked_once_before_iteration_binding(monkeypatch):
    from pcc.frontends.java.source_class_model import ClassAnalyzer

    original_expression = ClassAnalyzer.expression
    visits = []

    def observe_expression(analyzer, node, assigned):
        if node.kind == "call" and node.value == "values":
            visits.append((node, {name for scope in analyzer.scopes for name in scope}))
        return original_expression(analyzer, node, assigned)

    monkeypatch.setattr(ClassAnalyzer, "expression", observe_expression)
    program = checked_program("evaluate-iterable-once")
    loop, = [node for node in walk(program.methods[0].body) if node.kind == "enhanced_for"]
    assert len(visits) == 1
    iterable, names_in_scope = visits[0]
    assert iterable is loop.children[1]
    assert {"log", "sum"} <= names_in_scope and "value" not in names_in_scope


@pytest.mark.parametrize("source,target", [
    (source, target) for source, targets in ASSIGNABLE_TARGETS.items() for target in targets
])
def test_plan_retains_element_type_separately_from_iteration_variable_type(source, target):
    program = checked_program("element-" + source + "-to-" + target)
    loop, = [node for node in walk(program.methods[0].body) if node.kind == "enhanced_for"]
    declaration, iterable, _ = loop.children
    assert iterable.dtype == ArrayType(source)
    assert declaration.symbol.dtype == target
    assert loop.value.array_type == ArrayType(source) and loop.value.variable_type == target
    assert declaration.constant is None


def test_final_binding_is_assigned_but_never_a_constant_variable():
    program = checked_program("final-variable")
    loop, = [node for node in walk(program.methods[0].body) if node.kind == "enhanced_for"]
    declaration, _, body = loop.children
    assert declaration.is_final and declaration.symbol.is_final
    reads = [node for node in walk(body) if node.kind == "name" and node.value == "value"]
    assert reads and all(node.symbol is declaration.symbol for node in reads)
    assert all(node.dtype == "int" and node.constant is None for node in reads)


def test_iterable_field_lookup_precedes_shadowing_binding_scope():
    program = checked_program("instance-field-shadowed-only-in-body", VALID_ONLY)
    loop, = [node for node in walk(program.methods[0].body) if node.kind == "enhanced_for"]
    declaration, iterable, body = loop.children
    assert iterable.symbol is program.fields[0] and iterable.dtype == ArrayType("int")
    assert declaration.symbol.name == "values" and declaration.symbol.dtype == "int"
    read, = [node for node in walk(body) if node.kind == "name" and node.value == "values"]
    assert read.symbol is declaration.symbol and read.dtype == "int"
    assert iterable.symbol is not declaration.symbol


def test_reused_iteration_names_have_distinct_lexical_bindings():
    program = checked_program("successive-loops-reuse-name")
    loops = [node for node in walk(program.methods[0].body) if node.kind == "enhanced_for"]
    first, second = [loop.children[0].symbol for loop in loops]
    assert first.name == second.name == "value"
    assert first is not second and first.slot != second.slot
    for loop in loops:
        reads = [node for node in walk(loop.children[2]) if node.kind == "name" and node.value == "value"]
        assert reads and all(node.symbol is loop.children[0].symbol for node in reads)


@pytest.mark.parametrize("name,jump_kind,target_kind", [
    ("labeled-break-is-outer", "break", "label"),
    ("labeled-continue-is-outer", "continue", "enhanced_for"),
    ("enhanced-loop-continues-basic-loop", "continue", "for"),
])
def test_labeled_jumps_retain_exact_outer_target_node(name, jump_kind, target_kind):
    program = checked_program(name)
    nodes = list(walk(program.methods[0].body))
    label, = [node for node in nodes if node.kind == "label" and node.value == "outer"]
    jump, = [node for node in nodes if node.kind == jump_kind and node.value == "outer"]
    expected = label if jump_kind == "break" else label.children[0]
    assert expected.kind == target_kind and jump.control_target is expected


def test_switch_break_does_not_steal_enhanced_loop_continue_target():
    program = checked_program("switch-break-and-loop-continue")
    nodes = list(walk(program.methods[0].body))
    loop, = [node for node in nodes if node.kind == "enhanced_for"]
    switch, = [node for node in nodes if node.kind == "switch"]
    jump, = [node for node in nodes if node.kind == "continue"]
    breaks = [node for node in nodes if node.kind == "break"]
    assert jump.control_target is loop
    assert breaks and all(node.control_target is switch for node in breaks)


def test_abrupt_body_does_not_make_zero_iteration_exit_unreachable():
    program = checked_program("return-from-body")
    loop, tail = program.methods[0].body.children
    assert loop.kind == "enhanced_for" and loop.completes
    assert not loop.children[2].completes
    assert tail.kind == "return" and not tail.completes


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-enhanced-for-model-reference")
def test_exact_enhanced_for_originals_against_selected_jdk_only(tmp_path, request):
    """Reference runtime/typechecks only; no PCC runtime or native acceptance."""
    check_originals_reference(
        tmp_path, request, PROGRAMS, VALID_ONLY + BOUNDARIES,
        INVALID + INCOMPLETE_TYPE_BOUNDARIES,
        source_input=Path(__file__),
    )
    # javac status 1 is a source compile error. Infrastructure/resource/tool
    # failures must never masquerade as expected rejection of an original.
    for name, _, _ in INVALID + INCOMPLETE_TYPE_BOUNDARIES:
        directory = tmp_path / name
        assert (directory / "compiler-status.txt").read_text() == "1"
        assert b"ArrayExample.java:" in (directory / "compiler.stderr.bin").read_bytes()
