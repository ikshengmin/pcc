"""Owned Windows C exit ABI, lifecycle models and target-only execution.

Host models and COFF emission are not native Windows execution evidence.
Native controls require an explicitly selected, source-matching runtime.
"""

import json
import os
from pathlib import Path
import platform
import re
import sys

import pytest

from tests.python.owned_regression_support import explicit_owned_runtime
from tests.python.process_timeout import run_process_group_timeout


ROOT = Path(__file__).resolve().parents[2]
TARGET = "x86_64-pc-windows-msvc"
MODULE = "freestanding_windows"


# Keep these existing lifecycle-model helpers local so selecting model tests
# never imports the C evaluator, compiler pipeline, or native FFI surface.
def _model(module, names, **bindings):
    """Run unchanged definitions with explicit machine-boundary test doubles."""
    import ast
    tree = ast.parse((ROOT / "pcc/runtime/py" / (module + ".py")).read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in functions} == set(names)
    namespace = {
        "i64": int, "c_ptr": object,
        "c_abi_export": lambda *args: lambda function: function,
        "c_abi_typed_export": lambda *args: lambda function: function,
        "c_abi_variadic_export": lambda *args: lambda function: function,
        **bindings,
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), module, "exec"), namespace)
    return namespace


class _RawMemory:
    """Deterministic byte-offset memory boundary for lifecycle model controls."""
    def __init__(self):
        self.cells = {}
        self.symbols = {}
        self.next = 4096
        self.allocations = []
        self.freed = []
        self.fail_allocation = None
        self.locked = False

    def global_addr(self, name):
        if name not in self.symbols:
            self.symbols[name] = self.next
            self.next += 4096
        return self.symbols[name]

    def load(self, pointer, offset):
        return self.cells.get(pointer + offset, 0)

    def store(self, pointer, offset, value):
        self.cells[pointer + offset] = value

    def malloc(self, size):
        self.allocations.append(size)
        if len(self.allocations) == self.fail_allocation:
            return 0
        pointer = self.next
        self.next += 4096
        return pointer

    def acquire(self):
        assert not self.locked
        self.locked = True

    def release(self):
        assert self.locked
        self.locked = False

    def cas(self, pointer, offset, expected, desired, *orders):
        old = self.load(pointer, offset)
        if old == expected:
            self.store(pointer, offset, desired)
        return old

    def bindings(self):
        return dict(global_addr=self.global_addr, load_ptr=self.load, load_i64=self.load,
                    store_ptr=self.store, store_i64=self.store, malloc=self.malloc,
                    free=self.freed.append, ptr_add=lambda pointer, offset: pointer + offset,
                    ptr_diff=lambda left, right: left - right, null=lambda: 0,
                    ptr_is_null=lambda pointer: pointer == 0, atomic_cas_i32=self.cas)


def _lifecycle(memory, **bindings):
    return _model(
        MODULE,
        ("atexit_c", "_run_exit_callbacks", "exit_c", "immediate_exit",
         "posix_immediate_exit", "process_exit"),
        **memory.bindings(), _atexit_acquire=memory.acquire,
        _atexit_release=memory.release,
        call_void_ptr0=lambda callback: callback(), **bindings,
    )


def test_windows_atexit_reserve_failure_lifo_and_reentrant_registration():
    memory = _RawMemory()
    events = []
    namespace = _lifecycle(memory)

    def callback(value):
        def run():
            assert not memory.locked
            events.append(value)
        return run

    for value in range(32):
        assert namespace["atexit_c"](callback(value)) == 0
    assert not memory.allocations
    memory.fail_allocation = 1
    assert namespace["atexit_c"](callback(999)) == -1
    assert not memory.locked
    memory.fail_allocation = None

    def register_during_exit():
        assert not memory.locked
        events.append(32)
        assert namespace["atexit_c"](callback(33)) == 0

    assert namespace["atexit_c"](register_during_exit) == 0
    namespace["_run_exit_callbacks"]()
    namespace["_run_exit_callbacks"]()
    assert events == [32, 33, *range(31, -1, -1)]
    assert len(memory.freed) == 2 and not memory.locked
    assert memory.load(memory.global_addr("pcc_c_atexit_head"), 0) == 0


class _Exited(Exception):
    pass


def test_windows_normal_exit_orders_callbacks_finalizers_flush_and_repeated_exit():
    memory = _RawMemory()
    events = []

    def terminate(status):
        events.append(("exit", status))
        raise _Exited

    namespace = _lifecycle(
        memory, ExitProcess=terminate,
        c_fflush=lambda stream: events.append(("flush", stream)) or -1,
    )

    def finalizer():
        events.append("fini-first")
        assert namespace["atexit_c"](lambda: events.append("late")) == 0

    begin = memory.global_addr("__fini_array_start")
    memory.symbols["__fini_array_end"] = begin + 16
    memory.store(begin, 0, finalizer)
    memory.store(begin, 8, lambda: events.append("fini-second"))
    assert namespace["atexit_c"](lambda: events.append("first")) == 0
    assert namespace["atexit_c"](lambda: events.append("second")) == 0
    with pytest.raises(_Exited):
        namespace["exit_c"](37)
    assert events == ["second", "first", "fini-second", "fini-first", "late",
                      ("flush", 0), ("exit", 37)]
    with pytest.raises(_Exited):
        namespace["exit_c"](41)
    assert events[-1] == ("exit", 41) and len(events) == 8


