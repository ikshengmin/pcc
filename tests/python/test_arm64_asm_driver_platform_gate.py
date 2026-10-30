"""Collection evaluates the existing Darwin gate even where os.uname is absent."""

import builtins
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


@pytest.mark.parametrize("system,machine,compiler,expected", (
    (None, None, "compiler", False),
    ("Darwin", "arm64", "compiler", True),
    ("Darwin", "x86_64", "compiler", False),
    ("Linux", "arm64", "compiler", False),
    ("Darwin", "arm64", None, False),
))
def test_arm64_assembler_collection_preserves_existing_platform_gate(
    system, machine, compiler, expected,
):
    source = Path(__file__).with_name("test_arm64_asm_driver.py")
    target_os = SimpleNamespace(environ={})
    if system is not None:
        target_os.uname = lambda: SimpleNamespace(sysname=system, machine=machine)
    target_shutil = SimpleNamespace(which=lambda name: compiler)
    real_import = builtins.__import__

    def target_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "os":
            return target_os
        if name == "shutil":
            return target_shutil
        return real_import(name, globals, locals, fromlist, level)

    module = ModuleType("_arm64_asm_driver_collection_contract")
    module.__file__ = str(source)
    module.__dict__["__builtins__"] = dict(vars(builtins), __import__=target_import)
    exec(compile(source.read_text(), str(source), "exec"), module.__dict__)
    assert module._IS_ARM64_DARWIN is (system == "Darwin" and machine == "arm64")
    assert module._GATE == (None if expected else "needs cc and Darwin arm64")
    assert module.pytestmark.name == "pcc_gate"
    assert module.pytestmark.kwargs == {"unavailable": module._GATE}
    assert callable(module.test_incremental_module_builder_matches_complete_driver)
