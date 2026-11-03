"""Partial source String constants with explicit missing bootstrap ownership.

JLS21 3.10.5,4.12.4,12.4.1,15.18.1,15.29. Exact originals reach the
nonlowering model and separate JDK observers. UTF-16 content is not a managed
reference, interned identity, runtime layout or complete String dependency.
All new tests/reference execution are authored for CI and remain UNRUN.
"""
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, analyze_class_sources, compile_source, compile_sources
from pcc.frontends.java.source_class_model import ClassReferenceFieldSlot
from pcc.frontends.java.source_string_model import MAX_STRING_FOLD_WORK, MAX_STRING_UNITS, StringSourceConstant
from pcc.frontends.java.source_types import BootstrapStringType, ClassType
from tests.java.test_source_array_model import check_originals_reference
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_unicode_escapes import JDK_REASON


def literal(units):
    return '"' + "".join("\\%03o" % unit if unit < 256 else "\\u%04x" % unit for unit in units) + '"'


def original(expression, units, *, members="", body="return VALUE;"):
    return ("class ArrayExample{" + members + "static final String VALUE=" + expression + ";"
            "String text(){" + body + "}String expected(){return " + literal(units) + ";}}")


ROWS = [
    ("empty", original('""', ()), ()),
    ("literal", original('"original"', tuple(map(ord, "original"))), tuple(map(ord, "original"))),
    ("original-primes-format", original('"%d is smaller than the minimum (%d)"', tuple(map(ord, "%d is smaller than the minimum (%d)"))), tuple(map(ord, "%d is smaller than the minimum (%d)"))),
    ("constant-concat", original('"ab"+"cd"', tuple(map(ord, "abcd"))), tuple(map(ord, "abcd"))),
    ("constant-name", original('PREFIX+"cd"', tuple(map(ord, "abcd")), members='static final String PREFIX="ab";'), tuple(map(ord, "abcd"))),
    ("qualified-constant-name", original('ArrayExample.PREFIX+"cd"', tuple(map(ord, "abcd")), members='static final String PREFIX="ab";'), tuple(map(ord, "abcd"))),
    ("grouped-constant", original('(PREFIX)+("cd")', tuple(map(ord, "abcd")), members='static final String PREFIX="ab";'), tuple(map(ord, "abcd"))),
    ("utf16-units", original(r'"\000\uD800"+"\uDFFF😀"', (0, 0xd800, 0xdfff, 0xd83d, 0xde00)), (0, 0xd800, 0xdfff, 0xd83d, 0xde00)),
    ("final-local-constant", original('"ab"', tuple(map(ord, "abcd")), body='final String local=VALUE+"cd";return local;'), tuple(map(ord, "abcd"))),
    ("final-local-direct-constant-name", original('"ab"', tuple(map(ord, "ab")), body='final String local=VALUE;return local;'), tuple(map(ord, "ab"))),
]
PROGRAMS = [(name, text) for name, text, _ in ROWS]


@pytest.mark.parametrize("name,text,units", ROWS, ids=[row[0] for row in ROWS])
def test_exact_string_originals_return_partial_source_facts_without_runtime_identity(name, text, units, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("model constructed native lowering"))
    inputs = {"ArrayExample.java": text}
    project = analyze_class_sources(inputs)
    model = project.models["ArrayExample"]
    method = next(method for method in model.source.methods if method.token.text == "text")
    result = method.body.children[-1].children[0]
    assert result.dtype == BootstrapStringType() and result.constant is None
    assert isinstance(result.string_constant, StringSourceConstant)
    assert result.string_constant.units == units
    assert method.descriptor == "()Ljava/lang/String;"
    assert inputs == {"ArrayExample.java": text}
    assert "java/lang/String" not in project.classes
    assert not project.string_context_complete and not model.runtime_registered
    assert not project.semantic_context_complete and not model.semantic_context_complete
    assert model.compiler_context_obligations == project.compiler_context_obligations
    obligation, = model.compiler_context_obligations
    assert obligation.type_name == "java/lang/String" and obligation.owner == "ArrayExample"
    assert {"owned-bootstrap-type-definition", "managed-literal-materialization", "interned-reference-identity",
            "shared-owning-root-and-safepoint-ABI"} <= set(obligation.requirements)
    assert all("initialization" not in requirement for requirement in obligation.requirements)
    with pytest.raises(FrozenInstanceError):
        result.string_constant.units = ()


