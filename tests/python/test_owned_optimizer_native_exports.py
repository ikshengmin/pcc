"""Real owned optimizer signatures must survive standalone native binding."""
from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.codegen.layer1_support import _default_native_module_exports
from pcc.frontends.python.pipeline_dependency_closure import _order_module_inits
from pcc.frontends.python.pipeline_exports import (
    _export_call_sig,
    _export_default_to_wire,
    _export_param_types,
    _export_return_type,
    _export_return_ty_or_none,
)
from pcc.frontends.python.py_ast import BoolLit, FuncDef, NoneLit
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module
from scripts.probe_fallback_categories import classify_py_cpy_symbol
from pcc.ir.optimization.ir_mutator import MutableModule


pytestmark = pytest.mark.xdist_group("owned-optimizer-native-exports")
ROOT = Path(__file__).resolve().parents[2]
CONSUMER = "pcc.frontends.python.compiled_owned_passes"
PROVIDERS = (
    ("pcc.ir.optimization.mem2reg", "mem2reg_text", "ptr", ("ptr",)),
    ("pcc.ir.optimization.instsimplify", "simplify_module_text", "ptr", ("ptr",)),
    ("pcc.ir.optimization.simplifycfg", "simplify_cfg_text", "ptr", ("ptr",)),
    ("pcc.ir.optimization.foldselect", "fold_two_entry_phi_text", "ptr", ("ptr",)),
    ("pcc.ir.optimization.instcombine", "instcombine_text", "ptr", ("ptr",)),
    ("pcc.ir.optimization.dce", "dce_module_text", "ptr", ("ptr", "ptr")),
    ("pcc.ir.optimization.inline", "inline_module", "ptr", ("ptr", "i1", "i1")),
    ("pcc.frontends.python.compiled_default_passes", "_has_py_cpy_call", "i1", ("ptr",)),
    ("pcc.frontends.python.compiled_default_passes", "_mem2reg_function", "ptr", ("ptr",)),
    ("pcc.frontends.python.compiled_default_passes", "_sroa_function", "ptr", ("ptr",)),
    ("pcc.frontends.python.compiled_default_passes", "run_compiled_default_tier", "ptr", ("ptr", "ptr", "i1")),
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
    for flag in ("box_int_abi", "manual_pointer_abi", "is_async", "returns_none", "may_park"):
        assert info[flag] is False
    assert info["manual_pointer_abi_symbol"] == ""
    # Declaring a real provider does not admit it as a standalone consumer.
    assert _default_native_module_exports(owner) is None


def test_defaults_are_literal_nodes_and_required_keyword_stays_required():
    exports = _default_native_module_exports(CONSUMER)
    dce = exports["pcc.ir.optimization.dce"]["dce_module_text"]["call_sig"]
    assert isinstance(dce[1]["default"], NoneLit) and dce[1]["has_default"]
    inline = exports["pcc.ir.optimization.inline"]["inline_module"]["call_sig"]
    assert inline[1]["name"] == "" and inline[1]["kind"] == "kw_only"
    for arg in inline[2:]:
        assert arg["kind"] == "kw_only" and arg["has_default"]
        assert isinstance(arg["default"], BoolLit) and arg["default"].value is False
    tier = exports["pcc.frontends.python.compiled_default_passes"]["run_compiled_default_tier"]["call_sig"]
    assert tier[-1]["kind"] == "kw_only" and not tier[-1]["has_default"]
    assert tier[-1]["default"] is None
    assert _default_native_module_exports("ordinary_user_module") is None


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
    assert re.search(r"@user_pcc_ir_optimization_inline_inline_module\(ptr [^,]+, i1 0, i1 0\)", text)
    assert re.search(r"@user_pcc_ir_optimization_inline_inline_module\(ptr [^,]+, i1 0, i1 1\)", text)


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
def bind(text: str):
    first = inline_module(text)
    second = inline_module(text, include_definitions=True)
    third = dce_module_text(text)
    return first, second, third
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
    _assert_complete_binding_body(text)
