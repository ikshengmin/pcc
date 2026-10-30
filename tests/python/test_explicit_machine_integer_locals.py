"""Explicit machine locals retain raw arithmetic through assignment planning."""

import re

import pytest

from tests.python.test_explicit_machine_arithmetic_boundaries import (
    MACHINE_FUNCTIONS,
    NATIVE_CHECKS,
    ORDINARY_FUNCTIONS,
    _function,
    _pipeline_ir,
    _run_native_all_gc,
)


def _augmented_functions():
    source = MACHINE_FUNCTIONS
    for operation in ("+", "-", "*", "//", "%", "<<", ">>"):
        expression = "    return a " + operation + " b\n"
        assert source.count(expression) == 2
        source = source.replace(
            expression,
            "    a " + operation + "= b\n    return a\n",
        )
    return source


LOCAL_FUNCTIONS = """def local_counter(limit: i64) -> i64:
    index: i64 = 0
    while index < limit:
        index += 1
    return index
def local_signed(maximum: i64, one: i64) -> i64:
    value: i64 = maximum
    value += one
    return value
def local_unsigned(zero: u64, one: u64) -> u64:
    value: u64 = zero
    value -= one
    return value
def local_tuple(maximum: i64, one: i64) -> i64:
    left: i64 = 0
    right: i64 = 0
    left, right = maximum, one
    left += right
    return left
"""


LOCAL_CHECKS = """def local_checks():
    assert local_counter(0) == 0
    assert local_counter(1000) == 1000
    assert local_signed(9223372036854775807, 1) == -9223372036854775808
    assert local_unsigned(0, 1) == 18446744073709551615
    assert local_tuple(9223372036854775807, 1) == -9223372036854775808
local_checks()
"""


@pytest.mark.parametrize(
    "prologue",
    ("", "from pcc.unsafe import null\n", "__pcc_runtime_port__ = True\n"),
    ids=("ordinary", "unsafe", "runtime"),
)
def test_machine_integer_assignments_keep_raw_lanes(tmp_path, monkeypatch, prologue):
    text = _pipeline_ir(
        tmp_path, monkeypatch, prologue + _augmented_functions() + LOCAL_FUNCTIONS,
    )
    names = ["local_counter", "local_signed", "local_unsigned", "local_tuple"]
    for prefix in ("signed", "unsigned"):
        names.extend(
            prefix + "_" + suffix
            for suffix in ("add", "sub", "mul", "div", "mod", "left", "right")
        )
    for name in names:
        body = _function(text, name)
        assert re.match(r"define (?:external |internal )?i64 @", body)
        assert not re.search(
            r"\bcall\b[^\n]*@py_int_(?:add|sub|mul|floordiv|mod|shl|shr)\(", body,
        ), name
        assert not re.search(r"%(?:a|index|value|left|right)\.addr[^\n]*alloca ptr", body), name


@pytest.mark.integration
def test_machine_integer_augassign_and_locals_native_all_gc(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
):
    _run_native_all_gc(
        tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
        _augmented_functions() + LOCAL_FUNCTIONS + ORDINARY_FUNCTIONS
        + LOCAL_CHECKS + NATIVE_CHECKS,
        "MACHINE_ARITHMETIC_BOUNDARIES_OK",
    )
