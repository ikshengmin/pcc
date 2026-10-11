"""Tar metadata: fake-boundary models, separate object and Windows controls.

The model selectors import no compiler, runtime module or FFI. They execute
selected unchanged source definitions over explicit fake boundaries. They
are not native Windows evidence. Object/native selectors require separate
qualification and the native selector requires a source-matched archive.
"""

import ast
import os
from pathlib import Path
import platform
import re
import sys
from types import SimpleNamespace

import pytest

from tests.python.owned_regression_support import explicit_owned_runtime


ROOT = Path(__file__).resolve().parents[2]
WINDOWS = ROOT / "pcc/runtime/py/freestanding_windows.py"
TARFILE = ROOT / "pcc/stdlib/tarfile.py"
TARGET = "x86_64-pc-windows-msvc"


class _ExtractError(Exception):
    pass


def _definitions(path, names, bindings, class_name=None):
    tree = ast.parse(path.read_text())
    body = tree.body
    if class_name is not None:
        body = next(node.body for node in body
                    if isinstance(node, ast.ClassDef) and node.name == class_name)
    functions = [node for node in body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in functions} == set(names)
    namespace = {
        "i64": int, "c_ptr": object,
        "c_abi_export": lambda *args: lambda function: function,
        "c_abi_typed_export": lambda *args: lambda function: function,
        "ExtractError": _ExtractError, **bindings,
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


# Frozen baseline body from 81776ebc5d6ab80918321bd30e7f21ea7708283e.
# Do not regenerate this oracle from the candidate implementation.
_OLD_SET_ATTRS = '''
def old_set_attrs(self, member, target):
    if _chmod(_py_str_utf8(target), int(member.mode) & 0o777) != 0:
        raise ExtractError("could not apply tar member mode")
    times = malloc(16)
    if ptr_is_null(times):
        raise MemoryError("unable to allocate tar timestamp state")
    try:
        timestamp = int(member.mtime)
        store_i64(times, 0, timestamp)
        store_i64(times, 8, timestamp)
        if _utime(_py_str_utf8(target), times) != 0:
            raise ExtractError("could not apply tar member timestamp")
    finally:
        free(times)
'''


class _TarBoundaries:
    def __init__(self, mode_status=0, time_status=0, allocate=True, interpreted=False):
        self.events = []
        self.mode_status = mode_status
        self.time_status = time_status
        self.allocate = allocate
        self.interpreted = interpreted

    def path(self, target):
        self.events.append(("path", target))
        if self.interpreted:
            raise NotImplementedError("interpreted extern boundary")
        return target

    def chmod(self, target, mode):
        self.events.append(("chmod", target, mode))
        return self.mode_status

    def malloc(self, size):
        self.events.append(("malloc", size))
        return 101 if self.allocate else 0

    def utime(self, target, times):
        self.events.append(("utime", target, times))
        return self.time_status

    def bindings(self):
        return dict(
            _py_str_utf8=self.path, _chmod=self.chmod, chmod_file=self.chmod,
            malloc=self.malloc, ptr_is_null=lambda pointer: pointer == 0,
            store_i64=lambda *args: self.events.append(("store", *args)),
            _utime=self.utime, utime_file=self.utime, free=lambda pointer: self.events.append(("free", pointer)),
            ExtractError=_ExtractError,
        )

    def apply(self, member, target="folder/na\u00efve.txt", old=False):
        namespace = self.bindings()
        if old:
            exec(_OLD_SET_ATTRS, namespace)
            method = namespace["old_set_attrs"]
        else:
            method = _definitions(TARFILE, ("_set_attrs",), namespace, "TarFile")["_set_attrs"]
        try:
            method(None, member, target)
        except Exception as error:
            return type(error), str(error)
        return None


@pytest.mark.parametrize("mode_status,time_status,allocate,interpreted,mtime", (
    (0, 0, True, False, 123456789.9),
    (0, 0, True, False, -1.9),
    (0, 0, True, False, "12"),
    (0, 0, True, False, "bad"),
    (-13, 0, True, False, "bad"),
    (0, -1, True, False, 100),
    (0, 0, False, False, "bad"),
    (0, 0, True, True, 100),
))
def test_tar_attrs_model_preserves_frozen_order_errors_and_host_boundary(
    mode_status, time_status, allocate, interpreted, mtime,
):
    old = _TarBoundaries(mode_status, time_status, allocate, interpreted)
    current = _TarBoundaries(mode_status, time_status, allocate, interpreted)
    member = SimpleNamespace(mode=0o7777, mtime=mtime)
    assert current.apply(member) == old.apply(member, old=True)
    assert current.events == old.events
    if not interpreted:
        assert current.events[1] == ("chmod", "folder/na\u00efve.txt", 0o777)


@pytest.mark.parametrize("timestamp", (-(1 << 64), -(1 << 63) - 1, 1 << 63, 1 << 64, 1 << 100))
def test_tar_attrs_model_rejects_integer_wrapping_before_store(timestamp):
    boundary = _TarBoundaries()
    assert boundary.apply(SimpleNamespace(mode=0o644, mtime=timestamp)) == (
        _ExtractError, "could not apply tar member timestamp",
    )
    assert not any(event[0] in ("store", "utime") for event in boundary.events)
    assert boundary.events[-1] == ("free", 101)


@pytest.mark.parametrize("timestamp", (-(1 << 63), -1, 0, (1 << 63) - 1))
def test_tar_attrs_model_preserves_exact_i64_buffer(timestamp):
    boundary = _TarBoundaries()
    assert boundary.apply(SimpleNamespace(mode=0o644, mtime=timestamp)) is None
    assert [event for event in boundary.events if event[0] == "store"] == [
        ("store", 101, 0, timestamp), ("store", 101, 8, timestamp),
    ]


def test_tar_attrs_model_rejects_nul_before_any_metadata_mutation():
    boundary = _TarBoundaries()
    assert boundary.apply(SimpleNamespace(mode=0o644, mtime=0), "safe\0other") == (
        _ExtractError, "tar member target contains NUL",
    )
    assert boundary.events == []


def test_tar_attrs_model_close_failure_reports_committed_timestamp():
    boundary = _TarBoundaries(time_status=1)
    kind, message = boundary.apply(SimpleNamespace(mode=0o644, mtime=0))
    assert kind is _ExtractError
    assert message == "tar member timestamp was applied but its handle could not be closed"
    assert boundary.events[-1] == ("free", 101)


class _WindowsBoundaries:
    def __init__(self, access=0, write=0, wide=201, handle=301, set_ok=1, close_ok=1):
        self.cells = {101: access, 109: write}
        self.events = []
        self.wide, self.handle = wide, handle
        self.set_ok, self.close_ok = set_ok, close_ok
        self.last_error = 0
        self.errno = 987

    def utf16(self, path):
        self.events.append(("utf16", path))
        return self.wide

    def create(self, *args):
        self.events.append(("create", *args))
        self.last_error = 5
        return self.handle

    def free(self, pointer):
        self.events.append(("free", pointer))
        self.last_error = 112  # Cleanup deliberately clobbers GetLastError.

    def set_time(self, handle, creation, access, write):
        self.events.append(("set", handle, creation, access, write))
        self.last_error = 5
        return self.set_ok

    def close(self, handle):
        self.events.append(("close", handle))
        self.last_error = 6
        return self.close_ok

    def error(self):
        self.events.append(("error", self.last_error))
        return self.last_error

    def set_errno(self, value):
        self.events.append(("errno", value))
        self.errno = value

    def store(self, pointer, offset, value):
        self.cells[pointer + offset] = value & 0xffffffff

    def module(self):
        return _definitions(WINDOWS, ("error", "filetime_from_seconds", "utime_file"), dict(
            ptr_is_null=lambda pointer: pointer == 0, null=lambda: 0,
            ptr_to_int=lambda pointer: pointer, ptr_add=lambda pointer, offset: pointer + offset,
            load_i64=lambda pointer, offset: self.cells[pointer + offset],
            stack_alloc=lambda size: 401, store_i32=self.store,
            utf16=self.utf16, free=self.free, CreateFileW=self.create,
            SetFileTime=self.set_time, CloseHandle=self.close,
            GetLastError=self.error, pcc_errno_set=self.set_errno,
        ))

    def ticks(self, offset):
        return self.cells[401 + offset] | (self.cells[405 + offset] << 32)


@pytest.mark.parametrize("seconds,expected", (
    (-11644473601, -75), (-11644473600, -75), (-11644473599, 10000000),
    (-1, 116444735990000000), (0, 116444736000000000),
    (1, 116444736010000000), (910692730085, 9223372036850000000),
    (910692730086, -75), (-(1 << 63), -75), ((1 << 63) - 1, -75),
))
def test_windows_tar_time_model_exact_seconds_boundaries(seconds, expected):
    model = _WindowsBoundaries()
    assert model.module()["filetime_from_seconds"](seconds) == expected
    assert model.events == []


def test_windows_tar_time_model_ownership_flags_and_distinct_times():
    model = _WindowsBoundaries(access=-1, write=123456789)
    assert model.module()["utime_file"]("na\u00efve/\U0001f600.txt", 101) == 0
    assert model.events == [
        ("utf16", "na\u00efve/\U0001f600.txt"),
        ("create", 201, 256, 7, 0, 3, 33554432, 0),
        ("free", 201), ("set", 301, 0, 401, 409), ("close", 301),
    ]
    assert model.ticks(0) == 116444735990000000
    assert model.ticks(8) == 117679303890000000
    assert model.errno == 987


@pytest.mark.parametrize("field,seconds", (("access", -11644473600), ("write", -11644473601),
                                            ("access", 910692730086), ("write", (1 << 63) - 1)))
def test_windows_tar_time_model_range_failure_has_no_handle_or_allocation(field, seconds):
    model = _WindowsBoundaries(**{field: seconds})
    assert model.module()["utime_file"]("file", 101) == -1
    assert model.events == [("errno", 75)]


@pytest.mark.parametrize("path,times", ((0, 101), ("file", 0)))
def test_windows_tar_time_model_null_inputs_fail_closed(path, times):
    model = _WindowsBoundaries()
    assert model.module()["utime_file"](path, times) == -1
    assert model.events == [("errno", 22)]


@pytest.mark.parametrize("wide,handle,set_ok,close_ok,status,errno,closes", (
    (0, 301, 1, 1, -1, 22, 0),
    (201, -1, 1, 1, -1, 13, 0),
    (201, 301, 0, 1, -1, 13, 1),
    (201, 301, 0, 0, -1, 13, 1),
    (201, 301, 1, 0, 1, 9, 1),
))
def test_windows_tar_time_model_primary_error_and_cleanup_priority(
    wide, handle, set_ok, close_ok, status, errno, closes,
):
    model = _WindowsBoundaries(wide=wide, handle=handle, set_ok=set_ok, close_ok=close_ok)
    assert model.module()["utime_file"]("file", 101) == status
    assert model.errno == errno
    assert sum(event[0] == "close" for event in model.events) == closes
    assert [event for event in model.events if event[0] == "free"] == (
        [("free", 201)] if wide else []
    )
    if set_ok == 0:
        assert model.events.index(("error", 5)) < model.events.index(("close", 301))
    if handle == -1:
        assert model.events.index(("error", 5)) < model.events.index(("free", 201))


@pytest.mark.parametrize("fail", ("none", "size", "allocation", "conversion"))
def test_windows_tar_time_model_utf16_conversion_owns_one_buffer(fail):
    events = []
    path = "na\u00efve/\U0001f600.txt"
    expected = (path + "\0").encode("utf-16-le")

    def convert(codepage, flags, text, length, output, capacity):
        events.append(("convert", codepage, flags, text, length, output, capacity))
        assert (codepage, flags, text, length) == (65001, 8, path, -1)
        if (fail == "size" and output == 0) or (fail == "conversion" and output != 0):
            return 0
        if output:
            assert capacity == len(expected) // 2
        return len(expected) // 2

    def allocate(size):
        events.append(("malloc", size))
        return 0 if fail == "allocation" else 201

    namespace = _definitions(WINDOWS, ("utf16",), dict(
        ptr_is_null=lambda pointer: pointer == 0, null=lambda: 0,
        MultiByteToWideChar=convert, malloc=allocate,
        free=lambda pointer: events.append(("free", pointer)),
    ))
    assert namespace["utf16"](path) == (201 if fail == "none" else 0)
    assert sum(event[0] == "malloc" for event in events) == (0 if fail == "size" else 1)
    assert [event for event in events if event[0] == "free"] == (
        [("free", 201)] if fail == "conversion" else []
    )
    if fail != "size":
        assert ("malloc", len(expected)) in events


def test_tar_attrs_model_source_dispatch_is_finite_and_owned():
    def assignment(path, name):
        tree = ast.parse((ROOT / path).read_text())
        return next(node.value for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == name
                            for target in node.targets))

    signatures = ast.literal_eval(assignment(
        "pcc/frontends/python/codegen/windows_platform.py", "_SIGNATURES",
    ))
    assert signatures["chmod_file"] == ("i", "pi")
    assert signatures["utime_file"] == ("i", "pp")
    tree = ast.parse(TARFILE.read_text())
    imported = {item.name for node in tree.body if isinstance(node, ast.ImportFrom)
                and node.module == "pcc.unsafe" for item in node.names}
    assert {"chmod_file", "utime_file"} <= imported
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == "extern" and node.args
                   and isinstance(node.args[0], ast.Constant)
                   and node.args[0].value in ("chmod", "utime") for node in ast.walk(tree))
    machine_abis = ast.literal_eval(assignment(
        "pcc/frontends/python/codegen/platform_machine_abis.py", "PLATFORM_MACHINE_ABIS",
    ))
    assert machine_abis["SetFileTime"] == ("(c_ptr,c_ptr,c_ptr,c_ptr)", "c_int")
    windows_tree = ast.parse(WINDOWS.read_text())
    exports = {decorator.args[0].value for node in windows_tree.body
               if isinstance(node, ast.FunctionDef) for decorator in node.decorator_list
               if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name)
               and decorator.func.id in ("c_abi_export", "c_abi_typed_export")}
    assert "pcc_win_utime_file" in exports
    assert not {"utime", "chmod"} & exports


