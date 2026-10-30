"""Standalone lifter transforms bind their real native object-pointer ABI."""
from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.codegen.layer1_support import _default_native_module_exports
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module
from scripts.probe_fallback_categories import classify_py_cpy_symbol


pytestmark = pytest.mark.xdist_group("py-lift-helper-exports")
ROOT = Path(__file__).resolve().parents[2]
MODULE = "pcc.frontends.python.py_lift"
PROVIDERS = (
    ("freestanding_constants", "fold_module_int_constants", ("module",)),
    ("builtin_function_values", "inject_builtin_function_values", ("module",)),
    ("class_body_statements", "lower_class_body_statements", ("class_name", "body")),
    ("method_decorators", "split_general_method_decorators", ("body",)),
)


@pytest.mark.parametrize("owner,name,parameters", PROVIDERS)
def test_helper_exports_match_real_pointer_signature(owner, name, parameters):
    module_name = "pcc.frontends.python." + owner
    provider = ROOT / (module_name.replace(".", "/") + ".py")
    definition = next(
        node for node in ast.parse(provider.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    assert tuple(arg.arg for arg in definition.args.args) == parameters
    assert not definition.args.posonlyargs
    assert not definition.args.kwonlyargs
    assert not definition.args.defaults
    assert definition.args.vararg is None and definition.args.kwarg is None
    # Module is an ordinary object class, not a scalar/void/valueclass ABI.
    # Unannotated parameters and returns also use the object-pointer lane.
    annotations = [arg.annotation for arg in definition.args.args]
    annotations.append(definition.returns)
    assert all(annotation is None or ast.unparse(annotation) == "Module"
               for annotation in annotations)
    info = _default_native_module_exports(MODULE)[module_name][name]
    assert info["kind"] == "function"
    assert info["return_ty"] == ("dyn",)
    assert info["param_types"] == (("dyn",),) * len(parameters)
    assert info["call_sig"] == tuple(
        {"name": parameter, "kind": "pos", "annotation": ("dyn",),
         "default": None, "has_default": False}
        for parameter in parameters
    )
    assert info["box_int_abi"] is False
    # A provider declaration does not opt arbitrary consumers into the table.
    assert _default_native_module_exports(module_name) is None


def test_standalone_lifter_calls_four_helpers_without_cpython(tmp_path):
    source_path = ROOT / "pcc/frontends/python/py_lift.py"
    typed = infer_module(parse_and_lift(
        source_path.read_text(encoding="utf-8"), str(source_path), MODULE,
    ))
    codegen = L1CodeGen(
        typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="off",
    )
    assert codegen._strict_no_libpython is False
    text = str(codegen.generate(typed))
    (tmp_path / "py_lift.off.ll").write_text(text, encoding="utf-8")
    for owner, name, parameters in PROVIDERS:
        symbol = "user_pcc_frontends_python_" + owner + "_" + name
        calls = re.findall(r"call ptr[^\n]*@" + re.escape(symbol) + r"\(([^\n]*)\)", text)
        assert calls, symbol
        assert all(len(arguments.split(",")) == len(parameters) for arguments in calls)
        assert all(all(argument.strip().startswith("ptr ") for argument in arguments.split(","))
                   for arguments in calls)
        assert "cpy.mod.pcc.frontends.python." + owner not in text
        assert "cpy.attr." + name not in text
    actions = [
        line for line in text.splitlines()
        if (match := re.search(r"\bcall [^\n]*@(py_cpy_[a-z0-9_]+)", line))
        and classify_py_cpy_symbol(match.group(1))[0] == "action"
    ]
    # This separate, known Unicode-name-provider gap is not fixed by exports.
    # Keep it visible: removal of twelve actions is not literal zero fallback.
    assert len(actions) == 1, actions
    assert "cpy.import.unicodedata" in actions[0]
    assert "strict.nolib.stub" not in text


def test_builtin_value_injection_preserves_raw_module_boundary():
    ordinary = parse_and_lift("def f():\n    return len\n", "<ordinary>", "ordinary")
    names = [getattr(node, "name", "") for node in ordinary.body]
    assert "__pcc_builtin_value_len" in names
    for marker in ("__pcc_freestanding__", "__pcc_runtime_port__"):
        raw = parse_and_lift(marker + " = True\ndef f():\n    return len\n", "<raw>", "raw")
        assert not any(getattr(node, "name", "").startswith("__pcc_builtin_")
                       for node in raw.body)
