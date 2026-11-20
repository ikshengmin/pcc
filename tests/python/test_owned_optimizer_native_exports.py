"""Real owned optimizer signatures must survive standalone native binding."""
from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.codegen.layer1_support import _default_native_module_exports
from pcc.frontends.python.pipeline_context import build_closed_world_context
from pcc.frontends.python.pipeline_dependency_closure import _order_module_inits
from pcc.frontends.python.pipeline_exports import (
    _export_call_sig,
    _export_default_to_wire,
    _export_param_types,
    _export_return_type,
    _export_return_ty_or_none,
)
from pcc.frontends.python.py_ast import BoolLit, FuncDef, Name, NoneLit
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module
from scripts.probe_fallback_categories import classify_py_cpy_symbol
from pcc.ir.optimization.ir_mutator import MutableModule


pytestmark = pytest.mark.xdist_group("owned-optimizer-native-exports")
ROOT = Path(__file__).resolve().parents[2]
CONSUMER = "pcc.frontends.python.compiled_owned_passes"
INLINE_OWNER = "pcc.ir.optimization.inline"
INLINE_SYMBOL = "user_pcc_ir_optimization_inline_inline_module"
PROVIDERS = (
    ("pcc.ir.optimization.mem2reg", "mem2reg_text", "ptr", ("ptr",)),
    ("pcc.ir.optimization.instsimplify", "simplify_module_text", "ptr", ("ptr",)),
    ("pcc.ir.optimization.simplifycfg", "simplify_cfg_text", "ptr", ("ptr",)),
    ("pcc.ir.optimization.foldselect", "fold_two_entry_phi_text", "ptr", ("ptr",)),
    ("pcc.ir.optimization.instcombine", "instcombine_text", "ptr", ("ptr",)),
    ("pcc.ir.optimization.dce", "dce_module_text", "ptr", ("ptr", "ptr")),
    (INLINE_OWNER, "inline_module", "ptr", ("ptr", "i1", "i1", "ptr")),
    ("pcc.frontends.python.compiled_default_passes", "_has_py_cpy_call", "i1", ("ptr",)),
    ("pcc.frontends.python.compiled_default_passes", "_mem2reg_function", "ptr", ("ptr",)),
    ("pcc.frontends.python.compiled_default_passes", "_sroa_function", "ptr", ("ptr",)),
    ("pcc.frontends.python.compiled_default_passes", "run_compiled_default_tier", "ptr", ("ptr", "ptr")),
)


@pytest.fixture(scope="module")
def lifted_providers():
    modules = {}
    for owner, _name, _ret, _args in PROVIDERS:
        if owner not in modules:
            path = ROOT / (owner.replace(".", "/") + ".py")
            modules[owner] = parse_and_lift(path.read_text(encoding="utf-8"), str(path), owner)
    return modules


def _normalized_signature(signature):
    return tuple({**arg, "default": _export_default_to_wire(arg["default"])}
                 for arg in signature)


@pytest.mark.parametrize("owner,name,return_abi,param_abi", PROVIDERS)
def test_native_exports_match_actual_source(lifted_providers, owner, name, return_abi, param_abi):
    definitions = [node for node in lifted_providers[owner].body
                   if isinstance(node, FuncDef) and node.name == name]
    assert len(definitions) == 1
    definition = definitions[0]
    assert not definition.decorators and not definition.is_async
    info = _default_native_module_exports(CONSUMER)[owner][name]
    assert info["return_ty"] == _export_return_type(_export_return_ty_or_none(definition))
    assert info["param_types"] == tuple(_export_param_types(definition.args))
    assert _normalized_signature(info["call_sig"]) == _normalized_signature(
        _export_call_sig(definition.args, owner)
    )
    assert info["owning_module"] == owner and info["export_name"] == name
    assert info["kind"] == "function"
    source = ast.parse((ROOT / (owner.replace(".", "/") + ".py")).read_text())
    source_def = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == name)
    assert info["docstring"] == ast.get_docstring(source_def, clean=False)
    for flag in ("needs_object", "single_definition", "has_return_annotation"):
        assert info[flag] is True
    assert info["box_int_abi"] is (owner == INLINE_OWNER and name == "inline_module")
    for flag in ("manual_pointer_abi", "is_async", "returns_none", "may_park"):
        assert info[flag] is False
    assert info["manual_pointer_abi_symbol"] == ""
    # Declaring a real provider does not admit it as a standalone consumer.
    assert _default_native_module_exports(owner) is None


