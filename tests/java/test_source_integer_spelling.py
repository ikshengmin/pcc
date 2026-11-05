"""Long integer spellings retain Java magnitude and radix semantics.

JLS21 3.10.1 and 15.15.4:
https://docs.oracle.com/javase/specs/jls/se21/html/jls-3.html#jls-3.10.1
https://docs.oracle.com/javase/specs/jls/se21/html/jls-15.html#jls-15.15.4
Same original bytes feed owned native and development-only JDK obligations.
All execution is authored for CI and UNRUN at this source checkpoint.
"""
import hashlib
import json
from pathlib import Path

import pytest

from pcc.frontends.java import JavaError, compile_source
from pcc.frontends.java import source_sema
from pcc.frontends.java.source_project import SourceProject
from pcc.frontends.java.source_syntax import Node, SourceParser, Token
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner
from tests.java.test_source_raw_reference import (
    JVM_LIMITS, digest, observer_source, record_process,
)
from tests.java.test_source_scalars import NATIVE_REASON
from tests.java.test_source_static_imports import prepare_reference
from tests.java.test_source_unicode_escapes import JDK_REASON


def original(expression, lane="I", *, body=None):
    result = "long" if lane == "J" else "int"
    return ("public class NumberSpelling{public static " + result + " run(){"
            + (body if body is not None else "return " + expression + ";") + "}}")


ZEROES = "0" * 512
SEPARATORS = "_" * 512
CASES = [
    ("decimal-separators", original("4" + SEPARATORS + "2"), "I", 42),
    ("long-separators", original("4" + SEPARATORS + "2L", "J"), "J", 42),
    ("hex-leading-zeroes", original("0x" + ZEROES + "2a"), "I", 42),
    ("binary-leading-zeroes", original("0B" + ZEROES + "101010"), "I", 42),
    ("octal-leading-zeroes", original(ZEROES + "52"), "I", 42),
    ("all-zero-octal", original(ZEROES), "I", 0),
    ("octal-leading-separators", original("0" + SEPARATORS + "52"), "I", 42),
    ("hex-zeroes-separators", original("0X" + "0_" * 256 + "2A"), "I", 42),
    ("binary-zeroes-separators", original("0b" + "0_" * 256 + "101010L", "J"), "J", 42),
    ("int-hex-negative-one", original("0x" + ZEROES + "ffffffff"), "I", -1),
    ("int-binary-minimum", original("0b" + ZEROES + "1" + "0" * 31), "I", -2147483648),
    ("long-hex-negative-one", original("0x" + ZEROES + "ffffffffffffffffL", "J"), "J", -1),
    ("long-octal-negative-one", original(ZEROES + "1777777777777777777777L", "J"), "J", -1),
    ("decimal-int-minimum", original("-2" + SEPARATORS + "147483648"), "I", -2147483648),
    ("decimal-long-minimum", original("-9" + SEPARATORS + "223372036854775808L", "J"), "J", -9223372036854775808),
    ("narrow-local-initializer", original("", body="byte value=4" + SEPARATORS + "2;return value;"), "I", 42),
    ("switch-case-constant", original("", body="switch(42){case 0x" + ZEROES + "2a:return 42;default:return 0;}"), "I", 42),
]

INVALID = [
    ("decimal-int-overflow", original("2" + SEPARATORS + "147483648"), "literal exceeds int range"),
    ("decimal-long-overflow", original("9" + SEPARATORS + "223372036854775808L", "J"), "literal exceeds long range"),
    ("grouped-minimum-not-direct-operand", original("-(2" + SEPARATORS + "147483648)"), "literal exceeds int range"),
    ("hex-int-overflow", original("0x" + ZEROES + "100000000"), "literal exceeds int range"),
    ("hex-long-overflow", original("0x" + ZEROES + "10000000000000000L", "J"), "literal exceeds long range"),
    ("binary-int-overflow", original("0b" + ZEROES + "1" + "0" * 32), "literal exceeds int range"),
    ("octal-invalid-digit", original(ZEROES + "8"), "invalid int literal"),
    ("separator-before-suffix", original("4" + SEPARATORS + "L", "J"), "invalid long literal"),
    ("separator-after-hex-prefix", original("0x" + SEPARATORS + "2a"), "invalid int literal"),
    ("separator-after-binary-prefix", original("0b" + SEPARATORS + "1"), "invalid int literal"),
    ("trailing-separator", original("4" + SEPARATORS), "invalid int literal"),
    ("significant-digit-budget", original("1" * 512), "literal exceeds int range"),
]


