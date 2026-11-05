"""Nominal local inference retains supplied-source identity, not object storage.

JLS21 14.4.1, 6.3, 4.12.4, 15.9 and 16:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-14.html#jls-14.4.1
All model/native-boundary/JDK obligations are authored for CI and UNRUN.
The JDK alone executes objects; originals are never rewritten for PCC.
"""
from pathlib import Path

import pytest

from pcc.frontends.java import (
    JavaError, UnsupportedJava, analyze_class_sources, compile_source, compile_sources,
)
from pcc.frontends.java.managed_objects import CLASS_REQUIREMENTS, type_identity
from pcc.frontends.java.source_construction_model import ClassConstructionObligation
from pcc.frontends.java.source_sema import Binding
from pcc.frontends.java.source_types import ClassType
from pcc.support.managed_objects import unmet_requirements
from tests.java.test_source_array_model import check_originals_reference
from tests.java.test_source_construction_model import original, walk
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_local_inference import MODEL_REFERENCE_ORIGINALS
from tests.java.test_source_string_local_inference_model import MODEL_NOMINAL_ORIGINAL
from tests.java.test_source_unicode_escapes import JDK_REASON


PROGRAMS = [
    ("creation", original('', 'var value=new ArrayExample();return value==null?0:42;')),
    ("final-reference", original('', 'final var value=new ArrayExample();return value==null?0:42;')),
    ("alias", original('', 'var first=new ArrayExample();var value=first;return value==first?42:0;')),
    ("alias-rebind", original('', 'var first=new ArrayExample();var value=first;value=null;return first!=null&&value==null?42:0;')),
    ("receiver", original('', 'var value=this;return value==this?42:0;')),
    ("call-once", original('static ArrayExample make(int[] log){log[0]++;return new ArrayExample();}',
                           'int[] log={0};var value=make(log);return log[0]==1&&value!=null?42:0;')),
    ("typed-null", original('static ArrayExample missing(){return null;}', 'var value=missing();return value==null?42:0;')),
    ("conditional-null", original('', 'var value=true?new ArrayExample():null;return value==null?0:42;')),
    ("overload", original('static int pick(ArrayExample value){return 42;}static int pick(int value){return 0;}',
                          'var value=new ArrayExample();return pick(value);')),
    ("basic-for", original('', 'for(var value=new ArrayExample();value!=null;value=null){return 42;}return 0;')),
    ("type-value-namespaces", original('', 'var ArrayExample=new ArrayExample();return ArrayExample==null?0:42;')),
    ("argument-order", original('int order;ArrayExample(){}ArrayExample(int a,int b){}int next(int n){order=order*10+n;return n;}',
                                'var value=new ArrayExample(next(1),next(2));return order==12&&value!=null?42:0;')),
    ("field-initializer", original('ArrayExample saved;', 'var value=saved;return value==null?42:0;')),
]
VALID_ONLY = [
    ("parameter", 'class ArrayExample{ArrayExample pass(ArrayExample input){var value=input;return value;}}'),
    ("constructor-and-initializer", 'class ArrayExample{{var value=this;}ArrayExample(){var value=this;}}'),
]
INVALID = [
    ("self-read", original('', 'var value=value;return 42;'), "own inferred initializer"),
    ("self-constructor-argument", original('ArrayExample(){}ArrayExample(ArrayExample input){}',
                                          'var value=new ArrayExample(value);return 42;'), "own inferred initializer"),
    ("null-only", original('', 'var value=null;return 42;'), "cannot infer"),
    ("unassigned-input", original('', 'ArrayExample input;var value=input;return 42;'), "not definitely assigned"),
    ("final-rebind", original('', 'final var value=new ArrayExample();value=null;return 42;'), "final local"),
    ("primitive-rebind", original('', 'var value=new ArrayExample();value=42;return 42;'), "incompatible source reference assignment"),
    ("string-rebind", original('', 'var value=new ArrayExample();value="wrong";return 42;'), "incompatible source reference assignment"),
    ("nominal-rebind", original('', 'var value=new ArrayExample();value=new Other();return 42;')+'class Other{}', "incompatible source reference assignment"),
    ("duplicate", original('', 'int value=0;var value=new ArrayExample();return 42;'), "already in scope"),
    ("not-boolean", original('', 'var value=new ArrayExample();if(value)return 42;return 0;'), "condition requires boolean"),
]
GAPS = [
    ("member-read", original('int field;', 'var value=new ArrayExample();return value.field;'), "java.unsupported.object-expressions"),
    ("member-call", original('', 'var value=new ArrayExample();return value.run();'), "java.unsupported.object-expressions"),
]
PROJECT_CASES = [
    ("imported-default", {'A.java': 'package app;import provider.B;class A{int run(){var value=new B();return value==null?0:42;}}',
                          'B.java': 'package provider;public class B{}'}, None),
    ("imported-string-name", {'A.java': 'package app;import provider.String;class A{int run(){var value=new String();return value==null?0:42;}}',
                              'String.java': 'package provider;public class String{}'}, None),
    ("private-constructor", {'A.java': 'package app;import provider.B;class A{int run(){var value=new B();return 42;}}',
                             'B.java': 'package provider;public class B{private B(){}}'}, "not accessible"),
    ("inaccessible-class", {'A.java': 'package app;import provider.B;class A{int run(){var value=new B();return 42;}}',
                            'B.java': 'package provider;class B{}'}, "not accessible"),
]


