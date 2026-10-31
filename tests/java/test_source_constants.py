"""Original source constant variables, distinct from runtime static initialization."""
import json

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, compile_source, compile_sources
from pcc.frontends.java.source_syntax import SourceParser
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_scalars import NATIVE_REASON


def original(fields, body, result="int", helpers=""):
    return "class Constants{" + fields + helpers + "static " + result + " run(){" + body + "}}"


CASES = [
    ("integer-field", "static final int VALUE=42;", "return VALUE;", "int", "", 42),
    ("left-declarator", "static final int A=40,B=A+2;", "return B;", "int", "", 42),
    ("qualified-forward", "static final int A=Constants.B+2,B=40;", "return A;", "int", "", 42),
    ("private-field", "private static final int VALUE=42;", "return Constants.VALUE;", "int", "", 42),
    ("long-wrap", "static final long MAX=9223372036854775807L,MIN=MAX+1;", "return MIN;", "long", "", -9223372036854775808),
    ("char-zero-extension", "static final char LAST=65535;", "return LAST;", "long", "", 65535),
    ("byte-short-widening", "static final byte B=-128;static final short S=B;", "return S;", "int", "", -128),
    ("explicit-short-narrowing", "static final short S=(short)0x100008000L;", "return S;", "int", "", -32768),
    ("constant-boolean-da", "static final boolean NEVER=false;", "int x;if(NEVER)return x;return 42;", "int", "", 42),
    ("constant-boolean-loop", "static final boolean ALWAYS=true;", "while(ALWAYS){return 42;}", "int", "", 42),
    ("local-shadows-field", "static final int VALUE=7;", "int VALUE=40;VALUE+=2;return VALUE;", "int", "", 42),
    ("parameter-shadows-field", "static final int VALUE=7;", "return f(42);", "int", "static int f(int VALUE){return VALUE;}", 42),
    ("method-field-same-name", "static final int pick=40;", "return pick+pick();", "int", "static int pick(){return 2;}", 42),
    ("character-arithmetic", "static final char LETTER='A';", "return LETTER-23;", "int", "", 42),
    ("character-octal", r"static final char OCTAL='\377';", "return OCTAL;", "int", "", 255),
    ("character-unicode-bmp", "static final char LETTER='中';", "return LETTER;", "int", "", 20013),
    ("character-selects-int-overload", "", "return f('x');", "int", "static int f(int x){return 42;}static int f(long x){return 7;}", 42),
    ("char-to-byte-constant", "static final byte B='A';", "return B;", "int", "", 65),
]


