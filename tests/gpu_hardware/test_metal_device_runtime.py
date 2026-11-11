"""Gates for the Metal device runtime written in freestanding pcc-Python.

``pcc/gpu_runtime/metal_device.py`` reaches Metal through the Objective-C
runtime with typed C ABI calls. Every executable here is produced by the pcc
frontend, the self backend, the owned object writer and the owned Mach-O
linker. No Objective-C source, external compiler, ``ctypes`` adapter or
prebuilt bridge takes part.

Host Python runs the compiler, so these are host-pcc0 results. They are not
pcc1-native or five-GC evidence, and a synchronous launch is not a fence
lifetime proof.

A machine without a Metal device reports that in band. The report is accepted
by default and rejected when ``PCC_GPU_HARDWARE_STRICT=1``.
"""

from __future__ import annotations

import ast
import os
import struct
import subprocess
from pathlib import Path

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.macho_spec import parse_object
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.frontends.python import pipeline
from pcc.gpu_runtime import metal_device as abi
from pcc.kernel_ir.ir import (
    BufferParam,
    KernelFunc,
    KernelModule,
    KernelOp,
    MemoryScope,
    ScalarParam,
    ScalarType,
)
from pcc.kernel_ir.metal_finalize import emit_metal_source


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SOURCE = ROOT / "pcc" / "gpu_runtime" / "metal_device.py"
TARGET = "arm64-apple-darwin"

_IS_ARM64_DARWIN = (
    hasattr(os, "uname")
    and os.uname().sysname == "Darwin"
    and os.uname().machine == "arm64"
)
_needs_arm64_darwin = pytest.mark.pcc_gate(
    unavailable=None if _IS_ARM64_DARWIN else "needs Darwin arm64"
)

REPORT_WORDS = 12
REPORT_BYTES = REPORT_WORDS * 8
DIAGNOSTIC_BYTES = 512
NAME_BYTES = 128

REPORT_OPEN = 0
REPORT_PIPELINE = 1
REPORT_LIMITS = 2
REPORT_MAX_THREADS = 3
REPORT_WIDTH = 4
REPORT_SOURCE_BUFFER = 5
REPORT_TARGET_BUFFER = 6
REPORT_TARGET_LENGTH = 7
REPORT_DISPATCH = 8
REPORT_NAME_LENGTH = 9
REPORT_EXTRA_A = 10
REPORT_EXTRA_B = 11

_ALL_STATUSES = (
    abi.STATUS_BAD_ARGUMENT,
    abi.STATUS_OUT_OF_MEMORY,
    abi.STATUS_OBJC_RUNTIME_UNAVAILABLE,
    abi.STATUS_METAL_FRAMEWORK_UNAVAILABLE,
    abi.STATUS_SYMBOL_UNAVAILABLE,
    abi.STATUS_DEVICE_UNAVAILABLE,
    abi.STATUS_QUEUE_UNAVAILABLE,
    abi.STATUS_SOURCE_REJECTED,
    abi.STATUS_FUNCTION_NOT_FOUND,
    abi.STATUS_PIPELINE_REJECTED,
    abi.STATUS_BUFFER_UNAVAILABLE,
    abi.STATUS_BAD_BINDING,
    abi.STATUS_DISPATCH_EXCEEDS_PIPELINE,
    abi.STATUS_ENCODER_UNAVAILABLE,
    abi.STATUS_COMMAND_FAILED,
)
_UNAVAILABLE_STATUSES = (
    abi.STATUS_OBJC_RUNTIME_UNAVAILABLE,
    abi.STATUS_METAL_FRAMEWORK_UNAVAILABLE,
    abi.STATUS_SYMBOL_UNAVAILABLE,
    abi.STATUS_DEVICE_UNAVAILABLE,
    abi.STATUS_QUEUE_UNAVAILABLE,
)

PROBE_SOURCE = """#include <metal_stdlib>
using namespace metal;
kernel void pcc_probe(const device uint *src [[buffer(0)]],
                      device uint *dst [[buffer(1)]],
                      constant uint &count [[buffer(2)]],
                      uint gid [[thread_position_in_grid]]) {
    if (gid >= count) { return; }
    dst[gid] = src[gid] * 3u + 1u;
}
"""