@pytest.mark.parametrize("entry", ("process_exit", "immediate_exit", "posix_immediate_exit", "recursive"))
def test_windows_immediate_and_recursive_exit_skip_remaining_cleanup(entry):
    memory = _RawMemory()
    events = []

    def terminate(status):
        events.append(status)
        raise _Exited

    def forbidden(*args):
        raise AssertionError("immediate exit ran cleanup")

    namespace = _lifecycle(memory, ExitProcess=terminate, c_fflush=forbidden)
    assert namespace["atexit_c"](forbidden) == 0
    if entry == "recursive":
        assert namespace["atexit_c"](lambda: namespace["exit_c"](29)) == 0
    with pytest.raises(_Exited):
        namespace["exit_c" if entry == "recursive" else entry](29)
    assert events == [29] and not memory.locked


def test_windows_start_returns_through_normal_exit():
    from pcc.frontends.python.codegen.platform_machine_abis import PLATFORM_MACHINE_ABIS
    from pcc.frontends.python.pipeline_freestanding import freestanding_module_scope_extern_bindings

    source = (ROOT / "pcc/runtime/py/freestanding_windows_start.py").read_text()
    bindings = {name: (parameters, result) for name, parameters, result in
                freestanding_module_scope_extern_bindings(source)}
    assert bindings["exit"] == ("(c_int32,)", "c_void")
    # Source-level admission intentionally uses canonical spellings. Win32
    # APIs/main retain their separately declared c_int contracts; exit uses
    # the exact owned C lifecycle contract already used by Linux startup.
    for name, signature in bindings.items():
        assert signature == PLATFORM_MACHINE_ABIS[name], name
    declaration = 'c_exit = extern("exit", (c_int32,), c_void)'
    assert source.count(declaration) == 1
    for rejected in ("c_int", "c_int64", "c_ptr"):
        wrong = source.replace(declaration, declaration.replace("c_int32", rejected))
        wrong_bindings = {name: (parameters, result) for name, parameters, result in
                          freestanding_module_scope_extern_bindings(wrong)}
        assert wrong_bindings["exit"] != PLATFORM_MACHINE_ABIS["exit"]

    memory = _RawMemory()
    events = []
    begin = memory.global_addr("__init_array_start")
    memory.symbols["__init_array_end"] = begin + 8
    memory.store(begin, 0, lambda: events.append("init"))

    def normal_exit(status):
        events.append(("exit", status))
        raise _Exited

    def immediate_exit(status):
        raise AssertionError("normal main return bypassed owned exit")

    namespace = _model(
        "freestanding_windows_start", ("pcc_windows_start",),
        **memory.bindings(), stack_alloc=memory.malloc, load_i32=lambda *args: 0,
        GetCommandLineW=lambda: 1, CommandLineToArgvW=lambda *args: 2,
        LocalFree=lambda pointer: None, initial_environ=lambda: 3,
        env_init=lambda env: 0, call_void_ptr0=lambda callback: callback(),
        main=lambda *args: events.append("main") or 37,
        c_exit=normal_exit, ExitProcess=immediate_exit,
    )
    with pytest.raises(_Exited):
        namespace["pcc_windows_start"]()
    assert events == ["init", "main", ("exit", 37)]


