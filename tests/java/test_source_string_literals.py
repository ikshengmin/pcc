"""Ordinary string lexical/AST obligations, not managed String acceptance.

JLS21 3.1, 3.3, 3.10.5-7 and 4.3.3:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-3.html
https://docs.oracle.com/javase/specs/jls/se21/html/jls-4.html#jls-4.3.3

The same complete valid originals reach the PCC parser and a labeled JDK
reference. PCC still rejects managed String semantics before any IR lowering.
The unchanged whole Commons Numbers gate remains strict and separate.
Execution of every obligation in this file is delegated to CI.
"""
import json
from pathlib import Path

import pytest

from pcc.frontends.java import (
    JavaError, UnsupportedJava, analyze_class_sources, compile_source, compile_sources,
)
from pcc.frontends.java.source_syntax import SourceParser, tokenize
from scripts.ci_java_primes_project import ENTRY, PRODUCTION, load_inputs, original_sources
from scripts.ci_source_frontier_support import sha256
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_raw_reference import JVM_LIMITS, digest, record_process
from tests.java.test_source_unicode_escapes import JDK_REASON, compile_reference_batch


LITERALS = [
    ("empty", '""', ()),
    ("original-primes-format", '"%d is smaller than the minimum (%d)"',
     tuple(map(ord, "%d is smaller than the minimum (%d)"))),
    ("comment-delimiters-are-content", '"// /* */"', tuple(map(ord, "// /* */"))),
    ("standard-escapes", r'"\b\t\n\f\r\s\"\'\\"', (8, 9, 10, 12, 13, 32, 34, 39, 92)),
    ("one-octal-digit", r'"\7"', (7,)),
    ("two-octal-digits", r'"\77"', (63,)),
    ("three-octal-digits", r'"\377"', (255,)),
    ("three-digit-maximum", r'"\400"', (32, 48)),
    ("two-digit-maximum", r'"\777"', (63, 55)),
    ("nonoctal-after-escape", r'"\378"', (31, 56)),
    ("zero-then-eight", r'"\08"', (0, 56)),
    ("leading-zero-two-digits", r'"\078"', (7, 56)),
    ("zero-before-ascii", r'"\000a"', (0, 97)),
    ("raw-tab-and-formfeed", '"\t\f"', (9, 12)),
    ("raw-nul", '"\x00"', (0,)),
    ("raw-bmp", '"中文"', (0x4e2d, 0x6587)),
    ("raw-supplementary", '"😀"', (0xd83d, 0xde00)),
    ("escaped-supplementary", r'"\uD83D\uDE00"', (0xd83d, 0xde00)),
    ("unpaired-high-surrogate", r'"\uD800"', (0xd800,)),
    ("unpaired-low-surrogate", r'"\uDFFF"', (0xdfff,)),
    ("unicode-delimiters", r'\u0022text\u0022', (116, 101, 120, 116)),
    ("unicode-backslash-escape", r'"\u005cn"', (10,)),
    ("consecutive-escaped-backslashes", r'"\u005c\u005c"', (92,)),
    ("ineligible-unicode-marker", r'"\\u0041"', (92, 117, 48, 48, 52, 49)),
    ("unicode-is-not-recursive", r'"\u005c\u005cu0041"', (92, 117, 48, 48, 52, 49)),
    ("unicode-nul", r'"\u0000"', (0,)),
    ("unicode-line-separator-is-content", r'"\u2028"', (0x2028,)),
]


def original(literal, units):
    # This separate expected literal is part of the original program, not a
    # replacement of its input literal. Fixed-width octal avoids introducing
    # Unicode-translated quotes, backslashes or line terminators accidentally.
    expected = '"' + "".join("\\%03o" % unit if unit < 256 else "\\u%04x" % unit
                             for unit in units) + '"'
    return "class StringLiterals{static int run(){return " + literal + "==" + expected + "?42:7;}}"


CASES = [(name, "StringLiterals", original(literal, units), 42)
         for name, literal, units in LITERALS]


def string_nodes(node):
    if node.kind == "string":
        yield node
    for child in node.children:
        yield from string_nodes(child)


