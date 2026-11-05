"""String var source facts, with bootstrap and native execution still missing.

JLS21 14.4.1, 6.3, 4.12.4, 15.29 and 16:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-14.html#jls-14.4.1
Originals are shared unchanged with a development-only JDK observer. All
execution is UNRUN. UTF-16 facts are not String objects or interned identity.
"""
from pathlib import Path

import pytest

from pcc.frontends.java import (
    JavaError, UnsupportedJava, analyze_class_sources, compile_source, compile_sources,
)
from pcc.frontends.java.source_sema import Binding
from pcc.frontends.java.source_string_model import StringSourceConstant
from pcc.frontends.java.source_types import BootstrapStringType
from pcc.support.managed_objects import unmet_requirements
from tests.java.test_source_array_model import check_originals_reference
from tests.java.test_source_enhanced_for_model import walk
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_local_inference import MODEL_STRING_ORIGINAL
from tests.java.test_source_string_model import literal
from tests.java.test_source_unicode_escapes import JDK_REASON


def original(body, expected="answer", *, members=""):
    return ("class ArrayExample{" + members + "String text(){" + body + "}"
            "String expected(){return " + literal(tuple(map(ord, expected))) + ";}}")


ROWS = [
    ("literal", original('var value="answer";return value;'), None),
    ("grouped-literal", original('var value=("answer");return value;'), None),
    ("literal-concat", original('var value="an"+"swer";return value;'), None),
    ("final-literal", original('final var value="answer";return value;'), "answer"),
    ("final-utf16-units", original(r'final var value="\000\uD800";return value;', "\0\ud800"), "\0\ud800"),
    ("final-alias-concat", original('final var first="an";final var value=first+"swer";return value;'), "answer"),
    ("mutable-alias", original('var first="before";var value=first;value="answer";return value;'), None),
    ("final-alias-of-mutable", original('var first="answer";final var value=first;return value;'), None),
    ("constant-field", original('final var value=VALUE;return value;', members='static final String VALUE="answer";'), "answer"),
    ("qualified-constant-field", original('final var value=ArrayExample.VALUE;return value;', members='static final String VALUE="answer";'), "answer"),
    ("conditional-null", original('var value=true?"answer":null;return value;'), None),
    ("typed-null-call", original('var value=missing();value="answer";return value;', members='static String missing(){return null;}'), None),
    ("call-once", original('int[] log={0};var value=make(log);if(log[0]!=1)return "bad";return value;',
                           members='static String make(int[] log){log[0]++;return "answer";}'), None),
    ("overload", original('var value="answer";return choose(value);',
                          members='static String choose(String value){return value;}static String choose(int value){return "bad";}'), None),
    ("basic-for", original('String result="";for(var value="answer";true;){result=value;break;}return result;'), None),
]
PROGRAMS = [(name, text) for name, text, _ in ROWS]

VALID_ONLY = [
    ("parameter", "class ArrayExample{String text(String input){var value=input;return value;}}"),
    ("constructor-and-initializer", 'class ArrayExample{String saved;{var value="answer";saved=value;}'
     'ArrayExample(String input){var value=input;saved=value;}}'),
    ("source-String-shadow", 'class String{}class ArrayExample{int run(){var value="answer";return 42;}}'),
]

INVALID = [
    ("self-read", original('var value=value;return "answer";'), "own inferred initializer"),
    ("self-concat", original('var value="answer"+value;return "answer";'), "own inferred initializer"),
    ("null-only", original('var value=null;return "answer";'), "cannot infer"),
    ("duplicate-local", original('String value="";var value="answer";return value;'), "already in scope"),
    ("final-rebind", original('final var value="answer";value="bad";return value;'), "final local"),
    ("primitive-rebind", original('var value="answer";value=42;return value;'), "incompatible source reference assignment"),
    ("nominal-rebind", original('var value="answer";value=new ArrayExample();return value;'), "incompatible source reference assignment"),
    ("unassigned-input", original('String input;var value=input;return value;'), "not definitely assigned"),
    ("not-boolean", original('var value="answer";if(value)return value;return "bad";'), "condition requires boolean"),
]

MODEL_NOMINAL_ORIGINAL = original('var value=new ArrayExample();return "answer";')
GAPS = [
    ("mutable-concat", original('var value="an";return value+"swer";'), "java.unsupported.string-literals"),
    ("final-alias-of-mutable-concat", original('var first="an";final var value=first;return value+"swer";'), "java.unsupported.string-literals"),
    ("mixed-concat", original('var value="answer";return value+1;'), "java.unsupported.string-literals"),
    ("compound-concat", original('var value="an";value+="swer";return value;'), "java.unsupported.string-literals"),
    ("reference-equality", original('var value="answer";return value==null?"bad":value;'), "java.unsupported.string-literals"),
    ("member-call", original('var value="answer";return value.toString();'), "java.unsupported.object-expressions"),
]


def checked(text):
    return analyze_class_sources({"ArrayExample.java": text})