# The runtime is called from a second freestanding object through typed
# indirect calls: (driver name, exported suffix, result type, argument types).
_RUNTIME_CALLS = (
    ("device_open", "open", "i64", ("ptr",)),
    ("device_close", "close", "i64", ("ptr",)),
    ("device_name", "name", "i64", ("ptr", "ptr", "i64")),
    ("buffer_create", "buffer_create", "i64", ("ptr", "i64", "ptr")),
    ("buffer_contents", "buffer_contents", "ptr", ("ptr", "ptr")),
    ("buffer_length", "buffer_length", "i64", ("ptr", "ptr")),
    ("buffer_release", "buffer_release", "i64", ("ptr", "ptr")),
    (
        "pipeline_create", "pipeline_create", "i64",
        ("ptr", "ptr", "ptr", "ptr", "ptr", "i64"),
    ),
    ("pipeline_limits", "pipeline_limits", "i64", ("ptr", "ptr", "ptr")),
    ("pipeline_release", "pipeline_release", "i64", ("ptr", "ptr")),
    (
        "dispatch_sync", "dispatch_sync", "i64",
        ("ptr", "ptr", "ptr", "i64", "ptr", "ptr"),
    ),
)


def _runtime_call_wrappers() -> str:
    out = []
    for local, suffix, restype, argtypes in _RUNTIME_CALLS:
        names = [f"a{index}" for index in range(len(argtypes))]
        params = ", ".join(
            name + (": i64" if kind == "i64" else "")
            for name, kind in zip(names, argtypes)
        )
        types = "".join(repr(kind) + ", " for kind in argtypes)
        values = "".join(name + ", " for name in names)
        result = " -> i64" if restype == "i64" else ""
        load = "load_i64" if restype == "i64" else "load_ptr"
        out.append(
            f'@c_abi_export("pcc_test_{local}")\n'
            f"def {local}({params}){result}:\n"
            f"    out = stack_alloc(8)\n"
            f"    call_c_abi(\n"
            f'        raw_address(global_addr("pcc_metal_device_{suffix}")),\n'
            f'        "{restype}",\n'
            f"        ({types}), ({values}), out,\n"
            f"    )\n"
            f"    return {load}(out, 0)\n"
        )
    return "\n\n".join(out)


DRIVER_HEADER = '''from pcc import i64
from pcc.extern import c_abi_export
from pcc.unsafe import (
    call_c_abi,
    calloc,
    cstr,
    darwin_libsystem_symbol,
    global_addr,
    load_i64,
    load_ptr,
    null,
    ptr_is_null,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
)

__pcc_freestanding__ = True


@c_abi_export("pcc_test_raw_address")
def raw_address(value):
    """Keep a runtime entry an address so the call stays indirect."""
    return value


@RUNTIME_CALLS@

@c_abi_export("pcc_test_emit")
def emit(data, nbytes: i64) -> i64:
    write = darwin_libsystem_symbol(cstr("write"))
    out = stack_alloc(8)
    call_c_abi(write, "i64", ("i32", "ptr", "i64"), (1, data, nbytes), out)
    return load_i64(out, 0)


@c_abi_export("main")
def main() -> i64:
    report = calloc(@REPORT_WORDS@, 8)
    context_slot = stack_alloc(8)
    status: i64 = device_open(context_slot)
    store_i64(report, @R_OPEN@, status)
    if status != 0:
        emit(report, @REPORT_BYTES@)
        return 0
    context = load_ptr(context_slot, 0)
'''

PIPELINE_STEP = '''
    pipeline_slot = stack_alloc(8)
    diagnostic = calloc(1, @DIAGNOSTIC_BYTES@)
    status = pipeline_create(
        context, cstr(@SOURCE@), cstr(@FUNCTION@), pipeline_slot,
        diagnostic, @DIAGNOSTIC_BYTES@,
    )
    store_i64(report, @R_PIPELINE@, status)
    if status != 0:
        emit(report, @REPORT_BYTES@)
        emit(diagnostic, @DIAGNOSTIC_BYTES@)
        return 0
    pipeline = load_ptr(pipeline_slot, 0)
    limits = calloc(1, @LIMITS_BYTES@)
    store_i64(report, @R_LIMITS@, pipeline_limits(context, pipeline, limits))
    max_threads: i64 = load_i64(limits, @LIMIT_MAX_THREADS@)
    width: i64 = load_i64(limits, @LIMIT_EXECUTION_WIDTH@)
    store_i64(report, @R_MAX_THREADS@, max_threads)
    store_i64(report, @R_WIDTH@, width)
'''