@pytest.mark.parametrize("name,text", PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_partial_string_facts_never_enable_native_compilation(name, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "__init__", lambda *args: pytest.fail("String constructed native lowering"))
    for invoke in (lambda: compile_source(text, filename="ArrayExample.java"),
                   lambda: compile_sources({"ArrayExample.java": text}, entry_class="ArrayExample")):
        with pytest.raises(UnsupportedJava) as caught:
            invoke()
        assert caught.value.code == "java.unsupported.declared-result-type"


def test_scalar_lowering_cannot_guess_an_integer_lane_for_reference_model_types():
    from pcc.frontends.java.source_lowering import SourceLowerer
    from pcc.frontends.java.source_types import ArrayType
    for dtype in (BootstrapStringType(), ClassType("A"), ArrayType("int"), "null", "String", None):
        for lane in (SourceLowerer.scalar_type, SourceLowerer.abi_type):
            with pytest.raises(UnsupportedJava) as caught:
                lane(dtype)
            assert caught.value.code == "java.unsupported.declared-result-type"
    for dtype, expected in (("boolean", "i1"), ("byte", "i32"), ("short", "i32"), ("char", "i32"),
                            ("int", "i32"), ("long", "i64"), ("float", "float"), ("double", "double"), ("void", "void")):
        assert str(SourceLowerer.scalar_type(dtype)) == expected


def test_named_string_dependency_does_not_require_or_fabricate_a_literal_object():
    text = "class A{String value;String id(String value){return value;}}"
    project = analyze_class_sources({"A.java": text})
    model = project.models["A"]
    slot, = model.fields
    assert isinstance(slot, ClassReferenceFieldSlot)
    assert slot.offset is slot.size is slot.alignment is slot.default_value is None
    assert model.payload_size is model.payload_alignment is None
    assert not project.string_context_complete and "java/lang/String" not in project.classes
    obligation, = project.compiler_context_obligations
    assert set(obligation.requirements) == {"owned-bootstrap-type-definition", "shared-owning-root-and-safepoint-ABI"}
    assert model.source.methods[0].descriptor == "(Ljava/lang/String;)Ljava/lang/String;"


def test_explicit_bootstrap_import_is_a_dependency_not_a_fake_supplied_class():
    project = analyze_class_sources({"A.java": "import java.lang.String;import java.lang.String;class A{String id(String s){return s;}}"})
    assert project.classes.keys() == {"A"}
    assert isinstance(project.imports["A"]["String"], BootstrapStringType)
    assert not project.string_context_complete
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"A.java": "import absent.String;class A{String value;}"})
    assert caught.value.code == "java.unsupported.source-dependency"


def test_string_shadowing_cannot_retag_a_literal_or_hide_source_access_checks():
    with pytest.raises(JavaError, match="incompatible source reference assignment") as caught:
        analyze_class_sources({"String.java": 'class String{String value="x";}'})
    assert type(caught.value) is JavaError and caught.value.code is None
    project = analyze_class_sources({"A.java": "package p;class A{String value;}", "String.java": "package p;class String{}"})
    assert project.models["p/A"].fields[0].field.dtype == ClassType("p/String")
    assert project.string_context_complete


def test_source_constants_do_not_propagate_through_mutable_locals_parameters_or_primary_access():
    cases = [
        'class A{String text(){String value="x";return value;}}',
        'class A{String text(String value){return value;}}',
        'class A{static final String VALUE="x";String text(){return this.VALUE;}}',
        'class A{String value="x";String text(){return value;}}',
    ]
    for text in cases:
        project = analyze_class_sources({"A.java": text})
        method = next(method for method in project.classes["A"].methods if method.token.text == "text")
        assert method.body.children[-1].children[0].string_constant is None


