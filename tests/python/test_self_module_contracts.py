"""Declarative contracts for self-host-sensitive codegen modules."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from pcc.frontends.python.codegen import layer1_support
from pcc.frontends.python.codegen.self_module_contracts import (
    CLASS_LOWERING_RECEIVER_CONTRACT,
    IR_PROVIDER_BINDING_CONTRACT,
    IR_SCAFFOLD_CONTRACT,
    L1_CODEGEN_HOST_ATTR_CONTRACT,
    PY_AST_FIELD_CONSTRUCTOR_CONTRACT,
    PY_AST_FIELD_ORDER_CONTRACT,
    SELF_MODULE_CONTRACTS,
    module_for_class_symbol_contract,
    module_has_contract,
)


ROOT = Path(__file__).resolve().parents[2]


def test_self_module_capabilities_are_declared_as_registry_data():
    assert module_has_contract(
        "pcc.frontends.python.codegen.runtime_abi",
        IR_SCAFFOLD_CONTRACT,
    )
    assert module_has_contract(
        "pcc.frontends.python.codegen.layer1",
        L1_CODEGEN_HOST_ATTR_CONTRACT,
    )
    assert module_has_contract(
        "pcc.frontends.python.py_ast",
        PY_AST_FIELD_ORDER_CONTRACT,
    )
    assert module_has_contract(
        "pcc.frontends.python.py_ast", PY_AST_FIELD_CONSTRUCTOR_CONTRACT,
    )
    assert not module_has_contract(
        "third_party.module", PY_AST_FIELD_CONSTRUCTOR_CONTRACT,
    )
    assert not module_has_contract("third_party.module", IR_SCAFFOLD_CONTRACT)
    assert module_has_contract("pcc.ir.ir", IR_PROVIDER_BINDING_CONTRACT)
    assert not module_has_contract("pcc.ir.ir", IR_SCAFFOLD_CONTRACT)
    assert module_has_contract(
        "pcc.frontends.python.codegen.class_gen",
        CLASS_LOWERING_RECEIVER_CONTRACT,
    )
    assert not module_has_contract(
        "pcc.frontends.python.codegen.runtime_abi",
        CLASS_LOWERING_RECEIVER_CONTRACT,
    )


def test_extern_class_symbol_resolves_through_the_same_contract_registry():
    assert module_for_class_symbol_contract(
        ".class.pcc_frontends_python_py_ast.IntType",
        PY_AST_FIELD_ORDER_CONTRACT,
    ) == "pcc.frontends.python.py_ast"
    assert module_for_class_symbol_contract(
        ".class.unrelated_IntType",
        PY_AST_FIELD_ORDER_CONTRACT,
    ) is None


def test_codegen_sites_request_capabilities_instead_of_naming_owners():
    class_gen = (ROOT / "pcc/frontends/python/codegen/class_gen.py").read_text()
    scaffold = (
        ROOT / "pcc/frontends/python/codegen/ir_scaffold_lowering.py"
    ).read_text()
    assert "module_has_contract(" in class_gen
    assert "module_for_class_symbol_contract(" in class_gen
    assert '== "pcc.frontends.python.py_ast"' not in class_gen
    assert "module_has_contract(" in scaffold
    assert " in IR_SCAFFOLD_FORCED_MODULES" not in scaffold
    native_modules = (
        ROOT / "pcc/frontends/python/codegen/native_modules.py"
    ).read_text()
    assert 'module_name == "pcc.frontends.python.py_ast"' not in native_modules
    assert "module_has_contract(" in native_modules

    codegen_root = ROOT / "pcc/frontends/python/codegen"
    direct_source_guards = []
    for path in codegen_root.glob("*.py"):
        if 'module.name == "pcc.' in path.read_text():
            direct_source_guards.append(path.name)
    assert direct_source_guards == []


def test_default_native_exports_use_the_single_module_registry():
    for module_name in layer1_support._PCC_FRONTEND_STATIC_NATIVE_MODULES:
        assert layer1_support._default_native_module_exports(module_name) is (
            layer1_support._PCC_FRONTEND_STATIC_NATIVE_EXPORTS
        )
    assert layer1_support._default_native_module_exports("unknown.module") is None
    source = (ROOT / "pcc/frontends/python/codegen/layer1_support.py").read_text()
    function_source = source.split("def _default_native_module_exports", 1)[1]
    function_source = function_source.split("\ndef ", 1)[0]
    assert 'module_name == "pcc.' not in function_source


def _scaffold_contract_codegen(module_name, source="pass\n"):
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.type_infer import infer_module

    module = infer_module(parse_and_lift(source, "<contract>", module_name))
    return L1CodeGen(module, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")


@pytest.mark.parametrize("module_name", ["pcc.ir.ir", "relocated.ir_provider"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("source,imported,unstable", [
    ("pass\n", False, False),
    ("from pcc.ir.compat import ir\n", True, False),
    ("from pcc.ir import ir\n", True, False),
    ("import pcc.ir.ir as ir\n", True, False),
    ("ir = object()\n", False, True),
    ("from pcc.ir.compat import ir\nir = object()\n", True, True),
])
def test_provider_binding_capability_preserves_import_and_rebinding_guards(
    monkeypatch, module_name, enabled, source, imported, unstable,
):
    monkeypatch.setitem(
        SELF_MODULE_CONTRACTS, module_name,
        (IR_PROVIDER_BINDING_CONTRACT,) if enabled else (),
    )
    codegen = _scaffold_contract_codegen(module_name, source)
    expected = (enabled or imported) and not unstable
    assert codegen._scaffold_source_has_provider_binding() is expected
    assert codegen._scaffold_source_has_provider_binding() is expected


@pytest.mark.parametrize("module_name", [
    "pcc.frontends.python.codegen.class_gen", "relocated.class_lowering",
])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("owner,function,name,arguments,expected", [
    ("ClassLowering", "_emit_method_body", "fn", ("fn",), "Function"),
    ("ClassLowering", "__nested_bind_method_arg", "parent", ("parent",), "L1CodeGen"),
    ("ClassLowering", "emit_instantiate", "parent", ("parent",), "L1CodeGen"),
    (None, "_classgen_builder_call", "builder", ("builder",), "IRBuilder"),
    (None, "_classgen_call", "parent", ("parent",), "L1CodeGen"),
    ("ClassLowering", "_emit_method_body", "fn", (), ""),
    ("Unrelated", "_emit_method_body", "fn", ("fn",), ""),
    (None, "ordinary", "builder", ("builder",), ""),
])
def test_class_lowering_receiver_facts_follow_capability_and_call_shape(
    monkeypatch, module_name, enabled, owner, function, name, arguments, expected,
):
    monkeypatch.setitem(
        SELF_MODULE_CONTRACTS, module_name,
        (CLASS_LOWERING_RECEIVER_CONTRACT,) if enabled else (),
    )
    codegen = _scaffold_contract_codegen(module_name)
    codegen.current_func_def = SimpleNamespace(
        name=function, args=tuple(SimpleNamespace(name=arg) for arg in arguments),
    )
    codegen.current_class = (
        SimpleNamespace(name=owner, owning_module=module_name, export_class_name=None)
        if owner else None
    )
    assert codegen._scaffold_current_class_owner() == (
        "ClassLowering" if enabled and owner == "ClassLowering" else ""
    )
    assert codegen._scaffold_local_kind(name) == (expected if enabled else "")


@pytest.mark.parametrize("module_name", [
    "pcc.frontends.python.py_ast", "relocated.ast_records",
])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("argument_count", [1, 2])
@pytest.mark.xdist_group(name="full_field_constructor")
def test_static_field_constructor_uses_capability_and_complete_argument_guard(
    monkeypatch, module_name, enabled, argument_count,
):
    """The static call seam is distinct from a live Python import binding."""
    monkeypatch.setitem(
        SELF_MODULE_CONTRACTS, module_name,
        (PY_AST_FIELD_CONSTRUCTOR_CONTRACT,) if enabled else (),
    )
    arguments = ", ".join(str(value) for value in range(argument_count))
    codegen = _scaffold_contract_codegen(
        "constructor_consumer", "provider.Record(" + arguments + ")\n",
    )
    expression = codegen.ast_module.body[0].expr
    record = SimpleNamespace(
        name="Record", field_names=("first", "second"),
        methods={}, valueclass=False,
    )
    monkeypatch.setattr(
        codegen, "_native_module_expr_export_info",
        lambda *_: (module_name, {"kind": "class"}),
    )
    monkeypatch.setattr(
        codegen, "_ensure_native_module_alias_class_export", lambda *_: record,
    )
    monkeypatch.setattr(codegen, "_resolve_method_mro", lambda *_: None)
    monkeypatch.setattr(codegen, "_native_class_method_def", lambda *_: None)
    monkeypatch.setattr(
        codegen, "_ordinary_call_needs_runtime_binding", lambda *_: False,
    )
    projected = object()
    ordinary = object()

    def field_result(*_args, force=False, **_kwargs):
        return projected if force else ordinary

    monkeypatch.setattr(codegen, "_emit_no_init_field_instance", field_result)
    result = codegen._maybe_emit_native_module_alias_call(expression)
    assert result is (projected if enabled and argument_count == 2 else ordinary)


def test_provider_capability_does_not_type_unknown_or_shadowed_receivers(monkeypatch):
    from pcc.frontends.python.py_ast import ClassType, DynType, Name

    module_name = "relocated.ir_provider"
    monkeypatch.setitem(SELF_MODULE_CONTRACTS, module_name, (IR_PROVIDER_BINDING_CONTRACT,))
    codegen = _scaffold_contract_codegen(module_name)
    receiver = Name(span=None, ty=DynType(name="dyn"), ident="builder")
    codegen.current_func_def = SimpleNamespace(
        name="ordinary", args=(SimpleNamespace(name="builder"),),
    )
    assert not codegen._scaffold_receiver_admitted(receiver, "add")
    codegen._ir_builder_env_flags["builder"] = "IRBuilder"
    assert codegen._scaffold_receiver_admitted(receiver, "add")
    ordinary = Name(span=None, ty=ClassType(name="IRBuilder", module="application"), ident="builder")
    assert not codegen._scaffold_receiver_admitted(ordinary, "add")
    provider = Name(span=None, ty=DynType(name="dyn"), ident="ir")
    assert codegen._scaffold_receiver_admitted(provider, "IntType")
    codegen.env["ir"] = object()
    assert not codegen._scaffold_receiver_admitted(provider, "IntType")


@pytest.mark.parametrize("module_name", [
    "pcc.frontends.python.codegen.class_gen", "relocated.class_lowering",
])
@pytest.mark.parametrize("enabled", [False, True])
def test_class_lowering_capability_controls_emitted_receiver_dispatch(
    monkeypatch, module_name, enabled,
):
    import re

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "0")
    monkeypatch.setitem(
        SELF_MODULE_CONTRACTS, module_name,
        (CLASS_LOWERING_RECEIVER_CONTRACT,) if enabled else (),
    )
    codegen = _scaffold_contract_codegen(
        module_name,
        "from pcc.ir.compat import ir\n"
        "class ClassLowering:\n"
        "    def _emit_method_body(self, fn):\n"
        "        return fn.append_basic_block('entry')\n",
    )
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    text = str(codegen.generate(codegen.ast_module))
    symbol = "user_" + module_name.replace(".", "_") + "_ClassLowering__emit_method_body"
    body = re.search(
        r"^define [^\n]*@" + symbol + r"\([^\n]*\).*?^}", text, re.M | re.S,
    ).group(0)
    direct_call = re.search(
        r"\bcall [^\n]*@user_pcc_ir_ir_scaffold_Function_append_basic_block\(", body,
    )
    assert bool(direct_call) is enabled
    assert not re.search(r"\bcall [^\n]*@py_cpy_", body)
    if not enabled:
        # The dynamic owner resolves the method once into a rooted slot, then
        # calls that loaded method; it no longer uses the old combined entry.
        assert re.search(r"\bcall [^\n]*@py_obj_load_method\(", body)
        assert re.search(r"\bcall [^\n]*@py_obj_call_loaded_method\(", body)


@pytest.mark.parametrize("contextual", [False, True])
def test_scaffold_capabilities_lift_and_emit_with_native_export_metadata(
    tmp_path, monkeypatch, contextual,
):
    import re

    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.pipeline_context import build_closed_world_context
    from pcc.frontends.python.type_infer import infer_module

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "0")
    provider_name = "pcc.frontends.python.codegen.self_module_contracts"
    provider = ROOT / "pcc/frontends/python/codegen/self_module_contracts.py"
    consumer = tmp_path / "capabilities.py"
    consumer.write_text(
        "from " + provider_name + " import (IR_PROVIDER_BINDING_CONTRACT, "
        "CLASS_LOWERING_RECEIVER_CONTRACT, PY_AST_FIELD_CONSTRUCTOR_CONTRACT, "
        "module_has_contract)\n"
        "def provider_enabled(name: str) -> bool:\n"
        "    return module_has_contract(name, IR_PROVIDER_BINDING_CONTRACT)\n"
        "def receivers_enabled(name: str) -> bool:\n"
        "    return module_has_contract(name, CLASS_LOWERING_RECEIVER_CONTRACT)\n"
        "def fields_enabled(name: str) -> bool:\n"
        "    return module_has_contract(name, PY_AST_FIELD_CONSTRUCTOR_CONTRACT)\n"
    )
    modules, exports, derived = build_closed_world_context(
        [str(provider), str(consumer)], [provider_name, "capabilities"],
    )
    static_exports = layer1_support._PCC_FRONTEND_STATIC_NATIVE_EXPORTS
    for name, value in (
        ("IR_PROVIDER_BINDING_CONTRACT", IR_PROVIDER_BINDING_CONTRACT),
        ("CLASS_LOWERING_RECEIVER_CONTRACT", CLASS_LOWERING_RECEIVER_CONTRACT),
        ("PY_AST_FIELD_CONSTRUCTOR_CONTRACT", PY_AST_FIELD_CONSTRUCTOR_CONTRACT),
    ):
        expected = {"kind": "constant", "value_kind": "str", "value": value}
        contextual_constant = exports[provider_name][name]
        assert {key: contextual_constant[key] for key in expected} == expected
        assert contextual_constant["owning_module"] == provider_name
        assert contextual_constant["export_name"] == name
        assert contextual_constant["value_ty"] == ("str",)
        assert contextual_constant["storage_owner"] == "managed"
        assert static_exports[provider_name][name] == expected
    selected_exports = exports if contextual else static_exports
    for module in modules:
        typed = infer_module(module, external_exports=selected_exports, derived_class_map=derived)
        codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
        codegen._native_module_exports = selected_exports
        codegen._strict_no_libpython = True
        codegen._prefer_native_callable_values = True
        text = str(codegen.generate(typed))
        (tmp_path / (module.name + ".ll")).write_text(text)
        assert not re.search(r"\bcall [^\n]*@py_cpy_", text)
        if module.name == "capabilities":
            for function in ("provider_enabled", "receivers_enabled", "fields_enabled"):
                body = re.search(
                    r"^define [^\n]*@user_capabilities_" + function
                    + r"\([^\n]*\).*?^}", text, re.M | re.S,
                ).group(0)
                assert re.search(
                    r"\bcall [^\n]*@user_pcc_frontends_python_codegen_"
                    r"self_module_contracts_module_has_contract\(", body,
                )
