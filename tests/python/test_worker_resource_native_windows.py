"""Actual native RSS source against inert WinAPI/memory, plus bounded IR.

The deterministic API model checks the owned protocol, not Windows filesystem
semantics. No runtime, executable, native probe or real WinAPI is run here.
"""

import ast
import builtins
import ntpath
from pathlib import Path
import re
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
WINDOWS = ROOT / "pcc/runtime/py/freestanding_windows_rss.py"
PLATFORM = ROOT / "pcc/runtime/py/freestanding_platform_rss.py"
ERRORS = ROOT / "pcc/runtime/py/freestanding_windows.py"
POLICY = ROOT / "pcc/frontends/python/worker_resource_plan.py"


def _definitions(path, names, namespace):
    tree = ast.parse(path.read_text())
    body = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            body.append(node)
        elif isinstance(node, ast.Assign) and all(
            isinstance(target, ast.Name) and target.id.startswith("_RENAME_")
            for target in node.targets
        ):
            body.append(node)
    assert {node.name for node in body if isinstance(node, ast.FunctionDef)} == set(names)
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), "exec"), namespace)


class Model:
    """Bounds-checked buffers and clobbering LastError, no native operations."""
    def __init__(self, *, open_error=0, rename_error=0, close_error=0,
                 full_failure=0, full_growth=0, query_size=0, malloc_failure=0,
                 utf16_failure=0):
        self.memory, self.heap, self.freed = {}, set(), set()
        self.events = []
        self.next_address = 4096
        self.last_error = 0
        self.open_error, self.rename_error, self.close_error = open_error, rename_error, close_error
        self.full_failure, self.full_growth, self.query_size = full_failure, full_growth, query_size
        self.malloc_failure, self.utf16_failure = malloc_failure, utf16_failure
        self.malloc_count = self.utf16_count = self.full_count = 0
        self.committed = False
        self.handle = 0x123456789AB
        self.namespace = {
            "i64": int, "c_ptr": int, "malloc": self.malloc, "free": self.free,
            "stack_alloc": lambda size: self.allocate(size, heap=False),
            "null": lambda: 0, "ptr_add": lambda address, offset: address + offset,
            "ptr_is_null": lambda address: address == 0, "ptr_to_int": int,
            "load_i8": lambda address, offset: self.read(address + offset, 1)[0],
            "load_ptr": lambda address, offset: int.from_bytes(self.read(address + offset, 8), "little"),
            "store_ptr": lambda address, offset, value: self.write(address + offset, value.to_bytes(8, "little")),
            "store_i32": lambda address, offset, value: self.write(address + offset, value.to_bytes(4, "little")),
            "memset": lambda address, value, size: self.write(address, bytes([value]) * size),
            "memcpy": lambda destination, source, size: self.write(destination, self.read(source, size)),
            "utf16": self.utf16, "GetFullPathNameW": self.full_path,
            "CreateFileW": self.open, "SetFileInformationByHandle": self.rename,
            "CloseHandle": self.close, "GetLastError": self.get_last_error,
        }
        _definitions(ERRORS, {"error", "error_code"}, self.namespace)
        _definitions(WINDOWS, {"_worker_resource_full_path", "pcc_worker_resource_replace"}, self.namespace)

    def allocate(self, size, *, heap=True):
        assert 0 < size <= 65560
        address = self.next_address
        self.next_address += size + 16
        self.memory[address] = bytearray(size)
        if heap:
            self.heap.add(address)
        return address

    def region(self, address, size):
        for base, buffer in self.memory.items():
            if base <= address and address + size <= base + len(buffer):
                assert base not in self.freed, "use after free"
                return buffer, address - base
        raise AssertionError(("out of bounds", address, size))

    def read(self, address, size):
        buffer, offset = self.region(address, size)
        return bytes(buffer[offset:offset + size])

    def write(self, address, data):
        buffer, offset = self.region(address, len(data))
        buffer[offset:offset + len(data)] = data

    def wide_text(self, address):
        size = 0
        while self.read(address + size, 2) != b"\0\0":
            size += 2
        return self.read(address, size).decode("utf-16-le")

    def malloc(self, size):
        self.malloc_count += 1
        if self.malloc_count == self.malloc_failure:
            return 0
        return self.allocate(size)

    def free(self, address):
        assert address in self.heap and address not in self.freed
        self.freed.add(address)
        self.events.append(("free", address))
        self.last_error = 112  # A cleanup must never replace the primary error.

    def utf16(self, text):
        self.utf16_count += 1
        if self.utf16_count == self.utf16_failure:
            return 0
        encoded = text.encode("utf-16-le") + b"\0\0"
        address = self.allocate(len(encoded))
        self.write(address, encoded)
        return address

    def full_path(self, address, capacity, output, file_part):
        assert file_part == 0
        self.full_count += 1
        self.events.append(("full", self.full_count, capacity))
        if self.full_count == self.full_failure:
            self.last_error = 5
            return 0
        absolute = ntpath.normpath(ntpath.join("C:\\work", self.wide_text(address)))
        encoded = absolute.encode("utf-16-le") + b"\0\0"
        if capacity == 0:
            assert output == 0
            return self.query_size or len(encoded) // 2
        if self.full_count == self.full_growth:
            return capacity + 1  # Deliberately leave output uninitialized.
        assert capacity * 2 >= len(encoded)
        self.write(output, encoded)
        return len(encoded) // 2 - 1

    def open(self, path, access, share, security, disposition, flags, template):
        assert (access, share, security, disposition, flags, template) == (65536, 7, 0, 3, 128, 0)
        self.events.append(("open", self.wide_text(path)))
        self.last_error = self.open_error
        return -1 if self.open_error else self.handle

    def rename(self, handle, information_class, information, size):
        assert handle == self.handle and information_class == 22
        buffer = self.read(information, size)
        assert int.from_bytes(buffer[:4], "little") == 3
        assert buffer[4:16] == b"\0" * 12
        name_size = int.from_bytes(buffer[16:20], "little")
        assert size == 24 + name_size and name_size % 2 == 0
        assert buffer[20 + name_size:] == b"\0" * 4
        destination = buffer[20:20 + name_size].decode("utf-16-le")
        self.events.append(("rename", destination, name_size))
        self.last_error = self.rename_error
        if self.rename_error:
            return 0
        self.committed = True
        return 1

    def close(self, handle):
        assert handle == self.handle
        self.events.append(("close", handle))
        self.last_error = self.close_error or 87
        return 0 if self.close_error else 1

    def get_last_error(self):
        self.events.append(("error", self.last_error))
        return self.last_error

    def run(self, source="worker.rss.tmp", destination="worker.rss"):
        status = self.namespace["pcc_worker_resource_replace"](source, destination)
        assert self.heap == self.freed, "raw heap leak"
        opened = bool([row for row in self.events if row[0] == "open"]) and not self.open_error
        assert len([row for row in self.events if row[0] == "close"]) == int(opened)
        return status


