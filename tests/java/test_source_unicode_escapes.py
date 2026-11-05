"""Java raw Unicode escapes before lexical analysis, never input rewriting.

JLS21 3.2-3.5, 3.7, 3.10.4 and 3.10.7:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-3.html#jls-3.3
https://docs.oracle.com/javase/specs/jls/se21/html/jls-3.html#jls-3.5

The exact raw inputs are shared by PCC native and development-only JDK lanes.
Escaped strings and non-ASCII identifiers retain their separate boundaries.
"""
import hashlib
import json
import os
from pathlib import Path
import re

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, compile_source
from pcc.frontends.java.source_syntax import _translate_unicode_escapes, tokenize
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner, source as frontend_source
from tests.java.test_source_narrow_primitives import observer_source
from tests.java.test_source_raw_reference import JVM_LIMITS, digest, record_process, tool_identity
from tests.java.test_source_scalars import NATIVE_REASON


def original(body):
    return "class UnicodeEscapes{static int run(){" + body + "}}"


CASES = [
    # This is the former constant-source rejection input, unchanged.
    ("original-constant-character", "Constants", "class Constants{static int run(){return '\\u0041';}}", 65),
    ("escaped-class-keyword", "UnicodeEscapes", r"cl\u0061ss UnicodeEscapes{static int run(){return 42;}}", 42),
    ("escaped-method-declaration", "UnicodeEscapes", r"class UnicodeEscapes{sta\u0074ic \u0069nt r\u0075n(){return 42;}}", 42),
    ("escaped-local-name", "UnicodeEscapes", original(r"int n\u0061me=40;return na\u006de+2;"), 42),
    ("escaped-identifier-start", "UnicodeEscapes", original(r"int \u0078=42;return x;"), 42),
    ("repeated-marker", "UnicodeEscapes", original(r"return '\uuuu0041';"), 65),
    ("mixed-case-hex", "UnicodeEscapes", original(r"return '\u00fF';"), 255),
    ("escaped-digits", "UnicodeEscapes", original(r"return \u0034\u0032;"), 42),
    ("exactly-four-hex-digits", "UnicodeEscapes", original(r"return \u00342;"), 42),
    ("escaped-operator", "UnicodeEscapes", original(r"return 40\u002b2;"), 42),
    ("escaped-char-delimiters", "UnicodeEscapes", original(r"return \u0027A\u0027;"), 65),
    ("escaped-block-comment", "UnicodeEscapes", original(r"return 40\u002f\u002a ignored \u002a\u002f+2;"), 42),
    ("escaped-line-comment", "UnicodeEscapes", original("int x=42;" + r"\u002f\u002f x=7;" + "\nreturn x;"), 42),
    ("escaped-lf", "UnicodeEscapes", original("int x=7;// " + r"\u000ax=42;" + "\nreturn x;"), 42),
    ("escaped-cr", "UnicodeEscapes", original("int x=7;// " + r"\u000dx=42;" + "\nreturn x;"), 42),
    ("escaped-crlf", "UnicodeEscapes", original("int x=7;// " + r"\u000d\u000ax=42;" + "\nreturn x;"), 42),
    ("escaped-cr-raw-lf", "UnicodeEscapes", original("int x=7;// " + r"\u000d" + "\nx=42;return x;"), 42),
    ("raw-cr-escaped-lf", "UnicodeEscapes", original("int x=7;// \r" + r"\u000ax=42;return x;"), 42),
    ("translated-backslash-makes-next-eligible", "UnicodeEscapes", original("int x=7;// " + r"\u005c\u000ax=42;" + "\nreturn x;"), 42),
    ("translated-backslash-counts-in-parity", "UnicodeEscapes", original("int x=7;// " + r"\u005c\\u000ax=42;" + "\nreturn x;"), 42),
    ("mixed-backslashes-ineligible", "UnicodeEscapes", original("int x=7;// " + r"\u005c\\\u000ax=42;" + "\nreturn x;"), 7),
    ("nonrecursive-backslash", "UnicodeEscapes", original("int x=7;// " + r"\u005cu000ax=42;" + "\nreturn x;"), 7),
    ("uppercase-marker-is-raw", "UnicodeEscapes", original("int x=7;// " + r"\U000ax=42;" + "\nreturn x;"), 7),
    ("escaped-backslash-character", "UnicodeEscapes", original(r"return '\u005c\u005c';"), 92),
    ("escaped-character-escape", "UnicodeEscapes", original(r"return '\u005cn';"), 10),
    ("both-character-escape-characters", "UnicodeEscapes", original(r"return '\u005c\u006e';"), 10),
    ("nul-code-unit", "UnicodeEscapes", original(r"return '\u0000';"), 0),
    ("high-surrogate-code-unit", "UnicodeEscapes", original(r"return '\uD800';"), 0xD800),
    ("low-surrogate-code-unit", "UnicodeEscapes", original(r"return '\udfff';"), 0xDFFF),
    ("surrogate-pair-in-comment", "UnicodeEscapes", original(r"/* \uD83D\uDE00 */return 42;"), 42),
]
CASES += [
    ("backslash-parity-" + str(count), "UnicodeEscapes",
     original("int x=7;// " + "\\" * count + "u000ax=42;\nreturn x;"), 42 if count % 2 else 7)
    for count in range(1, 7)
]
CASES += [
    ("ineligible-malformed-" + str(count), "UnicodeEscapes",
     original("// " + "\\" * count + "u00G0\nreturn 42;"), 42)
    for count in (2, 4, 6)
]
# A final SUB is an input terminator, not whitespace or an interior token.
# Keep original bytes for all ordinary/native/JDK lanes, including the escape.
CASES += [
    ("terminal-sub-raw", "UnicodeEscapes", original("return 42;") + "\x1a", 42),
    ("terminal-sub-escaped", "UnicodeEscapes", original("return 42;") + r"\u001a", 42),
    ("terminal-sub-repeated-marker", "UnicodeEscapes", original("return 42;") + r"\uuuu001A", 42),
    ("terminal-sub-after-crlf", "UnicodeEscapes", original("return 42;") + "\r\n\x1a", 42),
    ("terminal-sub-after-escaped-lf", "UnicodeEscapes", original("return 42;") + r"\u000a\u001a", 42),
    ("terminal-sub-after-block-comment", "UnicodeEscapes", original("return 42;") + "/*\x1a*/\x1a", 42),
    ("terminal-sub-in-line-comment", "UnicodeEscapes", original("return 42;") + "//\x1a", 42),
    ("terminal-sub-after-line-comment", "UnicodeEscapes", original("return 42;") + "//\x1a\n\x1a", 42),
    ("sub-character-literal-is-retained", "UnicodeEscapes", original("return '\x1a';") + "\x1a", 26),
    ("sub-escaped-character-is-retained", "UnicodeEscapes", original(r"return '\u001a';") + r"\u001a", 26),
]