BUFFER_STEP = '''
    count: i64 = @COUNT@
    nbytes: i64 = count * 4
    source_slot = stack_alloc(8)
    target_slot = stack_alloc(8)
    store_i64(report, @R_SOURCE_BUFFER@, buffer_create(context, nbytes, source_slot))
    store_i64(report, @R_TARGET_BUFFER@, buffer_create(context, nbytes, target_slot))
    source_buffer = load_ptr(source_slot, 0)
    target_buffer = load_ptr(target_slot, 0)
    if ptr_is_null(source_buffer) or ptr_is_null(target_buffer):
        emit(report, @REPORT_BYTES@)
        return 0
    source_data = buffer_contents(context, source_buffer)
    target_data = buffer_contents(context, target_buffer)
    store_i64(report, @R_TARGET_LENGTH@, buffer_length(context, target_buffer))
@FILL@
    count_slot = stack_alloc(8)
    store_i32(count_slot, 0, count)
    bindings = calloc(3, @BINDING_BYTES@)
    store_i64(bindings, @B0_KIND@, @KIND_BUFFER@)
    store_i64(bindings, @B0_INDEX@, 0)
    store_ptr(bindings, @B0_POINTER@, source_buffer)
    store_i64(bindings, @B0_EXTENT@, 0)
    store_i64(bindings, @B1_KIND@, @KIND_BUFFER@)
    store_i64(bindings, @B1_INDEX@, 1)
    store_ptr(bindings, @B1_POINTER@, target_buffer)
    store_i64(bindings, @B1_EXTENT@, 0)
    store_i64(bindings, @B2_KIND@, @KIND_BYTES@)
    store_i64(bindings, @B2_INDEX@, 2)
    store_ptr(bindings, @B2_POINTER@, count_slot)
    store_i64(bindings, @B2_EXTENT@, 4)
    grid = calloc(3, 8)
    threads = calloc(3, 8)
    store_i64(grid, 8, 1)
    store_i64(grid, 16, 1)
    store_i64(threads, 8, 1)
    store_i64(threads, 16, 1)
'''

RAMP_FILL = '''    index: i64 = 0
    while index < count:
        store_i32(source_data, index * 4, index * 7 + 5)
        store_i32(target_data, index * 4, 0)
        index = index + 1
'''

FINISH_STEP = '''
    emit(report, @REPORT_BYTES@)
    emit(target_data, nbytes)
    buffer_release(context, target_buffer)
    buffer_release(context, source_buffer)
    pipeline_release(context, pipeline)
    device_close(context)
    return 0
'''


