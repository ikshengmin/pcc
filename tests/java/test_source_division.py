"""Nonthrowing Java division domain, with independent full-width ABI observers.

The originals are authored tests, not excerpts promoted to upstream project
qualification. Zero/dynamic divisors remain a shared-exception capability gap.
"""
import re

import pytest

from pcc.frontends.java import JavaError, UnsupportedJava, compile_source
from pcc.frontends.java.artifact import method_symbol
from tests.java.owned_execution import observe_scalar
from tests.java.test_source_frontend import forbid_bytecode_or_external_owner, source as initial_source
from tests.java.test_source_scalars import NATIVE_REASON, source as scalar_source


def original(dtype, body, helpers="", parameter=True):
    return "class Division{" + helpers + "static " + dtype + " run(" + (dtype + " x" if parameter else "") + "){" + body + "}}"


# Nonconstant dividend enters through an independently assembled ABI caller.
CASES = [
    ("int-positive", "int", "return x/3;", 2000000000, 666666666),
    ("int-negative-dividend", "int", "return x/3;", -2000000000, -666666666),
    ("int-negative-divisor", "int", "return x/-3;", 2000000000, -666666666),
    ("int-two-negatives", "int", "return x/-3;", -2000000000, 666666666),
    ("int-positive-rem", "int", "return x%3;", 2000000000, 2),
    ("int-negative-rem", "int", "return x%3;", -2000000000, -2),
    ("int-negative-divisor-rem", "int", "return x%-3;", 2000000000, 2),
    ("int-two-negatives-rem", "int", "return x%-3;", -2000000000, -2),
    ("int-min-div-minus-one", "int", "return x/-1;", -2147483648, -2147483648),
    ("int-min-rem-minus-one", "int", "return x%-1;", -2147483648, 0),
    ("int-min-div-min", "int", "return x/-2147483648;", -2147483648, 1),
    ("int-max-rem-min", "int", "return x%-2147483648;", 2147483647, 2147483647),
    ("int-div-one", "int", "return x/1;", -2147483648, -2147483648),
    ("int-small-negative", "int", "return x/7;", -6, 0),
    ("int-char-divisor", "int", "return x/'\\3';", -2000000000, -666666666),
    ("int-byte-divisor", "int", "return x/(byte)255;", -2147483648, -2147483648),
    ("int-final-divisor", "int", "final int d=6/2;return x/d;", -2000000000, -666666666),
    ("int-compound-long-promotion", "int", "x/=-1L;return x;", -2147483648, -2147483648),
    ("int-compound-long-rem", "int", "x%=4294967296L;return x;", -2147483648, -2147483648),
    ("int-compound-division", "int", "x/=-3;return x;", 2000000000, -666666666),
    ("int-compound-rem", "int", "x%=-3;return x;", -2000000000, -2),
    ("long-no-float-rounding", "long", "return x/3;", 9223372036854775807, 3074457345618258602),
    ("long-negative-dividend", "long", "return x/3L;", -9223372036854775807, -3074457345618258602),
    ("long-negative-divisor", "long", "return x/-3;", 9223372036854775807, -3074457345618258602),
    ("long-two-negatives", "long", "return x/-3L;", -9223372036854775807, 3074457345618258602),
    ("long-positive-rem", "long", "return x%3;", 9223372036854775807, 1),
    ("long-negative-rem", "long", "return x%-3L;", -9223372036854775807, -1),
    ("long-min-div-minus-one", "long", "return x/-1;", -9223372036854775808, -9223372036854775808),
    ("long-min-rem-minus-one", "long", "return x%-1;", -9223372036854775808, 0),
    ("long-min-div-min", "long", "return x/-9223372036854775808L;", -9223372036854775808, 1),
    ("long-max-rem-min", "long", "return x%-9223372036854775808L;", 9223372036854775807, 9223372036854775807),
    ("long-compound-division", "long", "x/=-3;return x;", 9223372036854775807, -3074457345618258602),
    ("long-compound-rem", "long", "x%=-1L;return x;", -9223372036854775808, 0),
]


@pytest.mark.parametrize("name,dtype,body,argument,expected", CASES, ids=[case[0] for case in CASES])
def test_nonzero_division_uses_owned_source_ir(name, dtype, body, argument, expected, monkeypatch):
    forbid_bytecode_or_external_owner(monkeypatch)
    lane = "J" if dtype == "long" else "I"
    compiled = compile_source(original(dtype, body), descriptor="(" + lane + ")" + lane)
    assert compiled.emit_object().startswith(b"\x7fELF")


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("name,dtype,body,argument,expected", CASES, ids=[case[0] for case in CASES])
def test_original_division_has_exact_native_result(name, dtype, body, argument, expected, tmp_path, monkeypatch):
    text = original(dtype, body)
    lane = "J" if dtype == "long" else "I"
    (tmp_path / "Division.java").write_text(text)
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_source(text, descriptor="(" + lane + ")" + lane)
    assert observe_scalar(compiled, tmp_path, monkeypatch, ((lane, argument),)) == expected
    assert (tmp_path / "Division.java").read_text() == text


