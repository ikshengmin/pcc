"""N4659 [lex.ccon], [lex.charset], [conv.ptr]/1, [temp.arg.nontype].

https://timsong-cpp.github.io/cppwp/n4659/lex.ccon
https://timsong-cpp.github.io/cppwp/n4659/conv.ptr

Source-only CI obligations. Preserve the three original escape/newline inputs
from CI 41411705867 (e82f57b5), not a lexer-only substitute for expression Sema.
All locally UNRUN; host-owned native execution is not native pcc1 qualification.
"""

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.constants import KNOWN
from tests.cpp.owned_execution import TARGET, compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only
from tests.cpp.test_cpp_source_newlines import LINE_ENDINGS


ORIGINAL_ESCAPE = "int f(){\n  return '\\r';\n}\n"
PROGRAMS = tuple(
    ("original-escaped-cr-" + name, (("entry.cpp", ORIGINAL_ESCAPE.replace("\n", ending) +
      "int cpp_test_entry(){return f()+29;}"),)) for name, ending in LINE_ENDINGS
) + (
    ("ordinary-char-overload-not-int", (("entry.cpp",
      "int f(char x){return 42;}int f(int x){return 1;}"
      "int cpp_test_entry(){return f('x');}"),)),
    ("zero-char-not-null-pointer", (("entry.cpp",
      "int f(char x){return 42;}int f(int*p){return 1;}"
      "int cpp_test_entry(){return f('\\0');}"),)),
    ("sizeof-and-decltype-char", (("entry.cpp",
      "int f(char x){return 41;}int f(int x){return 1;}"
      "int cpp_test_entry(){decltype('x') c='a';return f(c)+sizeof('x');}"),)),
    ("simple-octal-hex-escapes", (("entry.cpp",
      "int cpp_test_entry(){if('\\n'!=10||'\\t'!=9||'\\r'!=13||'\\a'!=7||"
      "'\\b'!=8||'\\f'!=12||'\\v'!=11||'\\\\'!=92||'\\\''!=39||"
      "'\\\"'!=34||'\\?'!=63)return 1;return '\\052'+'\\x2a'-'*';}"),)),
    ("constant-array-bound", (("entry.cpp",
      "int cpp_test_entry(){const char n='*';int values[n]={};"
      "return sizeof(values)/sizeof(int);}"),)),
    ("template-char-deduction", (("entry.cpp",
      "template<class T>T id(T x){return x;}int f(char x){return 42;}int f(int x){return 1;}"
      "int cpp_test_entry(){return f(id('*'));}"),)),
    ("template-body-char-zero-retains-type", (("entry.cpp",
      "int f(char x){return 42;}int f(int*p){return 1;}"
      "template<class T>int g(T x){return f('\\0');}"
      "int cpp_test_entry(){return g(0);}"),)),
    ("char-nontype-template-argument", (("entry.cpp",
      "template<char N>int f(){return N;}int cpp_test_entry(){return f<'*'>();}"),)),
    # This corpus uses the fixed x86_64 Linux owned adapter, not the platform
    # corpus. The separate target test below requires unsigned Linux AArch64.
    ("signed-numeric-byte", (("entry.cpp",
      "int cpp_test_entry(){if('\\xff'!=-1||'\\200'!=-128)return 1;return 42;}"),)),
)


INVALID = (
    ("empty", "int f(){return '';}", "cpp.invalid-character-literal"),
    ("hex-without-digits", "int f(){return '\\x';}", "cpp.invalid-character-literal"),
    ("char-zero-pointer-initializer", "int f(){int*p='\\0';return 0;}", "cpp.invalid-conversion"),
    ("char-zero-pointer-argument", "int f(int*);int g(){return f('\\0');}", "cpp.no-viable-overload"),
    ("char-zero-pointer-in-template", "int f(int*);template<class T>int g(T x){return f('\\0');}",
     "cpp.no-viable-overload"),
)


DEFERRED = ("L'a'", "u'a'", "U'a'", "'ab'", "'é'", "'\\u0041'", "'\\q'", "'\\x100'")


@pytest.mark.parametrize("name,units", PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_character_originals_compile_deterministically(owned_only, name, units):
    assert TARGET == "x86_64-unknown-linux-gnu", "character corpus requires the matching fixed-target reference"
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize("target,expected", (
    ("x86_64-unknown-linux-gnu", -1),
    ("arm64-apple-darwin", -1),
    ("aarch64-unknown-linux-gnu", 255),
    ("x86_64-pc-windows-msvc", -1),
))
def test_numeric_character_uses_target_plain_char_signedness(target, expected):
    parser = CppParser(target)
    parser.parse("const char value='\\xff';", "character.cpp")
    fact = parser.scope.names["value"].constant_fact
    assert fact.status == KNOWN and fact.dtype == "char" and fact.value == expected


def test_character_projection_is_char_cast_not_c_int_literal():
    parser = CppParser("x86_64-unknown-linux-gnu")
    tree = parser.parse("char f(){return '*';}")
    expression = tree.ext[0].body.block_items[0].expr
    assert isinstance(expression, c_ast.Cast)
    assert expression.to_type.type.type.names == ["char"]
    assert expression.expr.type == "int" and expression.expr.value == "42"


@pytest.mark.parametrize("name,source,code", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_character_input_stops_before_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid character expression reached object emission")

    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name + ".cpp", source),))
    assert error.value.code == code


@pytest.mark.parametrize("literal", DEFERRED)
def test_unowned_character_encodings_keep_capability_diagnostic(literal):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse("int f(){return " + literal + ";}")
    assert error.value.code == "cpp.unsupported.character-literals"