@pytest.mark.parametrize("name,text", PROGRAMS + VALID_ONLY, ids=[row[0] for row in PROGRAMS + VALID_ONLY])
def test_nominal_var_keeps_canonical_bindings_and_unresolved_storage(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("nominal var reached native lowering"))
    project = analyze_class_sources({"ArrayExample.java": text})
    program = project.classes["ArrayExample"]
    bodies = [method.body for method in (*program.methods, *program.constructors)]
    bodies.extend(program.instance_initializers)
    variables = [node for body in bodies for node in walk(body)
                 if node.kind == "var" and node.inference_token is not None]
    assert variables and not project.models["ArrayExample"].runtime_registered
    for variable in variables:
        initializer, = variable.children
        assert isinstance(variable.symbol, Binding)
        assert variable.value == variable.symbol.dtype == initializer.dtype == ClassType("ArrayExample")
        assert variable.inference_token.text == "var"
        assert variable.constant is initializer.constant is initializer.string_constant is None
        plan = initializer.managed_value
        assert plan.identity == type_identity(variable.value)
        assert unmet_requirements(plan) and set(CLASS_REQUIREMENTS) <= set(plan.requirements)
        if initializer.kind == "class_new":
            assert any(initializer.symbol is item for item in project.models["ArrayExample"].constructors)
            assert any(isinstance(item, ClassConstructionObligation) and initializer.token in item.tokens
                       for item in project.compiler_context_obligations)


def test_former_string_nominal_control_is_preserved_as_model_success():
    project = analyze_class_sources({"ArrayExample.java": MODEL_NOMINAL_ORIGINAL})
    method = next(item for item in project.classes["ArrayExample"].methods if item.token.text == "text")
    variable = method.body.children[0]
    assert variable.value == variable.symbol.dtype == ClassType("ArrayExample")
    assert not project.models["ArrayExample"].runtime_registered and not project.string_context_complete