@pytest.mark.parametrize("name,text", PROGRAMS + VALID_ONLY, ids=[row[0] for row in PROGRAMS + VALID_ONLY])
def test_string_var_keeps_bootstrap_identity_and_unresolved_managed_obligations(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("String var reached native lowering"))
    project = checked(text)
    program = project.classes["ArrayExample"]
    bodies = [method.body for method in (*program.methods, *program.constructors)]
    bodies.extend(program.instance_initializers)
    variables = [node for body in bodies for node in walk(body)
                 if node.kind == "var" and node.inference_token is not None]
    assert variables and not project.models["ArrayExample"].runtime_registered
    assert not project.string_context_complete and not project.semantic_context_complete
    assert "java/lang/String" not in project.classes
    for variable in variables:
        initializer, = variable.children
        assert isinstance(variable.symbol, Binding)
        assert variable.value == variable.symbol.dtype == initializer.dtype == BootstrapStringType()
        assert variable.inference_token.text == "var" and variable.constant is None
        assert initializer.managed_value is not None and unmet_requirements(initializer.managed_value)
    obligation, = project.models["ArrayExample"].compiler_context_obligations
    assert obligation.type_name == "java/lang/String"
    assert {"owned-bootstrap-type-definition", "shared-owning-root-and-safepoint-ABI"} <= set(obligation.requirements)


@pytest.mark.parametrize("name,text,constant", ROWS, ids=[row[0] for row in ROWS])
def test_inferred_string_facts_follow_existing_final_constant_variable_rules(name, text, constant):
    program = checked(text).classes["ArrayExample"]
    method = next(method for method in program.methods if method.token.text == "text")
    result = method.body.children[-1].children[0]
    assert result.dtype == BootstrapStringType() and result.constant is None
    if constant is None:
        assert result.string_constant is None
    else:
        assert result.string_constant == StringSourceConstant(tuple(map(ord, constant)))


def test_former_string_inference_original_now_has_partial_model_facts():
    project = analyze_class_sources({"Inferred.java": MODEL_STRING_ORIGINAL})
    variable = project.classes["Inferred"].methods[0].body.children[0]
    assert variable.value == variable.symbol.dtype == BootstrapStringType()
    assert variable.children[0].string_constant.units == tuple(map(ord, "answer"))
    assert not project.string_context_complete and not project.models["Inferred"].runtime_registered


def test_string_initializer_is_checked_once_while_own_name_is_pending(monkeypatch):
    from pcc.frontends.java.source_class_model import ClassAnalyzer
    from pcc.frontends.java.source_local_inference import PendingInferredLocal
    original_expression = ClassAnalyzer.expression
    visits = []

    def visit(analyzer, node, assigned):
        if node.kind == "call" and node.value == "make":
            visits.append((node, analyzer.scopes[-1]["value"]))
        return original_expression(analyzer, node, assigned)

    monkeypatch.setattr(ClassAnalyzer, "expression", visit)
    program = checked(dict(PROGRAMS)["call-once"]).classes["ArrayExample"]
    method = next(method for method in program.methods if method.token.text == "text")
    variable = method.body.children[1]
    assert len(visits) == 1 and visits[0][0] is variable.children[0]
    assert isinstance(visits[0][1], PendingInferredLocal) and visits[0][1] not in method.locals
    assert variable.symbol.dtype == BootstrapStringType()


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_string_var_originals_remain_ordinary_errors(name, text, message):
    with pytest.raises(JavaError, match=message) as caught:
        checked(text)
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.parametrize("name,text,code", GAPS, ids=[row[0] for row in GAPS])
def test_inference_does_not_supply_new_string_operations_or_nominal_inference(name, text, code):
    # Keep this historical locator; the exact nominal control is preserved
    # in test_source_nominal_local_inference_model as a positive obligation.
    with pytest.raises(UnsupportedJava) as caught:
        checked(text)
    assert caught.value.code == code


@pytest.mark.parametrize("api", ("source", "sources"))
def test_string_var_model_does_not_enable_native_string_compilation(api, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("native String inference reached IR"))
    with pytest.raises(UnsupportedJava) as caught:
        if api == "source":
            compile_source(MODEL_STRING_ORIGINAL)
        else:
            compile_sources({"Inferred.java": MODEL_STRING_ORIGINAL}, entry_class="Inferred")
    assert caught.value.code == "java.unsupported.string-literals"


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-string-var-model-reference")
def test_exact_string_var_originals_against_selected_jdk_only(tmp_path, request):
    check_originals_reference(tmp_path, request, PROGRAMS,
        VALID_ONLY + [(name, text) for name, text, _ in GAPS], INVALID,
        invocation="new ArrayExample().text().equals(new ArrayExample().expected())?42:0",
        source_input=Path(__file__))
    for name, _, _ in INVALID:
        assert (tmp_path / name / "compiler-status.txt").read_text() == "1"
        assert b"ArrayExample.java:" in (tmp_path / name / "compiler.stderr.bin").read_bytes()


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-string-var-model-reference")
def test_exact_former_string_inference_original_against_jdk_only(tmp_path, request):
    check_originals_reference(tmp_path, request, [("former-string-inference", MODEL_STRING_ORIGINAL)], [], [],
                              invocation="Inferred.run()", source_input=Path(__file__))