def _substitutions() -> dict[str, str]:
    values = {
        "REPORT_WORDS": REPORT_WORDS,
        "REPORT_BYTES": REPORT_BYTES,
        "DIAGNOSTIC_BYTES": DIAGNOSTIC_BYTES,
        "NAME_BYTES": NAME_BYTES,
        "R_OPEN": REPORT_OPEN * 8,
        "R_PIPELINE": REPORT_PIPELINE * 8,
        "R_LIMITS": REPORT_LIMITS * 8,
        "R_MAX_THREADS": REPORT_MAX_THREADS * 8,
        "R_WIDTH": REPORT_WIDTH * 8,
        "R_SOURCE_BUFFER": REPORT_SOURCE_BUFFER * 8,
        "R_TARGET_BUFFER": REPORT_TARGET_BUFFER * 8,
        "R_TARGET_LENGTH": REPORT_TARGET_LENGTH * 8,
        "R_DISPATCH": REPORT_DISPATCH * 8,
        "R_NAME_LENGTH": REPORT_NAME_LENGTH * 8,
        "R_EXTRA_A": REPORT_EXTRA_A * 8,
        "R_EXTRA_B": REPORT_EXTRA_B * 8,
        "LIMITS_BYTES": abi.LIMITS_BYTES,
        "LIMIT_MAX_THREADS": abi.LIMIT_MAX_THREADS,
        "LIMIT_EXECUTION_WIDTH": abi.LIMIT_EXECUTION_WIDTH,
        "BINDING_BYTES": abi.BINDING_BYTES,
        "KIND_BUFFER": abi.BINDING_KIND_BUFFER,
        "KIND_BYTES": abi.BINDING_KIND_BYTES,
    }
    for record in range(3):
        base = record * abi.BINDING_BYTES
        values[f"B{record}_KIND"] = base + abi.BINDING_KIND
        values[f"B{record}_INDEX"] = base + abi.BINDING_INDEX
        values[f"B{record}_POINTER"] = base + abi.BINDING_POINTER
        values[f"B{record}_EXTENT"] = base + abi.BINDING_EXTENT
    return {key: str(value) for key, value in values.items()}


def _render(template: str, **extra: str) -> str:
    values = _substitutions()
    values["RUNTIME_CALLS"] = _runtime_call_wrappers()
    values.update(extra)
    # Substitute caller-supplied fragments first so the placeholders inside
    # them are expanded by the table as well.
    for key in sorted(extra, key=len, reverse=True):
        template = template.replace("@" + key + "@", values[key])
    for key in sorted(values, key=len, reverse=True):
        template = template.replace("@" + key + "@", values[key])
    assert "@" not in template.replace("@c_abi_export", ""), template
    return template


def _compile_object(tmp_path: Path, name: str, source: str) -> bytes:
    path = tmp_path / (name + ".py")
    path.write_text(source)
    ir_path = tmp_path / (name + ".ll")
    pipeline.compile_python(
        str(path), str(ir_path), emit_llvm_only=True, python_library=True,
        backend="self", target_triple=TARGET,
    )
    data = emit_owned_object(ir_path.read_text(), TARGET)
    (tmp_path / (name + ".o")).write_bytes(data)
    return data


def _runtime_object(tmp_path: Path) -> bytes:
    return _compile_object(tmp_path, "metal_device", RUNTIME_SOURCE.read_text())


def _run_driver(tmp_path: Path, body: str, **extra: str) -> bytes:
    driver = _render(DRIVER_HEADER + body, **extra)
    image = link_executable(
        [_compile_object(tmp_path, "driver", driver), _runtime_object(tmp_path)],
        entry="_main",
        identifier=b"pcc-metal-device-gate",
    )
    executable = tmp_path / "metal-device-gate"
    executable.write_bytes(image)
    executable.chmod(0o755)
    ran = subprocess.run([str(executable)], capture_output=True, timeout=120)
    (tmp_path / "native.stdout").write_bytes(ran.stdout)
    (tmp_path / "native.stderr").write_bytes(ran.stderr)
    assert ran.returncode == 0, (ran.returncode, ran.stderr[-400:])
    assert len(ran.stdout) >= REPORT_BYTES, len(ran.stdout)
    return ran.stdout


def _report(stdout: bytes) -> tuple[int, ...]:
    return struct.unpack_from("<%dq" % REPORT_WORDS, stdout)


def _device_available(report: tuple[int, ...]) -> bool:
    """Return False for an accepted in-band unavailable-device report."""
    status = report[REPORT_OPEN]
    if status == abi.STATUS_OK:
        return True
    assert status in _UNAVAILABLE_STATUSES, status
    if os.environ.get("PCC_GPU_HARDWARE_STRICT") == "1":
        pytest.fail(
            "PCC_GPU_HARDWARE_STRICT=1 requires a Metal device; "
            f"pcc_metal_device_open returned {status}"
        )
    print(f"SKIPPED_WITH_REASON: pcc_metal_device_open returned {status}")
    return False


def _undefined_symbols(obj: bytes) -> set[str]:
    n_ext, n_type_mask = 0x01, 0x0E
    return {
        symbol["name"]
        for symbol in parse_object(obj).symbols()
        if symbol["n_type"] & n_ext
        and symbol["n_type"] & n_type_mask == 0
        and symbol["n_sect"] == 0
    }