@pytest.mark.parametrize("target", (
    "x86_64-pc-windows-msvc", "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu", "arm64-apple-darwin",
))
def test_tar_attrs_owned_platform_route_ir(tmp_path, target):
    """Cross-target compiler evidence, with no native or foreign execution."""
    from pcc.frontends.python import pipeline

    source = tmp_path / "tar_attribute_route.py"
    source.write_text('''
from pcc import i64
from pcc.extern import c_abi_export, c_ptr
from pcc.unsafe import chmod_file, utime_file
__pcc_freestanding__ = True
@c_abi_export("tar_attribute_route")
def apply(path: c_ptr, times: c_ptr) -> i64:
    if chmod_file(path, 420) != 0:
        return -1
    return utime_file(path, times)
''')
    output = tmp_path / "route.ll"
    pipeline.compile_python(str(source), str(output), emit_llvm_only=True,
                            libpython_mode="off", python_library=True, target_triple=target)
    text = output.read_text()
    if "windows" in target:
        assert "@pcc_win_chmod_file(" in text and "@pcc_win_utime_file(" in text
        assert "@chmod(" not in text and "@utime(" not in text
    else:
        assert "@utime(" in text
        assert re.search(r"\bcall i32\b[^\n]*@utime\(", text)
        assert re.search(r"\bsext i32\b[^\n]*to i64", text)
        assert "@pcc_win_chmod_file(" not in text and "@pcc_win_utime_file(" not in text
        assert ("@chmod(" in text) == ("darwin" in target)


