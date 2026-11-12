"""Primitive local-variable inference on the owned source-to-native route.

JLS21 3.8, 4.12.4, 6.3/6.4, 14.4, 14.14.1, 15.25 and 16:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-3.html#jls-3.8
https://docs.oracle.com/javase/specs/jls/se21/html/jls-14.html#jls-14.4
https://docs.oracle.com/javase/specs/jls/se21/html/jls-16.html

These are source-authored CI obligations, execution UNRUN until CI. Each native
and selected-JDK comparison consumes identical complete original source units;
only independent observers are added. No project method is extracted, source
rewritten, or javac added to production. Commons Numbers' Java 8 target and its
unchanged project gates are not advanced by this Java 10+ scalar capability.
"""
import hashlib
import json
import os
from pathlib import Path
import re

import pytest

from pcc.frontends.java import (
    JavaError,
    UnsupportedJava,
    analyze_class_sources,
    compile_source,
    compile_sources,
)
from pcc.frontends.java.artifact import method_symbol
from pcc.frontends.java.source_project import SourceProject
from pcc.frontends.java.source_sema import Binding, SourceAnalyzer
from pcc.frontends.java.source_syntax import SourceParser
from tests.java.owned_execution import observe_i32, observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_raw_reference import (
    JVM_LIMITS,
    digest,
    record_process,
    tool_identity,
)
from tests.java.test_source_scalars import NATIVE_REASON


LANES = {"boolean": "Z", "byte": "B", "short": "S", "char": "C",
         "int": "I", "long": "J", "float": "F", "double": "D"}
WIDTHS = {"Z": 1, "B": 1, "S": 2, "C": 2, "I": 4, "J": 8, "F": 4, "D": 8}
PRIMITIVES = tuple(LANES)


def original(body, result="int", parameters="", helpers=""):
    return "class Inferred{" + helpers + "static " + result + " run(" + parameters + "){" + body + "}}"


def descriptor(lane, arguments):
    return "(" + "".join(kind for kind, _ in arguments) + ")" + lane


# F/D expected values and arguments are raw IEEE bits, not host conversions.
PRIMITIVE_VALUES = {"boolean": 1, "byte": -128, "short": -32768, "char": 65535,
                    "int": 42, "long": 9007199254740993,
                    "float": 0x42280000, "double": 0x4045000000000000}