def test_defaults_are_literal_nodes_and_required_keyword_stays_required():
    exports = _default_native_module_exports(CONSUMER)
    dce = exports["pcc.ir.optimization.dce"]["dce_module_text"]["call_sig"]
    assert isinstance(dce[1]["default"], NoneLit) and dce[1]["has_default"]
    inline = exports[INLINE_OWNER]["inline_module"]["call_sig"]
    assert inline[1]["name"] == "" and inline[1]["kind"] == "kw_only"
    assert [arg["name"] for arg in inline[2:]] == [
        "require_alwaysinline", "include_definitions", "definition_limit",
    ]
    for arg in inline[2:4]:
        assert arg["kind"] == "kw_only" and arg["has_default"]
        assert isinstance(arg["default"], BoolLit) and arg["default"].value is False
    tier = exports["pcc.frontends.python.compiled_default_passes"]["run_compiled_default_tier"]["call_sig"]
    # The tier's strict flag was its only required keyword; the strict rule is
    # unconditional now and the flag is gone, so the property is checked on a
    # parsed signature instead.
    assert [arg["kind"] for arg in tier] == ["pos", "pos"]
    assert not any(arg["has_default"] for arg in tier)
    required = parse_and_lift(
        "def f(a, *, flag: bool):\n    return a\n", "<required-keyword>", "required_keyword"
    )
    signature = _export_call_sig(required.body[0].args, "required_keyword")
    assert signature[-1]["name"] == "flag" and signature[-1]["kind"] == "kw_only"
    assert not signature[-1]["has_default"] and signature[-1]["default"] is None
    assert _default_native_module_exports("ordinary_user_module") is None


def test_inline_definition_limit_resolves_in_its_provider():
    from pcc.ir.optimization.inline import DEFINITION_LIMIT, inline_module

    path = ROOT / "pcc/ir/optimization/inline.py"
    _, exports, _ = build_closed_world_context(
        [str(path)], [INLINE_OWNER], allow_local_int_abi_proofs=False,
    )
    source = exports[INLINE_OWNER]
    static = _default_native_module_exports(CONSUMER)[INLINE_OWNER]
    signature = static["inline_module"]["call_sig"]
    limit = signature[-1]
    assert limit["name"] == "definition_limit" and limit["kind"] == "kw_only"
    assert limit["has_default"] and limit["annotation"] == ("int", 64, True)
    assert isinstance(limit["default"], Name)
    assert limit["default"].ident == "DEFINITION_LIMIT"
    assert limit["default_native_global"] == {
        "owning_module": INLINE_OWNER, "name": "DEFINITION_LIMIT",
    }
    expected = source["inline_module"]
    assert _normalized_signature(signature) == _normalized_signature(expected["call_sig"])
    assert static["inline_module"]["box_int_abi"] is expected["box_int_abi"] is True
    for key in ("kind", "value_kind", "value"):
        assert static["DEFINITION_LIMIT"][key] == source["DEFINITION_LIMIT"][key]
    assert static["DEFINITION_LIMIT"]["value"] == inline_module.__kwdefaults__["definition_limit"]
    assert static["DEFINITION_LIMIT"]["value"] == DEFINITION_LIMIT == 32


def _assert_inline_definition_limit_calls(text, *, bound_path):
    # Omitting the Name default must leave it to the provider function
    # object's binder. The explicit keyword takes the four-operand ABI.
    assert f"declare external ptr @{INLINE_SYMBOL}(ptr, i1, i1, ptr)" in text
    omitted = re.findall(
        rf"^\s*%inline_module\.{re.escape(bound_path)}\.invoke\.[^ ]+ = "
        r"call i64[^\n]*@py_obj_call_slots\(",
        text, re.M,
    )
    assert len(omitted) == 2
    calls = re.findall(
        rf"\bcall ptr[^\n]*@{INLINE_SYMBOL}\("
        r"ptr [^,\n]+, i1 0, i1 ([^,\n]+), ptr (%[^)\s]+)\)",
        text,
    )
    assert len(calls) == 1
    return calls[0]