@pytest.mark.parametrize("name", ["worker.rss", "報告.rss", "report-😀.rss"])
def test_native_windows_report_uses_exact_extended_rename_layout(name):
    model = Model()
    assert model.run("sub/../" + name + ".tmp", name) == 0
    assert model.committed
    absolute = "C:\\work\\" + name
    assert ("open", absolute + ".tmp") in model.events
    assert ("rename", absolute, len(absolute.encode("utf-16-le"))) in model.events
    assert not [event for event in model.events if event[0] == "error"]


@pytest.mark.parametrize("source", ["other.rss.tmp", "other/worker.rss.tmp", "worker.rss", "worker.rss.tmpx"])
def test_native_windows_rejects_non_report_source_before_open(source):
    model = Model()
    assert model.run(source) == -22
    assert not model.committed and not any(event[0] == "open" for event in model.events)


@pytest.mark.parametrize("site", ["open", "rename"])
@pytest.mark.parametrize("code,expected", [(2, 2), (5, 13), (32, 13), (50, 5), (87, 22), (4395, 40)])
def test_native_windows_primary_error_survives_clobbering_cleanup(site, code, expected):
    model = Model(**{site + "_error": code})
    assert model.run() == -expected
    assert not model.committed
    assert [event for event in model.events if event[0] == "error"] == [("error", code)]
    if site == "open":
        assert not any(event[0] == "rename" for event in model.events)


@pytest.mark.parametrize("rename_error,primary", [(0, 0), (5, 13), (50, 5)])
@pytest.mark.parametrize("close_error,cleanup", [(6, 9), (5, 13)])
def test_native_windows_close_failure_carries_commit_and_both_errors(rename_error, primary, close_error, cleanup):
    model = Model(rename_error=rename_error, close_error=close_error)
    assert model.run() == primary * 65536 + cleanup
    assert model.committed is (primary == 0)
    expected = ([] if not rename_error else [("error", rename_error)]) + [("error", close_error)]
    assert [event for event in model.events if event[0] == "error"] == expected