INVALID = [
    ("malformed-in-line-comment", "UnicodeEscapes", original(r"// \u00G0" + "\nreturn 42;")),
    ("malformed-in-block-comment", "UnicodeEscapes", original(r"/* \uu0x00 */return 42;")),
    ("short-escape-at-eof", "UnicodeEscapes", original("return 42;") + r"\u123"),
    ("marker-at-eof", "UnicodeEscapes", original("return 42;") + r"\uu"),
    ("non-ascii-hex-digit", "UnicodeEscapes", original(r"/* \u00" + "４２ */return 42;")),
    ("escaped-lf-in-character", "UnicodeEscapes", original(r"return '\u000a';")),
    ("escaped-cr-in-character", "UnicodeEscapes", original(r"return '\u000d';")),
    ("escaped-quote-in-character", "UnicodeEscapes", original(r"return '\u0027';")),
    ("nonrecursive-character-backslash", "UnicodeEscapes", original(r"return '\u005cu0041';")),
    ("surrogate-pair-is-two-code-units", "UnicodeEscapes", original(r"return '\uD83D\uDE00';")),
    ("escaped-keyword-not-identifier", "UnicodeEscapes", original(r"int \u0069f=42;return 42;")),
    ("escaped-comment-close-exposes-invalid-token", "UnicodeEscapes", original(r"/* \u002a\u002f # */return 42;")),
    ("escaped-opening-comment-is-unterminated", "UnicodeEscapes", original(r"\u002f\u002a return 42;")),
    # Both former rejection originals remain byte-for-byte invalid inputs.
    ("original-frontend-comment", "Example", frontend_source("return 1; // \\u000a comment\n")),
    ("original-diagnostic-comment", "Diagnostic", "class Diagnostic{static int run(){return 42; // \\u000a comment\n}}"),
]
INVALID += [
    ("eligible-malformed-" + str(count), "UnicodeEscapes",
     original("// " + "\\" * count + "u00G0\nreturn 42;"))
    for count in (1, 3, 5)
]
INVALID += [
    ("sub-before-space", "UnicodeEscapes", original("return 42;") + "\x1a "),
    ("sub-before-lf", "UnicodeEscapes", original("return 42;") + "\x1a\n"),
    ("two-terminal-subs", "UnicodeEscapes", original("return 42;") + "\x1a\x1a"),
    ("escaped-sub-before-space", "UnicodeEscapes", original("return 42;") + r"\u001a\u0020"),
    ("sub-before-closing-braces", "UnicodeEscapes", original("return 42;\x1a")),
    ("sub-does-not-close-comment", "UnicodeEscapes", original("return 42;") + "/*\x1a"),
    ("sub-does-not-close-string", "UnicodeEscapes", original("return 42;") + '"\x1a'),
    ("sub-does-not-hide-malformed-escape", "UnicodeEscapes", original("return 42;") + r"\u00G0" + "\x1a"),
]


