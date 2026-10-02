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


def _function(text, symbol="user_binding_probe"):
    body = re.search(r"^define [^\n]*@" + symbol + r"\([^\n]*\).*?^}", text, re.M | re.S)
    assert body is not None, symbol
    return body.group(0)


def test_exact_positional_call_keeps_proven_direct_abi():
    body = _function(_emit("def target(value):\n    return value\ndef probe():\n    return target(7)\n"))
    assert len(re.findall(r"\bcall [^\n]*@user_binding_target\(", body)) == 1
    assert not re.search(r"\bcall [^\n]*@py_obj_call_slots\(", body)


@pytest.mark.parametrize("kind,formal,receiver", [
    ("staticmethod", "value", "Holder"),
    ("classmethod", "cls, value", "Holder"),
    ("", "self, value", "receiver"),
    ("", "self, value", "Holder"),
])
def test_method_descriptor_routes_keep_original_call(kind, formal, receiver):
    decorator = "    @" + kind + "\n" if kind else ""
    args = "value=7"
    if not kind and receiver == "Holder":
        args = "receiver, value=7"
    source = ("class Holder:\n" + decorator + "    def target(" + formal + "):\n"
              "        return value\nreceiver = Holder()\ndef probe():\n"
              "    return " + receiver + ".target(" + args + ")\n")
    body = _function(_emit(source))
    assert len(re.findall(r"\bcall [^\n]*@py_obj_call_slots\(", body)) == 1
    assert len(re.findall(r"\bcall [^\n]*@py_obj_getattr\(", body)) == 1
    assert not re.search(r"\bcall [^\n]*@user_binding_Holder_target\(", body)


def test_method_operand_publishes_before_outer_keyword_merge():
    body = _function(_emit("""class Holder:
    def make(self):
        return {'value': 7}
receiver = Holder()
def target(value):
    return value
def probe():
    return target(**receiver.make())
"""))
    invocations = list(re.finditer(r"\bcall [^\n]*@py_obj_call_slots\(", body))
    merges = list(re.finditer(r"\bcall [^\n]*@py_call_merge_kwargs_for_call\(", body))
    assert len(invocations) == 2
    assert len(merges) == 1
    assert invocations[0].start() < merges[0].start() < invocations[1].start()
    assert not re.search(r"\bcall [^\n]*@user_binding_Holder_make\(", body)


def test_imported_operand_publishes_once_before_keyword_merge(tmp_path):
    from pcc.frontends.python.pipeline_context import build_closed_world_context

    provider = tmp_path / "producer.py"
    entry = tmp_path / "binding.py"
    provider.write_text("def make():\n    return {'value': 7}\ndef target(value):\n    return value\n")
    entry.write_text("import producer\ndef probe():\n    return producer.target(**producer.make())\n")
    modules, exports, _ = build_closed_world_context([str(provider), str(entry)], ["producer", "binding"])
    emitted = {}
    for module in modules:
        typed = infer_module(module, external_exports={key: value for key, value in exports.items() if key != module.name})
        codegen = L1CodeGen(typed, ir_scaffold_mode="on")
        codegen._strict_no_libpython = True
        codegen._prefer_native_callable_values = True
        codegen._native_module_exports = exports
        emitted[module.name] = str(codegen.generate(typed))
    body = _function(emitted["binding"])
    invocations = list(re.finditer(r"\bcall [^\n]*@py_obj_call_slots\(", body))
    merges = list(re.finditer(r"\bcall [^\n]*@py_call_merge_kwargs_for_call\(", body))
    assert len(invocations) == 2
    assert len(merges) == 1
    assert invocations[0].start() < merges[0].start() < invocations[1].start()
    assert len(re.findall(r"\bcall [^\n]*@py_module_attr_get\(", body)) == 2
    assert not re.search(r"\bcall [^\n]*@user_producer_(?:make|target)\(", body)