def test_windows_exit_exports_exact_owned_coff_abi(tmp_path):
    from pcc.backend.coff_x86_64 import CoffError, parse_object
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.pe_x86_64 import system_dll
    from pcc.frontends.python.owned_runtime_build import _compile_runtime_module, runtime_ir_passes
    from pcc.ir.optimization.driver import optimize_ir

    runtime = ROOT / "pcc/runtime"
    output = tmp_path / "windows.ll"
    _compile_runtime_module(MODULE, str(runtime / "py" / (MODULE + ".py")),
                            str(output), TARGET)
    text = optimize_ir(output.read_text(), runtime_ir_passes(str(runtime)))
    for signature in ("i32 @atexit(ptr ", "void @exit(i32 ",
                      "void @_Exit(i32 ", "void @_exit(i32 "):
        assert signature in text
    obj = parse_object(emit_owned_object(text, TARGET))
    definitions = {symbol.name for symbol in obj.symbols if symbol.external and symbol.section}
    undefined = {symbol.name for symbol in obj.symbols if symbol.external and not symbol.section}
    for name in ("atexit", "exit", "_Exit", "_exit", "pcc_c_run_exit_callbacks"):
        assert name in definitions and name not in undefined
        with pytest.raises(CoffError, match="has no named system DLL ABI"):
            system_dll(name)
    assert not re.search(r"call[^\n]*@(py_box_|py_int_)", text)

    # Exercise the consuming startup module as well as the provider. Its
    # compilation must pass the real closed-world verifier before emission.
    startup = "freestanding_windows_start"
    startup_output = tmp_path / "windows_start.ll"
    _compile_runtime_module(startup, str(runtime / "py" / (startup + ".py")),
                            str(startup_output), TARGET)
    startup_text = optimize_ir(startup_output.read_text(), runtime_ir_passes(str(runtime)))
    assert re.search(r"\bcall void(?:\s+\(i32\))?\s+@exit\(i32 ", startup_text)
    startup_object = parse_object(emit_owned_object(startup_text, TARGET))
    assert any(symbol.name == "pcc_windows_start" and symbol.external and symbol.section
               for symbol in startup_object.symbols)
    assert any(symbol.name == "exit" and symbol.external and not symbol.section
               for symbol in startup_object.symbols)


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=lambda: sys.platform == "win32" and platform.machine().lower() in ("amd64", "x86_64"))
@pytest.mark.parametrize("name,extra,status,stdout,contents", (
    ("exit_lifecycle", (), 37, "last\nlate\nfirst\n", b"MBCA"),
    ("exit_lifecycle", ("explicit",), 37, "last\nlate\nfirst\n", b"MBCA"),
    ("immediate_bypass", (), 29, "", b""),
    ("immediate_bypass", ("posix",), 29, "", b""),
    ("recursive", (), 29, "", None),
))
def test_windows_c_exit_lifecycle_executes(tmp_path, explicit_owned_runtime,
                                          name, extra, status, stdout, contents):
    from scripts.verify_nolibpython import linked_libraries
    from tests.c.test_owned_linux_c_exports import _units

    fixtures = ROOT / "tests/c/fixtures/owned_linux_c_exports"
    source = ("#include <stdlib.h>\n"
              "static void unexpected(void) { _Exit(99); }\n"
              "static void recursive(void) { exit(29); }\n"
              "int main(void) {\n"
              "  if (atexit(unexpected) || atexit(recursive)) return 98;\n"
              "  exit(37); return 97;\n"
              "}\n" if name == "recursive" else
              (fixtures / (name + ".c")).read_text())
    evaluator, units = _units(source, TARGET)
    executable, output = tmp_path / "exit.exe", tmp_path / "output"
    evaluator.emit_executable(units, str(executable))
    libraries = [name.lower() for name in linked_libraries(executable.read_bytes())]
    assert not any(forbidden in name for name in libraries
                   for forbidden in ("msvcrt", "ucrt", "python", "llvm")), libraries
    result = run_process_group_timeout(
        [str(executable), str(output), *extra], env=dict(os.environ), timeout=10,
    )
    (tmp_path / "execution.stdout").write_text(result.stdout)
    (tmp_path / "execution.stderr").write_text(result.stderr)
    assert (result.returncode, result.stdout, result.stderr) == (status, stdout, "")
    if contents is not None:
        assert output.read_bytes() == contents


@pytest.mark.integration
@pytest.mark.pcc_gate(probe=lambda: sys.platform == "win32" and platform.machine().lower() in ("amd64", "x86_64"))
def test_windows_tempdir_atexit_keeps_runtime_alive_five_collectors(
    tmp_path, explicit_owned_runtime, capfd,
):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "tempdir_exit.py"
    source.write_text('''
import os
import sys
import tempfile
from pcc.extern import c_int32, c_int64, c_void, extern

finish = extern("exit", (c_int32,), c_void)
collector = extern("pcc_gc_backend", (), c_int64)

def main():
    manager = tempfile.TemporaryDirectory(dir=sys.argv[1])
    assert os.path.isdir(manager.name)
    assert collector() == int(os.environ["PCC_GC_BACKEND"])
    print("TEMPDIR_ATEXIT_OK")
    # The manager is still live in this frame. Only the registered shutdown
    # callback can remove its directory before this direct C exit completes.
    finish(37)
    raise AssertionError("exit returned")

main()
''')
    executable = tmp_path / "tempdir_exit.exe"
    try:
        compile_python(
            str(source), str(executable), backend="self", libpython_mode="off",
            ir_scaffold_mode="on", runtime_archive=str(explicit_owned_runtime),
        )
    finally:
        captured = capfd.readouterr()
        (tmp_path / "compile.stdout").write_text(captured.out)
        (tmp_path / "compile.stderr").write_text(captured.err)
    executions = []
    for backend in range(5):
        root = tmp_path / ("gc" + str(backend))
        root.mkdir()
        result = run_process_group_timeout(
            [str(executable), str(root)],
            env=dict(os.environ, PCC_GC_BACKEND=str(backend)), timeout=10,
        )
        executions.append({"collector": backend, "returncode": result.returncode,
                           "stdout": result.stdout, "stderr": result.stderr,
                           "remaining": [path.name for path in root.iterdir()]})
        (tmp_path / "tempdir-executions.json").write_text(json.dumps(executions, indent=2) + "\n")
        assert (result.returncode, result.stdout, result.stderr) == (37, "TEMPDIR_ATEXIT_OK\n", "")
        assert executions[-1]["remaining"] == []
