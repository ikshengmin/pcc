"""Fixture-free shared ordinary-call eligibility and owned-IR contracts."""
from __future__ import annotations

from dataclasses import replace
import re

import pytest

from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module


@pytest.mark.parametrize("signature, arguments, runtime", [
    ("a, b", "1, 2", False),
    ("a, /, b", "1, 2", False),
    ("a=1", "2", False),
    ("a=1", "", True),
    ("a, b", "1", True),
    ("a", "1, 2", True),
    ("a, b", "1, b=2", True),
    ("a, b", "b=2, a=1", True),
    ("a, /, **extras", "1, a=2", True),
    ("a, *, b", "1, 2", True),
    ("a, *, b=2", "1", True),
    ("*items", "1, 2", True),
    ("**extras", "", True),
    ("a, b", "*(1, 2)", True),
    ("a, b", "1, **{'b': 2}", True),
])
def test_ordinary_binding_classifier_is_pure(signature, arguments, runtime):
    module = infer_module(parse_and_lift(
        "def target(" + signature + "):\n    pass\ntarget(" + arguments + ")\n",
        "binding.py", "binding",
    ))
    codegen = L1CodeGen(module, ir_scaffold_mode="on")
    # No builder exists. Eligibility must never evaluate a receiver, splat,
    # default, or operand before deciding which complete lowering owns it.
    codegen.builder = None
    fd, call = module.body[0], module.body[1].expr
    assert codegen._ordinary_call_needs_runtime_binding(call, fd) is runtime
    assert codegen.builder is None
    assert not codegen._ordinary_call_needs_runtime_binding(call, replace(fd, manual_pointer_abi=True))
    codegen._runtime_port_module = True
    assert not codegen._ordinary_call_needs_runtime_binding(call, fd)


def _emit(source):
    module = infer_module(parse_and_lift(source, "binding.py", "binding"))
    codegen = L1CodeGen(module, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    return str(codegen.generate(module))


@pytest.mark.parametrize("annotation,value", [("int", "7"), ("float", "1.25"), ("bool", "True")])
def test_runtime_binding_preserves_return_projection(annotation, value):
    text = _emit("def target(x: " + annotation + ") -> " + annotation + ":\n"
                 "    return x\ndef probe():\n    return target(x=" + value + ")\n")
    body = re.search(r"^define [^\n]*@user_binding_probe\([^\n]*\).*?^}", text, re.M | re.S)
    assert body is not None
    assert len(re.findall(r"\bcall [^\n]*@py_obj_call_slots\(", body.group(0))) == 1
    assert not re.search(r"\bcall [^\n]*@user_binding_target\(", body.group(0))
    assert "@pcc_gc_foreign_lease_acquire(" in body.group(0)

