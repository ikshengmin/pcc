"""Public regex ABI contracts, using host source/IR/objects only.

These tests never link or execute the engine, canaries, collectors, or prior
diagnostics. Object controls compile the complete actual engine source so
its real internal providers remain inside the normal verified closure.
"""

import ast
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "pcc/runtime/py/freestanding_re_engine.py"
BRIDGE = ROOT / "pcc/runtime/py/py_re_engine_runtime.py"
PUBLIC = {
    "pcc_re_engine_supported": ("ptr",),
    "pcc_re_engine_supported_flags": ("ptr", "i64"),
    "pcc_re_engine_run_flags": ("ptr", "i64", "ptr", "i64", "i64", "i32", "ptr", "i32", "ptr"),
    "pcc_re_engine_run_from": ("ptr", "ptr", "i64", "i64", "i32", "ptr", "i32", "ptr"),
    "pcc_re_engine_run": ("ptr", "ptr", "i64", "i32", "ptr", "i32", "ptr"),
    "pcc_re_engine_group_names_flags": ("ptr", "i64", "ptr", "i32"),
    "pcc_re_engine_group_names": ("ptr", "ptr", "i32"),
}
BRIDGE_SYMBOLS = (
    "pcc_re_engine_supported", "pcc_re_engine_supported_flags",
    "pcc_re_engine_run_flags", "pcc_re_engine_group_names_flags",
)


def _core_functions():
    return {node.name: node for node in ast.parse(CORE.read_text()).body
            if isinstance(node, ast.FunctionDef)}


@pytest.mark.parametrize("name", PUBLIC)
def test_public_export_matches_exact_c_int_contract(name):
    node = _core_functions()[name]
    assert len(node.decorator_list) == 1
    decorator = node.decorator_list[0]
    assert isinstance(decorator, ast.Call)
    assert isinstance(decorator.func, ast.Name)
    assert decorator.func.id == "c_abi_typed_export"
    assert tuple(ast.literal_eval(arg) for arg in decorator.args) == (name, "i32", PUBLIC[name])
    assert not decorator.keywords


@pytest.mark.parametrize("name", BRIDGE_SYMBOLS)
def test_bridge_declaration_matches_provider_widths(name):
    tree = ast.parse(BRIDGE.read_text())
    calls = [node.value for node in tree.body if isinstance(node, ast.Assign)
             and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]
    assert len(calls) == 1
    call = calls[0]
    assert isinstance(call.func, ast.Name) and call.func.id == "extern"
    assert ast.literal_eval(call.args[0]) == name
    names = {"c_ptr": "ptr", "c_int32": "i32", "c_int64": "i64"}
    assert tuple(names[arg.id] for arg in call.args[1].elts) == PUBLIC[name]
    assert names[call.args[2].id] == "i32"


def test_capacity_and_status_guards_remain_exact():
    functions = _core_functions()
    run = functions["_run_flags"]
    expected = ast.dump(ast.parse("caps_length < needed", mode="eval").body)
    guards = [node for node in ast.walk(run) if isinstance(node, ast.If)
              and ast.dump(node.test) == expected]
    assert len(guards) == 1 and len(guards[0].body) == 1
    assert ast.literal_eval(guards[0].body[0].value) == -3
    needed = [node.value for node in ast.walk(run) if isinstance(node, ast.AnnAssign)
              and isinstance(node.target, ast.Name) and node.target.id == "needed"]
    assert len(needed) == 1
    assert ast.dump(needed[0]) == ast.dump(ast.parse("(group_count + 1) * 2", mode="eval").body)
    bridge = ast.parse(BRIDGE.read_text())
    calls = [node for node in ast.walk(bridge) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "pcc_re_engine_run_flags"]
    assert len(calls) == 2
    assert all(ast.literal_eval(call.args[7]) == 64 for call in calls)


@pytest.mark.parametrize(("target", "machine"), [
    ("x86_64-unknown-linux-gnu", 62), ("aarch64-unknown-linux-gnu", 183),
])
def test_real_export_ir_and_elf_have_matching_public_widths(tmp_path, monkeypatch, target, machine):
    from pcc.frontends.python.pipeline import compile_python
    from pcc.backend.self_backend_parse import parse_self_backend_module
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.elf_x86_64 import parse_relocatable, SHN_UNDEF
    import subprocess

    def reject(*args, **kwargs):
        pytest.fail("ABI host control attempted an external compiler or executable")

    monkeypatch.setattr(subprocess, "run", reject)
    monkeypatch.setattr(subprocess, "Popen", reject)
    source = tmp_path / "regex_exports.py"
    output = source.with_suffix(".ll")
    source.write_bytes(CORE.read_bytes())
    compile_python(str(source), str(output), emit_llvm_only=True,
                   backend="self", libpython_mode="off", python_library=True,
                   target_triple=target)
    text = output.read_text()
    run_flags_body = re.search(
        r"^define[^\n]*@pcc_re_engine_run_flags\([^\n]*\)[^\n]*\{\n(.*?)^\}",
        text, re.MULTILINE | re.DOTALL,
    )
    assert run_flags_body is not None
    # Public signed C-int arguments must be widened for the unchanged i64
    # core; the i64 status must be narrowed back to the public i32 result.
    body = run_flags_body.group(1)
    assert len(re.findall(r"\bsext i32 [^\n]+ to i64\b", body)) == 2
    assert len(re.findall(r"\btrunc i64 [^\n]+ to i32\b", body)) == 1
    module = parse_self_backend_module(text)
    functions = {function.name: function for function in module.functions}
    for name, arguments in PUBLIC.items():
        function = functions[name]
        assert function.ret_type.describe() == "i32"
        expected = tuple("void*" if kind == "ptr" else kind for kind in arguments)
        assert tuple(arg.type.describe() for arg in function.args) == expected
    data = emit_owned_object(text, target)
    obj = parse_relocatable(data)
    assert obj.machine == machine
    defined = {symbol.name for symbol in obj.symbols if symbol.section_index != SHN_UNDEF}
    assert set(PUBLIC) <= defined
    undefined = {symbol.name for symbol in obj.symbols if symbol.section_index == SHN_UNDEF and symbol.name}
    providers = {"pcc_re_core__run_flags", "pcc_re_core__compiled_program",
                 "pcc_re_core__cstrlen", "pcc_re_core__copy_bytes"}
    assert providers <= defined
    assert providers.isdisjoint(undefined)


@pytest.mark.parametrize("text", [
    "", 'target triple = "x86_64-unknown-linux-gnu"\n', '%Item = type { i64, ptr }\n',
])
def test_generic_type_context_keeps_empty_target_and_named_types(text):
    from pcc.backend.self_backend_parse import _parse_named_types
    context = _parse_named_types(text)
    assert context.target_triple == ("x86_64-unknown-linux-gnu" if text.startswith("target") else "")
    assert context.named_type_bodies == ({"%Item": "{ i64, ptr }"} if text.startswith("%") else {})