@pytest.mark.parametrize("name,literal,units", LITERALS, ids=[row[0] for row in LITERALS])
def test_valid_original_retains_utf16_units_only_in_source_ast(name, literal, units, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    text = original(literal, units)
    program = SourceParser(text, "StringLiterals.java").parse()
    nodes = list(string_nodes(program.methods[0].body))
    assert len(nodes) == 2 and all(node.value == units for node in nodes)
    assert all(isinstance(node.value, tuple) and node.dtype is node.constant is None for node in nodes)
    assert all(node.token.string_units is node.value for node in nodes)
    assert all(0 <= unit <= 0xffff for node in nodes for unit in node.value)
    assert text == original(literal, units)


@pytest.mark.parametrize("name,owner,text,expected", CASES, ids=[row[0] for row in CASES])
def test_same_valid_original_stops_at_managed_string_semantics(name, owner, text, expected, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("String reached IR lowering"))
    forbid_bytecode_or_external_owner(monkeypatch)
    for invoke, message in ((lambda: compile_source(text, filename=owner + ".java"), "shared managed String allocation"),
                            (lambda: analyze_class_sources({owner + ".java": text}), "String equality requires")):
        with pytest.raises(UnsupportedJava, match=message) as caught:
            invoke()
        assert caught.value.code == "java.unsupported.string-literals"


INVALID_LITERALS = [
    ("unterminated", '"text'),
    ("dangling-backslash", '"text\\'),
    ("unknown-escape", r'"\q"'),
    ("eight-not-octal", r'"\8"'),
    ("x-is-not-an-escape", r'"\x41"'),
    ("linefeed", '"a\nb"'),
    ("carriage-return", '"a\rb"'),
    ("crlf", '"a\r\nb"'),
    ("escaped-linefeed", '"a\\\nb"'),
    ("escaped-carriage-return", '"a\\\rb"'),
    ("unicode-linefeed", r'"a\u000ab"'),
    ("unicode-carriage-return", r'"a\u000db"'),
    ("nonrecursive-invalid-escape", r'"\u005cu0041"'),
    ("malformed-unicode", r'"\u00G0"'),
    ("text-block-without-newline", '"""text"""'),
]
INVALID = [(name, "StringLiterals", original(literal, ())) for name, literal in INVALID_LITERALS]


@pytest.mark.parametrize("name,owner,text", INVALID, ids=[row[0] for row in INVALID])
def test_same_invalid_original_is_invalid_java_before_ir(name, owner, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("invalid string reached IR"))
    with pytest.raises(JavaError) as caught:
        compile_source(text, filename=owner + ".java")
    assert type(caught.value) is JavaError and caught.value.code is None


@pytest.mark.parametrize("source,line,column,next_column", [
    (r'\u0022x\u0022 next', 1, 1, 15),
    (r'"\u0041" next', 1, 1, 10),
    ("\r\n  " + r'\u0022x\u0022 next', 2, 3, 17),
])
def test_string_tokens_retain_raw_positions_after_unicode_translation(source, line, column, next_column):
    literal, following, eof = tokenize(source, "Raw.java")
    assert literal.text in {'"x"', '"A"'}
    assert (literal.line, literal.column) == (line, column)
    assert (following.text, following.line, following.column) == ("next", line, next_column)
    assert eof.text == "<eof>"


@pytest.mark.parametrize("literal", ['"""\ntext\n"""', '""" \t\f\r\ntext\r\n"""'])
def test_valid_text_block_opening_keeps_explicit_lexical_boundary(literal):
    with pytest.raises(UnsupportedJava, match="text blocks") as caught:
        tokenize(literal, "Block.java")
    assert caught.value.code == "java.unsupported.string-literals"


def test_original_primes_lexing_preserves_the_next_parser_boundary(monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    _, files = load_inputs()
    before = {name: sha256(data) for name, data in files.items()}
    sources = original_sources(files, PRODUCTION)
    token_sets = {name: tokenize(text, name) for name, text in sources.items()}
    first = PRODUCTION[0]
    literal = next(token for token in token_sets[first] if token.text.startswith('"'))
    assert (literal.line, literal.column, literal.text) == (
        31, 44, '"%d is smaller than the minimum (%d)"')
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("original project reached IR"))
    forbid_bytecode_or_external_owner(monkeypatch)
    with pytest.raises(UnsupportedJava) as caught:
        compile_sources(sources, **ENTRY)
    # The literal's constant variable folds without storage; the enhanced for
    # and the throw parse; the next boundary is the List<Integer> result type.
    assert caught.value.code == "java.unsupported.declared-result-type"
    assert str(caught.value).startswith(first + ":108:19:")
    assert {name: sha256(data) for name, data in files.items()} == before
    # The independent original-project test still requires every actual stage
    # to PASS. Reaching a later capability boundary is not project acceptance.


def record_reference_inputs(tmp_path, request):
    root = Path(__file__).resolve().parents[2]
    paths = (Path(__file__), root / "pcc/frontends/java/source_syntax.py",
             root / "pcc/frontends/java/source_sema.py")
    before = {str(path): digest(path) for path in paths}
    (tmp_path / "string-reference-inputs.json").write_text(json.dumps({
        "inputs": before, "owner": "explicit external JDK reference only",
        "pcc_native": "UNRUN", "managed_string_acceptance": False,
    }, indent=2) + "\n")

    def unchanged():
        assert before == {str(path): digest(path) for path in paths}

    request.addfinalizer(unchanged)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
def test_same_valid_original_literals_match_jdk(tmp_path, request):
    record_reference_inputs(tmp_path, request)
    java = compile_reference_batch(tmp_path, request, CASES, runtime=True)
    for name, owner, text, expected in CASES:
        directory = tmp_path / name
        assert (directory / "compiler-status.txt").read_text() == "0", name
        actual = record_process((str(java), *JVM_LIMITS, "-cp", str(directory / "classes"),
                                 "PccNarrowObserve"), directory, "literal-reference-run")
        assert actual == (0, expected.to_bytes(4, "little", signed=True), b""), name
        assert (directory / (owner + ".java")).read_bytes() == text.encode("utf-8")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
def test_same_invalid_original_literals_are_rejected_by_jdk(tmp_path, request):
    record_reference_inputs(tmp_path, request)
    compile_reference_batch(tmp_path, request, INVALID, runtime=False)
    for name, owner, text in INVALID:
        directory = tmp_path / name
        assert int((directory / "compiler-status.txt").read_text()) != 0, name
        assert (directory / (owner + ".java")).read_bytes() == text.encode("utf-8")


def test_long_octal_input_has_one_bounded_scan_and_the_original_size_limit():
    text = '"' + r'\377' * 8192 + '"'
    token, eof = tokenize(text, "Long.java")
    assert token.string_units == (255,) * 8192 and eof.text == "<eof>"
    with pytest.raises(JavaError) as caught:
        tokenize(text[:-1], "Long.java")
    assert type(caught.value) is JavaError
    with pytest.raises(UnsupportedJava) as caught:
        tokenize('"' + "a" * 1_048_575 + '"', "Large.java")
    assert caught.value.code == "java.unsupported.source-size-limit"