def test_standalone_owned_dispatch_binds_native_providers(tmp_path):
    path = ROOT / "pcc/frontends/python/compiled_owned_passes.py"
    typed = infer_module(parse_and_lift(path.read_text(encoding="utf-8"), str(path), CONSUMER))
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="off")
    assert codegen._strict_no_libpython is False
    text = str(codegen.generate(typed))
    (tmp_path / "compiled_owned_passes.off.ll").write_text(text, encoding="utf-8")
    for owner, name, ret, args in PROVIDERS:
        symbol = "user_" + owner.replace(".", "_") + "_" + name
        calls = re.findall(r"call " + ret + r"[^\n]*@" + re.escape(symbol) + r"\(([^\n]*)\)", text)
        assert calls, symbol
        assert all(tuple(arg.strip().split(" ", 1)[0] for arg in call.split(",")) == args
                   for call in calls), (symbol, calls)
        assert "cpy.mod." + owner not in text
        assert "cpy.attr." + name not in text
    include, limit = _assert_inline_definition_limit_calls(text, bound_path="bound")
    assert include == "1"
    boxed = re.search(
        rf"{re.escape(limit)} = call ptr \(i64\) @py_int_from_i64\(i64 (%[^) ]+)\)", text,
    )
    assert boxed
    wide_limit = "@.modvar.pcc_frontends_python_compiled_owned_passes.WIDE_DEFINITION_LIMIT"
    assert f"{boxed[1]} = load i64, ptr {wide_limit}" in text
    assert f"store i64 96, ptr {wide_limit}" in text
    cpy_calls = [line for line in text.splitlines()
                 if re.search(r"\bcall [^\n]*@py_cpy_", line)]
    actions = [line for line in cpy_calls
               if classify_py_cpy_symbol(re.search(r"@(py_cpy_[a-z0-9_]+)", line)[1])[0] == "action"]
    # The prior exact standalone receipt had 35 actions; no ratchet change.
    assert actions == []
    assert cpy_calls == []
    assert "strict.nolib.stub" not in text
    decoded = re.sub(r"\\([0-9A-Fa-f]{2})", lambda match: chr(int(match[1], 16)), text)
    assert "no-libpython function unavailable" not in decoded
    assert "unsupported import" not in decoded
    # Defaulted pointer and bool operands must match the actual provider ABI.
    none_arg = re.search(r"@user_pcc_ir_optimization_dce_dce_module_text\(ptr [^,]+, ptr (%[^ )]+)\)", text)
    assert none_arg
    assert none_arg[1] + " = load ptr, ptr @py_None" in text


def _assert_complete_binding_body(text):
    module = MutableModule.parse(text)
    function = module.function("user_pcc_frontends_python_compiled_owned_passes_bind")
    assert function is not None, "missing binding function body"
    assert function.blocks, "empty binding function body"
    assert all(block.terminator is not None and block.terminator.is_terminator()
               for block in function.blocks), "unterminated binding block"
    assert any(instruction.opcode == "ret" for block in function.blocks
               for instruction in block.instructions), "missing binding return"
    return function


def test_strict_binding_preserves_function_object_guards(tmp_path):
    # A focused consumer fixture uses the real table, not fabricated providers.
    # This emits binding code only; provider execution is a separate gate.
    source = '''from pcc.ir.optimization.inline import inline_module
from pcc.ir.optimization.dce import dce_module_text
DEFINITION_LIMIT = 1
def bind(text: str):
    first = inline_module(text)
    second = inline_module(text, include_definitions=True)
    wide = inline_module(text, include_definitions=True, definition_limit=96)
    third = dce_module_text(text)
    return first, second, wide, third
'''
    path = tmp_path / "owned_binding_contract.py"
    path.write_text(source, encoding="utf-8")
    provider_names = [node.module for node in ast.parse(source).body
                      if isinstance(node, ast.ImportFrom)]
    provider_paths = [ROOT / (name.replace(".", "/") + ".py") for name in provider_names]
    assert all(provider.is_file() for provider in provider_paths)
    sibling_inits = _order_module_inits(
        [str(provider) for provider in provider_paths] + [str(path)],
        provider_names + [CONSUMER], CONSUMER,
    )
    assert set(sibling_inits) == set(provider_names)
    typed = infer_module(parse_and_lift(source, str(path), CONSUMER))
    # L1CodeGen itself defaults to off; the production pipeline defaults on.
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    # Static declarations alone do not establish an executing sibling import.
    codegen._sibling_module_inits = sibling_inits
    text = str(codegen.generate(typed))
    (tmp_path / "strict_binding.ll").write_text(text, encoding="utf-8")
    assert not re.search(r"\bcall [^\n]*@py_cpy_", text)
    assert "strict.nolib.stub" not in text
    decoded = re.sub(r"\\([0-9A-Fa-f]{2})", lambda match: chr(int(match[1], 16)), text)
    assert "no-libpython function unavailable" not in decoded
    assert "unsupported import" not in decoded
    for owner, name in (("inline", "inline_module"), ("dce", "dce_module_text")):
        assert "@user_pcc_ir_optimization_" + owner + "_" + name + "_native_adapter" in text
        assert name + ".import.direct." in text
        assert name + ".import.dynamic." in text

    assert re.search(r"call i64[^\n]*@py_func_slot_has_native_entry\(", text)
    assert re.search(r"call i64[^\n]*@py_obj_call_slots\(", text)
    _, limit = _assert_inline_definition_limit_calls(text, bound_path="obj.call")
    load = re.search(rf"{re.escape(limit)} = load ptr, ptr (%\S+)$", text, re.M)
    assert load
    store = re.search(rf"store ptr (%[^, ]+), ptr {re.escape(load[1])}$", text, re.M)
    assert store
    assert f"{store[1]} = inttoptr i64 193 to ptr" in text  # tagged 96
    _assert_complete_binding_body(text)