# --- ownership and ABI shape: no hardware needed ---------------------------


def test_runtime_source_imports_only_compiler_owned_intrinsics():
    tree = ast.parse(RUNTIME_SOURCE.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported == {"pcc", "pcc.extern", "pcc.unsafe"}
    assert any(
        isinstance(node, ast.Assign)
        and [getattr(target, "id", None) for target in node.targets]
        == ["__pcc_freestanding__"]
        and getattr(node.value, "value", None) is True
        for node in tree.body
    )


def test_runtime_status_and_layout_constants_are_distinct():
    assert abi.STATUS_OK == 0
    assert all(status > 0 for status in _ALL_STATUSES)
    assert len(set(_ALL_STATUSES)) == len(_ALL_STATUSES)
    binding = (
        abi.BINDING_KIND, abi.BINDING_INDEX, abi.BINDING_POINTER, abi.BINDING_EXTENT,
    )
    assert binding == (0, 8, 16, 24)
    assert abi.BINDING_BYTES == 32
    assert abi.BINDING_KIND_BUFFER != abi.BINDING_KIND_BYTES
    assert 0 not in (abi.BINDING_KIND_BUFFER, abi.BINDING_KIND_BYTES)
    limits = (
        abi.LIMIT_MAX_THREADS, abi.LIMIT_EXECUTION_WIDTH, abi.LIMIT_STATIC_MEMORY,
    )
    assert limits == (0, 8, 16)
    assert abi.LIMITS_BYTES == 24
    assert abi.MAX_BINDING_INDEX == 30


def test_runtime_object_binds_only_loader_and_allocator_symbols(tmp_path):
    obj = _runtime_object(tmp_path)
    undefined = _undefined_symbols(obj)
    # Objective-C and Metal entry points are resolved through the dynamic
    # loader at run time; none may become a link-time dependency.
    assert undefined == {"_dlopen", "_dlsym", "_dlclose", "_calloc", "_free"}
    defined = {
        symbol["name"] for symbol in parse_object(obj).symbols()
        if symbol["n_sect"] != 0
    }
    for name in (
        "open", "close", "name", "buffer_create", "buffer_contents",
        "buffer_length", "buffer_release", "pipeline_create",
        "pipeline_limits", "pipeline_release", "dispatch_sync",
    ):
        assert "_pcc_metal_device_" + name in defined


# --- real device execution --------------------------------------------------


@_needs_arm64_darwin
def test_device_runs_kernel_and_matches_cpu_oracle(tmp_path):
    count = 1000
    body = PIPELINE_STEP + BUFFER_STEP + '''
    store_i64(grid, 0, (count + width - 1) // width)
    store_i64(threads, 0, width)
    store_i64(report, @R_DISPATCH@, dispatch_sync(
        context, pipeline, bindings, 3, grid, threads,
    ))
    name = calloc(1, @NAME_BYTES@)
    store_i64(report, @R_NAME_LENGTH@, device_name(context, name, @NAME_BYTES@))
    emit(report, @REPORT_BYTES@)
    emit(name, @NAME_BYTES@)
    emit(target_data, nbytes)
    buffer_release(context, target_buffer)
    buffer_release(context, source_buffer)
    pipeline_release(context, pipeline)
    device_close(context)
    return 0
'''
    stdout = _run_driver(
        tmp_path, body, SOURCE=repr(PROBE_SOURCE), FUNCTION=repr("pcc_probe"),
        COUNT=str(count), FILL=RAMP_FILL,
    )
    report = _report(stdout)
    if not _device_available(report):
        return
    assert report[REPORT_PIPELINE] == abi.STATUS_OK
    assert report[REPORT_LIMITS] == abi.STATUS_OK
    assert 1 <= report[REPORT_WIDTH] <= report[REPORT_MAX_THREADS]
    assert report[REPORT_SOURCE_BUFFER] == abi.STATUS_OK
    assert report[REPORT_TARGET_BUFFER] == abi.STATUS_OK
    assert report[REPORT_TARGET_LENGTH] == count * 4
    assert report[REPORT_DISPATCH] == abi.STATUS_OK
    name_length = report[REPORT_NAME_LENGTH]
    assert 0 < name_length < NAME_BYTES
    name = stdout[REPORT_BYTES:REPORT_BYTES + NAME_BYTES]
    assert name[name_length:] == bytes(NAME_BYTES - name_length)
    assert name[:name_length].decode("utf-8").isprintable()
    payload = stdout[REPORT_BYTES + NAME_BYTES:]
    assert len(payload) == count * 4
    oracle = tuple(((i * 7 + 5) * 3 + 1) & 0xFFFFFFFF for i in range(count))
    assert struct.unpack("<%dI" % count, payload) == oracle


@_needs_arm64_darwin
def test_device_refuses_threadgroup_wider_than_pipeline_limit(tmp_path):
    # Metal does not execute such a dispatch and reports no error, so the
    # runtime must refuse it before encoding and leave the target untouched.
    count = 64
    body = PIPELINE_STEP + BUFFER_STEP + '''
    store_i64(grid, 0, 1)
    store_i64(threads, 0, max_threads + 1)
    store_i64(report, @R_DISPATCH@, dispatch_sync(
        context, pipeline, bindings, 3, grid, threads,
    ))
    store_i64(threads, 0, max_threads)
    store_i64(threads, 8, 2)
    store_i64(report, @R_EXTRA_A@, dispatch_sync(
        context, pipeline, bindings, 3, grid, threads,
    ))
''' + FINISH_STEP
    stdout = _run_driver(
        tmp_path, body, SOURCE=repr(PROBE_SOURCE), FUNCTION=repr("pcc_probe"),
        COUNT=str(count), FILL=RAMP_FILL,
    )
    report = _report(stdout)
    if not _device_available(report):
        return
    assert report[REPORT_PIPELINE] == abi.STATUS_OK
    assert report[REPORT_DISPATCH] == abi.STATUS_DISPATCH_EXCEEDS_PIPELINE
    assert report[REPORT_EXTRA_A] == abi.STATUS_DISPATCH_EXCEEDS_PIPELINE
    assert stdout[REPORT_BYTES:] == bytes(count * 4)


@_needs_arm64_darwin
def test_device_reports_shader_diagnostic_and_missing_function(tmp_path):
    broken = "kernel void pcc_probe( { this is not Metal }\n"
    stdout = _run_driver(
        tmp_path, PIPELINE_STEP + "\n    return 0\n",
        SOURCE=repr(broken), FUNCTION=repr("pcc_probe"),
    )
    report = _report(stdout)
    if not _device_available(report):
        return
    assert report[REPORT_PIPELINE] == abi.STATUS_SOURCE_REJECTED
    diagnostic = stdout[REPORT_BYTES:REPORT_BYTES + DIAGNOSTIC_BYTES]
    text = diagnostic.split(b"\0", 1)[0].decode("utf-8")
    assert "error" in text
    assert diagnostic[DIAGNOSTIC_BYTES - 1] == 0

    missing = tmp_path / "missing"
    missing.mkdir()
    stdout = _run_driver(
        missing, PIPELINE_STEP + "\n    return 0\n",
        SOURCE=repr(PROBE_SOURCE), FUNCTION=repr("absent_kernel"),
    )
    assert _report(stdout)[REPORT_PIPELINE] == abi.STATUS_FUNCTION_NOT_FOUND


@_needs_arm64_darwin
def test_device_rejects_invalid_launch_packets_then_still_executes(tmp_path):
    count = 96
    body = PIPELINE_STEP + BUFFER_STEP + '''
    store_i64(grid, 0, (count + width - 1) // width)
    store_i64(threads, 0, width)
    store_i64(bindings, @B2_KIND@, 7)
    store_i64(report, @R_EXTRA_A@, dispatch_sync(
        context, pipeline, bindings, 3, grid, threads,
    ))
    store_i64(bindings, @B2_KIND@, @KIND_BYTES@)
    store_i64(bindings, @B1_INDEX@, @BAD_INDEX@)
    store_i64(report, @R_EXTRA_B@, dispatch_sync(
        context, pipeline, bindings, 3, grid, threads,
    ))
    store_i64(bindings, @B1_INDEX@, 1)
    store_ptr(bindings, @B0_POINTER@, null())
    store_i64(report, @R_NAME_LENGTH@, dispatch_sync(
        context, pipeline, bindings, 3, grid, threads,
    ))
    store_ptr(bindings, @B0_POINTER@, source_buffer)
    store_i64(grid, 8, 0)
    store_i64(report, @R_TARGET_LENGTH@, dispatch_sync(
        context, pipeline, bindings, 3, grid, threads,
    ))
    store_i64(grid, 8, 1)
    store_i64(report, @R_DISPATCH@, dispatch_sync(
        context, pipeline, bindings, 3, grid, threads,
    ))
''' + FINISH_STEP
    stdout = _run_driver(
        tmp_path, body, SOURCE=repr(PROBE_SOURCE), FUNCTION=repr("pcc_probe"),
        COUNT=str(count), FILL=RAMP_FILL, BAD_INDEX=str(abi.MAX_BINDING_INDEX + 1),
    )
    report = _report(stdout)
    if not _device_available(report):
        return
    assert report[REPORT_EXTRA_A] == abi.STATUS_BAD_BINDING
    assert report[REPORT_EXTRA_B] == abi.STATUS_BAD_BINDING
    assert report[REPORT_NAME_LENGTH] == abi.STATUS_BAD_BINDING
    assert report[REPORT_TARGET_LENGTH] == abi.STATUS_BAD_ARGUMENT
    assert report[REPORT_DISPATCH] == abi.STATUS_OK
    oracle = tuple(((i * 7 + 5) * 3 + 1) & 0xFFFFFFFF for i in range(count))
    assert struct.unpack("<%dI" % count, stdout[REPORT_BYTES:]) == oracle


def _copy_module() -> KernelModule:
    func = KernelFunc(
        name="copy_kernel",
        params=(
            BufferParam(
                "src", ScalarType.F32, rank=2, shape=(2, 2), scope=MemoryScope.GLOBAL
            ),
            BufferParam(
                "dst", ScalarType.F32, rank=2, shape=(2, 2), scope=MemoryScope.GLOBAL
            ),
            ScalarParam("n", ScalarType.U32),
        ),
        body=(
            KernelOp("parallel", ("src", "dst", "n"), {"extent": 4}),
            KernelOp("copy", ("src", "dst")),
        ),
        grid=(1,),
        threads=16,
    )
    return KernelModule("device_runtime_copy_mod", funcs=(func,))


@_needs_arm64_darwin
def test_kernel_ir_copy_source_executes_on_the_owned_device(tmp_path):
    # The Metal source is exactly what the Kernel IR finalizer emits; only the
    # launcher differs from the Objective-C bridge path.
    module = _copy_module()
    source = emit_metal_source(module)
    values = (1.5, -2.25, 3.0, 4.75)
    fill = ""
    for index, value in enumerate(values):
        bits = struct.unpack("<i", struct.pack("<f", value))[0]
        fill += f"    store_i32(source_data, {index * 4}, {bits})\n"
        fill += f"    store_i32(target_data, {index * 4}, 0)\n"
    func = module.funcs[0]
    body = PIPELINE_STEP + BUFFER_STEP + f'''
    store_i64(grid, 0, {func.grid[0]})
    store_i64(threads, 0, {func.threads})
    store_i64(report, @R_DISPATCH@, dispatch_sync(
        context, pipeline, bindings, 3, grid, threads,
    ))
''' + FINISH_STEP
    stdout = _run_driver(
        tmp_path, body, SOURCE=repr(source), FUNCTION=repr(func.name),
        COUNT=str(len(values)), FILL=fill,
    )
    report = _report(stdout)
    if not _device_available(report):
        return
    assert report[REPORT_PIPELINE] == abi.STATUS_OK
    assert func.threads <= report[REPORT_MAX_THREADS]
    assert report[REPORT_DISPATCH] == abi.STATUS_OK
    assert struct.unpack("<4f", stdout[REPORT_BYTES:]) == values