CONSTANTS = [
    ("int", "return -7/3;", -2),
    ("int", "return 7/-3;", -2),
    ("int", "return -7%-3;", -1),
    ("int", "return -2147483648/-1;", -2147483648),
    ("int", "return -2147483648%-1;", 0),
    ("long", "return 9223372036854775807L/3;", 3074457345618258602),
    ("long", "return -9223372036854775808L/-1;", -9223372036854775808),
    ("long", "return -9223372036854775808L%-1;", 0),
    ("long", "return -2147483648/-1L;", 2147483648),
    ("long", "return -2147483648/-1;", -2147483648),
]


@pytest.mark.parametrize("dtype,body,expected", CONSTANTS)
def test_constant_division_uses_integer_truncation_and_promoted_width(dtype, body, expected):
    lane = "J" if dtype == "long" else "I"
    compiled = compile_source(original(dtype, body, parameter=False), descriptor="()" + lane)
    text = str(compiled.module)
    direct = "ret i" + ("64" if dtype == "long" else "32") + " " + str(expected)
    if direct not in text:
        # An int expression must wrap at i32 before the method result widens.
        assert dtype == "long"
        promoted = re.search(r"(%[\w.]+) = sext i32 " + str(expected) + r" to i64", text)
        assert promoted and "ret i64 " + promoted.group(1) in text
    assert "sdiv" not in text and "srem" not in text


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("dtype,body,expected", CONSTANTS)
def test_original_constant_division_has_exact_native_result(dtype, body, expected, tmp_path, monkeypatch):
    text = original(dtype, body, parameter=False)
    lane = "J" if dtype == "long" else "I"
    (tmp_path / "Division.java").write_text(text)
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_source(text, descriptor="()" + lane)
    assert observe_scalar(compiled, tmp_path, monkeypatch) == expected
    assert (tmp_path / "Division.java").read_text() == text


@pytest.mark.parametrize("body,helpers", [
    ("return x/0;", ""), ("return x%0L;", ""),
    ("return x/(1-1);", ""), ("return x/(int)4294967296L;", ""),
    ("x/=0;return x;", ""), ("x%=0;return x;", ""),
    ("int d=3;return x/d;", ""), ("final int d=f();return x/d;", "static int f(){return 3;}"),
    ("return x/x;", ""), ("return x/(x|1);", ""),
    ("if(false)return x/0;return x;", ""),
    ("return x;", "static int unused(int y){return y/0;}"),
    ("return x;", "static int unused(final int d){return 42/d;}"),
    ("return x;", "static final int ZERO=0;static int unused(){return 42/ZERO;}"),
])
def test_zero_and_dynamic_divisors_remain_explicit_before_lowering(body, helpers, monkeypatch):
    from pcc.frontends.java.source_lowering import SourceLowerer
    monkeypatch.setattr(SourceLowerer, "lower", lambda *args: pytest.fail("unsupported divisor reached IR"))
    with pytest.raises(UnsupportedJava, match="ArithmeticException"):
        compile_source(original("int", body, helpers), descriptor="(I)I")


@pytest.mark.parametrize("body", ["return x/true;", "return false%3;", "boolean b=true;b/=3;return x;", "x%=false;return x;"])
def test_nonintegral_division_is_a_type_error_not_a_runtime_boundary(body):
    with pytest.raises(JavaError) as failure:
        compile_source(original("int", body), descriptor="(I)I")
    assert type(failure.value) is JavaError


@pytest.mark.parametrize("operation", ["/", "%"])
def test_minus_one_lowering_preserves_dividend_call_once_without_poison(operation):
    text = original("int", "return f(x)" + operation + "-1;", "static int f(int x){return x;}")
    ir = str(compile_source(text, descriptor="(I)I").module)
    assert sum("call i32" in line and method_symbol("Division", "f", "(I)I") in line for line in ir.splitlines()) == 1
    assert "sdiv" not in ir and "srem" not in ir


def test_division_constant_fields_participate_in_assignment_narrowing():
    text = original("int", "return VALUE;", "static final char VALUE=126/3;", parameter=False)
    assert "ret i32 42" in str(compile_source(text).module)


# These exact source constructors/bodies were formerly Unsupported tests.
# Preserve the original programs while explicitly graduating only nonzero RHS.
GRADUATED = [
    (initial_source("int x=1;x%=2;return x;"), "I", 1),
    (scalar_source("long", "long x=1L;x%=2L;return x;", ""), "J", 1),
]


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=NATIVE_REASON)
@pytest.mark.parametrize("text,lane,expected", GRADUATED)
def test_original_nonzero_capability_cases_are_now_native(text, lane, expected, tmp_path, monkeypatch):
    (tmp_path / "original.java").write_text(text)
    with monkeypatch.context() as denied:
        forbid_bytecode_or_external_owner(denied)
        compiled = compile_source(text, descriptor="()" + lane)
    assert observe_scalar(compiled, tmp_path, monkeypatch) == expected