CASES = [
    ("parameter-" + dtype, original("var value=input;return value;", dtype, dtype + " input"),
     LANES[dtype], ((LANES[dtype], PRIMITIVE_VALUES[dtype]),), PRIMITIVE_VALUES[dtype])
    for dtype in PRIMITIVES
] + [
    ("literal-int", original("var value=42;return value;"), "I", (), 42),
    ("former-blank-final-boundary", original("final int value;value=42;return value;"), "I", (), 42),
    ("literal-long", original("var value=9007199254740993L;return value;", "long"), "J", (), 9007199254740993),
    ("literal-char", original("var value='*';return value;", "char"), "C", (), 42),
    ("literal-float", original("var value=42.0f;return value;", "float"), "F", (), 0x42280000),
    ("literal-double", original("var value=42.0;return value;", "double"), "D", (), 0x4045000000000000),
    ("boolean-mutation", original("var value=false;value|=true;return value;", "boolean"), "Z", (), 1),
    ("byte-cast-wrap", original("var value=(byte)127;value++;return value;", "byte"), "B", (), -128),
    ("short-cast-wrap", original("var value=(short)-32768;value--;return value;", "short"), "S", (), 32767),
    ("char-cast-wrap", original("var value=(char)65535;value++;return value;", "char"), "C", (), 0),
    ("long-promotion", original("var value=1+41L;return value;", "long"), "J", (), 42),
    ("unary-byte-promotes", original("byte b=1;var value=+b;return pick(value);", helpers="static int pick(byte x){return 0;}static int pick(int x){return 42;}"), "I", (), 42),
    ("literal-one-stays-int", original("var value=1;return pick(value);", helpers="static int pick(byte x){return 0;}static int pick(int x){return 42;}"), "I", (), 42),
    ("byte-call-keeps-overload", original("var value=narrow();return pick(value);", helpers="static byte narrow(){return 41;}static int pick(byte x){return x+1;}static int pick(int x){return 0;}"), "I", (), 42),
    ("conditional-byte-constant", original("byte small=41;var value=flag?small:42;return pick(value);", parameters="boolean flag", helpers="static int pick(byte x){return x;}static int pick(int x){return 0;}"), "I", (("Z", 0),), 42),
    ("conditional-byte-short", original("var value=flag?(byte)41:(short)42;return value;", "short", "boolean flag"), "S", (("Z", 0),), 42),
    ("conditional-char-constant", original("char small=65535;var value=flag?small:42;return pick(value);", parameters="boolean flag", helpers="static int pick(char x){return x==65535?42:0;}static int pick(int x){return 0;}"), "I", (("Z", 1),), 42),
    ("conditional-int-long", original("var value=flag?41:42L;return value;", "long", "boolean flag"), "J", (("Z", 0),), 42),
    ("conditional-float-double", original("var value=flag?41.0f:42.0;return value;", "double", "boolean flag"), "D", (("Z", 0),), 0x4045000000000000),
    ("initializer-effect-once", original("int n=40;var value=++n;return value+n-40;"), "I", (), 42),
    ("initializer-call-once", original("int n=40;var value=next(++n);return value+n-40;", helpers="static int next(int n){return n;}"), "I", (), 42),
    ("conditional-only-chosen-effect", original("int n=40;var value=flag?++n:++n;return value+n-40;", parameters="boolean flag"), "I", (("Z", 1),), 42),
    ("initializer-assigns-earlier", original("int n;var value=(n=21);return value+n;"), "I", (), 42),
    ("final-chain-narrowing", original("final var first=40;final var value=first+2;byte answer=value;return answer;"), "I", (), 42),
    ("final-case-constant", original("final var value=42;switch(42){case value:return 42;default:return 0;}"), "I", (), 42),
    ("final-boolean-da", original("final var never=false;int n;if(never)return n;return 42;"), "I", (), 42),
    ("nonfinal-literal-stays-mutable", original("var value=41;value++;return value;"), "I", (), 42),
    ("for-local", original("int sum=0;for(var i=0;i<3;i++){sum+=14;}return sum;"), "I", (), 42),
    ("for-final", original("for(final var value=42;true;){return value;}"), "I", (), 42),
    ("for-continue-and-scope", original("int sum=0;for(var i=0;i<3;i++){sum+=14;continue;}var i=0;return sum+i;"), "I", (), 42),
    ("successive-blocks-distinct", original("int sum=0;{var value=20;sum+=value;}{var value=22;sum+=value;}return sum;"), "I", (), 42),
    ("name-var", original("var var=42;return var;"), "I", (), 42),
    ("explicit-name-var", original("int var=41;var++;return var;"), "I", (), 42),
    ("parameter-name-var", original("var value=var;return value;", parameters="int var"), "I", (("I", 42),), 42),
    ("method-name-var", original("var value=var();return value;", helpers="static int var(){return 42;}"), "I", (), 42),
    ("call-prefix-var", original("var();return 42;", helpers="static void var(){}"), "I", (), 42),
    ("for-expression-prefix-var", original("int var=0;int sum=0;for(var=0;var<3;var++){sum+=14;}return sum;"), "I", (), 42),
    ("same-name-method-namespace", original("var value=value();return value;", helpers="static int value(){return 42;}"), "I", (), 42),
    ("qualified-shadowed-field", original("var value=Inferred.value;return value;", helpers="static final int value=42;"), "I", (), 42),
]


