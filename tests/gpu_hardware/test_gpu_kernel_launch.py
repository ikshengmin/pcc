"""Public @gpu.kernel / gpu.launch execution through the owned Metal host.

The host compiler is pcc0/self; the emitted executable runs on Darwin arm64.
These gates require actual GPU readback, not an unavailable-device result.
They do not certify pcc1 or asynchronous/five-GC resource lifetime.

Contract: pcc/gpu.py and docs/design/pcc-local-model-architecture.md, L0/L1.
MSL semantics: Metal Shading Language specification, kernel functions and
thread_position_in_grid. The arithmetic oracle is ordinary host Python.
"""

from __future__ import annotations

import ctypes
import os
import struct
import subprocess
import sys

import pytest

from scripts.verify_nolibpython import linked_libraries


_IS_ARM64_DARWIN = (
    hasattr(os, "uname")
    and os.uname().sysname == "Darwin"
    and os.uname().machine == "arm64"
)
pytestmark = [
    pytest.mark.integration,
    pytest.mark.pcc_gate(
        unavailable=None if _IS_ARM64_DARWIN else "needs Darwin arm64 Metal"
    ),
    pytest.mark.xdist_group(name="owned_gpu_kernel_launch"),
]


SAXPY = """\
from pcc import gpu

@gpu.kernel
def saxpy(a: gpu.ptr_f32, b: gpu.ptr_f32, out: gpu.ptr_f32, n: gpu.u32, alpha: gpu.f32):
    i = gpu.thread_id_x()
    if i < n:
        value = a[i] * alpha + b[i]
        if value > 0.0:
            out[i] = value
        else:
            out[i] = b[i] - alpha


def main():
    a = {left!r}
    b = bytearray({right!r})
    out = bytearray({initial!r})
    result = gpu.launch(saxpy, a, b, out, {count}, {alpha!r}, count={count})
    assert result is None
    assert bytes(b) == {right!r}
    print(out.hex())

main()
"""


_GC_REPORT = '''
from pcc.extern import c_int64, extern
observed_gc_backend = extern("pcc_gc_backend", (), c_int64)
print("GC", observed_gc_backend())
'''


def _compile_program(tmp_path, name, text, pcc_runtime_archive):
    source = tmp_path / (name + ".py")
    source.write_text(text + _GC_REPORT)
    binary = tmp_path / name
    env = dict(
        os.environ,
        PCC_NO_AUTO_PCC1="1",
        PCC_RUNTIME_ARCHIVE=str(pcc_runtime_archive),
    )
    compiled = subprocess.run(
        [sys.executable, "-m", "pcc", "--backend=self", "--gpu-backend=metal",
         str(source), "-o", str(binary)],
        capture_output=True, text=True, timeout=90, env=env,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    libraries = linked_libraries(binary.read_bytes())
    assert libraries
    assert not any("libpython" in name.lower() or "llvm" in name.lower() for name in libraries)
    return binary, env


def _assert_all_collectors(binary, env, expected):
    # This proves synchronous execution under each observed collector, not
    # asynchronous resource lifetime, relocation stress or bootstrap parity.
    for collector in range(5):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(env, PCC_GC_BACKEND=str(collector)),
        )
        detail = f"GC{collector}: " + result.stdout + result.stderr
        assert result.returncode == 0, detail
        assert result.stderr == "", detail
        assert result.stdout == expected + f"\nGC {collector}\n", detail
        print(f"{binary.name}: GC{collector} result and runtime identity matched")


def test_cli_kernel_executes_saxpy_and_writes_back(tmp_path, pcc_runtime_archive):
    # A partial final 256-thread group, both branches, immutable and mutable
    # inputs, a scalar f32, and a tail canary that no thread may overwrite.
    count = 257
    left = [float(i % 9 - 4) for i in range(count)]
    right = [float(i % 7 - 3) for i in range(count)]
    alpha = 2.5
    expected = [
        a * alpha + b if a * alpha + b > 0.0 else b - alpha
        for a, b in zip(left, right)
    ]
    canary = b"\x5a\xa5\x5a\xa5" * 3
    text = SAXPY.format(
        left=struct.pack(f"<{count}f", *left),
        right=struct.pack(f"<{count}f", *right),
        initial=bytes(count * 4) + canary,
        count=count,
        alpha=alpha,
    )
    binary, env = _compile_program(tmp_path, "saxpy", text, pcc_runtime_archive)
    artifacts = tmp_path / "saxpy.gpu"
    assert (artifacts / "saxpy.metal").is_file()
    assert (artifacts / "metal_device.o").is_file()
    assert not list(tmp_path.rglob("*.metallib"))
    assert not list(tmp_path.rglob("*.air"))
    assert not list(tmp_path.rglob("*.m"))
    # The executable carries its source. No generated build sidecar is read
    # by the application, including the device runtime's object/IR files.
    artifacts.rename(tmp_path / "hidden-build-artifacts")
    _assert_all_collectors(
        binary, env, (struct.pack(f"<{count}f", *expected) + canary).hex(),
    )


def test_cli_rejects_aliased_writable_buffers_before_dispatch(tmp_path, pcc_runtime_archive):
    # Two independent packet buffers would return 1, while true aliasing
    # returns 8. Until launch packets represent aliasing, reject this shape.
    text = '''from pcc import gpu

@gpu.kernel
def update(a: gpu.ptr_f32, b: gpu.ptr_f32, n: gpu.u32):
    i = gpu.thread_id_x()
    if i < n:
        a[i] = 7.0
        b[i] = b[i] + 1.0


def main():
    data = bytearray(4)
    try:
        gpu.launch(update, data, data, 1, count=1)
    except ValueError as error:
        assert "aliased" in str(error)
        assert "a" in str(error) and "b" in str(error)
    else:
        raise AssertionError("aliased writable buffers were silently copied")
    assert data == bytearray(4)
    left = bytearray(4)
    right = bytearray(4)
    gpu.launch(update, left, right, 1, count=1)
    print(left.hex(), right.hex())

main()
'''
    binary, env = _compile_program(tmp_path, "alias", text, pcc_runtime_archive)
    _assert_all_collectors(
        binary, env,
        struct.pack("<f", 7.0).hex() + " " + struct.pack("<f", 1.0).hex(),
    )


