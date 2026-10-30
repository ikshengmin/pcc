"""Real owned raw-library compilation, not a modeled mapper execution test.

The host memory/kernel contracts live in test_freestanding_linux_elf_image.py.
This file additionally requires the production mapper to pass the unchanged
freestanding verifier and emit an owned ELF object with its exact raw ABI.
Native loading/execution, Python import, and GC qualification are separate.
"""

import ast
import builtins
import hashlib
import json
from pathlib import Path
import subprocess

from pcc.backend import elf_x86_64 as elf
from pcc.frontends.python import owned_runtime_build
from pcc.frontends.python.pipeline_freestanding import (
    freestanding_allowed_external_symbols,
    validate_freestanding_ir,
)
from pcc.backend.self_backend_parse import parse_ir_type, parse_self_backend_module


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "pcc/runtime"
SOURCE = RUNTIME / "py/freestanding_linux_elf_image.py"
TARGET = "x86_64-unknown-linux-gnu"
PUBLIC_ABI = {
    "pcc_linux_elf_image_open": ("i64", ("ptr", "ptr", "ptr")),
    "pcc_linux_elf_image_symbol": ("ptr", ("ptr", "ptr")),
    "pcc_linux_elf_image_close": ("i64", ("ptr",)),
}
HELPERS = {
    "_u16", "_u32", "_span", "_same", "_name_length", "_contains",
    "_unsupported_section_name", "_section", "_symbol", "_host_address",
    "_validate_headers", "_validate_note_and_symbols", "_relocation_width",
    "_validate_relocations", "_dispose", "_map_sections", "_resolve_symbols",
    "_apply_one", "_relocate_and_protect",
}


def _definitions():
    return [node for node in ast.parse(SOURCE.read_text()).body
            if isinstance(node, ast.FunctionDef)]


def _symbol(node):
    assert len(node.decorator_list) == 1
    decorator = node.decorator_list[0]
    assert isinstance(decorator, ast.Call)
    assert isinstance(decorator.func, ast.Name) and decorator.func.id == "c_abi_export"
    assert len(decorator.args) == 1 and not decorator.keywords
    assert isinstance(decorator.args[0], ast.Constant)
    return decorator.args[0].value


def _raw_type(annotation):
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return "void"
    assert isinstance(annotation, ast.Name) and annotation.id in ("i64", "c_ptr")
    return {"i64": "i64", "c_ptr": "ptr"}[annotation.id]


def test_mapper_helpers_declare_explicit_fully_typed_namespaced_raw_abi():
    definitions = _definitions()
    helpers = [node for node in definitions if node.name.startswith("_")]
    assert {node.name for node in helpers} == HELPERS
    for node in helpers:
        assert _symbol(node) == "pcc_linux_elf_image_" + node.name
    assert {_symbol(node) for node in definitions if not node.name.startswith("_")} == set(PUBLIC_ABI)
    for node in definitions:
        assert not node.args.posonlyargs and not node.args.kwonlyargs
        assert node.args.vararg is None and node.args.kwarg is None
        assert not node.args.defaults and not node.args.kw_defaults
        assert _raw_type(node.returns) in ("i64", "ptr", "void")
        assert all(_raw_type(argument.annotation) in ("i64", "ptr") for argument in node.args.args)


def test_mapper_emits_owned_raw_library_object_without_managed_or_external_tool_escape(tmp_path, monkeypatch):
    original_import = builtins.__import__

    def checked_import(name, *args, **kwargs):
        if name == "llvmlite" or name.startswith("llvmlite."):
            raise AssertionError("owned mapper compilation imported " + name)
        return original_import(name, *args, **kwargs)

    def forbidden_process(*args, **kwargs):
        raise AssertionError("owned mapper compilation attempted an external process")

    monkeypatch.setattr(builtins, "__import__", checked_import)
    monkeypatch.setattr(subprocess, "Popen", forbidden_process)
    monkeypatch.setenv("PCC_WITH_THREADS", "1")
    monkeypatch.setenv("PCC_REFCOUNT_KIND", "atomic")
    config = owned_runtime_build.runtime_build_config()
    data = owned_runtime_build._build_runtime_module(
        str(RUNTIME), SOURCE.stem, TARGET, str(tmp_path),
        owned_runtime_build.runtime_ir_passes(str(RUNTIME)), config,
    )
    object_path = tmp_path / (SOURCE.stem + ".o")
    ir_path = object_path.with_suffix(".ll")
    text = ir_path.read_text()
    validate_freestanding_ir(text, freestanding_allowed_external_symbols(SOURCE.read_text()))
    module = parse_self_backend_module(text)
    functions = {function.name: function for function in module.functions}
    expected = {
        _symbol(node): (_raw_type(node.returns), tuple(_raw_type(arg.annotation) for arg in node.args.args))
        for node in _definitions()
    }
    assert {name: expected[name] for name in PUBLIC_ABI} == PUBLIC_ABI
    for name, (result, arguments) in expected.items():
        function = functions[name]
        assert function.ret_type == parse_ir_type(result)
        assert tuple(argument.type for argument in function.args) == tuple(map(parse_ir_type, arguments))
    obj = elf.parse_relocatable(data)
    assert object_path.read_bytes() == data
    assert obj.machine == elf.EM_X86_64
    defined = {symbol.name for symbol in obj.symbols if symbol.name and
               symbol.section_index != elf.SHN_UNDEF and symbol.binding != elf.STB_LOCAL
               and symbol.type == elf.STT_FUNC}
    assert defined == set(expected)
    undefined = {symbol.name for symbol in obj.symbols if symbol.name and symbol.section_index == elf.SHN_UNDEF}
    assert undefined <= {"malloc", "calloc", "free", "memcpy"}
    receipt = json.loads(Path(str(object_path) + ".provenance.json").read_text())
    assert receipt["source"] == "pcc/runtime/py/freestanding_linux_elf_image.py"
    assert receipt["source_sha256"] == hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert receipt["object_sha256"] == hashlib.sha256(data).hexdigest()
    assert receipt["ir_sha256"] == hashlib.sha256(ir_path.read_bytes()).hexdigest()
    assert receipt["producer_kind"] == "pcc-python-library-ir-to-obj"
    assert receipt["object_emitter"] == "pcc-self-backend-object-writer"
    assert receipt["uses_host_cc"] is False
    assert receipt["target_triple"] == TARGET
    assert receipt["runtime_build_config"] == config
    assert config["threads"] is True and config["refcount"] == "atomic"