INVALID = [
    ("missing-initializer", original("var value;return 42;")),
    ("final-missing-initializer", original("final var value;return 42;")),
    ("multiple-initializers", original("var a=1,b=41;return a+b;")),
    ("multiple-uninitialized", original("var a,b;return 42;")),
    ("for-multiple", original("for(var a=0,b=1;a<b;a++){}return 42;")),
    ("declarator-brackets", original("var value[]=new int[]{42};return 42;")),
    ("type-brackets", original("var[] value=new int[]{42};return 42;")),
    ("final-type-brackets", original("final var[] value=new int[]{42};return 42;")),
    ("bare-array-initializer", original("var value={42};return 42;")),
    ("self-read", original("var value=value;return 42;")),
    ("self-read-hides-field", original("var value=value;return 42;", helpers="static final int value=42;")),
    ("self-group-read", original("var value=(value);return 42;")),
    ("self-assignment", original("var value=(value=42);return 42;")),
    ("self-compound", original("var value=(value+=42);return 42;")),
    ("self-prefix", original("var value=++value;return 42;")),
    ("self-postfix", original("var value=value++;return 42;")),
    ("self-dead-and", original("var value=false&&value;return 42;")),
    ("self-dead-or", original("var value=true||value;return 42;")),
    ("self-dead-conditional", original("var value=true?42:value;return 42;")),
    ("self-dead-assignment", original("var value=true?42:(value=1);return 42;")),
    ("self-dead-increment", original("var value=true?42:++value;return 42;")),
    ("self-qualified-method", original("var Inferred=Inferred.next();return 42;", helpers="static int next(){return 42;}")),
    ("self-qualified-field", original("var Inferred=Inferred.value;return 42;", helpers="static final int value=42;")),
    ("self-dead-qualified", original("var Inferred=true?42:Inferred.next();return 42;", helpers="static int next(){return 42;}")),
    ("void-initializer", original("var value=empty();return 42;", helpers="static void empty(){}")),
    ("null-initializer", original("var value=null;return 42;")),
    ("grouped-null-initializer", original("var value=(null);return 42;")),
    ("conditional-null-initializer", original("var value=flag?null:null;return 42;", parameters="boolean flag")),
    ("null-addition", original("var value=null+1;return 42;")),
    ("null-relation", original("var value=null<null;return 42;")),
    ("null-logical", original("var value=null&&true;return 42;")),
    ("null-integer-equality", original("var value=null==1;return 42;")),
    ("null-boolean-inequality", original("var value=null!=false;return 42;")),
    ("unassigned-earlier-local", original("int n;var value=n;return 42;")),
    ("duplicate-local", original("int value=1;var value=42;return value;")),
    ("duplicate-parameter", original("var value=42;return value;", parameters="int value")),
    ("nested-duplicate", original("var value=1;{var value=42;}return value;")),
    ("for-variable-out-of-scope", original("for(var i=0;i<2;i++){}return i;")),
    ("for-body-redeclaration", original("for(var i=0;i<2;i++){var i=42;}return 42;")),
    ("if-body-not-declaration", original("if(true)var value=42;return 42;")),
    ("label-body-not-declaration", original("label:var value=42;return 42;")),
    ("for-update-not-declaration", original("for(int i=0;i<1;var value=42){}return 42;")),
    ("int-does-not-narrow", original("var value=42;byte answer=value;return answer;")),
    ("int-does-not-change-on-store", original("var value=42;value=42L;return value;")),
    ("int-not-boolean", original("var value=1;if(value)return 42;return 0;")),
    ("nonfinal-not-case-constant", original("var value=42;switch(42){case value:return 42;default:return 0;}")),
    ("nonfinal-not-vacuous-da", original("var never=false;int value;if(never)return value;return 42;")),
    ("final-reassignment", original("final var value=41;value=42;return value;")),
    ("final-increment", original("final var value=41;value++;return value;")),
    ("final-dead-assignment", original("final var value=42;if(false)value=0;return value;")),
    ("class-name-var", "class var{static int run(){return 42;}}"),
    ("parameter-type-var", "class Inferred{static int run(var value){return 42;}}"),
    ("result-type-var", "class Inferred{static var run(){return 42;}}"),
    ("field-type-var", "class Inferred{static var value=42;static int run(){return 42;}}"),
]


NATIVE_GAPS = [
    ("enhanced-for", original("for(var value:new int[]{42}){}return 42;"), "java.unsupported.local-inference-domain"),
    ("final-enhanced-for", original("for(final var value:new int[]{42}){}return 42;"), "java.unsupported.local-inference-domain"),
    # Native array creation is the array-storage gap (source_native_arrays).
    ("new-array", original("var value=new int[]{42};return 42;"), "java.unsupported.array-storage"),
    ("new-object", original("var value=new Inferred();return 42;"), "java.unsupported.object-expressions"),
    ("string", original('var value="answer";return 42;'), "java.unsupported.string-literals"),
    ("null-comparison", original("var value=null==null;return value?42:0;"), "java.unsupported.object-expressions"),
    ("null-comparison-nested", original("var value=(null)==(null);return value?42:0;"), "java.unsupported.object-expressions"),
    ("null-inequality", original("var value=null!=null;return value?0:42;"), "java.unsupported.object-expressions"),
    ("null-conditional-comparison", original("var value=(true?null:null)==null;return value?42:0;"), "java.unsupported.object-expressions"),
    ("null-outside-inference", original("return null==null?42:0;"), "java.unsupported.object-expressions"),
]
MODEL_ARRAY_ORIGINAL = original("var value=new int[]{42};return 42;")
MODEL_STRING_ORIGINAL = original('var value="answer";return 42;')
MODEL_REFERENCE_ORIGINALS = [
    ("reference-inference", original("var value=new Inferred();return 42;")),
    ("reference-call-inference", original("var value=make();return 42;", helpers="static Inferred make(){return null;}")),
]