def test_function_metadata_uses_defining_namespace_and_rooted_setters():
    module = infer_module(parse_and_lift("def target(value):\n    return value\n", "provider.py", "provider"))
    codegen = L1CodeGen(module, ir_scaffold_mode="on")
    codegen._skip_program_main = True
    codegen._strict_no_libpython = True
    text = str(codegen.generate(module))
    for value in ("provider", "__module__", "__qualname__"):
        encoded = 'c"' + "".join("\\" + format(byte, "02X") for byte in value.encode() + b"\0") + '"'
        assert encoded in text, value
    # Each new callable is immediately stored to its registered metadata
    # owner before any setter can allocate or invoke a callback.
    assert re.search(r"(%[^ ]+) = call [^\n]*@py_func_new_named\([^\n]*\)\n\s+store ptr \1, ptr %function.metadata.result", text)
    assert "@pcc_gc_foreign_lease_acquire(" in text


def test_shared_call_method_registry_matches_live_signatures():
    from inspect import signature
    from pcc.frontends.python.codegen.host_contract import L1_CODEGEN_HOST_METHODS
    from pcc.frontends.python.codegen._l1_codegen_static_methods import L1_CODEGEN_STATIC_METHODS
    from pcc.frontends.python.codegen.layer1_support import _default_native_module_exports

    static = {entry["name"]: entry for entry in L1_CODEGEN_STATIC_METHODS}
    exports = _default_native_module_exports("pcc.frontends.python.codegen.layer1")
    native = {entry["name"]: entry for entry in exports["pcc.frontends.python.codegen.layer1"]["L1CodeGen"]["methods"]}
    for name in ("_ordinary_call_needs_runtime_binding", "_slot_call_published_module_ref",
                 "_emit_slot_call_module_value", "_emit_runtime_bound_user_call",
                 "_native_class_method_def", "_func_c_abi_export_symbol",
                 "_finish_native_callable_metadata", "_emit_slot_call_object",
                 "_emit_slot_call_kwargs_object", "_emit_slot_call_conditional"):
        assert name in L1_CODEGEN_HOST_METHODS
        assert static[name] == native[name]
        assert tuple(item["name"] for item in static[name]["call_sig"]) == tuple(signature(getattr(L1CodeGen, name)).parameters)


CONDITIONAL_PROGRAM = """events = []
def choose(flag):
    events.append('condition')
    return flag
def left(value):
    events.append('left')
    return value
def right(value):
    events.append('right')
    return value
def target(value):
    events.append('body')
    return value
def probe(flag, first, second):
    return target(value=left(first) if choose(flag) else right(second))
def main():
    first = [1]
    second = [2]
    assert probe(True, first, second) is first
    assert events == ['condition', 'left', 'body']
    events.clear()
    assert probe(False, first, second) is second
    assert events == ['condition', 'right', 'body']
    print('CONDITIONAL_BINDING_OK')
main()
"""


def test_conditional_operand_reference(capsys):
    exec(compile(CONDITIONAL_PROGRAM, "conditional_binding.py", "exec"), {})
    assert capsys.readouterr().out == "CONDITIONAL_BINDING_OK\n"


def test_conditional_operand_moves_selected_branch_owner():
    body = _function(_emit(CONDITIONAL_PROGRAM))
    for name in ("choose", "left", "right"):
        assert len(re.findall(r"\bcall [^\n]*@user_binding_" + name + r"\(", body)) == 1
    assert body.index("@user_binding_choose(") < body.index("@user_binding_left(")
    assert len(re.findall(r"%call.slot.if.move[^\n]*= call [^\n]*@pcc_gc_root_move\(", body)) == 2
    assert re.search(r"br i1 [^\n]*label %call.slot.if.true[^\n]*label %call.slot.if.false", body)
    assert not re.search(r"phi ptr[^\n]*call.slot.if", body)


def test_conditional_keyword_original_tls_shape_owned_ir():
    text = _emit("""TLS_OK = 0
class Adapter:
    def _result(self, status: int, request: int, count: int = 0, server_name=''):
        return count
    def read(self, status: int, request: int, count: int):
        return self._result(status, request, count=count if status == TLS_OK else 0)
""")
    body = _function(text, "user_binding_Adapter_read")
    assert len(re.findall(r"\bcall [^\n]*@py_obj_call_slots\(", body)) == 1
    assert len(re.findall(r"%call.slot.if.move[^\n]*= call [^\n]*@pcc_gc_root_move\(", body)) == 2
