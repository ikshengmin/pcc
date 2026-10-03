"""Keep dynamic division's runtime target and publish before caller cleanup."""
from __future__ import annotations

import re

import pytest

from pcc.frontends.python import (
    type_infer,
)
from pcc.frontends.python.py_ast import (
    BinOp,
    ComplexType,
    DynType,
    FloatType,
    IntType,
    Name,
)
from pcc.frontends.python.py_lift import (
    parse_and_lift,
)
from tests.python.test_shared_call_binding import (
    _emit as emit_binding,
)
from tests.python.test_slot_call_operand_roots import (
    SlotProbeCodegen,
    _calls,
    _emit as emit_operand,
)


def assert_division_publication(text, count=1):
    calls = list(re.finditer(r"^  (%[^ ]+) = call [^\n]*@py_obj_truediv\([^\n]*\)\n", text, re.M))
    assert len(calls) == count
    for call in calls:
        following = text[call.end():].splitlines()[0]
        assert following.lstrip().startswith("store ptr " + call.group(1) + ","), following
    assert _calls(text, "pcc_gc_foreign_lease_acquire")
    assert _calls(text, "pcc_gc_foreign_lease_release")
    assert not _calls(text, "py_int_to_i64_lane")


@pytest.mark.parametrize("expression,count", [
    ("left / right", 1),
    ("left / 2", 1),
    ("2 / right", 1),
    ("(left / right) / right", 2),
    ("left / (right / left)", 2),
    ("left / len(right)", 1),
    ("left[0] / right", 1),
])
def test_dynamic_division_publishes_before_error_poll_and_operand_cleanup(expression, count):
    text = emit_operand("def probe(left, right):\n    return slot_operand_probe(" + expression + ")\n")
    assert_division_publication(text, count)


@pytest.mark.parametrize("site", ("argument", "default", "return", "later-error"))
def test_dynamic_division_rooted_contexts(site):
    source = "def take(*, value, later=None):\n    return value\ndef fail():\n    raise ValueError('later')\n"
    if site == "default":
        body = "    def target(value=left / right):\n        return value\n    return target()\n"
    elif site == "return":
        body = "    return left / right\n"
    elif site == "later-error":
        body = "    return take(value=left / right, later=fail())\n"
    else:
        body = "    return take(value=left / right)\n"
    assert_division_publication(emit_binding(source + "def probe(left, right):\n" + body))


@pytest.mark.parametrize("body", ("int(a / b) if b != 0 else None", "a / b"))
def test_lambda_division_uses_the_same_runtime_operator(body):
    text = emit_binding("operations = {'/': lambda a, b: " + body + "}\n")
    assert_division_publication(text)


@pytest.mark.parametrize("numeric", [
    IntType(name="pcc.i64"),
    IntType(name="pcc.u64", signed=False),
    IntType(name="int", width=32),
    FloatType(name="float", width=32),
    FloatType(name="pcc.f64"),
])
def test_dynamic_division_does_not_capture_an_explicit_machine_operand(numeric):
    module = type_infer.infer_module(parse_and_lift("", "division_projection.py", "division_projection"))
    codegen = SlotProbeCodegen(module, ir_scaffold_mode="on")
    left = Name(span=None, ty=numeric, ident="left")
    right = Name(span=None, ty=DynType(name="dyn"), ident="right")
    expr = BinOp(span=None, ty=DynType(name="dyn"), op="/", lhs=left, rhs=right)
    assert codegen._slot_call_binary_runtime(expr, object_boundary=True) is None


@pytest.mark.parametrize("numeric", (IntType(name="int"), FloatType(name="float"), ComplexType(name="complex")))
def test_fully_typed_division_retains_its_separate_route(numeric):
    module = type_infer.infer_module(parse_and_lift("", "typed_division.py", "typed_division"))
    codegen = SlotProbeCodegen(module, ir_scaffold_mode="on")
    left = Name(span=None, ty=numeric, ident="left")
    right = Name(span=None, ty=numeric, ident="right")
    expr = BinOp(span=None, ty=numeric, op="/", lhs=left, rhs=right)
    assert codegen._slot_call_binary_runtime(expr, object_boundary=True) is None