MODEL_INVALID = [
    ("self-array-store", original("var value=(value[0]=1);return 42;")),
    ("self-array-increment", original("var value=value[0]++;return 42;")),
    ("self-field-store", original("var value=(value.field=1);return 42;")),
]


PROJECTS = [
    ("former-final-var-original", {
        "Diagnostic.java": "class Diagnostic{static int run(){final var value=42;return value;}}",
    }, "Diagnostic"),
    ("single-import-var-package", {
        "Main.java": "package p;import var.tools.Helper;public class Main{public static int run(){var value=Helper.answer();return value;}}",
        "Helper.java": "package var.tools;public class Helper{public static int answer(){return 42;}}",
    }, "p.Main"),
    ("static-import-var-method", {
        "Main.java": "package p;import static var.tools.Helper.var;public class Main{public static int run(){var value=var();return value;}}",
        "Helper.java": "package var.tools;public class Helper{public static int var(){return 42;}}",
    }, "p.Main"),
    ("static-import-var-field", {
        "Main.java": "package p;import static var.tools.Helper.var;public class Main{public static int run(){var value=var;return value;}}",
        "Helper.java": "package var.tools;public class Helper{public static final int var=42;}",
    }, "p.Main"),
    ("inferred-cross-unit-overload", {
        "Main.java": "public class Main{public static int run(){var value=Helper.narrow();return Helper.pick(value);}}",
        "Helper.java": "class Helper{static byte narrow(){return 41;}static int pick(byte x){return x+1;}static int pick(int x){return 0;}}",
    }, "Main"),
]


def compile_original(api, text, lane="I", arguments=()):
    if api == "source":
        return compile_source(text, filename="Inferred.java", descriptor=descriptor(lane, arguments))
    return compile_sources({"Inferred.java": text}, entry_class="Inferred", descriptor=descriptor(lane, arguments))


def forbid_lowering(monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    forbid_bytecode_or_external_owner(monkeypatch)
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("invalid or unsupported inference reached IR"))