GAPS = [
    ("string-equality", 'class ArrayExample{boolean value(){return "a"=="a";}}', "string-literals"),
    ("string-inequality", 'class ArrayExample{boolean value(){return "a"!="b";}}', "string-literals"),
    ("mixed-concat", 'class ArrayExample{String value(){return "a"+1;}}', "string-literals"),
    ("null-concat", 'class ArrayExample{String value(){return "a"+null;}}', "string-literals"),
    ("mutable-local-concat", 'class ArrayExample{String value(){String x="a";return x+"b";}}', "string-literals"),
    ("local-compound-concat", 'class ArrayExample{String value(){String x="a";x+="b";return x;}}', "string-literals"),
    ("field-compound-concat", 'class ArrayExample{String x="a";void value(){x+="b";}}', "string-literals"),
    ("call-concat", 'class ArrayExample{String id(){return "a";}String value(){return id()+"b";}}', "string-literals"),
    ("null-static-final", 'class ArrayExample{static final String VALUE=null;}', "static-initialization"),
    ("call-static-final", 'class ArrayExample{static String id(){return "a";}static final String VALUE=id();}', "static-initialization"),
    ("string-switch", 'class ArrayExample{int value(String x){switch(x){case "a":return 1;default:return 0;}}}', "string-literals"),
    ("string-format", 'class ArrayExample{String value(){return String.format("%d",1);}}', "source-dependency"),
    ("literal-method", 'class ArrayExample{int value(){return "a".length();}}', "object-expressions"),
    ("string-method", 'class ArrayExample{int value(String x){return x.length();}}', "object-expressions"),
    ("string-cast", 'class ArrayExample{String value(){return (String)null;}}', "object-expressions"),
]


@pytest.mark.parametrize("name,text,code", GAPS, ids=[row[0] for row in GAPS])
def test_adjacent_string_execution_and_conversion_forms_remain_explicit(name, text, code):
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"ArrayExample.java": text})
    assert caught.value.code == "java.unsupported." + code


FLOW_COUNTEREXAMPLE = 'class ArrayExample{void value(){while("a"=="b") {}}}'


def test_string_constant_comparison_never_becomes_an_unknown_flow_boolean():
    with pytest.raises(UnsupportedJava) as caught:
        analyze_class_sources({"ArrayExample.java": FLOW_COUNTEREXAMPLE})
    assert caught.value.code == "java.unsupported.string-literals"


@pytest.mark.parametrize("expression", ['"a"-"b"', '"a"<"b"', 'true&&"a"', '"a"|"b"', '"a"==1'])
def test_known_invalid_string_operators_remain_ordinary_java_errors(expression):
    text = "class A{void value(){boolean result=" + expression + ";}}"
    with pytest.raises(JavaError) as caught:
        analyze_class_sources({"A.java": text})
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.parametrize("text", [
    'class A{void n(){}String value(){return "a"+n();}}',
    'class A{void n(){}String value(){return n()+"a";}}',
    'class A{void n(){}void value(){String s="";s+=n();}}',
    'class A{String s="";void n(){}void value(){s+=n();}}',
])
def test_void_values_are_invalid_before_any_string_concat_capability_gap(text):
    with pytest.raises(JavaError, match="void value cannot participate") as caught:
        analyze_class_sources({"A.java": text})
    assert type(caught.value) is JavaError and caught.value.code is None


def test_cross_class_inferred_string_uses_retain_the_callers_missing_context():
    sources = {"A.java": "class A{int run(){return B.take(B.S);}}",
               "B.java": 'class B{static final String S="x";static int take(String value){return 42;}}'}
    project = analyze_class_sources(sources)
    assert not project.string_context_complete
    for name in ("A", "B"):
        obligation, = project.models[name].compiler_context_obligations
        assert obligation.owner == name
        assert {"owned-bootstrap-type-definition", "managed-literal-materialization", "interned-reference-identity",
                "shared-owning-root-and-safepoint-ABI"} <= set(obligation.requirements)