@pytest.mark.parametrize("options,expected", [
    ({"full_failure": 1}, -13), ({"full_failure": 2}, -13),
    ({"full_failure": 3}, -13), ({"full_failure": 4}, -13),
    ({"full_growth": 2}, -36), ({"full_growth": 4}, -36),
    ({"query_size": 32769}, -36), ({"query_size": 2147483647}, -36),
    ({"malloc_failure": 1}, -12), ({"malloc_failure": 2}, -12),
    ({"malloc_failure": 3}, -12), ({"utf16_failure": 1}, -22),
    ({"utf16_failure": 2}, -22),
])
def test_native_windows_setup_failure_never_opens_or_leaks(options, expected):
    model = Model(**options)
    assert model.run() == expected
    assert not any(event[0] in ("open", "rename", "close") for event in model.events)


def test_native_windows_real_error_mapping_fits_status_protocol():
    model = Model()
    function = next(node for node in ast.parse(ERRORS.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == "error_code")
    codes = {node.value for node in ast.walk(function)
             if isinstance(node, ast.Constant) and type(node.value) is int}
    for code in codes | {0, 1, 50, 65535, 0x7fffffff, -1}:
        assert 0 < -model.namespace["error_code"](code) < 65536


@pytest.mark.parametrize("status,committed,primary,cleanup", [
    (9, True, 0, 9), (13 * 65536 + 9, False, 13, 9), (5 * 65536 + 13, False, 5, 13),
])
def test_native_dispatch_cleanup_failure_is_nonretryable(monkeypatch, status, committed, primary, cleanup):
    namespace = {}
    _definitions(POLICY, {"_replace_worker_resource"}, namespace)
    original = builtins.__import__
    types = SimpleNamespace(c_int64=object(), c_obj=object(), c_str=object())
    def declare(symbol, arguments, result):
        assert symbol == "pcc_worker_resource_replace"
        assert arguments == (types.c_str, types.c_str) and result is types.c_int64
        return lambda source, destination: status
    types.extern = declare
    def importing(name, *args, **kwargs):
        if name == "sys":
            return SimpleNamespace(implementation=SimpleNamespace(name="pcc"), platform="win32")
        if name == "pcc.extern":
            return types
        return original(name, *args, **kwargs)
    with monkeypatch.context() as selected:
        selected.setattr(builtins, "__import__", importing)
        with pytest.raises(RuntimeError) as caught:
            namespace["_replace_worker_resource"]("worker.rss.tmp", "worker.rss")
    assert not isinstance(caught.value, PermissionError)
    assert str(caught.value) == (
        "worker RSS handle cleanup failed: publication_committed=" + str(committed)
        + " primary_errno=" + str(primary) + " handle_cleanup_errno=" + str(cleanup))
    function = next(node for node in ast.parse(POLICY.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == "_replace_worker_resource")
    assert not any(isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store)
                   for node in ast.walk(function))


def test_nonwindows_stub_is_explicit_and_original_rss_owners_are_target_exclusive():
    from pcc.frontends.python.owned_runtime_build import runtime_modules
    from pcc.frontends.python.codegen.runtime_abi import RUNTIME_SIGNATURES, _I64, _CSTR
    assert RUNTIME_SIGNATURES["pcc_worker_resource_replace"] == (_I64, [_CSTR, _CSTR], False)
    assert "int64_t   pcc_worker_resource_replace(const char *source, const char *destination);" in (
        ROOT / "pcc/runtime/include/py_runtime.h").read_text()
    namespace = {"i64": int, "c_ptr": int}
    _definitions(PLATFORM, {"pcc_worker_resource_replace"}, namespace)
    assert namespace["pcc_worker_resource_replace"](0, 0) == -38
    for target in ("x86_64-pc-windows-msvc", "aarch64-unknown-linux-gnu", "arm64-apple-darwin"):
        names = runtime_modules(str(ROOT / "pcc/runtime"), target)
        assert ("freestanding_windows_rss" in names) is ("windows" in target)
        assert ("freestanding_platform_rss" in names) is ("windows" not in target)


def _body(text, symbol):
    match = re.search(r'^define[^\n]*@"?' + re.escape(symbol) + r'"?\([^\n]*\).*?^}', text, re.M | re.S)
    assert match, symbol
    return match.group(0)


@pytest.mark.parametrize("target", [
    "x86_64-pc-windows-msvc", "aarch64-unknown-linux-gnu", "arm64-apple-darwin",
])
def test_rss_replace_runtime_module_has_verified_raw_ir(tmp_path, monkeypatch, target):
    from pcc.frontends.python.owned_runtime_build import _compile_runtime_module, runtime_ir_passes
    from pcc.ir.optimization.driver import optimize_ir
    from tests.owned_ir_validation import verify_ir_text
    monkeypatch.setenv("PCC_WITH_THREADS", "0")
    monkeypatch.setenv("PCC_REFCOUNT_KIND", "atomic")
    source = WINDOWS if "windows" in target else PLATFORM
    output = tmp_path / "rss.ll"
    _compile_runtime_module(source.stem, str(source), str(output), target)
    raw = output.read_text()
    for text in (raw, optimize_ir(raw, runtime_ir_passes(str(ROOT / "pcc/runtime")))):
        parsed = verify_ir_text(text)
        expected = {"pcc_os_current_rss_bytes", "pcc_os_peak_rss_bytes", "pcc_worker_resource_replace"}
        expected |= ({"pcc_windows_rss_bytes", "pcc_windows_worker_resource_full_path"}
                     if "windows" in target else {"pcc_platform_rss_linux_proc_status_bytes"})
        assert {function.name for function in parsed.functions} == expected
        body = _body(text, "pcc_worker_resource_replace")
        assert re.search(r"^define external i64 @pcc_worker_resource_replace\(ptr \S+, ptr \S+\)", body)
        assert not re.search(r"\bcall[^\n]*@(?:pcc_gc_|py_)", text)
        if "windows" in target:
            call = re.search(r"@SetFileInformationByHandle\(ptr [^,]+, i32 ([^,]+),", body)
            assert call
            information_class = call.group(1)
            assert information_class == "22" or re.search(
                re.escape(information_class) + r" = trunc i64 22 to i32", body)
            assert "@CloseHandle(" in body and "@CreateFileW(" in body
            assert "@MoveFileEx" not in text and "@SetFileAttributes" not in text
        else:
            assert "ret i64 -38" in body
            assert "@SetFileInformationByHandle" not in text


@pytest.mark.parametrize("target", [
    "x86_64-pc-windows-msvc", "aarch64-unknown-linux-gnu", "arm64-apple-darwin",
])
def test_actual_dispatch_ir_uses_counted_string_leases_and_original_posix_call(tmp_path, monkeypatch, target):
    from pcc.frontends.python.pipeline import compile_python
    from tests.owned_ir_validation import verify_ir_text
    monkeypatch.setenv("PCC_WITH_THREADS", "0")
    dispatch = next(node for node in ast.parse(POLICY.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == "_replace_worker_resource")
    source = tmp_path / "rss_dispatch.py"
    source.write_text("import os\n" + ast.unparse(dispatch) + "\n")
    output = source.with_suffix(".ll")
    compile_python(str(source), str(output), emit_llvm_only=True, python_library=True,
                   libpython_mode="off", backend="self", target_triple=target)
    text = output.read_text()
    verify_ir_text(text)
    body = _body(text, "user_rss_dispatch__replace_worker_resource")
    # sys.platform remains a runtime condition here, so both call sites must
    # exist on every target. The dispatcher model separately verifies selection.
    assert "@py_os_replace(" in body
    foreign = body.index("@pcc_worker_resource_replace(")
    projection = body.index("@py_str_utf8(")
    leases = list(re.finditer(
        r"(%extern\.lease\.acquire[\w.]*) = call i64 \(ptr\) @pcc_gc_foreign_lease_acquire\(",
        body[:foreign]))
    assert len(leases) == 2 and max(match.start() for match in leases) < projection < foreign
    assert body[:foreign].count("@py_str_utf8(") == 2
    for lease in leases:
        assert re.search(r"@pcc_gc_foreign_lease_release\([^\n]*i64 "
                         + re.escape(lease.group(1)) + r"\)", body[foreign:])
    call = re.search(r"@pcc_worker_resource_replace\(ptr (%[\w.]+), ptr (%[\w.]+)\)", body)
    assert call
    for value in call.groups():
        assert re.search(re.escape(value) + r" = call ptr \(ptr\) @py_str_utf8\(", body[:foreign])
    assert "extern.lease.acquire.error" in body and "extern.lease.cleanup.error" in body
    assert "@py_obj_setattr" not in body
    assert "worker_resource_host_windows" not in text
