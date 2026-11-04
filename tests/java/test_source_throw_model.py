"""Null-typed throw source plans, never owned exception execution.

JLS21 4.1, 11.2.2, 14.18, 14.22 and 16.2.13:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-4.html#jls-4.1
https://docs.oracle.com/javase/specs/jls/se21/html/jls-11.html#jls-11.2.2
https://docs.oracle.com/javase/specs/jls/se21/html/jls-14.html#jls-14.18
https://docs.oracle.com/javase/specs/jls/se21/html/jls-14.html#jls-14.22
https://docs.oracle.com/javase/specs/jls/se21/html/jls-16.html#jls-16.2.13

Every fixture is a complete original Java unit. PCC checks its source model;
only the separately selected JDK executes it. NullPointerException names a
specified runtime obligation, not an invented bootstrap class or a registered
runtime type. All source-authored execution obligations remain UNRUN until CI.
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
from pcc.frontends.java.source_syntax import SourceParser
from pcc.frontends.java.source_throw_model import (
    THROW_NULL_STEPS,
    THROW_OPERAND_ABRUPT_STEPS,
    THROW_OPERAND_STEPS,
    THROW_REQUIREMENTS,
    ThrowContextObligation,
    ThrowSourcePlan,
)
from tests.java.test_source_array_model import check_originals_reference
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_unicode_escapes import JDK_REASON


def original(body, helpers=""):
    return "class ArrayExample{static int run(){" + body + "}" + helpers + "}"


# These exact originals return 42 normally in the selected-JDK observer. Their
# throw sites remain checked even when execution takes a different branch.
PROGRAMS = [
    ("unselected-if", original("if(false)throw null;return 42;")),
    ("unselected-else", original("if(true)return 42;else throw null;")),
    ("da-excludes-throwing-then", original(
        "int result;if(flag())throw null;else result=42;return result;",
        "static boolean flag(){return false;}")),
    ("da-excludes-throwing-else", original(
        "int result;if(flag())result=42;else throw null;return result;",
        "static boolean flag(){return true;}")),
    ("nested-conditional-block", original(
        "int result;if(flag()){{throw ((null));}}else{{result=42;}}return result;",
        "static boolean flag(){return false;}")),
    ("labeled-break-bypasses-throw", original(
        "outer:{if(flag())break outer;throw null;}return 42;",
        "static boolean flag(){return true;}")),
    ("while-zero-iterations", original(
        "int count=0;while(count>0){throw null;}return 42;")),
    ("basic-for-zero-iterations", original(
        "for(int count=0;count>0;count++){throw null;}return 42;")),
    ("enhanced-for-zero-iterations", original(
        "for(int value:new int[0]){throw null;}return 42;")),
    ("enhanced-for-conditional-throw", original(
        "int result=0;for(int value:new int[]{7,35}){if(value<0)throw null;result+=value;}"
        "return result;")),
    ("switch-colon-throw-or-return", original(
        "switch(1){case 0:throw null;default:return 42;}")),
    ("switch-arrow-throw-or-return", original(
        "switch(1){case 0->throw null;default->{return 42;}}")),
    ("switch-colon-da-excludes-throw", original(
        "int result;switch(1){case 0:throw null;default:result=42;}return result;")),
    ("switch-arrow-da-excludes-throw", original(
        "int result;switch(1){case 0->throw null;default->result=42;}return result;")),
    ("nested-switch-and-labeled-loop", original(
        "outer:for(int value:new int[]{1,2}){switch(value){case 1:continue outer;"
        "case 2:break outer;default:throw null;}}return 42;")),
    ("uncalled-throwing-method", original(
        "return 42;", "static int unused(){throw null;}")),
    ("unconstructed-throwing-constructor", original(
        "return 42;", "ArrayExample(){throw null;}")),
    ("unconstructed-conditional-initializer", original(
        "return 42;", "{if(fail())throw null;}static boolean fail(){return false;}")),
    ("method-constructor-initializer-sites", original(
        "return 42;", "static int unused(){throw null;}"
        "ArrayExample(){throw null;}{if(false)throw null;}")),
]

# These run methods intentionally throw. A separate observer catches the JDK's
# actual NPE; the original unit contains no catch adapter and is never rewritten.
THROWN_PROGRAMS = [
    ("literal-null", original("throw null;")),
    ("grouped-null", original("throw (((null)));")),
    ("conditional-null-null", original("throw true?null:null;")),
    ("called-condition-null-null", original(
        "throw flag()?null:null;", "static boolean flag(){return false;}")),
    ("boolean-assignment-before-throw", original("boolean flag;throw (flag=true)?null:null;")),
    ("nested-block-throw", original("{{throw null;}}")),
    ("both-if-branches-throw", original("if(true)throw null;else throw (null);")),
    ("labeled-throw", original("outer:inner:{throw null;}")),
    ("infinite-while-throw", original("while(true){throw null;}")),
    ("infinite-basic-for-throw", original("for(;;){throw null;}")),
    ("do-body-throw", original("do{throw null;}while(false);")),
    ("switch-colon-all-throw", original("switch(1){case 0:throw null;default:throw (null);}")),
    ("switch-arrow-all-throw", original("switch(1){case 0->throw null;default->throw (null);}")),
    ("operand-call-abrupt-before-null-check", original(
        "throw fail()?null:null;", "static boolean fail(){throw null;}")),
]

VALID_ONLY = [
    ("void-method-throw", "class ArrayExample{static void work(){throw null;}}"),
    ("reference-result-needs-no-return",
        "class ArrayExample{static ArrayExample work(){throw null;}}"),
    ("array-result-needs-no-return",
        "class ArrayExample{static int[] work(){throw null;}}"),
    ("constructor-throw", "class ArrayExample{ArrayExample(){throw null;}}"),
    ("constructor-throw-after-delegation",
        "class ArrayExample{ArrayExample(){this(1);throw null;}ArrayExample(int value){}}"),
    ("initializer-conditional-throw-can-complete",
        "class ArrayExample{{if(true)throw null;}}"),
    ("initializer-call-conditional-throw",
        "class ArrayExample{{if(flag())throw null;}boolean flag(){return false;}}"),
]

EFFECT_PROGRAMS = [
    ("operand-effect-before-null-throw",
        "class ArrayExample{static int run(int[] log){throw mark(log)?null:null;}"
        "static boolean mark(int[] log){log[0]++;return true;}}"),
    ("operand-effect-before-abrupt-call",
        "class ArrayExample{static int run(int[] log){throw fail(log)?null:null;}"
        "static boolean fail(int[] log){log[0]++;throw null;}}"),
]

ABRUPT_PROGRAMS = [
    ("operand-bounds-failure-is-not-replaced-by-npe",
        "class ArrayExample{static int run(int[] log){throw fail(log)?null:null;}"
        "static boolean fail(int[] log){log[0]++;int[] empty=new int[0];return empty[0]==0;}}"),
]

INVALID = [
    ("missing-operand", original("throw;"), "expression|identifier"),
    ("missing-semicolon", original("throw null"), "expected ';'"),
    ("comma-is-not-throw-expression", original("throw null,null;"), "expected ';'"),
    ("throw-is-not-return-expression", original("return throw null;"), "expression|statement"),
    ("arrow-throw-missing-semicolon",
        original("switch(0){default->throw null}"), "expected ';'"),
    ("int-operand", original("throw 42;"), "throw|Throwable"),
    ("boolean-operand", original("throw true;"), "throw|Throwable"),
    ("byte-operand", original("throw (byte)1;"), "throw|Throwable"),
    ("short-operand", original("throw (short)1;"), "throw|Throwable"),
    ("char-operand", original("throw '*';"), "throw|Throwable"),
    ("long-operand", original("throw 42L;"), "throw|Throwable"),
    ("float-operand", original("throw 1.0f;"), "throw|Throwable"),
    ("double-operand", original("throw 1.0d;"), "throw|Throwable"),
    ("void-operand", original("throw nothing();", "static void nothing(){}"), "throw|Throwable|void"),
    ("new-array-operand", original("throw new int[0];"), "throw|Throwable"),
    ("array-reference-null-is-still-array",
        original("int[] value=null;throw value;"), "throw|Throwable"),
    ("null-array-conditional-is-still-array",
        original("throw true?null:new int[0];"), "throw|Throwable"),
    ("unassigned-operand-condition",
        original("boolean flag;throw flag?null:null;"), "not definitely assigned"),
    ("unassigned-class-operand-before-hierarchy-check",
        original("ArrayExample value;throw value;"), "not definitely assigned"),
    ("unassigned-string-operand-before-hierarchy-check",
        original("String value;throw value;"), "not definitely assigned"),
    ("undefined-operand-condition",
        original("throw missing?null:null;"), "undefined local"),
    ("integer-condition-is-not-boolean",
        original("throw 1?null:null;"), "boolean"),
    ("tail-after-unconditional-throw",
        original("throw null;return 42;"), "unreachable"),
    ("tail-after-both-branches-throw",
        original("if(true)throw null;else throw null;return 42;"), "unreachable"),
    ("tail-after-labeled-throw",
        original("outer:{throw null;}return 42;"), "unreachable"),
    ("tail-after-switch-all-throw",
        original("switch(1){case 0:throw null;default:throw null;}return 42;"), "unreachable"),
    ("tail-inside-enhanced-for-after-throw",
        original("for(int value:new int[0]){throw null;value++;}return 42;"), "unreachable"),
    ("enhanced-for-throw-does-not-prove-return",
        original("for(int value:new int[]{42}){throw null;}"), "missing return"),
    ("empty-enhanced-for-throw-does-not-prove-return",
        original("for(int value:new int[0]){throw null;}"), "missing return"),
    ("one-if-throw-does-not-prove-return", original("if(true)throw null;"), "missing return"),
    ("switch-without-default-does-not-prove-return",
        original("switch(1){case 1:throw null;}"), "missing return"),
    ("da-missing-on-nonthrowing-path",
        original("int result;if(false)throw null;return result;"), "not definitely assigned"),
    ("da-throwing-loop-body-does-not-assign-exit",
        original("int result;for(int value:new int[0]){result=42;throw null;}return result;"),
        "not definitely assigned"),
    ("uncalled-invalid-throw-is-still-checked",
        original("return 42;", "static void unused(){throw 42;}"), "throw|Throwable"),
    ("constructor-tail-after-throw",
        "class ArrayExample{ArrayExample(){throw null;int value=42;}}", "unreachable"),
    ("initializer-cannot-complete-abruptly",
        "class ArrayExample{{throw null;}}", "initializer must be able to complete normally"),
    ("initializer-both-branches-throw",
        "class ArrayExample{{if(true)throw null;else throw null;}}",
        "initializer must be able to complete normally"),
]

# Every boundary below is rejected by javac too, but PCC cannot discharge the
# missing owned Throwable hierarchy merely from a reference's source spelling.
# In particular, a reference initialized to null does not have the null type.
TYPE_BOUNDARIES = [
    ("class-reference-null", original("ArrayExample value=null;throw value;")),
    ("class-construction-operand", original("throw new ArrayExample();")),
    ("class-returned-null", original(
        "throw missing();", "static ArrayExample missing(){return null;}")),
    ("class-null-conditional", original("ArrayExample value=null;throw true?null:value;")),
    ("instance-this-operand", "class ArrayExample{void work(){throw this;}}"),
    ("string-literal-operand", original('throw "bad";')),
    ("string-reference-null", original("String value=null;throw value;")),
    ("string-returned-null", original(
        "throw missing();", "static String missing(){return null;}")),
    ("string-null-conditional", original('throw true?null:"bad";')),
    ("exception-spelling-is-not-a-hierarchy", "class Exception{void work(){throw this;}}"),
    ("throwable-spelling-is-not-a-hierarchy", "class Throwable{void work(){throw this;}}"),
    ("runtime-exception-spelling-is-not-a-hierarchy", "class RuntimeException{void work(){throw this;}}"),
    ("npe-spelling-is-not-a-hierarchy", "class NullPointerException{void work(){throw this;}}"),
]

DECLARATION_BOUNDARIES = [
    ("declared-unchecked-exception",
        "class ArrayExample{static int run() throws NullPointerException{throw null;}}"),
    ("declared-checked-constructor-exception",
        "class ArrayExample{ArrayExample() throws Exception{throw null;}}"),
]

EXTERNAL_TYPE_BOUNDARIES = [
    ("absent-owned-npe-type",
        "class ArrayExample{static void work(NullPointerException value){throw value;}}"),
    ("absent-owned-runtime-exception-type",
        "class ArrayExample{static void work(RuntimeException value){throw value;}}"),
]

ADMITTED_ORIGINALS = PROGRAMS + THROWN_PROGRAMS + VALID_ONLY + EFFECT_PROGRAMS + ABRUPT_PROGRAMS


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def throw_nodes(project):
    for program in project.programs:
        for method in (*program.methods, *program.constructors):
            yield from (node for node in walk(method.body) if node.kind == "throw")
        for initializer in program.instance_initializers:
            yield from (node for node in walk(initializer) if node.kind == "throw")


def checked_program(name, rows=PROGRAMS):
    text = dict(rows)[name]
    return analyze_class_sources({"ArrayExample.java": text}).classes["ArrayExample"]


@pytest.mark.parametrize("name,text", ADMITTED_ORIGINALS,
                         ids=[row[0] for row in ADMITTED_ORIGINALS])
def test_exact_null_throw_originals_are_nonlowering_source_plans(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer

    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__",
                        lambda *args: pytest.fail("throw source model reached native lowering"))
    sources = {"ArrayExample.java": text}
    project = analyze_class_sources(sources)
    assert sources == {"ArrayExample.java": text}
    nodes = list(throw_nodes(project))
    assert nodes and not project.throw_context_complete
    assert not project.semantic_context_complete
    obligations = [item for item in project.compiler_context_obligations
                   if isinstance(item, ThrowContextObligation)]
    assert len(obligations) == len(nodes)
    for node in nodes:
        assert node.token.text == "throw" and not node.completes
        assert len(node.children) == 1 and node.children[0].dtype == "null"
        assert isinstance(node.value, ThrowSourcePlan)
        assert node.value.operand_type == "null"
        assert node.value.runtime_exception_type == "java/lang/NullPointerException"
        assert not node.value.runtime_registered
        assert node.value.operand_steps == THROW_OPERAND_STEPS
        assert node.value.normal_operand_steps == THROW_NULL_STEPS
        assert node.value.abrupt_operand_steps == THROW_OPERAND_ABRUPT_STEPS
        assert node.value.requirements == THROW_REQUIREMENTS
        obligation, = [item for item in obligations if item.tokens[0] is node.token]
        assert obligation.tokens == (node.token, node.children[0].token)
        assert obligation.owner == "ArrayExample" and obligation.requirements == THROW_REQUIREMENTS
        assert obligation in project.models[obligation.owner].compiler_context_obligations
        assert set(THROW_REQUIREMENTS) <= set(project.models[obligation.owner].managed_requirements)
        assert node.constant is node.string_constant is node.managed_value is None
        assert not hasattr(node.value, "address") and not hasattr(node.value, "object")
        with pytest.raises(FrozenInstanceError):
            node.value.operand_type = "int"
        with pytest.raises(FrozenInstanceError):
            obligation.owner = "invented"
    assert all(not model.runtime_registered for model in project.models.values())
    assert not {"java/lang/Object", "java/lang/Throwable", "java/lang/NullPointerException"} & project.classes.keys()


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_throw_originals_are_ordinary_java_errors(name, text, message):
    with pytest.raises(JavaError, match=message) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert type(caught.value) is JavaError and caught.value.code is None
    assert "ArrayExample.java:" in str(caught.value)


@pytest.mark.parametrize("name,text", TYPE_BOUNDARIES, ids=[row[0] for row in TYPE_BOUNDARIES])
def test_reference_throw_types_require_owned_hierarchy_not_names_or_null_values(name, text):
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert caught.value.code == "java.unsupported.throwable-types"
    assert "ArrayExample.java:" in str(caught.value)


@pytest.mark.parametrize("name,text", DECLARATION_BOUNDARIES,
                         ids=[row[0] for row in DECLARATION_BOUNDARIES])
def test_throws_declarations_keep_checked_exception_analysis_explicit(name, text):
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert caught.value.code == "java.unsupported.throwable-types"
    assert "ArrayExample.java:" in str(caught.value)


@pytest.mark.parametrize("name,text", EXTERNAL_TYPE_BOUNDARIES,
                         ids=[row[0] for row in EXTERNAL_TYPE_BOUNDARIES])
def test_null_throw_plan_does_not_supply_bootstrap_exception_definitions(name, text):
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert caught.value.code == "java.unsupported.source-dependency"


def test_throw_plan_separates_operand_failure_from_null_exception_creation():
    project = analyze_class_sources({"ArrayExample.java": dict(ABRUPT_PROGRAMS)[
        "operand-bounds-failure-is-not-replaced-by-npe"]})
    node, = throw_nodes(project)
    plan = node.value
    assert plan.operand_steps == ("evaluate-operand-once", "branch-on-operand-completion")
    assert plan.abrupt_operand_steps == ("propagate-original-exception-unchanged",)
    assert plan.normal_operand_steps == (
        "create-owned-NullPointerException-or-propagate-allocation-failure",
        "retain-exception-in-owning-root", "match-handler-and-unwind-with-cleanup",
        "transfer-to-handler-or-uncaught-exception-owner",
    )
    assert plan.requirements == (
        "owned-bootstrap-Throwable-hierarchy-and-runtime-type-identity",
        "owned-NullPointerException-definition-allocation-and-initialization",
        "operand-and-exception-allocation-abrupt-completion",
        "shared-owning-root-and-safepoint-ABI",
        "exception-root-survival-through-handler-search-and-relocation",
        "shared-owned-exception-unwind-and-handler-dispatch",
        "Java-finally-monitor-cleanup-and-cross-language-unwind-policy",
        "owned-Java-uncaught-exception-delivery",
    )
    operand = node.children[0]
    assert operand.kind == "conditional" and operand.children[0].kind == "call"
    assert operand.children[0].symbol is project.classes["ArrayExample"].methods[1]
    assert all(child.kind == "null" for child in operand.children[1:])


def test_throw_context_sites_keep_actual_method_constructor_and_initializer_identity():
    text = dict(PROGRAMS)["method-constructor-initializer-sites"]
    project = analyze_class_sources({"ArrayExample.java": text})
    program = project.classes["ArrayExample"]
    obligations = [item for item in project.compiler_context_obligations
                   if isinstance(item, ThrowContextObligation)]
    methods = (*program.methods, *program.constructors,
               *(initializer.symbol for initializer in program.instance_initializers))
    assert len(obligations) == 3
    for method in methods:
        for node in (item for item in walk(method.body) if item.kind == "throw"):
            obligation, = [item for item in obligations if item.tokens[0] is node.token]
            assert obligation.method_name == method.token.text
            assert obligation.method_descriptor == method.descriptor
            assert obligation.owner == method.owner == program.name


def test_absence_of_throw_sites_does_not_claim_a_complete_runtime_context():
    project = analyze_class_sources({"ArrayExample.java": original("return 42;")})
    assert project.throw_context_complete
    assert not any(isinstance(item, ThrowContextObligation)
                   for item in project.compiler_context_obligations)
    assert not project.semantic_context_complete
    assert not project.models["ArrayExample"].runtime_registered


@pytest.mark.parametrize("name", [
    "literal-null", "grouped-null", "called-condition-null-null",
    "boolean-assignment-before-throw", "operand-call-abrupt-before-null-check",
])
def test_original_operand_is_retained_and_checked_exactly_once(name, monkeypatch):
    from pcc.frontends.java.source_class_model import ClassAnalyzer

    original_statement, original_expression = ClassAnalyzer.statement, ClassAnalyzer.expression
    sites, visits = [], []

    def observe_statement(analyzer, node, assigned):
        if node.kind == "throw":
            sites.append((node, node.children[0]))
        return original_statement(analyzer, node, assigned)

    def observe_expression(analyzer, node, assigned):
        visits.append(node)
        return original_expression(analyzer, node, assigned)

    monkeypatch.setattr(ClassAnalyzer, "statement", observe_statement)
    monkeypatch.setattr(ClassAnalyzer, "expression", observe_expression)
    project = analyze_class_sources({"ArrayExample.java": dict(THROWN_PROGRAMS)[name]})
    assert len(sites) == len(list(throw_nodes(project)))
    for statement, operand in sites:
        assert statement.children[0] is operand and operand.dtype == "null"
        assert sum(node is operand for node in visits) == 1
        assert sum(node is operand for node in walk(statement)) == 1


def test_operand_boolean_assignment_is_checked_before_abrupt_completion(monkeypatch):
    from pcc.frontends.java.source_class_model import ClassAnalyzer

    original_expression = ClassAnalyzer.expression
    checked = []

    def observe_expression(analyzer, node, assigned):
        result = original_expression(analyzer, node, assigned)
        if node.kind == "conditional":
            checked.append((node, frozenset(assigned)))
        return result

    monkeypatch.setattr(ClassAnalyzer, "expression", observe_expression)
    program = checked_program("boolean-assignment-before-throw", THROWN_PROGRAMS)
    declaration, statement = program.methods[0].body.children
    operand, assigned = checked[0]
    assert len(checked) == 1 and operand is statement.children[0]
    assert declaration.symbol in assigned
    mutations = [node for node in walk(operand) if node.kind == "mutation"]
    assert len(mutations) == 1 and mutations[0].symbol is declaration.symbol
    assert not statement.completes and not program.methods[0].body.completes


@pytest.mark.parametrize("name,rows", [
    ("da-excludes-throwing-then", PROGRAMS),
    ("da-excludes-throwing-else", PROGRAMS),
    ("switch-colon-da-excludes-throw", PROGRAMS),
    ("switch-arrow-da-excludes-throw", PROGRAMS),
])
def test_da_joins_keep_only_normal_paths(name, rows):
    program = checked_program(name, rows)
    declaration, branch, tail = program.methods[0].body.children
    assert branch.completes and tail.kind == "return" and not tail.completes
    assert tail.children[0].symbol is declaration.symbol
    assert all(not node.completes for node in walk(branch) if node.kind == "throw")


def test_zero_iteration_enhanced_for_retains_normal_exit_after_throwing_body():
    program = checked_program("enhanced-for-zero-iterations")
    loop, tail = program.methods[0].body.children
    assert loop.kind == "enhanced_for" and loop.completes
    assert not loop.children[2].completes
    assert tail.kind == "return" and tail.children[0].constant == 42


@pytest.mark.parametrize("name", ["switch-colon-all-throw", "switch-arrow-all-throw"])
def test_throw_only_switch_rules_have_no_normal_completion(name):
    program = checked_program(name, THROWN_PROGRAMS)
    switch, = program.methods[0].body.children
    assert switch.kind == "switch" and not switch.completes
    assert all(not group.completes for group in switch.children[1:])
    assert all(child.kind == "throw" for group in switch.children[1:] for child in group.children)


def test_nested_throw_does_not_acquire_loop_or_label_jump_target():
    program = checked_program("nested-switch-and-labeled-loop")
    nodes = list(walk(program.methods[0].body))
    label, = [node for node in nodes if node.kind == "label"]
    loop, = [node for node in nodes if node.kind == "enhanced_for"]
    continued, = [node for node in nodes if node.kind == "continue"]
    broken, = [node for node in nodes if node.kind == "break"]
    thrown, = [node for node in nodes if node.kind == "throw"]
    assert continued.control_target is loop and broken.control_target is label
    assert thrown.control_target is None and not thrown.completes


NATIVE_ORIGINALS = [
    dict(THROWN_PROGRAMS)["literal-null"],
    dict(THROWN_PROGRAMS)["called-condition-null-null"],
    dict(PROGRAMS)["unselected-if"],
    dict(PROGRAMS)["uncalled-throwing-method"],
    dict(PROGRAMS)["switch-colon-throw-or-return"],
    dict(PROGRAMS)["switch-arrow-throw-or-return"],
]


@pytest.mark.parametrize("text", NATIVE_ORIGINALS)
def test_native_parser_and_public_entries_reject_throw_before_ir(text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer

    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__",
                        lambda *args: pytest.fail("throw source plan reached scalar IR"))
    for invoke in (
        lambda: SourceParser(text, "ArrayExample.java").parse(),
        lambda: compile_source(text, filename="ArrayExample.java"),
        lambda: compile_sources({"ArrayExample.java": text}, entry_class="ArrayExample"),
    ):
        with pytest.raises(UnsupportedJava) as caught:
            invoke()
        assert caught.value.code == "java.unsupported.runtime-statements"
        assert "ArrayExample.java:" in str(caught.value)


def test_native_throws_clause_keeps_explicit_hierarchy_boundary_before_ir(monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer

    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__",
                        lambda *args: pytest.fail("throws clause reached scalar IR"))
    text = dict(DECLARATION_BOUNDARIES)["declared-unchecked-exception"]
    for invoke in (
        lambda: SourceParser(text, "ArrayExample.java").parse(),
        lambda: compile_source(text, filename="ArrayExample.java"),
        lambda: compile_sources({"ArrayExample.java": text}, entry_class="ArrayExample"),
    ):
        with pytest.raises(UnsupportedJava) as caught:
            invoke()
        assert caught.value.code == "java.unsupported.throwable-types"
        assert "ArrayExample.java:" in str(caught.value)


def assert_source_rejections(root, cases):
    # javac status 1 means a source diagnostic. Crashes, resource exhaustion,
    # timeouts and tool setup errors must never count as expected rejections.
    for name, _, _ in cases:
        directory = root / name
        assert (directory / "compiler-status.txt").read_text() == "1"
        assert b"ArrayExample.java:" in (directory / "compiler.stderr.bin").read_bytes()


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-throw-model-reference")
def test_exact_throw_originals_against_selected_jdk_only(tmp_path, request):
    boundary_rejections = [(name, text, "missing owned Throwable hierarchy")
                           for name, text in TYPE_BOUNDARIES]
    rejected = INVALID + boundary_rejections
    check_originals_reference(
        tmp_path, request, PROGRAMS,
        VALID_ONLY + DECLARATION_BOUNDARIES + EXTERNAL_TYPE_BOUNDARIES, rejected,
        source_input=Path(__file__),
    )
    assert_source_rejections(tmp_path, rejected)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-throw-model-reference")
def test_exact_null_throw_runtime_is_observed_only_by_selected_jdk(tmp_path, request):
    invocation = (
        "((java.util.function.IntSupplier)()->{"
        "try{ArrayExample.run();return 0;}"
        "catch(NullPointerException expected){return 42;}"
        "}).getAsInt()"
    )
    check_originals_reference(
        tmp_path, request, THROWN_PROGRAMS, [], [],
        invocation=invocation, source_input=Path(__file__),
    )


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-throw-model-reference")
def test_exact_operand_effects_precede_jdk_null_throw_or_abrupt_operand(tmp_path, request):
    invocation = (
        "((java.util.function.IntSupplier)()->{int[] log={0};"
        "try{ArrayExample.run(log);return 0;}"
        "catch(NullPointerException expected){return log[0]==1?42:0;}"
        "}).getAsInt()"
    )
    check_originals_reference(
        tmp_path, request, EFFECT_PROGRAMS, [], [],
        invocation=invocation, source_input=Path(__file__),
    )


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-throw-model-reference")
def test_exact_abrupt_operand_exception_is_not_replaced_by_jdk_null_conversion(tmp_path, request):
    invocation = (
        "((java.util.function.IntSupplier)()->{int[] log={0};"
        "try{ArrayExample.run(log);return 0;}"
        "catch(ArrayIndexOutOfBoundsException expected){return log[0]==1?42:0;}"
        "}).getAsInt()"
    )
    check_originals_reference(
        tmp_path, request, ABRUPT_PROGRAMS, [], [],
        invocation=invocation, source_input=Path(__file__),
    )