@pytest.mark.parametrize("sources,owner", [
    ({"A.java": "class A{int run(){return B.take(null);}}",
      "B.java": "class B{static int take(String value){return 42;}}"}, "A"),
    ({"A.java": "package app;import provider.B;class A{int run(){return B.take(null);}}",
      "B.java": "package provider;public class B{public static int take(String value){return 42;}}"}, "app/A"),
])
def test_null_actual_records_the_selected_formal_string_contract_in_the_caller(sources, owner):
    original = dict(sources)
    project = analyze_class_sources(sources)
    model = project.models[owner]
    caller, = model.source.methods
    call = caller.body.children[0].children[0]
    assert call.dtype == "int" and call.children[0].dtype == "null"
    assert call.symbol.parameter_types == (BootstrapStringType(),)
    obligation, = model.compiler_context_obligations
    assert obligation.owner == owner and call.token in obligation.tokens
    assert set(obligation.requirements) == {"owned-bootstrap-type-definition", "shared-owning-root-and-safepoint-ABI"}
    assert not project.string_context_complete and not model.semantic_context_complete
    assert not model.runtime_registered and sources == original


def test_primitive_selected_overload_does_not_inherit_an_unselected_string_contract():
    sources = {"A.java": "class A{int run(){return B.take(42);}}",
               "B.java": "class B{static int take(String value){return 7;}static int take(int value){return 42;}}"}
    project = analyze_class_sources(sources)
    model = project.models["A"]
    call = model.source.methods[0].body.children[0].children[0]
    assert call.symbol.parameter_types == ("int",)
    assert model.compiler_context_obligations == ()
    assert not project.string_context_complete and not model.semantic_context_complete


def test_receiver_and_constructor_call_sites_record_their_formal_string_contracts():
    text = ("class A{A(){this(null);}A(String value){}"
            "int take(String value){return 42;}int run(){return this.take(null);}}")
    project = analyze_class_sources({"A.java": text})
    model = project.models["A"]
    constructor = next(item for item in model.constructors if not item.parameters)
    delegation = constructor.body.children[0]
    caller = next(method for method in model.source.methods if method.token.text == "run")
    call = caller.body.children[0].children[0]
    obligation, = model.compiler_context_obligations
    assert delegation.symbol.parameter_types == call.symbol.parameter_types == (BootstrapStringType(),)
    assert delegation.token in obligation.tokens and call.token in obligation.tokens


def test_string_source_value_and_folding_work_budgets_precede_expansion():
    large = 'class A{static final String VALUE="' + "x" * (MAX_STRING_UNITS + 1) + '";}'
    with pytest.raises(UnsupportedJava, match="bounded UTF-16"):
        analyze_class_sources({"A.java": large})
    fields = ['static final String S0="x";']
    fields += ["static final String S" + str(i) + "=S" + str(i-1) + "+S" + str(i-1) + ";" for i in range(1, 18)]
    with pytest.raises(UnsupportedJava, match="bounded UTF-16"):
        analyze_class_sources({"A.java": "class A{" + "".join(fields) + "}"})
    base = "x" * 32_768
    repeated = ['static final String BASE="' + base + '";']
    repeated += ['static final String S' + str(i) + '=BASE+"";' for i in range(MAX_STRING_FOLD_WORK // len(base) + 1)]
    with pytest.raises(UnsupportedJava, match="bounded UTF-16"):
        analyze_class_sources({"A.java": "class A{" + "".join(repeated) + "}"})


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-array-model-reference")
def test_exact_string_content_originals_match_independent_jdk_only(tmp_path, request):
    check_originals_reference(tmp_path, request, PROGRAMS,
        [(name, text) for name, text, _ in GAPS],
        [("constant-false-flow", FLOW_COUNTEREXAMPLE, "unreachable")],
        invocation="new ArrayExample().text().equals(new ArrayExample().expected())?42:0",
        source_input=Path(__file__))