@pytest.mark.parametrize("name,text,lane,expected", CASES, ids=[row[0] for row in CASES])
def test_long_spelling_original_has_owned_ir(name, text, lane, expected, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    program = SourceParser(text, "NumberSpelling.java").parse()
    SourceProject((program,)).analyze()
    statement = program.methods[0].body.children[0]
    if statement.kind == "switch":
        value = statement.children[1].value[0].children[0]  # First case constant.
    else:
        assert statement.kind in {"return", "var"}
        value = statement.children[0]  # Returned expression or local initializer.
    assert (value.dtype, value.constant) == ("long" if lane == "J" else "int", expected)
    compiled = compile_source(text, filename="NumberSpelling.java", descriptor="()" + lane)
    assert compiled.emit_object().startswith(b"\x7fELF")


@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_spelling_original_fails_before_ir(name, text, message, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("invalid literal reached IR"))
    with pytest.raises(JavaError, match=message) as caught:
        compile_source(text, filename="NumberSpelling.java")
    assert type(caught.value) is JavaError and caught.value.code is None


def test_host_conversion_receives_only_bounded_significant_digits(monkeypatch):
    seen = []

    def bounded_int(digits, radix):
        assert len(digits) <= 64
        seen.append((digits, radix))
        return int(digits, radix)

    monkeypatch.setattr(source_sema, "int", bounded_int, raising=False)
    spelling = "0x" + "0_" * 10_000 + "2aL"
    token = Token(spelling, "NumberSpelling.java", 1, 1)
    assert source_sema.integer_literal(Node("integer", token, spelling)) == ("long", 42)
    assert seen == [("2a", 16)]
    with pytest.raises(JavaError, match="literal exceeds int range"):
        source_sema.integer_literal(Node("integer", token, "1" * 10_000))
    assert seen == [("2a", 16)]


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,text,lane,expected", CASES, ids=[row[0] for row in CASES])
def test_long_spelling_original_has_exact_native_result(name, text, lane, expected, tmp_path, monkeypatch):
    unit = tmp_path / "NumberSpelling.java"
    unit.write_bytes(text.encode("utf-8"))
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_source(text, filename=unit.name, descriptor="()" + lane)
    assert observe_scalar(compiled, tmp_path, monkeypatch) == expected
    assert unit.read_bytes() == text.encode("utf-8")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON or JDK_REASON)
@pytest.mark.xdist_group(name="java-integer-spelling-reference")
@pytest.mark.parametrize("name,text,lane,expected", CASES, ids=[row[0] for row in CASES])
def test_identical_long_spelling_original_matches_jdk_raw_processes(name, text, lane, expected, tmp_path, request, monkeypatch):
    sources = {"NumberSpelling.java": text}
    inputs = (Path(__file__), Path(__file__).parents[2] / "pcc/frontends/java/source_sema.py")
    before = {str(path): digest(path) for path in inputs}
    native = tmp_path / "native"
    native.mkdir()
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_source(text, filename="NumberSpelling.java", descriptor="()" + lane)
    assert observe_scalar(compiled, native, monkeypatch) == expected
    actual = record_process((str(native / "observed"),), native, "raw-native")
    java, classes, compiler = prepare_reference(sources, tmp_path, request,
                                                observer=observer_source("NumberSpelling", lane, ()))
    assert compiler[0] == 0, compiler
    reference = record_process((str(java), *JVM_LIMITS, "-cp", str(classes), "PccRawObserve"),
                               tmp_path, "raw-reference")
    expected_bytes = expected.to_bytes(8 if lane == "J" else 4, "little", signed=True)
    after = {str(path): digest(path) for path in inputs}
    (tmp_path / "comparison.json").write_text(json.dumps({
        "case": name, "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "source_rewritten": False, "comparison": "raw_process_streams",
        "inputs_before": before, "inputs_after": after,
        "object_sha256": digest(native / "methods.o"), "executable_sha256": digest(native / "observed"),
        "native": {"exit_code": actual[0], "stdout_hex": actual[1].hex(), "stderr_hex": actual[2].hex()},
        "reference": {"exit_code": reference[0], "stdout_hex": reference[1].hex(), "stderr_hex": reference[2].hex()},
    }, indent=2) + "\n")
    assert before == after
    assert actual == reference == (0, expected_bytes, b"")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=JDK_REASON)
@pytest.mark.xdist_group(name="java-integer-spelling-reference")
@pytest.mark.parametrize("name,text,message", INVALID, ids=[row[0] for row in INVALID])
def test_identical_invalid_spelling_original_has_jdk_rejection(name, text, message, tmp_path, request):
    _, _, compiler = prepare_reference({"NumberSpelling.java": text}, tmp_path, request)
    assert compiler[0] == 1, compiler
    assert b"NumberSpelling.java:" in compiler[2]
