"""Live module calls may bypass packing only after native-entry validation."""

import os
import re
import subprocess
import sys

import pytest

from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.pipeline import compile_python
from pcc.frontends.python.pipeline_context import build_closed_world_context
from pcc.frontends.python.type_infer import infer_module


PROVIDER = '''def combine(value, *, offset=2, enabled=True):
    return [value, offset, enabled]


def identity(value, *, extra=None):
    return value


def fail(value):
    raise ValueError(str(value))


def half(value: int) -> float:
    return value / 2


def flag(value: int) -> bool:
    return value > 1


def note(items: list) -> None:
    items.append(1)
'''

MAIN = '''import guardcall_provider as provider


def use(value):
    return provider.combine(value, offset=3)


def gen():
    yield provider.combine(7)
    yield provider.identity([8])


def alternate(value, *, offset=2, enabled=True):
    return [value, -offset, enabled]


def replace(value):
    provider.combine = alternate
    return value


def shadow(provider):
    return provider.combine(9)


class Receiver:
    def combine(self, value, *, offset=2, enabled=True):
        return [value, 99, enabled]


class Descriptor:
    @property
    def combine(self):
        print("get")
        return alternate


class Box:
    def __init__(self, value):
        self.value = value

    def __del__(self):
        print("drop", self.value)


def alias():
    result = provider.identity(Box(12))
    print("live", result.value)
    del result


def main():
    print(use(1))
    print(provider.flag(3), provider.flag(0), provider.half(5))
    items = []
    print(provider.note(items), items)
    print(list(gen()))
    alias()
    try:
        provider.fail(4)
    except ValueError as exc:
        print("caught", exc)
    print(provider.combine(replace(5)))
    print(use(6))
    print(shadow(Receiver()))
    print(shadow(Descriptor()))
    try:
        provider.combine(1, missing=2)
    except TypeError:
        print("invalid")
    del provider.combine
    try:
        use(0)
    except AttributeError:
        print("deleted")


main()
'''

EXPECTED = '''[1, 3, True]
True False 2.5
None [1]
[[7, 2, True], [8]]
live 12
drop 12
caught 4
[5, 2, True]
[6, -3, True]
[9, 99, True]
get
[9, -2, True]
invalid
deleted
'''


def _write(tmp_path):
    (tmp_path / "guardcall_provider.py").write_text(PROVIDER)
    main = tmp_path / "guardcall_main.py"
    main.write_text(MAIN)
    return main


def test_live_module_call_ir_guards_and_preserves_dynamic_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    main = _write(tmp_path)
    modules, exports, derived = build_closed_world_context(
        [str(tmp_path / "guardcall_provider.py"), str(main)],
        ["guardcall_provider", "guardcall_main"],
    )
    module = next(module for module in modules if module.name == "guardcall_main")
    external = {name: value for name, value in exports.items() if name != module.name}
    typed = infer_module(module, external_exports=external, derived_class_map=derived)
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    codegen._native_module_exports = exports
    codegen._sibling_module_inits = tuple(external)
    text = str(codegen.generate(typed))
    for suffix in ("use", "shadow", "gen__gen_resume"):
        match = re.search(r"^define [^\n]*@user_guardcall_main_" + suffix + r"\(.*?^}", text, re.M | re.S)
        assert match, suffix
        body = match.group(0)
        assert "@py_func_slot_has_native_entry(" in body, suffix
        assert "@user_guardcall_provider_combine(" in body, suffix
        assert "@py_obj_call_slots(" in body, suffix


def test_live_module_call_cpython_reference(tmp_path):
    result = subprocess.run([sys.executable, str(_write(tmp_path))], capture_output=True,
                            text=True, timeout=15)
    assert (result.returncode, result.stdout, result.stderr) == (0, EXPECTED, "")


@pytest.mark.integration
def test_live_module_calls_native(tmp_path, pcc_runtime_archive):
    main = _write(tmp_path)
    output = tmp_path / "guardcall_main"
    compile_python(str(main), str(output), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for gc in range(5):
        result = subprocess.run(
            [str(output)], env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc)),
            capture_output=True, text=True, timeout=20,
        )
        assert (result.returncode, result.stdout, result.stderr) == (0, EXPECTED, ""), gc


def test_compiled_builtin_alias_is_only_a_guard_candidate():
    from pcc.frontends.python.codegen.native_modules import NativeModuleAliasMixin

    class Aliases(NativeModuleAliasMixin):
        _native_extension_module_env = {}
        _module_globals = {}
        _native_module_aliases = {}
        _native_module_exports = {"provider": {"operation": {"kind": "function"}}}

        def _native_module_object_for_name(self, name):
            return None

        def _native_builtin_module_for_name(self, name):
            return "provider" if name == "alias" else None

    host = Aliases()
    assert host._native_module_alias_export_info("alias", "operation") is None
    assert host._native_module_alias_export_info("alias", "operation", True) == (
        "provider", {"kind": "function"},
    )
    assert host._native_module_alias_export_info("unknown", "operation", True) is None
