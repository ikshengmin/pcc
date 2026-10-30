"""Machine comparisons use primitive signedness, including ordinary modules."""

import operator
import re

import pytest

from tests.python.test_explicit_machine_arithmetic_boundaries import (
    _function,
    _pipeline_ir,
    _run_native_all_gc,
)


OPERATIONS = (
    ("eq", "==", operator.eq),
    ("ne", "!=", operator.ne),
    ("lt", "<", operator.lt),
    ("le", "<=", operator.le),
    ("gt", ">", operator.gt),
    ("ge", ">=", operator.ge),
)


def _functions():
    source = "from pcc import i64, u64\n"
    for name, kind in (("signed", "i64"), ("unsigned", "u64")):
        for suffix, operation, _predicate in OPERATIONS:
            source += (
                "def " + name + "_" + suffix
                + "(a: " + kind + ", b: " + kind + ") -> bool:\n"
                + "    return a " + operation + " b\n"
            )
    source += """def raw_counter(limit: i64) -> i64:
    index: i64 = 0
    while index < limit:
        index += 1
    return index
def exact_less(a: int, b: int) -> bool:
    return a < b
"""
    return source


def _checks():
    source = "def main():\n"
    for name, values in (
        ("signed", (-9223372036854775808, -1, 0, 1, 9223372036854775807)),
        ("unsigned", (0, 1, 9223372036854775808, 18446744073709551615)),
    ):
        for left in values:
            for right in values:
                for suffix, _operation, predicate in OPERATIONS:
                    source += (
                        "    assert " + name + "_" + suffix + "("
                        + str(left) + ", " + str(right) + ") is "
                        + str(predicate(left, right)) + "\n"
                    )
    source += """    assert raw_counter(0) == 0
    assert raw_counter(1000) == 1000
    huge = 1267650600228229401496703205376
    assert exact_less(huge, huge + 1)
    assert not exact_less(huge, huge - 1)
    assert exact_less(-huge, 0)
    print("MACHINE_COMPARISONS_OK")
main()
"""
    return source


@pytest.mark.parametrize(
    "prologue",
    ("", "from pcc.unsafe import null\n", "__pcc_runtime_port__ = True\n"),
    ids=("ordinary", "unsafe", "runtime"),
)
def test_machine_comparisons_do_not_box(tmp_path, monkeypatch, prologue):
    text = _pipeline_ir(tmp_path, monkeypatch, prologue + _functions())
    for name, sign in (("signed", "s"), ("unsigned", "u")):
        for suffix, _operation, _predicate in OPERATIONS:
            body = _function(text, name + "_" + suffix)
            opcode = suffix if suffix in ("eq", "ne") else sign + suffix
            assert re.search(r"\bicmp " + opcode + r" i64\b", body), name + suffix
            assert not re.search(r"\bcall\b[^\n]*@py_int_", body), name + suffix
    counter = _function(text, "raw_counter")
    assert re.search(r"\bicmp slt i64\b", counter)
    assert not re.search(r"\bcall\b[^\n]*@py_int_", counter)


@pytest.mark.integration
def test_machine_comparison_boundaries_native_all_gc(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
):
    _run_native_all_gc(
        tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
        _functions() + _checks(), "MACHINE_COMPARISONS_OK",
    )
