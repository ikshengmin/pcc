"""Host model of the owned integer kernel's negative-power zero boundary.

Only the two real implementation bodies are executed. Intrinsics and native
allocation/TLS are modeled, so passing these tests is not native qualification.
The existing dynamic-power native gate remains the required execution check.
"""
from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


class Integer:
    def __init__(self, representation, value):
        self.representation = representation
        self.value = value


def runtime_model():
    text = (ROOT / "pcc/runtime/py/py_int_ops.py").read_text()
    parsed = ast.parse(text)
    functions = []
    for node in parsed.body:
        if isinstance(node, ast.FunctionDef) and node.name in ("py_int_pow", "_int_shift_sign"):
            node.decorator_list = []
            functions.append(node)
    assert len(functions) == 2
    yes = Integer("bool", 1)
    no = Integer("bool", 0)
    events = []
    errors = []
    def load_i32(value, offset):
        if offset == 8:
            return 1 if value.representation == "bool" else 2
        assert offset == 16
        return (value.value > 0) - (value.value < 0)
    def c_pow(base, exponent):
        events.append(("pow", base, exponent))
        # Match the actual retained C ABI implementation, which publishes
        # errno/IEEE infinity rather than a Python TLS exception for 0**-n.
        if base == 0.0 and exponent < 0.0:
            return math.inf
        return math.pow(base, exponent)
    def float_new(value):
        events.append(("float", value))
        return value
    def bigint_from_any(value):
        events.append(("bigint", value.value))
        return value.value
    namespace = {
        "PYOBJECTHEADER_TYPE_TAG_OFFSET": 8, "PYINTOBJECT_SIGN_OFFSET": 16,
        "PY_TYPE_BOOL": 1,
        "is_tagged_int": lambda value: value.representation == "tagged",
        "untag_int": lambda value: value.value,
        "load_i32": load_i32,
        "ptr_eq": lambda left, right: left is right,
        "global_load_ptr": lambda name: yes if name == "py_True" else no,
        "_int_to_double": lambda value: float(value.value),
        "pow_c": c_pow, "_float_new": float_new,
        "null": lambda: None, "ptr_is_null": lambda value: value is None,
        "cstr": lambda value: value,
        "py_exc_new": lambda kind, message: (kind, message),
        "py_raise_owned": lambda error: errors.append(error),
        "py_bigint_from_any": bigint_from_any,
        "py_bigint_pow": pow,
        "free": lambda value: events.append(("free", value)),
        "_wrap_bigint": lambda value: value,
    }
    tree = ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[]))
    exec(compile(tree, "py_int_ops_source_model", "exec"), namespace)
    return namespace["py_int_pow"], yes, no, events, errors


@pytest.mark.parametrize("base_kind", ("tagged", "bool", "heap"))
@pytest.mark.parametrize("exponent_kind,exponent", (("tagged", -1), ("tagged", -2),
                                                   ("heap", -2), ("heap", -(1 << 80))))
def test_zero_negative_power_sets_exception_before_numeric_or_allocation_work(base_kind, exponent_kind, exponent):
    power, _yes, no, events, errors = runtime_model()
    base = no if base_kind == "bool" else Integer(base_kind, 0)
    result = power(base, Integer(exponent_kind, exponent))
    assert result is None
    assert errors == [(9, "0.0 cannot be raised to a negative power")]
    assert events == []


@pytest.mark.parametrize("base_kind", ("tagged", "bool", "heap"))
@pytest.mark.parametrize("exponent", (0, 1, 2))
def test_zero_nonnegative_power_keeps_ordinary_result(base_kind, exponent):
    power, _yes, no, _events, errors = runtime_model()
    base = no if base_kind == "bool" else Integer(base_kind, 0)
    assert power(base, Integer("tagged", exponent)) == 0 ** exponent
    assert errors == []


@pytest.mark.parametrize("base,exponent", ((2, -2), (-2, -3), (1, -10), (3, -2)))
def test_nonzero_negative_power_preserves_float_result(base, exponent):
    power, _yes, _no, events, errors = runtime_model()
    result = power(Integer("tagged", base), Integer("tagged", exponent))
    assert type(result) is float
    assert result == base ** exponent
    assert errors == []
    assert events == [("pow", float(base), float(exponent)), ("float", result)]


def test_bool_one_negative_power_is_still_one():
    power, yes, _no, events, errors = runtime_model()
    assert power(yes, Integer("tagged", -2)) == 1.0
    assert errors == []
    assert events == [("pow", 1.0, -2.0), ("float", 1.0)]


def test_heap_zero_is_an_explicit_runtime_representation():
    # The source-matched runtime exposes py_int_new_heap(v), which allocates
    # from v and registers the heap pointer without tagged canonicalization.
    # This assertion is intentionally about the modeled representation only;
    # native heap-zero execution still requires a later runtime-backed check.
    power, _yes, _no, events, errors = runtime_model()
    assert power(Integer("heap", 0), Integer("tagged", -2)) is None
    assert errors and errors[0][0] == 9
    assert events == []