def test_cli_rejects_invalid_arguments_then_accepts_readonly_aliases(
    tmp_path, pcc_runtime_archive,
):
    cases = [
        ("[0, 0, 128, 63], a, out, 1, 2.5, count=1", "TypeError", "bytes or bytearray"),
        ("a, a, a, 1, 2.5, count=1", "TypeError", "pass a bytearray"),
        ("b'', a, out, 1, 2.5, count=1", "ValueError", "is empty"),
        ("b'x', a, out, 1, 2.5, count=1", "ValueError", "whole number of elements"),
        ("a, a, out, -1, 2.5, count=1", "ValueError", "u32 argument is out of range"),
        ("a, a, out, 4294967296, 2.5, count=1", "ValueError", "u32 argument is out of range"),
        ("a, a, out, 1, 2.5, count=0", "ValueError", "count must be at least 1"),
        ("a, a, out, 1, 2.5, count=-1", "ValueError", "count must be at least 1"),
        ("out, a, out, 1, 2.5, count=1", "ValueError", "aliased buffers 'a' and 'out'"),
        ("a, out, out, 1, 2.5, count=1", "ValueError", "aliased buffers 'b' and 'out'"),
    ]
    lines = [
        SAXPY.split("\ndef main():", 1)[0],
        "def main():",
        f"    a = {struct.pack('<f', 1.0)!r}",
        "    out = bytearray(4)",
    ]
    for arguments, error, message in cases:
        lines += [
            "    try:",
            f"        gpu.launch(saxpy, {arguments})",
            f"    except {error} as error:",
            f"        assert {message!r} in str(error), str(error)",
            "    else:",
            f"        raise AssertionError({('accepted invalid arguments: ' + arguments)!r})",
            "    assert out == bytearray(4)",
        ]
    lines += [
        "    gpu.launch(saxpy, a, a, out, 1, 2.5, count=1)",
        "    print(out.hex())",
        "main()",
    ]
    binary, env = _compile_program(
        tmp_path, "arguments", "\n".join(lines) + "\n", pcc_runtime_archive,
    )
    _assert_all_collectors(binary, env, struct.pack("<f", 3.5).hex())


def test_owned_compile_and_link_do_not_invoke_external_tools(
    tmp_path, pcc_runtime_archive, monkeypatch,
):
    from pcc.frontends.python import pipeline

    text = SAXPY.format(
        left=struct.pack("<f", 1.0), right=struct.pack("<f", 2.0),
        initial=bytes(4), count=1, alpha=2.5,
    )
    source = tmp_path / "no_tools.py"
    source.write_text(text + _GC_REPORT)
    binary = tmp_path / "no_tools"

    def refuse_subprocess(*args, **kwargs):
        raise AssertionError(f"GPU host compilation invoked a subprocess: {args!r}")

    with monkeypatch.context() as denied:
        # Deny even absolute tool paths. This is the real public compilation
        # pipeline with a verified prebuilt runtime, not fake tool results.
        denied.setenv("PCC_RUNTIME_ARCHIVE", str(pcc_runtime_archive))
        denied.setattr(subprocess, "Popen", refuse_subprocess)
        pipeline.compile_python(
            str(source), str(binary), backend="self", gpu_backend="metal",
        )
    libraries = linked_libraries(binary.read_bytes())
    assert libraries
    assert not any("libpython" in name.lower() or "llvm" in name.lower() for name in libraries)
    _assert_all_collectors(binary, os.environ, struct.pack("<f", 4.5).hex())


def test_generated_float_packing_executes_binary32_rounding_in_native_code(
    tmp_path, pcc_runtime_archive,
):
    from pcc.kernel_ir.gpu_host_launch import F32_BITS_SOURCE

    # IEEE 754 binary64 -> binary32 round-to-nearest-even, including signed
    # zero, overflow, normal/subnormal boundaries and halfway cases. The
    # reference is the host C float conversion, not the emitted algorithm.
    values = [
        0.0, -0.0, 1.0, -2.5, float("inf"), -float("inf"),
        2.0 ** -150, 2.0 ** -149, 3.0 * 2.0 ** -150,
        2.0 ** -126 - 2.0 ** -150, 2.0 ** -126,
        1.0 + 2.0 ** -24, 1.0 + 3.0 * 2.0 ** -24,
        2.0 ** 128 - 2.0 ** 104, 2.0 ** 128 - 2.0 ** 103, 2.0 ** 128,
    ]
    words = [struct.unpack("<q", struct.pack("<d", value))[0] for value in values]
    expected = [
        struct.unpack("<I", struct.pack("<f", ctypes.c_float(value).value))[0]
        for value in values
    ]
    statements = ["def main():"] + [
        f"    print(__pcc_gpu_f32_bits({word}))" for word in words
    ] + ["main()"]
    binary, env = _compile_program(
        tmp_path, "float_packing", F32_BITS_SOURCE + "\n".join(statements) + "\n",
        pcc_runtime_archive,
    )
    _assert_all_collectors(binary, env, "\n".join(str(word) for word in expected))