def test_windows_tar_attrs_owned_coff_abi_and_static_mode_route(tmp_path):
    """Compiler/object evidence only; does not execute Windows or a native binary."""
    from pcc.backend.coff_x86_64 import CoffError, parse_object
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.pe_x86_64 import system_dll
    from pcc.frontends.python import pipeline
    from pcc.frontends.python.owned_runtime_build import _compile_runtime_module, runtime_ir_passes
    from pcc.ir.optimization.driver import optimize_ir

    runtime = ROOT / "pcc/runtime"
    output = tmp_path / "windows.ll"
    _compile_runtime_module("freestanding_windows", str(WINDOWS), str(output), TARGET)
    text = optimize_ir(output.read_text(), runtime_ir_passes(str(runtime)))
    assert "define external i64 @pcc_win_utime_file(ptr " in text
    obj = parse_object(emit_owned_object(text, TARGET))
    definitions = {symbol.name for symbol in obj.symbols if symbol.external and symbol.section}
    undefined = {symbol.name for symbol in obj.symbols if symbol.external and not symbol.section}
    assert {"pcc_win_utime_file", "pcc_win_filetime_from_seconds", "pcc_win_chmod_file"} <= definitions
    assert "SetFileTime" in undefined
    assert "utime" not in undefined and "chmod" not in undefined
    assert system_dll("SetFileTime") == "KERNEL32.dll"
    for symbol in ("chmod", "utime", "_utime", "_utime64"):
        with pytest.raises(CoffError, match="has no named system DLL ABI"):
            system_dll(symbol)

    # Compile the real changed managed consumer, not merely the provider.
    consumer = tmp_path / "tarfile.ll"
    pipeline.compile_python(str(TARFILE), str(consumer), emit_llvm_only=True,
                            libpython_mode="off", python_library=True, target_triple=TARGET)
    consumer_text = consumer.read_text()
    assert "@pcc_win_chmod_file(" in consumer_text
    assert "@pcc_win_utime_file(" in consumer_text
    assert "@utime(" not in consumer_text
    assert "@chmod(" not in consumer_text
    assert "@_utime" not in consumer_text


