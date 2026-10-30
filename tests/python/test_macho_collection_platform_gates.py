"""Import the affected test modules without executing their native test bodies."""

import ast
import builtins
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


_MODULES = (
    ("test_macho_archive", ("cc", "ar"), "needs cc/ar on Darwin arm64"),
    ("test_macho_codesign", ("cc",), "needs cc and Darwin arm64"),
    ("test_macho_exec_link", ("cc",), "needs cc and Darwin arm64"),
    ("test_macho_link_bodies_match_ld", (), "needs Darwin arm64"),
    ("test_macho_link_cc_objects", ("cc",), "needs cc on Darwin arm64"),
    ("test_macho_link_lossless", (), "needs Darwin arm64"),
    ("test_macho_link_relocatable", ("cc",), "needs cc and Darwin arm64"),
    ("test_macho_obj_data_section", ("cc",), "needs cc and Darwin arm64"),
    ("test_macho_obj_full_sections", ("cc",), "needs cc and Darwin arm64"),
    ("test_macho_obj_minimal", ("cc",), "needs cc and Darwin arm64"),
    ("test_macho_obj_reloc", ("cc",), "needs cc and Darwin arm64"),
    ("test_macho_obj_reloc_remaining", ("cc",), "needs cc and Darwin arm64"),
    ("test_macho_obj_remaining_sections", (), None),
    ("test_macho_spec", ("cc",), "needs cc and a Darwin SDK"),
    ("test_self_backend_null_callee_call", (), None),
    ("test_self_link_pcc_route", ("cc",), "needs cc and Darwin arm64"),
    ("test_self_obj_pcc_route", ("cc",), "needs cc and Darwin arm64"),
)


def _assert_gate(mark, available, reason):
    assert mark.name == "pcc_gate"
    assert mark.args == ()
    assert mark.kwargs == {"unavailable": None if available else reason}


@pytest.mark.parametrize("module_name,required_tools,reason", _MODULES)
@pytest.mark.parametrize("system,machine,missing_tool", (
    (None, None, None),
    ("Darwin", "arm64", None),
    ("Darwin", "x86_64", None),
    ("Linux", "x86_64", None),
    ("Linux", "arm64", None),
    ("Darwin", "arm64", "cc"),
    ("Darwin", "arm64", "as"),
    ("Darwin", "arm64", "ar"),
))
def test_macho_collection_preserves_platform_and_tool_gates(
    module_name, required_tools, reason, system, machine, missing_tool,
):
    source = Path(__file__).with_name(module_name + ".py")
    text = source.read_text()
    platform_name = {None: "win32", "Darwin": "darwin", "Linux": "linux"}[system]
    target_os = SimpleNamespace(environ={}, sys=SimpleNamespace(platform=platform_name))
    if system is not None:
        target_os.uname = lambda: SimpleNamespace(sysname=system, machine=machine)
    target_shutil = SimpleNamespace(
        which=lambda name: None if name == missing_tool else "/tools/" + name,
    )
    target_platform = SimpleNamespace(machine=lambda: machine or "AMD64")
    real_import = builtins.__import__

    def target_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "os":
            return target_os
        if name == "shutil":
            return target_shutil
        if name == "platform":
            return target_platform
        return real_import(name, globals, locals, fromlist, level)

    module = ModuleType("_collection_contract_" + module_name)
    module.__file__ = str(source)
    module.__dict__["__builtins__"] = dict(vars(builtins), __import__=target_import)
    exec(compile(text, str(source), "exec"), module.__dict__)
    darwin_arm64 = system == "Darwin" and machine == "arm64"
    darwin = system == "Darwin"
    platform_ok = darwin if module_name == "test_macho_spec" else darwin_arm64
    predicate = "_IS_DARWIN" if module_name == "test_macho_spec" else "_IS_ARM64_DARWIN"
    assert getattr(module, predicate) is platform_ok
    if reason is not None:
        available = platform_ok and missing_tool not in required_tools
        assert module._GATE == (None if available else reason)
        mark = module._DARWIN_GATE if module_name == "test_self_obj_pcc_route" else module.pytestmark
        _assert_gate(mark, available, reason)
    if module_name == "test_macho_obj_remaining_sections":
        _assert_gate(module._DIFF_GATE, darwin_arm64 and missing_tool != "as",
                     "needs as(1) on Darwin arm64")
        _assert_gate(module._RUN_GATE, darwin_arm64 and missing_tool not in ("cc", "as"),
                     "needs cc and as(1) on Darwin arm64")
    if module_name == "test_self_backend_null_callee_call":
        test = module.test_null_callee_trap_aborts_a_program_that_reaches_it
        mark = next(mark for mark in test.pytestmark if mark.name == "pcc_gate")
        _assert_gate(mark, darwin_arm64, "needs Darwin arm64")
    if module_name == "test_self_obj_pcc_route":
        linux_x86 = system == "Linux" and machine == "x86_64"
        assert module._IS_X86_64_LINUX is linux_x86
        _assert_gate(module._LINUX_GATE, linux_x86 and missing_tool != "cc",
                     "needs cc and Linux x86_64")
    for node in ast.parse(text).body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            test = getattr(module, node.name)
            assert callable(test)
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Name) and decorator.id.endswith("_GATE"):
                    assert getattr(module, decorator.id).mark in test.pytestmark
