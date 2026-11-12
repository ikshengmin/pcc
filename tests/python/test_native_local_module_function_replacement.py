"""Function-local module imports use the live namespace for function writes.

Python's import binding, attribute assignment and call evaluation order apply
equally inside a function (Language Reference 6.3, 6.16, 7.2 and 7.11). This is
the shape used by diagnostics.wiring to install its pipeline profile wrapper.
The original wrapper and its profiling contract must not be removed to build
Stage1. These tests run only in the requested CI validation, not on import.
"""

import os
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest


PROVIDER = '''def operation(value, *, offset=2):
    if value < 0:
        raise ValueError("negative")
    return value + offset
'''

PROGRAM = '''events = []

def install():
    IMPORT_PIPELINE
    original = pipeline.operation
    if getattr(original, "_profiled", False):
        return original
    def profiled(*args, **kwargs):
        events.append("begin")
        try:
            return original(*args, **kwargs)
        finally:
            events.append("end")
    profiled._profiled = True
    pipeline.operation = profiled
    return original

def invoke(value):
    IMPORT_PIPELINE
    return pipeline.operation(value, offset=3)

def restore(original):
    IMPORT_PIPELINE
    pipeline.operation = original

def alternate(value, *, offset=2):
    return value - offset

def replace_argument():
    IMPORT_PIPELINE
    pipeline.operation = alternate
    return 6

def evaluation_order():
    IMPORT_PIPELINE
    return pipeline.operation(replace_argument())

class Receiver:
    def operation(self, value, *, offset=2):
        return value * offset

def shadow(pipeline):
    return pipeline.operation(9, offset=4)

def main():
    assert invoke(5) == 8
    original = install()
    installed = install()
    assert installed is install()
    assert installed is not original
    assert invoke(5) == 8
    assert events == ["begin", "end"]
    try:
        invoke(-1)
    except ValueError as error:
        assert str(error) == "negative"
    else:
        raise AssertionError("profile wrapper swallowed the exception")
    assert events == ["begin", "end", "begin", "end"]
    assert shadow(Receiver()) == 36
    restore(original)
    assert invoke(5) == 8
    assert len(events) == 4
    # Callable lookup precedes argument evaluation. A mutation in the argument
    # changes the next call, not the callable already captured for this one.
    assert evaluation_order() == 8
    assert invoke(5) == 2
    restore(original)
    assert invoke(5) == 8
    print("local-module-function-replacement-ok")

main()
'''

IMPORTS = (
    "from localmod_pkg import provider as pipeline",
    "import localmod_pkg.provider as pipeline",
)
EXPECTED = "local-module-function-replacement-ok\n"


def _write(tmp_path, statement):
    package = tmp_path / "localmod_pkg"
    package.mkdir()
    init = package / "__init__.py"
    init.write_text("", encoding="utf-8")
    provider = package / "provider.py"
    provider.write_text(PROVIDER, encoding="utf-8")
    entry = tmp_path / "localmod_entry.py"
    entry.write_text(PROGRAM.replace("IMPORT_PIPELINE", statement), encoding="utf-8")
    return init, provider, entry


@pytest.mark.parametrize("statement", IMPORTS)
def test_local_module_function_write_real_ir(tmp_path, statement):
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.pipeline_context import build_closed_world_context
    from pcc.frontends.python.type_infer import infer_module

    paths = _write(tmp_path, statement)
    modules, exports, derived = build_closed_world_context(
        [str(path) for path in paths],
        ["localmod_pkg", "localmod_pkg.provider", "localmod_entry"],
    )
    module = next(item for item in modules if item.name == "localmod_entry")
    external = {name: value for name, value in exports.items() if name != module.name}
    typed = infer_module(module, external_exports=external, derived_class_map=derived)
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
    codegen._strict_no_libpython = True
    codegen._native_module_exports = exports
    codegen._sibling_module_inits = tuple(external)
    text = str(codegen.generate(typed))
    (tmp_path / "local-module.ll").write_text(text, encoding="utf-8")
    for name in ("install", "restore", "replace_argument"):
        body = re.search(r"^define [^\n]*@user_localmod_entry_" + name
                         + r"\(.*?^}", text, re.M | re.S)
        assert body is not None, name
        assert "@py_compiled_module_import_by_name(" in body.group(0), name
        assert "@py_obj_setattr(" in body.group(0), name
        assert ".modvar.localmod_entry.pipeline" not in body.group(0), name
    assert "strict.nolib.stub" not in text


