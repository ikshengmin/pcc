"""Integral division/remainder source plans, never owned exception execution.

JLS21 5.6, 15.7.1/2, 15.17.2/3, 15.26.2, 15.29 and 16:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-5.html#jls-5.6
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.7.1
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.17.2
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.17.3
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.26.2
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.29
https://docs.oracle.com/javase/specs/jls/se21/html/jls-16.html

Every independently authored fixture is a complete original Java unit. PCC
checks source plans only. The selected JDK alone executes the unchanged units
with separate observers. The upstream Primes inputs remain whole and guarded;
these fixtures do not replace their build or original tests. All new execution
obligations are authored UNRUN for CI.
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
from pcc.frontends.java.source_integral_arithmetic_model import (
    INTEGRAL_ABRUPT_OPERAND_STEPS,
    INTEGRAL_ARITHMETIC_REQUIREMENTS,
    INTEGRAL_ZERO_STEPS,
    IntegralArithmeticContextObligation,
    IntegralArithmeticPlan,
)
from pcc.frontends.java.source_syntax import Node, SourceParser, Token
from scripts.ci_java_primes_project import (
    ENTRY,
    PRODUCTION,
    TESTS,
    load_inputs,
    original_sources,
)
from tests.java.test_source_array_model import check_originals_reference
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_unicode_escapes import JDK_REASON


def original(body, helpers=""):
    return "class ArrayExample{static int run(){" + body + "}" + helpers + "}"


def instance_original(members, body):
    return "class ArrayExample{" + members + "int run(){" + body + "}}"


PROGRAMS = [
    ("int-division", original("int n=84,d=2;return n/d;")),
    ("int-negative-dividend", original("int n=-85,d=2;return -(n/d);")),
    ("int-negative-divisor", original("int n=85,d=-2;return -(n/d);")),
    ("int-negative-both", original("int n=-85,d=-2;return n/d;")),
    ("int-remainder-negative-dividend", original("int n=-85,d=43;return -(n%d);")),
    ("int-remainder-negative-divisor", original("int n=85,d=-43;return n%d;")),
    ("int-remainder-negative-both", original("int n=-85,d=-43;return -(n%d);")),
    ("int-min-overflow-quotient", original(
        "int n=-2147483648,d=-1;return n/d==n?42:0;")),
    ("int-min-overflow-remainder", original(
        "int n=-2147483648,d=-1;return n%d==0?42:0;")),
    ("long-negative-division", original("long n=-85L,d=2L;return (int)-(n/d);")),
    ("long-negative-remainder", original("long n=-85L,d=43L;return (int)-(n%d);")),
    ("long-min-overflow-quotient", original(
        "long n=-9223372036854775808L,d=-1L;return n/d==n?42:0;")),
    ("long-min-overflow-remainder", original(
        "long n=-9223372036854775808L,d=-1L;return n%d==0L?42:0;")),
    ("int-left-long-divisor", original("int n=84;long d=2L;return (int)(n/d);")),
    ("long-left-int-divisor", original("long n=85L;int d=43;return (int)(n%d);")),
    ("final-iteration-divisor-binary", original(
        "int n=85;for(final int p:new int[]{43}){n=n%p;}return n;")),
    ("final-iteration-divisor-compound", original(
        "int n=85;for(final int p:new int[]{43}){n%=p;}return n;")),
    ("narrow-binary-promotion", original("byte n=84;short d=2;return n/d;")),
    ("char-binary-promotion", original("char n=85,d=43;return n%d;")),
    ("local-divide-statement", original("int n=84,d=2;n/=d;return n;")),
    ("local-remainder-statement", original("int n=85,d=43;n%=d;return n;")),
    ("grouped-local-statement", original("int n=84,d=2;((n))/=d;return n;")),
    ("local-divide-expression", original("int n=84,d=2;return (n/=d);")),
    ("grouped-local-expression", original("int n=85,d=43;return (((n))%=d);")),
    ("local-saves-old-before-rhs-write", original("int n=84;n/=(n=2);return n;")),
    ("local-expression-saves-old-before-rhs-write", original("int n=85;return (n%=(n=43));")),
    ("local-nine-save-before-rhs", original("int n=9;n/=(n=3);return n==3?42:0;")),
    ("local-nine-mutation-save-before-rhs", original("int n=9;return (n/=(n=3))==3?42:0;")),
    ("local-nine-group-save-before-rhs", original("int n=9;((n))/=(n=3);return n==3?42:0;")),
    ("local-byte-narrow-after-division", original("byte n=-128;int d=-1;n/=d;return n==-128?42:0;")),
    ("local-char-narrow-after-division", original("char n=65535;int d=-1;n/=d;return n+41;")),
    ("local-short-long-promotion", original("short n=85;long d=43L;n%=d;return n;")),
    ("operand-assignment-definite-assignment", original("int n,d;int q=(n=84)/(d=2);return q+n-d-82;")),
    ("conditional-unselected-zero", original("return true?42:1/0;")),
    ("short-circuit-unselected-zero", original("return true||1/0==0?42:0;")),
    ("unselected-zero-statement", original("if(false){int n=1/0;}return 42;")),
    ("uncalled-zero-method", original("return 42;", "static int unused(){return 1%0;}")),
    ("array-divide-statement", original("int[] a={84};int d=2;a[0]/=d;return a[0];")),
    ("grouped-array-remainder-statement", original("int[] a={85};int d=43;((a[0]))%=d;return a[0];")),
    ("array-byte-narrow-after-division", original("byte[] a={-128};int d=-1;a[0]/=d;return a[0]==-128?42:0;")),
    ("array-char-narrow-after-division", original("char[] a={65535};int d=-1;a[0]/=d;return a[0]+41;")),
    ("array-short-long-promotion", original("short[] a={85};long d=43L;a[0]%=d;return a[0];")),
    ("array-long-int-promotion", original("long[] a={85L};int d=43;a[0]%=d;return (int)a[0];")),
    ("array-saves-old-before-rhs-write", original(
        "int[] a={84};a[0]/=rhs(a);return a[0];",
        "static int rhs(int[] a){a[0]=6;return 2;}")),
    ("array-saves-index-before-rhs-write", original(
        "int[] a={84,7};int i=0;a[i]/=(i=2);return i==2&&a[0]==42&&a[1]==7?42:0;")),
    ("array-saves-reference-and-index", original(
        "int[] a={84,7};int i=0,d=2;a[i++]/=d;return i==1&&a[0]==42&&a[1]==7?42:0;")),
    ("binary-evaluates-operands-left-to-right-once", original(
        "int[] log={0};int q=left(log)/right(log);return log[0]==12&&q==42?42:0;",
        "static int left(int[] log){log[0]=log[0]*10+1;return 84;}"
        "static int right(int[] log){log[0]=log[0]*10+2;return 2;}")),
]

INSTANCE_PROGRAMS = [
    ("implicit-field-divide", instance_original("int value=84;", "int d=2;value/=d;return value;")),
    ("explicit-field-remainder", instance_original("int value=85;", "int d=43;this.value%=d;return value;")),
    ("grouped-field-shadow", instance_original("int value=84;", "int value=7,d=2;((this.value))/=d;return this.value+value-7;")),
    ("field-byte-narrow", instance_original("byte value=-128;", "int d=-1;value/=d;return value==-128?42:0;")),
    ("field-long-promotion", instance_original("short value=85;", "long d=43L;value%=d;return value;")),
    ("field-saves-old-before-rhs-call", instance_original(
        "int value=84;int rhs(){value=6;return 2;}", "value/=rhs();return value;")),
    ("array-saves-reference-before-field-rebind", instance_original(
        "int[] values={84};int rhs(){values=new int[]{6};return 2;}",
        "int[] saved=values;values[0]/=rhs();return saved[0]==42&&values[0]==6?42:0;")),
    ("field-initializer-divide", instance_original(
        "int divisor=2;int value=84/divisor;", "return value;")),
    ("constructor-remainder", instance_original(
        "int value=85;ArrayExample(){int d=43;value%=d;}", "return value;")),
    ("instance-initializer-divide", instance_original(
        "int value=84;{int d=2;value/=d;}", "return value;")),
]

ZERO_PROGRAMS = [
    ("literal-zero-division", original("return 1/0;")),
    ("literal-zero-remainder", original("return 1%0;")),
    ("folded-zero-divisor", original("return 1/(2-2);")),
    ("long-zero-division", original("return (int)(1L/0L);")),
    ("long-zero-remainder", original("return (int)(1L%0L);")),
    ("final-local-zero", original("final int d=0;return 1/d;")),
    ("dynamic-local-zero", original("int d=0;return 1/d;")),
    ("literal-zero-local-compound", original("int n=1;n/=0;return n;")),
    ("zero-local-mutation", original("int n=1;return (n%=0);")),
    ("zero-does-not-make-tail-unreachable", original("int n=1/0;return 42;")),
    ("zero-does-not-make-loop-body-unreachable", original("while(1/0==0){return 42;}return 0;")),
    ("zero-does-not-infer-constant-value", original("final int n=1%0;return n;")),
]

# Uniform observers can see effects and retained stores without adding catch
# handlers to the exact original source passed to PCC.
ARITHMETIC_EFFECT_PROGRAMS = [
    ("binary-zero-after-both-operands",
        "class ArrayExample{static int run(int[] log,int[] a){return left(log)/right(log);}"
        "static int left(int[] log){log[0]=log[0]*10+1;return 84;}"
        "static int right(int[] log){log[0]=log[0]*10+2;return 0;}}"),
    ("array-zero-does-not-store",
        "class ArrayExample{static int run(int[] log,int[] a){a[index(log)]/=rhs(log);return 0;}"
        "static int index(int[] log){log[0]=log[0]*10+1;return 0;}"
        "static int rhs(int[] log){log[0]=log[0]*10+2;return 0;}}"),
]

ARRAY_SAVED_VALUE_PROGRAMS = [
    ("array-remainder-rhs-write-survives-failure",
        "class ArrayExample{static int run(int[] log,int[] a){a[index(log)]%=rhs(log,a);return 0;}"
        "static int index(int[] log){log[0]=log[0]*10+1;return 0;}"
        "static int rhs(int[] log,int[] a){log[0]=log[0]*10+2;a[0]=6;return 0;}}"),
]

ARRAY_CHECK_PROGRAMS = [
    ("array-division-check-before-rhs",
        "class ArrayExample{static int run(int[] log,int[] a){reference(log,a)[index(log)]/=rhs(log);return 0;}"
        "static int[] reference(int[] log,int[] a){log[0]=log[0]*10+1;return a;}"
        "static int index(int[] log){log[0]=log[0]*10+2;return 0;}"
        "static int rhs(int[] log){log[0]=log[0]*10+3;return 0;}}"),
    ("array-remainder-check-before-rhs",
        "class ArrayExample{static int run(int[] log,int[] a){reference(log,a)[index(log)]%=rhs(log);return 0;}"
        "static int[] reference(int[] log,int[] a){log[0]=log[0]*10+1;return a;}"
        "static int index(int[] log){log[0]=log[0]*10+2;return 0;}"
        "static int rhs(int[] log){log[0]=log[0]*10+3;return 0;}}"),
]

OPERAND_FAILURE_PROGRAMS = [
    ("left-operand-failure-skips-zero-rhs",
        "class ArrayExample{static int run(int[] log,int[] a){return left(log,a)/right(log);}"
        "static int left(int[] log,int[] a){log[0]=log[0]*10+1;return a[0];}"
        "static int right(int[] log){log[0]=log[0]*10+2;return 0;}}", 1),
    ("zero-times-failing-divisor-preserves-operand-failure",
        "class ArrayExample{static int run(int[] log,int[] a){return 1/(0*fail(log,a));}"
        "static int fail(int[] log,int[] a){log[0]=1;return a[0];}}", 1),
    ("right-operand-failure-is-not-arithmetic-exception",
        "class ArrayExample{static int run(int[] log,int[] a){return left(log)%right(log,a);}"
        "static int left(int[] log){log[0]=log[0]*10+1;return 84;}"
        "static int right(int[] log,int[] a){log[0]=log[0]*10+2;return a[0];}}", 12),
]

ARRAY_ABRUPT_PROGRAMS = [
    ("array-reference-failure-skips-index-and-rhs",
        "class ArrayExample{static int run(int[] log,int[] a){reference(log,a)[index(log)]/=rhs(log);return 0;}"
        "static int[] reference(int[] log,int[] a){log[0]=1;throw null;}"
        "static int index(int[] log){log[0]=log[0]*10+2;return 0;}"
        "static int rhs(int[] log){log[0]=log[0]*10+3;return 0;}}", 1),
    ("array-index-failure-skips-rhs",
        "class ArrayExample{static int run(int[] log,int[] a){reference(log,a)[index(log)]%=rhs(log);return 0;}"
        "static int[] reference(int[] log,int[] a){log[0]=1;return a;}"
        "static int index(int[] log){log[0]=log[0]*10+2;throw null;}"
        "static int rhs(int[] log){log[0]=log[0]*10+3;return 0;}}", 12),
]

FIELD_FAILURE_PROGRAMS = [
    ("field-rhs-write-survives-failed-division", instance_original(
        "int value=84;int log;int rhs(){log++;value=6;return 0;}", "value/=rhs();return 0;")),
    ("grouped-field-rhs-write-survives-failed-remainder", instance_original(
        "int value=84;int log;int rhs(){log++;value=6;return 0;}", "((this.value))%=rhs();return 0;")),
]

INVALID = [
    ("boolean-dividend", original("int d=2;return true/d;"), "integer value required"),
    ("boolean-divisor", original("int n=84;return n%false;"), "integer value required"),
    ("null-divisor", original("return 84/null;"), "integer value required"),
    ("void-divisor", original("return 84/nothing();", "static void nothing(){}"), "integer value required"),
    ("unassigned-dividend", original("int n,d=2;return n/d;"), "not definitely assigned"),
    ("unassigned-divisor", original("int n=84,d;return n/d;"), "not definitely assigned"),
    ("unassigned-compound-target", original("int n,d=2;n/=d;return 42;"), "not definitely assigned"),
    ("unassigned-mutation-target", original("int n,d=2;return (n%=d);"), "not definitely assigned"),
    ("rhs-assignment-does-not-initialize-old-target", original("int n;n/=(n=2);return 42;"), "not definitely assigned"),
    ("final-compound-target", original("final int n=84;int d=2;n/=d;return n;"), "cannot assign"),
    ("final-mutation-target", original("final int n=85;int d=43;return (n%=d);"), "cannot assign"),
    ("boolean-compound-target", original("boolean n=true;int d=2;n/=d;return 42;"), "compound arithmetic"),
    ("zero-case-label-is-not-constant", original("switch(0){case 1/0:return 42;default:return 0;}"), "constant expression"),
    ("zero-final-is-not-case-constant", original("final int n=1%0;switch(0){case n:return 42;default:return 0;}"), "constant expression"),
    ("zero-does-not-supply-missing-return", original("int n=1/0;"), "missing return"),
    ("zero-does-not-supply-da-path", original("int n;if(1/0==0)n=42;return n;"), "not definitely assigned"),
    ("conditional-zero-arm-is-not-constant", original("byte n=true?42:1/0;return n;"), "incompatible assignment"),
    ("zero-does-not-permit-implicit-byte-narrow", original("byte n=1/0;return n;"), "incompatible assignment"),
    ("zero-does-not-permit-implicit-char-narrow", original("char n=1%0;return n;"), "incompatible assignment"),
    ("unassigned-array-reference", original("int[] a;int d=2;a[0]/=d;return 42;"), "not definitely assigned"),
    ("array-length-remains-final", original("int[] a={84};int d=2;a.length/=d;return 42;"), "length is final"),
    ("long-index", original("int[] a={84};int d=2;a[0L]/=d;return 42;"), "promote to int"),
    ("boolean-component", original("boolean[] a={true};int d=2;a[0]%=d;return 42;"), "integer component"),
    ("final-receiver-field", instance_original("static final int value=84;", "int d=2;this.value/=d;return 42;"), "cannot assign a final"),
    ("forward-compound-field-read", instance_original("{int d=2;value/=d;}int value=84;", "return 42;"), "forward reference"),
    ("static-receiver-compound", "class ArrayExample{int value=84;static int run(){int d=2;this.value/=d;return 42;}}", "static context"),
]

GAPS = [
    ("field-expression-target", instance_original("int value=84;", "int d=2;return (this.value/=d);"), "assignment-expression-target"),
    ("array-expression-target", original("int[] a={84};int d=2;return (a[0]/=d);"), "assignment-expression-target"),
    ("float-remainder", original("float n=85.0f,d=43.0f;return (int)(n%d);"), "floating-remainder"),
    ("double-remainder-compound", original("double n=85.0,d=43.0;n%=d;return (int)n;"), "floating-remainder"),
    ("nonconstant-static-initializer", "class ArrayExample{static final int VALUE=1/0;}", "static-initialization"),
]

ADMITTED_ORIGINALS = (
    PROGRAMS + INSTANCE_PROGRAMS + ZERO_PROGRAMS + ARITHMETIC_EFFECT_PROGRAMS
    + ARRAY_SAVED_VALUE_PROGRAMS + ARRAY_CHECK_PROGRAMS
    + [(name, text) for name, text, _ in OPERAND_FAILURE_PROGRAMS + ARRAY_ABRUPT_PROGRAMS]
    + FIELD_FAILURE_PROGRAMS
)


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def source_nodes(project):
    for program in project.programs:
        for field in program.fields:
            if field.initializer is not None:
                yield from walk(field.initializer)
        for method in (*program.methods, *program.constructors):
            yield from walk(method.body)
        for initializer in program.instance_initializers:
            yield from walk(initializer)


def arithmetic_nodes(project):
    return [node for node in source_nodes(project) if node.arithmetic_plan is not None]


def obligations(project):
    return [item for item in project.compiler_context_obligations
            if isinstance(item, IntegralArithmeticContextObligation)]


@pytest.mark.parametrize("name,text", ADMITTED_ORIGINALS,
                         ids=[row[0] for row in ADMITTED_ORIGINALS])
def test_exact_integral_originals_have_nonlowering_immutable_source_plans(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer

    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__",
                        lambda *args: pytest.fail("integral source plan reached native lowering"))
    sources = {"ArrayExample.java": text}
    project = analyze_class_sources(sources)
    nodes = arithmetic_nodes(project)
    contexts = obligations(project)
    assert sources == {"ArrayExample.java": text}
    assert nodes and len(contexts) == len(nodes)
    assert not project.integral_arithmetic_context_complete
    assert not project.semantic_context_complete
    for node in nodes:
        plan = node.arithmetic_plan
        assert isinstance(plan, IntegralArithmeticPlan)
        assert plan.operator == node.value.removesuffix("=")
        assert plan.promoted_type == node.operation_type
        assert plan.promoted_type in {"int", "long"}
        assert plan.divisor_constant in (None, 0)
        if name.startswith("final-iteration-divisor-"):
            divisor = node.children[-1]
            assert divisor.symbol.is_final and divisor.symbol.name == "p"
            assert divisor.constant is plan.divisor_constant is None
            loop, = [item for item in source_nodes(project) if item.kind == "enhanced_for"]
            assert divisor.symbol is loop.children[0].symbol
        assert plan.runtime_exception_type == "java/lang/ArithmeticException"
        assert not plan.runtime_registered
        assert plan.requirements == INTEGRAL_ARITHMETIC_REQUIREMENTS
        assert node.constant is node.string_constant is node.managed_value is None
        assert all(isinstance(steps, tuple) for steps in (
            plan.operand_steps, plan.operation_steps, plan.zero_steps,
            plan.abrupt_operand_steps, plan.requirements,
        ))
        context, = [item for item in contexts if item.tokens[0] is node.token]
        assert context.owner == "ArrayExample"
        assert context.tokens == (node.token, node.children[-1].token)
        assert context.tokens[1] is node.children[-1].token
        assert context.requirements == INTEGRAL_ARITHMETIC_REQUIREMENTS
        assert context in project.models[context.owner].compiler_context_obligations
        assert set(plan.requirements) <= set(project.models[context.owner].managed_requirements)
        assert not hasattr(plan, "address") and not hasattr(plan, "object")
        with pytest.raises(FrozenInstanceError):
            plan.operator = "+"
        with pytest.raises(FrozenInstanceError):
            context.owner = "invented"
    assert all(not model.runtime_registered for model in project.models.values())
    assert not {"java/lang/Object", "java/lang/Throwable", "java/lang/ArithmeticException"} & project.classes.keys()


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_integral_originals_remain_ordinary_java_errors(name, text, message):
    with pytest.raises(JavaError, match=message) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert type(caught.value) is JavaError and caught.value.code is None
    assert "ArrayExample.java:" in str(caught.value)


@pytest.mark.parametrize("name,text,code", GAPS, ids=[row[0] for row in GAPS])
def test_integral_plan_does_not_widen_other_frontend_boundaries(name, text, code):
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert caught.value.code == "java.unsupported." + code
    assert "ArrayExample.java:" in str(caught.value)


@pytest.mark.parametrize("name,target_kind,target_type,promoted_type", [
    ("int-division", "value", None, "int"),
    ("int-left-long-divisor", "value", None, "long"),
    ("long-left-int-divisor", "value", None, "long"),
    ("narrow-binary-promotion", "value", None, "int"),
    ("local-divide-statement", "local", "int", "int"),
    ("local-divide-expression", "local", "int", "int"),
    ("local-byte-narrow-after-division", "local", "byte", "int"),
    ("local-short-long-promotion", "local", "short", "long"),
    ("grouped-field-shadow", "receiver-field", "int", "int"),
    ("field-byte-narrow", "receiver-field", "byte", "int"),
    ("field-long-promotion", "receiver-field", "short", "long"),
    ("array-divide-statement", "array-component", "int", "int"),
    ("array-char-narrow-after-division", "array-component", "char", "int"),
    ("array-short-long-promotion", "array-component", "short", "long"),
    ("array-long-int-promotion", "array-component", "long", "long"),
])
def test_source_plans_keep_promoted_operation_separate_from_store_type(name, target_kind, target_type, promoted_type):
    project = analyze_class_sources({"ArrayExample.java": dict(ADMITTED_ORIGINALS)[name]})
    node, = arithmetic_nodes(project)
    plan = node.arithmetic_plan
    assert (plan.target_kind, plan.target_type, plan.promoted_type) == (target_kind, target_type, promoted_type)
    if target_kind in {"local", "receiver-field"}:
        assert node.symbol.dtype == target_type
    if target_kind == "array-component":
        target = node.children[0]
        while target.kind == "group":
            target = target.children[0]
        assert target.kind == "array_place" and target.dtype == target_type
        assert node.runtime_steps == (
            "array-reference", "index", "null-check", "bounds-check", "read-component",
            "rhs", "operation", "narrow", "store-component",
        )



@pytest.mark.parametrize("name", [
    "int-division", "long-left-int-divisor", "local-divide-statement",
    "local-divide-expression", "local-short-long-promotion", "grouped-field-shadow",
    "field-long-promotion", "array-divide-statement", "array-short-long-promotion",
])
def test_plan_separates_operand_completion_zero_exception_and_successful_store(name):
    project = analyze_class_sources({"ArrayExample.java": dict(ADMITTED_ORIGINALS)[name]})
    node, = arithmetic_nodes(project)
    plan = node.arithmetic_plan
    expected_operands = {
        "value": (
            "evaluate-dividend-once", "branch-on-dividend-completion",
            "evaluate-divisor-once", "branch-on-divisor-completion",
        ),
        "local": (
            "remember-local-variable", "read-and-save-lhs-value",
            "evaluate-divisor-once", "branch-on-divisor-completion",
        ),
        "receiver-field": (
            "retain-current-receiver", "remember-instance-field", "read-and-save-lhs-value",
            "evaluate-divisor-once", "branch-on-divisor-completion",
        ),
        "array-component": (
            "evaluate-array-reference-once", "branch-on-array-reference-completion",
            "evaluate-index-once", "branch-on-index-completion", "null-check", "bounds-check",
            "branch-on-array-access-check-completion", "remember-array-component",
            "read-and-save-lhs-value", "evaluate-divisor-once", "branch-on-divisor-completion",
        ),
    }
    assert plan.operand_steps == expected_operands[plan.target_kind]
    operation = (
        "binary-numeric-promotion-to-" + plan.promoted_type,
        "test-divisor-zero-after-operands", "branch-on-zero",
        "handle-MIN-by-minus-one-without-overflow-trap", "truncate-quotient-toward-zero",
        "produce-quotient" if plan.operator == "/" else "produce-dividend-minus-quotient-times-divisor",
    )
    if plan.target_type is not None:
        operation += (
            "convert-result-to-" + plan.target_type,
            "store-saved-variable-only-after-normal-operation", "yield-stored-value",
        )
    assert plan.operation_steps == operation
    assert plan.zero_steps == INTEGRAL_ZERO_STEPS == (
        "create-owned-ArithmeticException-or-propagate-allocation-failure",
        "retain-exception-in-owning-root", "match-handler-and-unwind-with-cleanup",
        "transfer-to-handler-or-uncaught-exception-owner",
    )
    assert plan.abrupt_operand_steps == INTEGRAL_ABRUPT_OPERAND_STEPS == (
        "skip-remaining-operands", "propagate-original-exception-unchanged",
        "do-not-perform-arithmetic-or-store",
    )
    assert plan.requirements == INTEGRAL_ARITHMETIC_REQUIREMENTS == (
        "owned-integral-divisor-check-and-Java-MIN-minus-one-lowering",
        "owned-bootstrap-Throwable-hierarchy-and-runtime-type-identity",
        "owned-ArithmeticException-definition-allocation-and-initialization",
        "operand-and-exception-allocation-abrupt-completion",
        "shared-owning-root-and-safepoint-ABI",
        "exception-root-survival-through-handler-search-and-relocation",
        "shared-owned-exception-unwind-and-handler-dispatch",
        "Java-finally-monitor-cleanup-and-cross-language-unwind-policy",
        "owned-Java-uncaught-exception-delivery",
    )


def test_plan_absence_is_not_runtime_completion_and_node_default_is_empty():
    assert Node("integer", Token("1", "ArrayExample.java", 1, 1), "1").arithmetic_plan is None
    project = analyze_class_sources({"ArrayExample.java": original("return 42;")})
    assert project.integral_arithmetic_context_complete and not obligations(project)
    assert not project.semantic_context_complete
    assert not project.models["ArrayExample"].runtime_registered


@pytest.mark.parametrize("body", [
    "return 84/2;",
    "return -85%-43+84;",
    "final int d=2;int n=84;return n/d;",
    "int n=84;n/=2;return n;",
    "double n=84.0,d=2.0;return (int)(n/d);",
    "float n=84.0f,d=2.0f;n/=d;return (int)n;",
    "double n=1.0/0.0;return n>0.0?42:0;",
])
def test_proven_nonzero_and_floating_division_keep_existing_semantic_owner(body, monkeypatch):
    from pcc.frontends.java.source_sema import SourceAnalyzer

    original_domain = SourceAnalyzer.arithmetic_domain
    calls = []

    def observe_domain(analyzer, node, operator, dtype, divisor):
        calls.append((node, operator, dtype, divisor))
        return original_domain(analyzer, node, operator, dtype, divisor)

    monkeypatch.setattr(SourceAnalyzer, "arithmetic_domain", observe_domain)
    project = analyze_class_sources({"ArrayExample.java": original(body)})
    assert calls and all(node.arithmetic_plan is None for node, _, _, _ in calls)
    assert project.integral_arithmetic_context_complete and not obligations(project)


@pytest.mark.parametrize("name", [
    "int-division", "local-divide-statement", "local-divide-expression",
    "grouped-field-shadow", "array-saves-old-before-rhs-write",
    "binary-evaluates-operands-left-to-right-once",
])
def test_original_operands_and_target_bindings_are_retained_and_checked_once(name, monkeypatch):
    from pcc.frontends.java.source_class_model import ClassAnalyzer

    original_expression = ClassAnalyzer.expression
    original_domain = ClassAnalyzer.arithmetic_domain
    visits, sites = [], []

    def observe_expression(analyzer, node, assigned):
        visits.append(node)
        return original_expression(analyzer, node, assigned)

    def observe_domain(analyzer, node, operator, dtype, divisor):
        sites.append((node, tuple(node.children), divisor))
        return original_domain(analyzer, node, operator, dtype, divisor)

    monkeypatch.setattr(ClassAnalyzer, "expression", observe_expression)
    monkeypatch.setattr(ClassAnalyzer, "arithmetic_domain", observe_domain)
    project = analyze_class_sources({"ArrayExample.java": dict(ADMITTED_ORIGINALS)[name]})
    assert len(sites) == 1 and len(arithmetic_nodes(project)) == 1
    node, children, divisor = sites[0]
    assert all(actual is saved for actual, saved in zip(node.children, children))
    assert len(node.children) == len(children)
    assert sum(item is divisor for item in visits) == 1
    if node.kind in {"binary", "array_assign"}:
        assert sum(item is children[0] for item in visits) == 1
    if node.kind == "mutation":
        assert children[0].symbol is node.symbol
        assert sum(item is children[0] for item in visits) == 0
    if name == "grouped-field-shadow":
        assert node.symbol is project.classes["ArrayExample"].fields[0]
        assert node.symbol is not project.classes["ArrayExample"].methods[0].locals[0]
    subtree = list(walk(node))
    assert len({id(item) for item in subtree}) == len(subtree)


def test_contexts_keep_actual_method_constructor_initializer_and_field_identity():
    text = (
        "class ArrayExample{int divisor=2;int value=84/divisor;"
        "{int d=43;value%=d;}ArrayExample(){int d=2;value/=d;}"
        "int divide(int n,int d){return n/d;}long divide(long n,long d){return n%d;}}"
    )
    project = analyze_class_sources({"ArrayExample.java": text})
    program = project.classes["ArrayExample"]
    contexts = obligations(project)
    assert len(contexts) == len(arithmetic_nodes(project)) == 5
    assert {(item.context_kind, item.context_name, item.context_descriptor) for item in contexts} == {
        ("field-initializer", "value", "I"),
        ("instance-initializer", "<instance-initializer>", "()V"),
        ("constructor", "ArrayExample", "()V"),
        ("method", "divide", "(II)I"),
        ("method", "divide", "(JJ)J"),
    }
    for method in (*program.methods, *program.constructors):
        node, = [item for item in walk(method.body) if item.arithmetic_plan is not None]
        context, = [item for item in contexts if item.tokens[0] is node.token]
        assert context.owner == method.owner == program.name
        assert context.context_name == method.token.text
        assert context.context_descriptor == method.descriptor
    assert all(item.tokens[0].filename == "ArrayExample.java" for item in contexts)


def test_zero_divisor_is_not_folded_or_used_as_constant_flow():
    text = original("final int n=1/0;int q=true?42:1%0;return q;")
    project = analyze_class_sources({"ArrayExample.java": text})
    method = project.classes["ArrayExample"].methods[0]
    first, second, tail = method.body.children
    assert first.completes and second.completes and tail.kind == "return"
    assert first.children[0].constant is None and second.children[0].constant is None
    assert all(node.arithmetic_plan.divisor_constant == 0 for node in arithmetic_nodes(project))
    assert not method.body.completes


def test_operand_assignments_are_definitely_assigned_on_the_normal_successor():
    project = analyze_class_sources({"ArrayExample.java": dict(PROGRAMS)["operand-assignment-definite-assignment"]})
    method = project.classes["ArrayExample"].methods[0]
    node, = arithmetic_nodes(project)
    mutations = [item for item in walk(node) if item.kind == "mutation"]
    assert len(mutations) == 2 and mutations[0].symbol is not mutations[1].symbol
    assert all(symbol in state for state in node.flow_outcomes for symbol in (item.symbol for item in mutations))
    assert node.constant is None and method.body.children[-1].kind == "return"


NATIVE_ORIGINALS = [
    dict(PROGRAMS)[name] for name in (
        "int-division", "long-left-int-divisor", "local-divide-statement",
        "local-remainder-statement", "local-divide-expression", "grouped-local-expression",
        "conditional-unselected-zero", "uncalled-zero-method",
    )
] + [dict(ZERO_PROGRAMS)[name] for name in (
    "literal-zero-division", "literal-zero-remainder", "long-zero-division", "literal-zero-local-compound",
)]


@pytest.mark.parametrize("text", NATIVE_ORIGINALS)
def test_public_native_entries_still_reject_integral_runtime_arithmetic_before_ir(text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer

    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__",
                        lambda *args: pytest.fail("integral model reached scalar native IR"))
    for invoke in (
        lambda: compile_source(text, filename="ArrayExample.java"),
        lambda: compile_sources({"ArrayExample.java": text}, entry_class="ArrayExample"),
    ):
        with pytest.raises(UnsupportedJava) as caught:
            invoke()
        assert caught.value.code == "java.unsupported.arithmetic-exception"
        assert "ArrayExample.java:" in str(caught.value)


def test_complete_primes_original_keeps_modulo_site_and_existing_project_boundaries():
    _, files = load_inputs()
    sources = original_sources(files, PRODUCTION)
    before = dict(sources)
    first = PRODUCTION[0]
    parsed = []

    class OriginalParser(SourceParser):
        def expression(self, minimum=0, *, statement_target=False):
            node = super().expression(minimum, statement_target=statement_target)
            if node.kind == "binary" and node.value == "%":
                parsed.append(node)
            return node

    # The class model parses the complete Primes original, including the
    # List<Integer> result type of primeFactors.
    OriginalParser(sources[first], first, class_model=True).parse()
    assert [(node.token.line, node.value) for node in parsed] == [(54, "%"), (83, "%")]
    node, = [node for node in parsed if node.token.line == 54]
    assert [child.value for child in node.children] == ["n", "p"]
    assert node.arithmetic_plan is None  # Parsing never proves typed runtime plans.
    with pytest.raises(UnsupportedJava) as class_error:
        analyze_class_sources(sources)
    # SmallPrimes.PRIMES, a static array field, needs class initialization.
    assert class_error.value.code == "java.unsupported.array-storage"
    assert PRODUCTION[1] + ":39:18:" in str(class_error.value)
    with pytest.raises(UnsupportedJava) as native_error:
        compile_sources(sources, **ENTRY)
    # The String constant variable folds; the enhanced for and the nextPrime
    # throw parse; the List<Integer> result type of primeFactors is next.
    assert native_error.value.code == "java.unsupported.declared-result-type"
    assert first + ":108:19:" in str(native_error.value)
    assert sources == before
    assert all(sources[name].encode("utf-8") == files[name] for name in sources)


def test_complete_original_smallprimes_arithmetic_sites_and_upstream_tests_stay_intact():
    lock, files = load_inputs()
    sources = original_sources(files, PRODUCTION + TESTS)
    lines = sources[PRODUCTION[1]].splitlines()
    assert lines[152].strip() == "while (0 == n % p) {"
    assert lines[153].strip() == "n /= p;"
    assert lines[197].strip() == "} else if (0 == n % f) {"
    assert lines[198].strip() == "n /= f;"
    assert lines[269].strip() == "final long square = ((long) y) * y;"
    assert lines[270].strip() == "y = (int) (square % n);"
    assert tuple(sources) == PRODUCTION + TESTS and len(files) == 17
    assert lock["complete_upstream_test_source_closure"] is True
    assert lock["complete_build_closure"] is False
    assert all(sources[name].encode("utf-8") == files[name] for name in sources)
    assert all(b"@Test" in files[name] for name in TESTS)


def test_complete_smallprimes_original_still_stops_at_static_array_storage():
    _, files = load_inputs()
    sources = original_sources(files, PRODUCTION)
    path = PRODUCTION[1]
    before = sources[path]
    with pytest.raises(UnsupportedJava) as caught:
        SourceParser(sources[path], path, class_model=True).parse()
    assert caught.value.code == "java.unsupported.array-storage"
    assert path + ":39:" in str(caught.value)
    assert sources[path] == before and before.encode("utf-8") == files[path]


def assert_source_rejections(root, cases):
    for name, _, _ in cases:
        directory = root / name
        assert (directory / "compiler-status.txt").read_text() == "1"
        assert b"ArrayExample.java:" in (directory / "compiler.stderr.bin").read_bytes()


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-integral-arithmetic-reference")
def test_exact_integral_originals_and_source_errors_against_selected_jdk_only(tmp_path, request):
    check_originals_reference(
        tmp_path, request, PROGRAMS, [(name, text) for name, text, _ in GAPS], INVALID,
        source_input=Path(__file__),
    )
    assert_source_rejections(tmp_path, INVALID)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-integral-arithmetic-reference")
def test_exact_receiver_integral_originals_against_selected_jdk_only(tmp_path, request):
    check_originals_reference(
        tmp_path, request, INSTANCE_PROGRAMS, [], [],
        invocation="new ArrayExample().run()", source_input=Path(__file__),
    )


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-integral-arithmetic-reference")
def test_exact_zero_divisors_raise_real_jdk_arithmetic_exception(tmp_path, request):
    invocation = (
        "((java.util.function.IntSupplier)()->{try{ArrayExample.run();return 0;}"
        "catch(ArithmeticException expected){return 42;}}).getAsInt()"
    )
    check_originals_reference(
        tmp_path, request, ZERO_PROGRAMS, [], [], invocation=invocation, source_input=Path(__file__),
    )


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-integral-arithmetic-reference")
def test_exact_effects_precede_jdk_zero_check_without_failed_store(tmp_path, request):
    invocation = (
        "((java.util.function.IntSupplier)()->{int[] log={0},a={84};"
        "try{ArrayExample.run(log,a);return 0;}"
        "catch(ArithmeticException expected){return log[0]==12&&a[0]==84?42:0;}}).getAsInt()"
    )
    check_originals_reference(
        tmp_path, request, ARITHMETIC_EFFECT_PROGRAMS, [], [],
        invocation=invocation, source_input=Path(__file__),
    )


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-integral-arithmetic-reference")
def test_exact_array_rhs_mutation_is_not_rolled_back_by_jdk_failed_remainder(tmp_path, request):
    invocation = (
        "((java.util.function.IntSupplier)()->{int[] log={0},a={84};"
        "try{ArrayExample.run(log,a);return 0;}"
        "catch(ArithmeticException expected){return log[0]==12&&a[0]==6?42:0;}}).getAsInt()"
    )
    check_originals_reference(
        tmp_path, request, ARRAY_SAVED_VALUE_PROGRAMS, [], [],
        invocation=invocation, source_input=Path(__file__),
    )


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-integral-arithmetic-reference")
@pytest.mark.parametrize("array,exception,log", [
    ("null", "NullPointerException", 12),
    ("new int[0]", "ArrayIndexOutOfBoundsException", 12),
    ("new int[]{84}", "ArithmeticException", 123),
])
def test_exact_array_reference_index_checks_and_rhs_have_jdk_order(tmp_path, request, array, exception, log):
    invocation = (
        "((java.util.function.IntSupplier)()->{int[] log={0};int[] a=" + array + ";"
        "try{ArrayExample.run(log,a);return 0;}catch(" + exception + " expected){"
        "return log[0]==" + str(log) + "&&(a==null||a.length==0||a[0]==84)?42:0;}}).getAsInt()"
    )
    check_originals_reference(
        tmp_path, request, ARRAY_CHECK_PROGRAMS, [], [],
        invocation=invocation, source_input=Path(__file__),
    )


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-integral-arithmetic-reference")
@pytest.mark.parametrize("array,exception", [
    ("null", "NullPointerException"),
    ("new int[0]", "ArrayIndexOutOfBoundsException"),
])
@pytest.mark.parametrize("name,text,log", OPERAND_FAILURE_PROGRAMS,
                         ids=[row[0] for row in OPERAND_FAILURE_PROGRAMS])
def test_exact_abrupt_operands_keep_original_jdk_exception_identity(tmp_path, request, array, exception, name, text, log):
    invocation = (
        "((java.util.function.IntSupplier)()->{int[] log={0};int[] a=" + array + ";"
        "try{ArrayExample.run(log,a);return 0;}catch(" + exception + " expected){"
        "return log[0]==" + str(log) + "?42:0;}}).getAsInt()"
    )
    check_originals_reference(
        tmp_path, request, [(name, text)], [], [],
        invocation=invocation, source_input=Path(__file__),
    )


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-integral-arithmetic-reference")
def test_exact_receiver_rhs_mutation_survives_jdk_failed_compound_store(tmp_path, request):
    invocation = (
        "((java.util.function.IntSupplier)()->{ArrayExample value=new ArrayExample();"
        "try{value.run();return 0;}catch(ArithmeticException expected){"
        "return value.log==1&&value.value==6?42:0;}}).getAsInt()"
    )
    check_originals_reference(
        tmp_path, request, FIELD_FAILURE_PROGRAMS, [], [],
        invocation=invocation, source_input=Path(__file__),
    )


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-integral-arithmetic-reference")
@pytest.mark.parametrize("name,text,log", ARRAY_ABRUPT_PROGRAMS,
                         ids=[row[0] for row in ARRAY_ABRUPT_PROGRAMS])
def test_exact_abrupt_array_reference_or_index_skips_later_jdk_effects(tmp_path, request, name, text, log):
    invocation = (
        "((java.util.function.IntSupplier)()->{int[] log={0},a={84};"
        "try{ArrayExample.run(log,a);return 0;}catch(NullPointerException expected){"
        "return log[0]==" + str(log) + "&&a[0]==84?42:0;}}).getAsInt()"
    )
    check_originals_reference(
        tmp_path, request, [(name, text)], [], [],
        invocation=invocation, source_input=Path(__file__),
    )