@pytest.mark.parametrize("name,fields,body,result,helpers,expected", CASES, ids=[x[0] for x in CASES])
def test_original_constant_source_uses_direct_ir(name, fields, body, result, helpers, expected, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    compiled = compile_source(original(fields, body, result, helpers), descriptor="()J" if result == "long" else "()I")
    assert compiled.emit_object().startswith(b"\x7fELF")
    assert " global " not in str(compiled.module)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,fields,body,result,helpers,expected", CASES, ids=[x[0] for x in CASES])
def test_original_constant_source_has_exact_native_result(name, fields, body, result, helpers, expected, tmp_path, monkeypatch):
    text = original(fields, body, result, helpers)
    (tmp_path / "Constants.java").write_text(text)
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_source(text, descriptor="()J" if result == "long" else "()I")
    assert observe_scalar(compiled, tmp_path, monkeypatch) == expected
    assert (tmp_path / "Constants.java").read_text() == text


@pytest.mark.parametrize("spelling,value", [
    ("'A'", 65), ("'中'", 20013), ("'\uffff'", 65535), ("'\"'", 34),
    (r"'\b'", 8), (r"'\t'", 9), (r"'\n'", 10), (r"'\f'", 12), (r"'\r'", 13),
    (r"'\s'", 32), (r"'\"'", 34), (r"'\''", 39), (r"'\\'", 92),
    (r"'\0'", 0), (r"'\7'", 7), (r"'\77'", 63), (r"'\377'", 255),
])
def test_character_literal_is_one_unsigned_utf16_code_unit(spelling, value):
    compiled = compile_source(original("", "return " + spelling + ";"))
    assert "ret i32 " + str(value) in str(compiled.module)


@pytest.mark.parametrize("spelling", ["''", "'ab'", "'😀'", r"'\400'", r"'\777'", r"'\8'", r"'\x41'", "'\n'", "'\r'", "'\\\n'"])
def test_invalid_character_literal_fails_before_ir(spelling, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("invalid char reached IR"))
    with pytest.raises(JavaError, match="character literal"):
        compile_source(original("", "return " + spelling + ";"))


@pytest.mark.parametrize("fields,body,message", [
    ("static final int A=B,B=42;", "return A;", "illegal forward"),
    ("static final int A=A;", "return A;", "self reference"),
    ("static final int A=1,A=2;", "return A;", "duplicate field"),
    ("static final int A=42;", "A=7;return A;", "cannot assign a final"),
    ("static final int A=42;", "A++;return A;", "cannot assign a final"),
    ("static final int A=1L;", "return A;", "incompatible assignment"),
    ("static final byte A=128;", "return A;", "incompatible assignment"),
    ("static final char A=-1;", "return A;", "incompatible assignment"),
    ("static final char A=1L;", "return A;", "incompatible assignment"),
    ("static final boolean A=1;", "return 42;", "boolean value required"),
    ("static final int A=true;", "return A;", "integer value required"),
    ("static strictfp final int A=1;", "return A;", "invalid constant-field modifier"),
    ("static final boolean NEVER=false;", "while(NEVER){}return 42;", "unreachable loop body"),
    ("static final int A=42;", "int A;return A;", "not definitely assigned"),
    ("static final int Constants=42;", "return Constants.run();", "cannot be dereferenced"),
])
def test_invalid_constant_source_fails_before_ir(fields, body, message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("invalid constant reached IR"))
    with pytest.raises(JavaError, match=message):
        compile_source(original(fields, body))


@pytest.mark.parametrize("text,message", [
    (original("static int VALUE=42;", "return 42;"), "mutable static fields"),
    (original("static final int VALUE;", "return 42;"), "blank final fields"),
    (original("static final int VALUE=f();", "return 42;", helpers="static int f(){return 7;}"), "nonconstant static initialization"),
    (original("static final boolean VALUE=false&&f();", "return 42;", helpers="static boolean f(){return true;}"), "nonconstant static initialization"),
    (original("static final int VALUE=Constants.VALUE;", "return 42;"), "cyclic field initialization"),
    (original("static final int A=Constants.B,B=Constants.A;", "return 42;"), "cyclic field initialization"),
    (original("static final int[] VALUES={2,3,5};", "return 42;"), "array fields"),
    (original('static final String MESSAGE="unchanged";', "return 42;"), "unsupported Java source token"),
    (original("static { int value=42; }", "return 42;"), "source method results"),
    (original("", "return '\\u0041';"), "Unicode-escape translation"),
])
def test_runtime_or_lexical_boundaries_do_not_drop_original_declarations(text, message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("runtime-dependent declaration reached IR"))
    with pytest.raises(UnsupportedJava, match=message):
        compile_source(text)


PROJECT = {
    "Main.java": "package app;import library.Values;class Main{static long run(){return Values.MAX+Values.ONE;}}",
    "Values.java": "package library;public class Values{public static final long MAX=9223372036854775807L;public static final int ONE=1;}",
}
CHAIN_PROJECT = {
    "A.java": "package p;class A{static final long VALUE=Z.VALUE+1;}",
    "Z.java": "package p;class Z{protected static final long VALUE=41;}",
    "Main.java": "package p;class Main{static long run(){return A.VALUE;}}",
}


def test_constant_project_declares_every_field_before_resolving_and_is_order_independent(monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    a = compile_sources(PROJECT, entry_class="app.Main", descriptor="()J")
    b = compile_sources(dict(reversed(list(PROJECT.items()))), entry_class="app.Main", descriptor="()J")
    assert str(a.module) == str(b.module)
    assert a.emit_object() == b.emit_object()


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
def test_original_cross_class_constant_project_has_exact_native_result(tmp_path, monkeypatch):
    (tmp_path / "original-project.json").write_text(json.dumps(PROJECT, sort_keys=True))
    compiled = compile_sources(PROJECT, entry_class="app.Main", descriptor="()J")
    assert observe_scalar(compiled, tmp_path, monkeypatch) == -9223372036854775808


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
def test_original_cross_class_constant_dependency_chain_has_exact_native_result(tmp_path, monkeypatch):
    (tmp_path / "original-project.json").write_text(json.dumps(CHAIN_PROJECT, sort_keys=True))
    compiled = compile_sources(CHAIN_PROJECT, entry_class="p.Main", descriptor="()J")
    assert observe_scalar(compiled, tmp_path, monkeypatch) == 42


def test_cyclic_cross_class_initialization_is_not_constant_folded_to_default_zero():
    units = {"A.java": "package p;class A{static final int VALUE=Z.VALUE;static int run(){return 42;}}",
             "Z.java": "package p;class Z{static final int VALUE=A.VALUE;}"}
    with pytest.raises(UnsupportedJava, match="cyclic field initialization"):
        compile_sources(units, entry_class="p.A")


def test_unused_class_with_nonconstant_initializer_is_not_omitted():
    units = {"Main.java": "class Main{static int run(){return 42;}}",
             "Unused.java": "class Unused{static final int VALUE=f();static int f(){return 7;}}"}
    with pytest.raises(UnsupportedJava, match="nonconstant static initialization"):
        compile_sources(units, entry_class="Main")


def test_local_obscures_constant_field_class_qualifier():
    units = {"Main.java": "class Main{static int run(){int Values=0;return Values.VALUE;}}",
             "Values.java": "class Values{static final int VALUE=42;}"}
    with pytest.raises(JavaError, match="cannot be dereferenced"):
        compile_sources(units, entry_class="Main")


@pytest.mark.parametrize("access", ["private", "protected", ""])
def test_cross_class_constant_access_is_checked_even_for_inlined_values(access):
    units = {"Main.java": "package app;import library.Values;class Main{static int run(){return Values.VALUE;}}",
             "Values.java": "package library;public class Values{" + access + " static final int VALUE=42;}"}
    with pytest.raises(JavaError, match="constant field is not accessible"):
        compile_sources(units, entry_class="app.Main")


def test_method_can_refer_to_a_field_declared_after_its_body():
    text = "class Constants{static int run(){return VALUE;}static final int VALUE=42;}"
    assert "ret i32 42" in str(compile_source(text).module)


def test_parser_preserves_constant_field_identity_and_initializer():
    parsed = SourceParser("class C{static final int A=40,B=A+2;static int run(){return B;}}", "C.java").parse()
    assert [(f.owner, f.token.text, f.dtype, f.ordinal) for f in parsed.fields] == [("C", "A", "int", 0), ("C", "B", "int", 1)]
    assert parsed.fields[1].initializer.kind == "binary"


def test_uncalled_bad_method_is_not_discarded_after_constant_field_evaluation():
    with pytest.raises(JavaError, match="missing return"):
        compile_source(original("static final int VALUE=42;", "return VALUE;", helpers="static int unused(){}"))


def test_package_qualified_constant_without_implicit_type_metadata_is_explicitly_gated():
    original_units = {"Main.java": "package app;class Main{static int run(){return library.Values.VALUE;}}",
                      "Values.java": "package library;public class Values{public static final int VALUE=42;}"}
    with pytest.raises(UnsupportedJava, match="complete implicit-type metadata"):
        compile_sources(original_units, entry_class="app.Main")