@pytest.mark.parametrize("statement", IMPORTS)
def test_local_module_function_write_cpython_reference(tmp_path, statement):
    entry = _write(tmp_path, statement)[-1]
    result = subprocess.run([sys.executable, str(entry)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=20)
    assert (result.returncode, result.stdout, result.stderr) == (0, EXPECTED, "")


@pytest.mark.integration
@pytest.mark.parametrize("statement", IMPORTS)
def test_local_module_function_write_native(
    tmp_path, statement, pcc_runtime_archive, python_program_compiler,
):
    entry = _write(tmp_path, statement)[-1]
    output = tmp_path / "local-module-functions"
    python_program_compiler(str(entry), str(output), backend="self",
                            ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        result = subprocess.run(
            [str(output)], cwd=tmp_path, capture_output=True, text=True, timeout=20,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
        )
        assert (result.returncode, result.stdout, result.stderr) == (0, EXPECTED, ""), backend


def test_local_import_binding_does_not_change_namespace_ownership():
    from pcc.frontends.python.codegen.cpy_import_state import live_import_name_binding

    local, global_slot = object(), object()
    host = SimpleNamespace(
        current_func_def=object(), env={"pipeline": (local, None)},
        _native_module_aliases={"pipeline": "provider"},
        _module_globals={}, _native_extension_module_env={},
    )
    assert live_import_name_binding(host, "pipeline")
    assert not live_import_name_binding(host, "unknown")
    host._module_globals["pipeline"] = (global_slot, None)
    assert live_import_name_binding(host, "pipeline")
    host.env["pipeline"] = (global_slot, None)
    assert not live_import_name_binding(host, "pipeline")
    host._native_extension_module_env["pipeline"] = global_slot
    assert live_import_name_binding(host, "pipeline")
    for mode in ("_runtime_port_module", "_freestanding_module"):
        setattr(host, mode, True)
        assert not live_import_name_binding(host, "pipeline")
        setattr(host, mode, False)


INITIALIZING_WIRING = '''import functools

_installed = False
_enabled = False
events = []

def install():
    global _installed, _enabled
    if _installed:
        return
    _enabled = True
    _installed = True

def install_owner(owner):
    if not _enabled or getattr(owner, "_registered", False):
        return
    original = owner.operation
    assert not getattr(owner, "finished", False)
    @functools.wraps(original)
    def wrapped(*args, **kwargs):
        events.append("begin")
        try:
            return original(*args, **kwargs)
        finally:
            events.append("end")
    wrapped._profiled = True
    owner.operation = wrapped
    owner._registered = True
'''

INITIALIZING_OWNER = '''import sys

def operation(value, *, offset=2):
    if value < 0:
        raise ValueError("negative")
    return value + offset

try:
    from initializing_pkg.wiring import install_owner
    install_owner(sys.modules[__name__])
except Exception:
    pass
finished = True
'''

INITIALIZING_ENTRY = '''from initializing_pkg.provider import operation
from initializing_pkg import provider, wiring

def main():
    assert provider.finished
    assert provider._registered
    assert operation is provider.operation
    assert operation._profiled
    assert operation(5, offset=3) == 8
    assert wiring.events == ["begin", "end"]
    wiring.install()
    wiring.install_owner(provider)
    assert operation is provider.operation
    try:
        operation(-1)
    except ValueError as error:
        assert str(error) == "negative"
    else:
        raise AssertionError("initialization wrapper swallowed the exception")
    assert wiring.events == ["begin", "end", "begin", "end"]
    print("initializing-owner-registration-ok")

main()
'''
INITIALIZING_EXPECTED = "initializing-owner-registration-ok\n"


def _write_initializing_owner(tmp_path):
    # Language Reference 5.4 and 7.11: publish the live owner before executing
    # its initializer and finish replacement before a from-import captures it.
    package = tmp_path / "initializing_pkg"
    package.mkdir()
    (package / "__init__.py").write_text(
        "from initializing_pkg.wiring import install\ninstall()\n", encoding="utf-8",
    )
    (package / "wiring.py").write_text(INITIALIZING_WIRING, encoding="utf-8")
    (package / "provider.py").write_text(INITIALIZING_OWNER, encoding="utf-8")
    entry = tmp_path / "initializing_entry.py"
    entry.write_text(INITIALIZING_ENTRY, encoding="utf-8")
    return entry


def test_owner_registration_during_initialization_cpython_reference(tmp_path):
    entry = _write_initializing_owner(tmp_path)
    result = subprocess.run(
        [sys.executable, "-B", str(entry)], cwd=tmp_path,
        capture_output=True, text=True, timeout=20,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, INITIALIZING_EXPECTED, "")


@pytest.mark.integration
def test_owner_registration_during_initialization_native(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    entry = _write_initializing_owner(tmp_path)
    output = tmp_path / "initializing-owner"
    python_program_compiler(
        str(entry), str(output), backend="self", ir_scaffold_mode="on",
        runtime_archive=str(pcc_runtime_archive),
    )
    for backend in range(5):
        result = subprocess.run(
            [str(output)], cwd=tmp_path, capture_output=True, text=True, timeout=20,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
        )
        assert (result.returncode, result.stdout, result.stderr) == (
            0, INITIALIZING_EXPECTED, "",
        ), backend
