"""JLS String constant conditionals are content facts, never runtime identity.

JLS21 15.25,15.29,4.12.4,16:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.29
All model/native-boundary/JDK execution is UNRUN. JDK observers consume the
unchanged originals; no owned String runtime or bootstrap class is supplied.
"""
from pathlib import Path

import pytest

from pcc.frontends.java import (
    JavaError, UnsupportedJava, analyze_class_sources, compile_source, compile_sources,
)
from pcc.frontends.java.source_string_model import StringSourceConstant
from pcc.frontends.java.source_types import BootstrapStringType
from pcc.support.managed_objects import unmet_requirements
from tests.java.test_source_array_model import check_originals_reference
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_string_local_inference_model import original
from tests.java.test_source_string_model import original as field_original
from tests.java.test_source_unicode_escapes import JDK_REASON


ROWS = [
    ("true-arm", original('return true?"answer":"wrong";'), "answer"),
    ("false-arm", original('return false?"wrong":"answer";'), "answer"),
    ("nested", original('return true?(false?"wrong":"answer"):"other";'), "answer"),
    ("concat-arm", original('return true?"an"+"swer":"wrong";'), "answer"),
    ("concat-result", original('return (false?"wrong":"an")+"swer";'), "answer"),
    ("final-local", original('final String value=true?"answer":"wrong";return value;'), "answer"),
    ("final-var", original('final var value=false?"wrong":"answer";return value;'), "answer"),
    ("final-condition", original('final boolean flag=true;return flag?"answer":"wrong";'), "answer"),
    ("constant-condition-expression", original('return 6*7==42?"answer":"wrong";'), "answer"),
    ("static-field", field_original('true?"answer":"wrong"', tuple(map(ord, "answer"))), "answer"),
    ("utf16", original(r'return false?"wrong":"\000\uD800";', "\0\ud800"), "\0\ud800"),
]
PROGRAMS = [(name, text) for name, text, _ in ROWS]
NONCONSTANT = [
    ("mutable-condition", original('boolean flag=true;return flag?"answer":"wrong";')),
    ("mutable-string-arm", original('String value="answer";return true?value:"wrong";')),
    ("unselected-call", original('return true?"answer":other();', members='static String other(){return "wrong";}')),
    ("unselected-null", original('return true?"answer":null;')),
    ("primary-constant-field", original('return true?this.VALUE:"wrong";', members='static final String VALUE="answer";')),
    ("mutable-var-alias", original('var value=true?"answer":"wrong";return value;')),
]
INVALID = [
    ("not-boolean", original('return "condition"?"answer":"wrong";'), "condition requires boolean"),
    ("void-arm", original('return true?"answer":nothing();', members='static void nothing(){}'), "void"),
    ("self-in-unselected-arm", original('var value=true?"answer":value;return value;'), "own inferred initializer"),
    ("wrong-assignment", original('int value=true?"answer":"wrong";return "answer";'), "integer value required"),
]
GAPS = [
    ("nonconstant-arm-concat", original('return (true?"answer":other())+"";', members='static String other(){return "wrong";}'), "java.unsupported.string-literals"),
    ("null-arm-concat", original('return (true?"answer":null)+"";'), "java.unsupported.string-literals"),
    ("equality", original('return (true?"answer":"wrong")=="answer"?"answer":"wrong";'), "java.unsupported.string-literals"),
    ("nonconstant-field", field_original('true?"answer":other()', tuple(map(ord, "answer")),
                                         members='static String other(){return "wrong";}'), "java.unsupported.static-initialization"),
]


def checked_result(text):
    project = analyze_class_sources({"ArrayExample.java": text})
    method = next(item for item in project.classes["ArrayExample"].methods if item.token.text == "text")
    return project, method.body.children[-1].children[0]


@pytest.mark.parametrize("name,text,expected", ROWS, ids=[row[0] for row in ROWS])
def test_string_conditionals_retain_selected_utf16_facts_and_unresolved_runtime(name, text, expected, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("String conditional reached lowering"))
    project, result = checked_result(text)
    assert result.dtype == BootstrapStringType() and result.constant is None
    assert result.string_constant == StringSourceConstant(tuple(map(ord, expected)))
    assert unmet_requirements(result.managed_value)
    assert {"managed-literal-materialization", "interned-reference-identity"} <= set(result.managed_value.requirements)
    assert not project.string_context_complete and not project.semantic_context_complete
    assert "java/lang/String" not in project.classes and not project.models["ArrayExample"].runtime_registered


@pytest.mark.parametrize("name,text", NONCONSTANT, ids=[row[0] for row in NONCONSTANT])
def test_runtime_or_null_operands_do_not_become_string_constant_expressions(name, text):
    _, result = checked_result(text)
    assert result.dtype == BootstrapStringType() and result.constant is result.string_constant is None


def test_both_string_arms_are_checked_once_before_reusing_selected_fact(monkeypatch):
    from pcc.frontends.java.source_class_model import ClassAnalyzer
    original_expression = ClassAnalyzer.expression
    visits = []

    def visit(analyzer, node, assigned):
        visits.append(node)
        return original_expression(analyzer, node, assigned)

    monkeypatch.setattr(ClassAnalyzer, "expression", visit)
    _, result = checked_result(dict(PROGRAMS)["true-arm"])
    _, yes, no = result.children
    assert sum(node is yes for node in visits) == sum(node is no for node in visits) == 1
    assert yes.string_constant is not None and no.string_constant is not None
    assert result.string_constant is yes.string_constant


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_string_conditional_originals_are_not_hidden_by_constant_condition(name, text, message):
    with pytest.raises(JavaError, match=message) as caught:
        checked_result(text)
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.parametrize("name,text,code", GAPS, ids=[row[0] for row in GAPS])
def test_string_conditional_facts_do_not_expand_runtime_operations(name, text, code):
    with pytest.raises(UnsupportedJava) as caught:
        checked_result(text)
    assert caught.value.code == code


@pytest.mark.parametrize("api", ("source", "sources"))
def test_string_conditional_model_does_not_enable_native_string_compilation(api, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("native String conditional reached IR"))
    text = 'class ConditionalString{static int run(){var value=true?"answer":"wrong";return 42;}}'
    with pytest.raises(UnsupportedJava) as caught:
        if api == "source":
            compile_source(text)
        else:
            compile_sources({"ConditionalString.java": text}, entry_class="ConditionalString")
    assert caught.value.code == "java.unsupported.string-literals"


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-string-conditional-model-reference")
def test_exact_string_conditional_originals_against_selected_jdk_only(tmp_path, request):
    check_originals_reference(tmp_path, request, PROGRAMS + NONCONSTANT,
        [(name, text) for name, text, _ in GAPS], INVALID,
        invocation="new ArrayExample().text().equals(new ArrayExample().expected())?42:0",
        source_input=Path(__file__))
    for name, _, _ in INVALID:
        assert (tmp_path / name / "compiler-status.txt").read_text() == "1"
        assert b"ArrayExample.java:" in (tmp_path / name / "compiler.stderr.bin").read_bytes()