@pytest.mark.parametrize("raw,expected", [
    (r"\u005cu005a", "\\u005a"),
    (r"\u005c\u005a", "\\Z"),
    (r"\\u005a", "\\\\u005a"),
    (r"\\\u005a", "\\\\Z"),
    (r"\u005c\\u005a", "\\\\Z"),
    (r"\u005c\\\u005a", "\\\\\\\\u005a"),
    (r"\u005c\u005c\u005a", "\\\\Z"),
    (r"\uuuu005a", "Z"),
    (r"\uD83D\uDE00", "\ud83d\ude00"),
])
def test_jls_33_eligibility_and_nonrecursive_code_units(raw, expected):
    translated, _, _ = _translate_unicode_escapes(raw, "Raw.java", [0])
    assert translated == expected


@pytest.mark.parametrize("name,owner,text,expected", CASES, ids=[row[0] for row in CASES])
def test_escaped_original_uses_owned_source_ir(name, owner, text, expected, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    result = compile_source(text, filename=owner + ".java")
    assert result.class_name == owner
    assert result.emit_object().startswith(b"\x7fELF")


@pytest.mark.parametrize("name,owner,text", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_escaped_original_fails_before_ir(name, owner, text, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("invalid escape reached IR"))
    with pytest.raises(JavaError) as caught:
        compile_source(text, filename=owner + ".java")
    assert type(caught.value) is JavaError
    assert caught.value.code is None


@pytest.mark.parametrize("text,needle,line,column", [
    (r"\u0069nt x;", "int", 1, 1),
    (r"\u0069nt x;", "x", 1, 10),
    (r"\uuuu0069nt x;", "x", 1, 13),
    (r"\u0069\u006et x;", "x", 1, 15),
    (r"// \u000a int x;", "int", 1, 11),
    ("// \\u000d\\u000a\r\nint x;", "int", 2, 1),
    ("// raw\rint x;", "int", 2, 1),
    ("\r\n" + r"\u0069nt x;", "x", 2, 10),
    (r"\u003b", "<eof>", 1, 7),
    ("\r\n" + r"\u003b", "<eof>", 2, 7),
])
def test_translated_tokens_retain_raw_source_locations(text, needle, line, column):
    token = next(token for token in tokenize(text, "Raw.java") if token.text == needle)
    assert (token.filename, token.line, token.column) == ("Raw.java", line, column)


@pytest.mark.parametrize("text,line,column", [
    ("\x1a", 1, 2),
    (r"\u001a", 1, 7),
    (r"\uuuu001A", 1, 10),
    (";\r\n\x1a", 2, 2),
    (r"\u003b\u001a", 1, 13),
    (";\r\n" + r"\u001a", 2, 7),
])
def test_terminal_sub_retains_raw_eof_location(text, line, column):
    tokens = tokenize(text, "Raw.java")
    assert [token.text for token in tokens] == ([";"] if text.startswith((";", r"\u003b")) else []) + ["<eof>"]
    assert (tokens[-1].filename, tokens[-1].line, tokens[-1].column) == ("Raw.java", line, column)


@pytest.mark.parametrize("text", ['"\x1a"\x1a', r'"\u001a"\u001a'])
def test_terminal_sub_does_not_strip_literal_contents(text):
    tokens = tokenize(text, "Raw.java")
    assert len(tokens) == 2 and tokens[0].string_units == (26,)


def test_terminal_sub_does_not_relax_source_size_limit():
    with pytest.raises(UnsupportedJava) as caught:
        tokenize(" " * 1_048_576 + "\x1a", "Raw.java")
    assert caught.value.code == "java.unsupported.source-size-limit"


@pytest.mark.parametrize("text,line,column,message", [
    (r"\u0069nt x; \u00G0", 1, 13, "malformed Java Unicode escape"),
    ("// first\r\n" + r" \uu12x4", 2, 2, "malformed Java Unicode escape"),
    (r"// \u000a #", 1, 11, "unsupported Java source token"),
    (r"\u002f\u002a", 1, 1, "unterminated comment"),
])
def test_lexical_errors_retain_raw_source_locations(text, line, column, message):
    with pytest.raises(JavaError) as caught:
        tokenize(text, "Raw.java")
    assert str(caught.value) == f"Raw.java:{line}:{column}: {message}"


@pytest.mark.parametrize("text,suffix", [
    (original(r"int \u03c0=1;return 42;"), "unicode-tokens"),
    (original(r"return \u0022text\u0022;"), "string-literals"),
    (original(r"\u00a0return 42;"), "unicode-tokens"),
])
def test_translation_does_not_claim_unimplemented_lexical_domains(text, suffix):
    with pytest.raises(UnsupportedJava) as caught:
        compile_source(text)
    assert caught.value.code == "java.unsupported." + suffix


def test_raw_size_limit_precedes_escape_contraction():
    text = r"\u0020" * 174_763
    with pytest.raises(UnsupportedJava) as caught:
        tokenize(text, "Raw.java")
    assert caught.value.code == "java.unsupported.source-size-limit"


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,owner,text,expected", CASES, ids=[row[0] for row in CASES])
def test_escaped_original_has_exact_native_result(name, owner, text, expected, tmp_path, monkeypatch):
    unit = tmp_path / (owner + ".java")
    unit.write_bytes(text.encode("utf-8"))
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        result = compile_source(text, filename=unit.name)
    assert observe_scalar(result, tmp_path, monkeypatch) == expected
    assert unit.read_bytes() == text.encode("utf-8")


JDK_REASON = None if os.environ.get("PCC_JAVA_RAW_REFERENCE") else "select an existing JDK with PCC_JAVA_RAW_REFERENCE"


def compile_reference_batch(tmp_path, request, cases, *, runtime):
    java = Path(os.environ["PCC_JAVA_RAW_REFERENCE"]).resolve(strict=True)
    level = os.environ.get("PCC_JAVA_RAW_REFERENCE_SOURCE", "21")
    assert level in {"17", "21"}
    version = re.search(r'^JAVA_VERSION="([^"]+)"', (java.parent.parent / "release").read_text(), re.MULTILINE)
    assert version and int(version.group(1).split(".")[0]) >= int(level)
    root = Path(__file__).parent
    inputs = (Path(__file__), root / "NarrowReference.java", root / "owned_execution.py",
              root / "test_source_narrow_primitives.py", root / "test_source_raw_reference.py",
              root.parents[1] / "pcc/frontends/java/source_syntax.py")
    before = {"tools": tool_identity(java), "inputs": {str(path): digest(path) for path in inputs}}
    retained = {}
    (tmp_path / "owner-before.json").write_text(json.dumps({**before, "version": version.group(1),
        "source_level": level, "target_level": level, "production_dependency": False,
        "platform_contract": "selected current JDK platform, not --release compatibility"}, indent=2))

    def finish_identity():
        after = {"tools": tool_identity(java), "inputs": {str(path): digest(path) for path in inputs}}
        unchanged = all(digest(path) == value for path, value in retained.items())
        (tmp_path / "owner-post.json").write_text(json.dumps({**after, "unchanged": unchanged,
            "original_observer_hashes": {str(path): value for path, value in retained.items()}}, indent=2))
        assert before == after and unchanged

    request.addfinalizer(finish_identity)
    assert record_process((str(java), *JVM_LIMITS, "--version"), tmp_path, "version")[0] == 0
    rows = []
    for name, owner, text, *_ in cases:
        directory = tmp_path / name
        directory.mkdir()
        unit = directory / (owner + ".java")
        unit.write_bytes(text.encode("utf-8"))
        retained[unit] = digest(unit)
        if runtime:
            observer = directory / "PccNarrowObserve.java"
            observer.write_text(observer_source(owner, "I", ()), encoding="utf-8")
            retained[observer] = digest(observer)
        rows.append(name + ("\trun\t" if runtime else "\treject\t") + unit.name)
    (tmp_path / "cases.tsv").write_text("\n".join(rows) + "\n")
    driver = tmp_path / "NarrowReference.java"
    driver.write_bytes((root / "NarrowReference.java").read_bytes())
    retained[driver] = digest(driver)
    driver_classes = tmp_path / "driver-classes"
    driver_classes.mkdir()
    compilation = record_process((str(java), *JVM_LIMITS, "--module", "jdk.compiler/com.sun.tools.javac.Main",
        "-proc:none", "-encoding", "UTF-8", "-source", level, "-target", level,
        "-classpath", str(driver_classes), "-d", str(driver_classes), str(driver)), tmp_path, "driver-compile", 20)
    assert compilation[0] == 0, compilation
    batch = record_process((str(java), *JVM_LIMITS, "-cp", str(driver_classes), "NarrowReference",
        str(tmp_path), level), tmp_path, "batch-compile", 30)
    assert batch[0] == 0, batch
    return java


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON or JDK_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
@pytest.mark.parametrize("shard", range(8))
def test_identical_escaped_originals_match_raw_jdk(shard, tmp_path, monkeypatch, request):
    cases = CASES[shard::8]
    java = compile_reference_batch(tmp_path, request, cases, runtime=True)
    for name, owner, text, expected in cases:
        directory = tmp_path / name
        assert (directory / "compiler-status.txt").read_text() == "0", name
        reference = record_process((str(java), *JVM_LIMITS, "-cp", str(directory / "classes"),
            "PccNarrowObserve"), directory, "reference-run")
        native = directory / "native"
        native.mkdir()
        with monkeypatch.context() as denied:
            forbid_bytecode_or_external_owner(denied)
            result = compile_source(text, filename=owner + ".java")
        observe_scalar(result, native, monkeypatch)
        actual = record_process((str(native / "observed"),), native, "raw-native")
        expected_bytes = expected.to_bytes(4, "little", signed=True)
        (directory / "comparison.json").write_text(json.dumps({"case": name, "comparison": "raw_process_streams",
            "raw_source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "native": {"exit_code": actual[0], "stdout_hex": actual[1].hex(), "stderr_hex": actual[2].hex()},
            "reference": {"exit_code": reference[0], "stdout_hex": reference[1].hex(), "stderr_hex": reference[2].hex()},
            "object_sha256": digest(native / "methods.o"), "executable_sha256": digest(native / "observed"),
        }, indent=2))
        assert actual == reference == (0, expected_bytes, b""), name


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-raw-reference")
def test_identical_invalid_escaped_originals_are_rejected_by_jdk(tmp_path, request):
    compile_reference_batch(tmp_path, request, INVALID, runtime=False)
    for name, *_ in INVALID:
        assert int((tmp_path / name / "compiler-status.txt").read_text()) != 0, name