def test_nominal_initializer_once_and_aliases_do_not_create_constant_identity(monkeypatch):
    from pcc.frontends.java.source_class_model import ClassAnalyzer
    from pcc.frontends.java.source_local_inference import PendingInferredLocal
    original_expression = ClassAnalyzer.expression
    visits = []

    def visit(analyzer, node, assigned):
        if node.kind == "call" and node.value == "make":
            visits.append((node, analyzer.scopes[-1]["value"]))
        return original_expression(analyzer, node, assigned)

    monkeypatch.setattr(ClassAnalyzer, "expression", visit)
    project = analyze_class_sources({"ArrayExample.java": dict(PROGRAMS)["call-once"]})
    method = next(item for item in project.classes["ArrayExample"].methods if item.token.text == "run")
    assert len(visits) == 1 and visits[0][0] is method.body.children[1].children[0]
    assert isinstance(visits[0][1], PendingInferredLocal) and visits[0][1] not in method.locals
    project = analyze_class_sources({"ArrayExample.java": dict(PROGRAMS)["alias"]})
    method = project.classes["ArrayExample"].methods[0]
    first, second, result = method.body.children
    assert first.symbol is not second.symbol and first.symbol.slot != second.symbol.slot
    assert second.children[0].symbol is first.symbol and second.children[0].constant is None
    assert result.children[0].children[0].constant is None  # Equality is not a source constant.


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_nominal_var_originals_remain_ordinary_errors(name, text, message):
    with pytest.raises(JavaError, match=message) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.parametrize("name,text,code", GAPS, ids=[row[0] for row in GAPS])
def test_nominal_inference_does_not_grant_member_access(name, text, code):
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert caught.value.code == code


@pytest.mark.parametrize("name,sources,message", PROJECT_CASES)
def test_nominal_inference_keeps_original_project_identity_and_access(name, sources, message):
    if message is not None:
        with pytest.raises(JavaError, match=message) as caught:
            analyze_class_sources(sources)
        assert type(caught.value) is JavaError and caught.value.code is None
        return
    project = analyze_class_sources(sources)
    variable = project.classes["app/A"].methods[0].body.children[0]
    expected = ClassType("provider/String" if name == "imported-string-name" else "provider/B")
    assert variable.value == variable.symbol.dtype == variable.children[0].dtype == expected
    assert project.string_context_complete and not project.models["app/A"].runtime_registered


@pytest.mark.parametrize("api", ("source", "sources"))
def test_nominal_inference_does_not_enable_native_reference_compilation(api, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("native nominal inference reached IR"))
    text = dict(MODEL_REFERENCE_ORIGINALS)["reference-inference"]
    with pytest.raises(UnsupportedJava) as caught:
        if api == "source":
            compile_source(text)
        else:
            compile_sources({"Inferred.java": text}, entry_class="Inferred")
    assert caught.value.code == "java.unsupported.object-expressions"


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-nominal-var-model-reference")
def test_exact_nominal_var_originals_against_selected_jdk_only(tmp_path, request):
    check_originals_reference(tmp_path, request, PROGRAMS,
        VALID_ONLY + [(name, text) for name, text, _ in GAPS], INVALID,
        invocation="new ArrayExample().run()", source_input=Path(__file__))
    for name, _, _ in INVALID:
        assert (tmp_path / name / "compiler-status.txt").read_text() == "1"
        assert b"ArrayExample.java:" in (tmp_path / name / "compiler.stderr.bin").read_bytes()


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-nominal-var-model-reference")
def test_exact_former_nominal_inference_originals_against_jdk_only(tmp_path, request):
    check_originals_reference(tmp_path, request, MODEL_REFERENCE_ORIGINALS, [], [],
                              invocation="Inferred.run()", source_input=Path(__file__))


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-nominal-var-model-reference")
def test_exact_former_string_nominal_control_against_jdk_only(tmp_path, request):
    check_originals_reference(tmp_path, request, [("former-string-nominal", MODEL_NOMINAL_ORIGINAL)], [], [],
        invocation="new ArrayExample().text().equals(new ArrayExample().expected())?42:0", source_input=Path(__file__))


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-nominal-var-model-reference")
def test_exact_nominal_project_originals_against_jdk_only(tmp_path, request, monkeypatch):
    from tests.java import test_source_construction_model as reference
    monkeypatch.setattr(reference, "PROJECT_CASES", PROJECT_CASES)
    reference.test_exact_project_creation_inputs_match_independent_jdk_access_checks(tmp_path, request)