@pytest.mark.integration
@pytest.mark.parametrize("import_style", ("module", "static"))
@pytest.mark.pcc_gate(probe=lambda: sys.platform == "win32" and platform.machine().lower() in ("amd64", "x86_64"))
def test_windows_tar_attrs_native_files_directories_unicode_and_negative_epoch(
    tmp_path, explicit_owned_runtime, capfd, import_style,
):
    """Normal Windows CI only: run the actual tarfile consumer with owned runtime."""
    import io
    import stat
    import tarfile
    from pcc.frontends.python.pipeline import compile_python
    from scripts.verify_nolibpython import linked_libraries
    from tests.python.process_timeout import run_process_group_timeout

    source_tar = tmp_path / "metadata.tar"
    destination = tmp_path / "extracted"
    entries = (
        ("na\u00efve-\U0001f600.txt", 0o640, -1),
        ("readonly.txt", 0o444, 0),
        ("directory", 0o755, 123456789),
    )
    with tarfile.open(source_tar, "w", format=tarfile.PAX_FORMAT) as archive:
        for name, mode, seconds in entries:
            member = tarfile.TarInfo(name)
            member.mode, member.mtime = mode, seconds
            member.type = tarfile.DIRTYPE if name == "directory" else tarfile.REGTYPE
            payload = b"metadata fixture"
            member.size = 0 if member.isdir() else len(payload)
            archive.addfile(member, None if member.isdir() else io.BytesIO(payload))
    # The host oracle uses the same filesystem, including its time resolution.
    # This is not a claim that FAT/exFAT can store arbitrary exact FILETIMEs.
    oracle_directory = tmp_path / "oracle"
    oracle_directory.mkdir()
    expected = {}
    for name, mode, seconds in entries:
        oracle = oracle_directory / name
        if name == "directory":
            oracle.mkdir()
        else:
            oracle.write_bytes(b"metadata fixture")
        os.chmod(oracle, mode)
        try:
            os.utime(oracle, (seconds, seconds))
        except OSError as error:
            pytest.fail("Windows timestamp fixture requires filesystem support for "
                        f"Unix second {seconds}: {error}")
        expected[name] = oracle.stat()
    source = tmp_path / "metadata.py"
    imports = "import tarfile\n\n" if import_style == "module" else "from tarfile import open as tar_open\n\n"
    opener = "tarfile.open" if import_style == "module" else "tar_open"
    source.write_text(imports +
                      "def main():\n"
                      f"    with {opener}({str(source_tar)!r}) as archive:\n"
                      f"        archive.extractall({str(destination)!r})\n"
                      "    print('TAR_METADATA_OK')\n\nmain()\n", encoding="utf-8")
    executable = tmp_path / "metadata.exe"
    compile_python(str(source), str(executable), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(explicit_owned_runtime))
    captured = capfd.readouterr()
    (tmp_path / "compiler.stdout").write_text(captured.out)
    (tmp_path / "compiler.stderr").write_text(captured.err)
    libraries = [name.lower() for name in linked_libraries(executable.read_bytes())]
    assert not any(bad in library for library in libraries
                   for bad in ("msvcrt", "ucrt", "python", "llvm")), libraries
    result = run_process_group_timeout([str(executable)], env=dict(os.environ), timeout=30)
    (tmp_path / "execution.stdout").write_text(result.stdout)
    (tmp_path / "execution.stderr").write_text(result.stderr)
    assert (result.returncode, result.stdout, result.stderr) == (0, "TAR_METADATA_OK\n", "")
    for name, mode, seconds in entries:
        actual = (destination / name).stat()
        assert actual.st_mtime_ns == expected[name].st_mtime_ns
        assert actual.st_atime_ns == expected[name].st_atime_ns
        assert bool(actual.st_mode & stat.S_IWRITE) == bool(mode & stat.S_IWRITE)
