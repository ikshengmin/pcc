"""Physical source lines: N4659 [lex.phases]/1-3, [lex.ccon], [lex.string].

https://timsong-cpp.github.io/cppwp/n4659/lex.phases
These are authored newline fixtures, not rewritten upstream inputs. The exact
TinyXML2 body remains in test_cpp_const_casts and the platform execution corpus.
"""

import hashlib

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.driver import preprocess_cpp
from tests.cpp.test_cpp_const_casts import (
    INTERNED_DECLARATION, INTERNED_PROGRAM, INTERNED_SHA256,
)


LINE_ENDINGS = (("lf", "\n"), ("crlf", "\r\n"), ("cr", "\r"))
_PROGRAM = (
    "// a physical line comment\n"
    "int cpp_test_entry(){\n"
    "  int value=40; // not source: @\n"
    "  /* a block comment\n"
    "     over two physical lines */\n"
    "  return value + \\\n"
    "    2;\n"
    "}\n"
)
PROGRAMS = tuple(
    ("source-newline-" + name, _PROGRAM.replace("\n", ending), 42)
    for name, ending in LINE_ENDINGS
)


def token_rows(source, filename="physical.cpp"):
    return [
        (token.kind, token.value, token.coord.file, token.coord.line, token.coord.column)
        for token in CppParser()._tokenize(source, filename)
    ]


@pytest.mark.parametrize("name,ending", LINE_ENDINGS, ids=[row[0] for row in LINE_ENDINGS])
def test_physical_endings_keep_tokens_comments_splices_and_coordinates(name, ending):
    source = _PROGRAM.replace("\n", ending)
    original = source.encode()
    assert token_rows(source) == token_rows(_PROGRAM)
    CppParser().parse(source, "physical.cpp")
    assert source.encode() == original
    rows = {row[1]: row for row in token_rows(source)}
    assert rows["2"][3:] == (7, 5)
    assert rows["<eof>"][3:] == (9, None)


def test_mixed_physical_endings_count_once_and_preserve_indentation():
    rows = {row[1]: row for row in token_rows("int first;\r\nint second;\r  int third;\n")}
    assert rows["first"][3:] == (1, 5)
    assert rows["second"][3:] == (2, 5)
    assert rows["third"][3:] == (3, 7)
    assert rows["<eof>"][3:] == (4, None)


@pytest.mark.parametrize("name,ending", LINE_ENDINGS, ids=[row[0] for row in LINE_ENDINGS])
def test_preprocessor_splices_before_comments_and_token_boundaries(name, ending):
    source = (
        "#define ANSWER 4\\\n"
        "2\n"
        "// continued comment \\\n"
        "#error still part of the comment\n"
        "int cpp_test_entry(){return ANS\\\n"
        "WER;}\n"
    ).replace("\n", ending)
    original = source.encode()
    preprocessed = preprocess_cpp(source)
    assert [row[1] for row in token_rows(preprocessed)] == [
        "int", "cpp_test_entry", "(", ")", "{", "return", "42", ";", "}", "<eof>",
    ]
    CppParser().parse(preprocessed, "spliced.cpp")
    assert source.encode() == original


@pytest.mark.parametrize("name,ending", LINE_ENDINGS, ids=[row[0] for row in LINE_ENDINGS])
@pytest.mark.parametrize("quote", ("'", '"'))
def test_unescaped_physical_newlines_in_ordinary_quotes_are_rejected(name, ending, quote):
    prefix = "int f(){return "
    source = ("int prefix;\n" + prefix + quote + "a\nb" + quote + ";}\n").replace("\n", ending)
    with pytest.raises(CppDiagnostic) as caught:
        CppParser().parse(source, "quoted.cpp")
    diagnostic = caught.value
    assert diagnostic.code == "cpp.lexical"
    assert "unterminated" in diagnostic.message
    assert (diagnostic.coord.file, diagnostic.coord.line, diagnostic.coord.column) == (
        "quoted.cpp", 2, len(prefix) + 3,
    )
    # Public preprocessing must not erase an invalid raw newline either.
    with pytest.raises(CppDiagnostic) as public:
        CppParser().parse(preprocess_cpp(source), "quoted.cpp")
    assert public.value.code == "cpp.lexical"


@pytest.mark.parametrize("name,ending", LINE_ENDINGS, ids=[row[0] for row in LINE_ENDINGS])
def test_escaped_carriage_return_is_not_a_physical_newline(name, ending):
    source = "int f(){\n  return '\\r';\n}\n".replace("\n", ending)
    tokens = token_rows(source)
    assert next(row for row in tokens if row[0] == "CHAR_CONST")[1] == "'\\r'"
    CppParser().parse(source, "escape.cpp")


@pytest.mark.parametrize("name,ending", LINE_ENDINGS, ids=[row[0] for row in LINE_ENDINGS])
def test_lexical_semantic_and_line_marker_diagnostics_keep_coordinates(name, ending):
    for source, code, location in (
        ("// comment\nint f(){\n  @\n}\n", "cpp.lexical", ("physical.cpp", 3, 3)),
        ("// comment\nint f(){\n  return absent;\n}\n", "cpp.undeclared", ("physical.cpp", 3, 10)),
        ('#line 80 "upstream.hpp"\n  @\n', "cpp.lexical", ("upstream.hpp", 80, 3)),
    ):
        with pytest.raises(CppDiagnostic) as caught:
            CppParser().parse(source.replace("\n", ending), "physical.cpp")
        diagnostic = caught.value
        assert diagnostic.code == code
        assert (diagnostic.coord.file, diagnostic.coord.line, diagnostic.coord.column) == location


@pytest.mark.parametrize("separator", ("\u0085", "\u2028", "\u2029"))
def test_other_unicode_separators_are_not_silently_rewritten(separator):
    with pytest.raises(CppDiagnostic) as caught:
        CppParser().parse("int first;" + separator + "int second;", "separator.cpp")
    assert caught.value.code == "cpp.lexical"
    assert (caught.value.coord.line, caught.value.coord.column) == (1, 11)


def test_exact_upstream_body_remains_raw_input_not_a_normalized_fixture():
    original = INTERNED_PROGRAM.encode()
    assert b"\r\n" in original
    assert hashlib.sha256(INTERNED_DECLARATION.encode()).hexdigest() == INTERNED_SHA256
    CppParser().parse(INTERNED_PROGRAM, "original-interned-body.cpp")
    assert INTERNED_PROGRAM.encode() == original