@pytest.mark.parametrize("api", ("source", "sources"))
@pytest.mark.parametrize("name,text,lane,arguments,expected", CASES, ids=[row[0] for row in CASES])
def test_original_inference_uses_owned_ir_and_object(api, name, text, lane, arguments, expected, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    compiled = compile_original(api, text, lane, arguments)
    assert compiled.entry == ("run", descriptor(lane, arguments))
    assert compiled.emit_object().startswith(b"\x7fELF")
    assert "bc_" not in str(compiled.module)
    assert "nsw" not in str(compiled.module) and "nuw" not in str(compiled.module)


@pytest.mark.parametrize("api", ("source", "sources", "model"))
@pytest.mark.parametrize("name,text", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_original_inference_fails_before_ir(api, name, text, monkeypatch):
    forbid_lowering(monkeypatch)
    with pytest.raises(JavaError) as caught:
        if api == "model":
            analyze_class_sources({"Inferred.java": text})
        else:
            compile_original(api, text)
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.parametrize("api", ("source", "sources"))
@pytest.mark.parametrize("name,text,code", NATIVE_GAPS, ids=[row[0] for row in NATIVE_GAPS])
def test_inference_native_boundaries_remain_explicit(api, name, text, code, monkeypatch):
    forbid_lowering(monkeypatch)
    with pytest.raises(UnsupportedJava) as caught:
        compile_original(api, text)
    assert caught.value.code == code


@pytest.mark.parametrize("name,text", MODEL_REFERENCE_ORIGINALS, ids=[row[0] for row in MODEL_REFERENCE_ORIGINALS])
def test_inference_model_does_not_invent_reference_storage(name, text, monkeypatch):
    from pcc.frontends.java.source_types import ClassType
    from pcc.support.managed_objects import unmet_requirements
    forbid_lowering(monkeypatch)
    project = analyze_class_sources({"Inferred.java": text})
    method = next(item for item in project.classes["Inferred"].methods if item.token.text == "run")
    variable = method.body.children[0]
    assert variable.value == variable.symbol.dtype == ClassType("Inferred")
    assert variable.children[0].dtype == variable.value and variable.constant is None
    assert unmet_requirements(variable.children[0].managed_value)
    assert not project.models["Inferred"].runtime_registered


@pytest.mark.parametrize("name,text,code", NATIVE_GAPS[:2], ids=[row[0] for row in NATIVE_GAPS[:2]])
def test_original_enhanced_var_model_gaps_now_have_primitive_bindings(name, text, code, monkeypatch):
    forbid_lowering(monkeypatch)
    model = analyze_class_sources({"Inferred.java": text})
    declaration = model.programs[0].methods[0].body.children[0].children[0]
    assert declaration.kind == "enhanced_var" and declaration.value == "int"
    assert declaration.inference_token.text == "var"
    assert declaration.symbol.dtype == "int" and declaration.constant is None
    assert not model.models["Inferred"].runtime_registered


@pytest.mark.parametrize("dtype", PRIMITIVES)
def test_inferred_binding_has_concrete_primitive_identity_and_existing_slot(dtype):
    text = original("var value=input;return value;", dtype, dtype + " input")
    program = SourceParser(text, "Inferred.java").parse()
    method = program.methods[0]
    declaration, result = method.body.children
    keyword = declaration.inference_token
    assert declaration.kind == "var" and declaration.value == "var"
    assert keyword.text == "var" and declaration.token.text == "value"
    SourceProject((program,)).analyze()
    parameter, binding = method.locals
    assert type(parameter) is type(binding) is Binding
    assert binding.dtype == declaration.value == declaration.children[0].dtype == dtype
    assert binding.slot == 1 and parameter.slot == 0
    assert declaration.inference_token is keyword and declaration.kind == "var"
    assert declaration.symbol is binding and result.children[0].symbol is binding
    assert declaration.children[0].symbol is parameter
    model_method = analyze_class_sources({"Inferred.java": text}).programs[0].methods[0]
    assert [(item.name, item.dtype, item.slot) for item in model_method.locals] == [
        ("input", dtype, 0), ("value", dtype, 1)]


@pytest.mark.parametrize("dtype,initializer,lane", [
    ("boolean", "true", "i1"), ("byte", "(byte)42", "i32"),
    ("short", "(short)42", "i32"), ("char", "'*'", "i32"),
    ("int", "42", "i32"), ("long", "42L", "i64"),
    ("float", "42.0f", "float"), ("double", "42.0", "double"),
])
def test_inferred_local_reuses_existing_native_slot_and_store(dtype, initializer, lane, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    compiled = compile_source(original("var value=" + initializer + ";return value;", dtype), descriptor="()" + LANES[dtype])
    ir = str(compiled.module)
    assert "alloca " + lane in ir and "store " + lane in ir
    assert "load " + lane in ir


@pytest.mark.parametrize("prefix", ("", "final "))
def test_inference_initializer_is_checked_once_and_pending_never_enters_flow(prefix, monkeypatch):
    from pcc.frontends.java.source_local_inference import PendingInferredLocal
    text = original(prefix + "var value=false&&check();return value?0:42;", helpers="static boolean check(){return true;}")
    program = SourceParser(text, "Inferred.java").parse()
    method = next(item for item in program.methods if item.token.text == "run")
    declaration = method.body.children[0]
    initializer = declaration.children[0]
    seen = []
    expression = SourceAnalyzer.expression

    def checked(self, node, assigned):
        if node is initializer:
            seen.append(node)
            assert any(isinstance(scope.get("value"), PendingInferredLocal) for scope in self.scopes)
            assert all(type(binding) is Binding for binding in self.method.locals)
        return expression(self, node, assigned)

    monkeypatch.setattr(SourceAnalyzer, "expression", checked)
    SourceProject((program,)).analyze()
    assert len(seen) == 1
    assert len(method.locals) == 1 and type(method.locals[0]) is Binding
    assert method.locals[0].is_final == bool(prefix)

    def inspect(node):
        if node.flow_outcomes is not None:
            assert all(type(binding) is Binding for state in node.flow_outcomes for binding in state)
            assert all(not isinstance(binding, PendingInferredLocal) for state in node.flow_outcomes for binding in state)
        for child in node.children:
            inspect(child)

    inspect(method.body)


def test_runtime_initializer_has_one_owned_call_and_typed_reload(monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    text = original("var value=next(41);return value+1;", helpers="static int next(int value){return value;}")
    ir = str(compile_source(text).module)
    calls = [line for line in ir.splitlines() if "call i32" in line and method_symbol("Inferred", "next", "(I)I") in line]
    assert len(calls) == 1 and "load i32" in ir


@pytest.mark.parametrize("statement,kind", [
    ("var();", "expression"), ("var=42;", "assign"),
    ("var++;", "assign"), ("var.member();", "expression"),
])
def test_identifier_prefix_var_is_not_reparsed_as_inference(statement, kind):
    program = SourceParser(original("int var=0;" + statement + "return 42;"), "Inferred.java").parse()
    node = program.methods[0].body.children[1]
    assert node.kind == kind and node.inference_token is None


def test_array_identifier_var_keeps_component_statement_semantics():
    text = original("int[] var={1};var[0]=41;var[0]++;return var[0];")
    method = analyze_class_sources({"Inferred.java": text}).programs[0].methods[0]
    declaration, store, increment, result = method.body.children
    assert declaration.symbol.name == "var" and declaration.inference_token is None
    assert store.kind == increment.kind == "array_assign"
    assert store.children[0].kind == increment.children[0].kind == "array_place"
    assert result.children[0].kind == "array_access"
    assert all(node.children[0].children[0].symbol is declaration.symbol
               for node in (store, increment))


@pytest.mark.parametrize("name,text", MODEL_INVALID, ids=[row[0] for row in MODEL_INVALID])
def test_pending_inference_is_an_error_before_unimplemented_model_write(name, text):
    with pytest.raises(JavaError, match="own inferred initializer") as caught:
        analyze_class_sources({"Inferred.java": text})
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.parametrize("name,sources,entry", PROJECTS, ids=[row[0] for row in PROJECTS])
def test_inference_preserves_project_identity_imports_and_contextual_names(name, sources, entry, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    before = dict(sources)
    compiled = compile_sources(sources, entry_class=entry)
    assert compiled.class_name == entry.replace(".", "/")
    assert compiled.emit_object().startswith(b"\x7fELF")
    assert sources == before
    if name == "inferred-cross-unit-overload":
        assert ("Helper", "pick", "(B)I") in compiled.emitted_class_methods
        assert ("Helper", "pick", "(I)I") in compiled.emitted_class_methods
        assert method_symbol("Helper", "pick", "(B)I") in str(compiled.module)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,text,lane,arguments,expected", CASES, ids=[row[0] for row in CASES])
def test_original_inference_executes_native_exact_scalar(name, text, lane, arguments, expected, tmp_path, monkeypatch):
    unit = tmp_path / "Inferred.java"
    unit.write_bytes(text.encode("utf-8"))
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_source(text, filename=unit.name, descriptor=descriptor(lane, arguments))
    assert observe_scalar(compiled, tmp_path, monkeypatch, arguments) == expected
    assert unit.read_bytes() == text.encode("utf-8")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,sources,entry", PROJECTS, ids=[row[0] for row in PROJECTS])
def test_original_inference_projects_execute_native_i32(name, sources, entry, tmp_path, monkeypatch):
    before = dict(sources)
    for filename, text in sources.items():
        (tmp_path / filename).write_bytes(text.encode("utf-8"))
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_sources(sources, entry_class=entry)
    assert observe_i32(compiled, tmp_path, monkeypatch) == 42
    assert sources == before
    assert all((tmp_path / filename).read_bytes() == text.encode("utf-8") for filename, text in sources.items())


def observer_source(owner, lane, arguments):
    values = []
    for kind, value in arguments:
        if kind == "Z":
            values.append("true" if value else "false")
        elif kind in {"B", "S", "C"}:
            values.append("(" + {"B": "byte", "S": "short", "C": "char"}[kind] + ")" + str(value))
        elif kind == "F":
            values.append("Float.intBitsToFloat(0x" + format(value, "08x") + ")")
        elif kind == "D":
            values.append("Double.longBitsToDouble(0x" + format(value, "016x") + "L)")
        else:
            values.append(str(value) + ("L" if kind == "J" else ""))
    call = owner + ".run(" + ",".join(values) + ")"
    value = {"Z": "(" + call + "?1L:0L)", "F": "Float.floatToRawIntBits(" + call + ")",
             "D": "Double.doubleToRawLongBits(" + call + ")"}.get(lane, call)
    return ("public final class PccNarrowObserve{public static void main(String[] args){long value=" + value +
            ";for(int i=0;i<" + str(WIDTHS[lane]) + ";i++)System.out.write((int)(value >>> (8*i))&255);System.out.flush();}}")


@pytest.mark.parametrize("lane", tuple(WIDTHS))
def test_reference_observer_calls_unchanged_original_once_and_keeps_exact_width(lane):
    text = observer_source("Original", lane, (("B", -128), ("F", 0x42280000), ("D", 0x4045000000000000)))
    assert text.count("Original.run(") == 1
    assert "(byte)-128,Float.intBitsToFloat(0x42280000),Double.longBitsToDouble(0x4045000000000000L)" in text
    assert "i<" + str(WIDTHS[lane]) in text
    assert ("Float.floatToRawIntBits" in text) == (lane == "F")
    assert ("Double.doubleToRawLongBits" in text) == (lane == "D")


JDK_REASON = None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else "select an existing JDK with PCC_JAVA_RAW_REFERENCE"


def reference_owner(tmp_path, request):
    java = Path(os.environ["PCC_JAVA_RAW_REFERENCE"]).resolve(strict=True)
    level = os.environ.get("PCC_JAVA_RAW_REFERENCE_SOURCE", "21")
    assert level in {"17", "21"}
    version = re.search(r'^JAVA_VERSION="([^\"]+)"', (java.parent.parent / "release").read_text(), re.MULTILINE)
    assert version and int(version.group(1).split(".")[0]) >= int(level)
    root = Path(__file__).parent
    inputs = tuple(root / name for name in (
        "test_source_local_inference.py", "NarrowReference.java", "owned_execution.py", "test_source_raw_reference.py"))
    before = {"tools": tool_identity(java), "inputs": {str(path): digest(path) for path in inputs}}
    retained = {}
    owner = {**before, "version": version.group(1), "source_level": level, "target_level": level,
             "platform_contract": "selected current JDK platform, not --release compatibility",
             "production_dependency": False}
    (tmp_path / "owner-before.json").write_text(json.dumps(owner, indent=2))

    def finish_identity():
        after = {"tools": tool_identity(java), "inputs": {str(path): digest(path) for path in inputs}}
        unchanged = all(digest(path) == expected for path, expected in retained.items())
        (tmp_path / "owner-post.json").write_text(json.dumps({**after,
            "original_observer_hashes": {str(path): checksum for path, checksum in retained.items()},
            "source_inputs_unchanged": unchanged}, indent=2))
        assert before == after and unchanged

    request.addfinalizer(finish_identity)
    assert record_process((str(java), *JVM_LIMITS, "--version"), tmp_path, "version")[0] == 0
    modules = record_process((str(java), *JVM_LIMITS, "--list-modules"), tmp_path, "modules")
    assert modules[0] == 0 and any(line.split(b"@")[0] == b"jdk.compiler" for line in modules[1].splitlines())
    return java, level, retained


def reference_batch(tmp_path, request, *, runtime):
    java, level, retained = reference_owner(tmp_path, request)
    rows = []
    for name, text, *rest in CASES if runtime else INVALID + MODEL_INVALID:
        directory = tmp_path / name
        directory.mkdir()
        unit = directory / "Inferred.java"
        unit.write_bytes(text.encode("utf-8"))
        retained[unit] = digest(unit)
        if runtime:
            lane, arguments, _ = rest
            observer = directory / "PccNarrowObserve.java"
            observer.write_text(observer_source("Inferred", lane, arguments), encoding="utf-8")
            retained[observer] = digest(observer)
        rows.append(name + ("\trun\t" if runtime else "\treject\t") + unit.name)
    manifest = tmp_path / "cases.tsv"
    manifest.write_text("\n".join(rows) + "\n")
    retained[manifest] = digest(manifest)
    driver = tmp_path / "NarrowReference.java"
    driver.write_bytes(Path(__file__).with_name("NarrowReference.java").read_bytes())
    retained[driver] = digest(driver)
    classes = tmp_path / "driver-classes"
    classes.mkdir()
    compiler = record_process((str(java), *JVM_LIMITS, "--module", "jdk.compiler/com.sun.tools.javac.Main",
        "-proc:none", "-encoding", "UTF-8", "-source", level, "-target", level,
        "-classpath", str(classes), "-d", str(classes), str(driver)), tmp_path, "driver-compile", 20)
    assert compiler[0] == 0, compiler
    batch = record_process((str(java), *JVM_LIMITS, "-cp", str(classes), "NarrowReference", str(tmp_path), level),
                           tmp_path, "batch-compile", 30)
    assert batch[0] == 0, batch
    return java


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON or JDK_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
def test_identical_inference_originals_match_jdk_raw_processes(tmp_path, monkeypatch, request):
    java = reference_batch(tmp_path, request, runtime=True)
    for name, text, lane, arguments, expected in CASES:
        directory = tmp_path / name
        assert (directory / "compiler-status.txt").read_text() == "0", name
        reference = record_process((str(java), *JVM_LIMITS, "-cp", str(directory / "classes"), "PccNarrowObserve"),
                                   directory, "reference-run")
        native = directory / "native"
        native.mkdir()
        with monkeypatch.context() as denied:
            forbid_bytecode_or_external_owner(denied)
            compiled = compile_source(text, descriptor=descriptor(lane, arguments))
        assert observe_scalar(compiled, native, monkeypatch, arguments) == expected
        actual = record_process((str(native / "observed"),), native, "raw-native")
        expected_bytes = int(expected).to_bytes(WIDTHS[lane], "little", signed=lane not in {"Z", "C", "F", "D"})
        (directory / "comparison.json").write_text(json.dumps({
            "case": name, "comparison": "raw_process_streams",
            "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "original_modifiers_or_source_rewritten": False,
            "descriptor": descriptor(lane, arguments), "arguments": arguments,
            "observer_sha256": digest(directory / "PccNarrowObserve.java"),
            "object_sha256": digest(native / "methods.o"), "executable_sha256": digest(native / "observed"),
            "native": {"exit_code": actual[0], "stdout_hex": actual[1].hex(), "stderr_hex": actual[2].hex()},
            "reference": {"exit_code": reference[0], "stdout_hex": reference[1].hex(), "stderr_hex": reference[2].hex()},
        }, indent=2))
        assert actual == reference == (0, expected_bytes, b""), name


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
def test_identical_invalid_inference_originals_are_rejected_by_jdk(tmp_path, request):
    reference_batch(tmp_path, request, runtime=False)
    for name, _ in INVALID + MODEL_INVALID:
        directory = tmp_path / name
        assert int((directory / "compiler-status.txt").read_text()) != 0, name
        diagnostic = (directory / "compiler.stderr.bin").read_bytes()
        assert re.search(rb"Inferred\.java:\d+:", diagnostic), (name, diagnostic)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON or JDK_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
def test_identical_inference_projects_match_jdk_raw_processes(tmp_path, monkeypatch, request):
    java, level, retained = reference_owner(tmp_path, request)
    for name, sources, entry in PROJECTS:
        directory = tmp_path / name
        directory.mkdir()
        units = []
        for filename, text in sources.items():
            unit = directory / filename
            unit.write_bytes(text.encode("utf-8"))
            retained[unit] = digest(unit)
            units.append(str(unit))
        observer = directory / "PccNarrowObserve.java"
        observer.write_text(observer_source(entry, "I", ()), encoding="utf-8")
        retained[observer] = digest(observer)
        classes = directory / "classes"
        classes.mkdir()
        compiler = record_process((str(java), *JVM_LIMITS, "--module", "jdk.compiler/com.sun.tools.javac.Main",
            "-proc:none", "-encoding", "UTF-8", "-source", level, "-target", level,
            "-classpath", str(classes), "-d", str(classes), *units, str(observer)), directory, "reference-compile", 20)
        assert compiler[0] == 0, compiler
        reference = record_process((str(java), *JVM_LIMITS, "-cp", str(classes), "PccNarrowObserve"), directory, "reference-run")
        native = directory / "native"
        native.mkdir()
        with monkeypatch.context() as denied:
            forbid_bytecode_or_external_owner(denied)
            compiled = compile_sources(sources, entry_class=entry)
        assert observe_i32(compiled, native, monkeypatch) == 42
        actual = record_process((str(native / "observed"),), native, "raw-native")
        (directory / "comparison.json").write_text(json.dumps({
            "case": name, "comparison": "raw_process_streams", "entry_class": entry,
            "original_modifiers_or_source_rewritten": False,
            "source_sha256": {filename: hashlib.sha256(text.encode("utf-8")).hexdigest() for filename, text in sources.items()},
            "observer_sha256": digest(observer), "object_sha256": digest(native / "methods.o"),
            "executable_sha256": digest(native / "observed"),
            "native": {"exit_code": actual[0], "stdout_hex": actual[1].hex(), "stderr_hex": actual[2].hex()},
            "reference": {"exit_code": reference[0], "stdout_hex": reference[1].hex(), "stderr_hex": reference[2].hex()},
        }, indent=2))
        assert actual == reference == (0, b"\x2a\x00\x00\x00", b""), name
