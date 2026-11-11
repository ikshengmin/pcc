"""Host launch generation for ``@gpu.kernel``: no device needed.

The generated launcher is checked as text and as Python; its execution on a
device is covered by ``tests/gpu_hardware/test_gpu_kernel_launch.py``.
"""

from __future__ import annotations

import ast
import ctypes
import importlib.util
import random
import struct

import pytest

from pcc.frontends.python import pipeline
from pcc.gpu_runtime import metal_device as abi
from pcc.kernel_ir import gpu_host_launch
from pcc.kernel_ir.entry import (
    GpuKernelError,
    host_source_launches_gpu,
    lower_function_to_kernel_ir,
    lower_function_to_launch,
    prepare_gpu_kernels_for_source,
    rewrite_gpu_host_launch_calls,
)
from pcc.kernel_ir.gpu_host_launch import (
    GpuHostLaunchError,
    GpuKernelLaunch,
    GpuLaunchBinding,
    render_launch_prelude,
)
from pcc.kernel_ir.metal_finalize import metal_kernel_bindings


ADD_KERNEL = """\
@gpu.kernel
def add(a: gpu.ptr_f32, b: gpu.ptr_f32, out: gpu.ptr_f32, n: gpu.u32):
    i = gpu.thread_id_x()
    if i < n:
        out[i] = a[i] + b[i]
"""

SAXPY_KERNEL = """\
@gpu.kernel
def saxpy(a: gpu.ptr_f32, b: gpu.ptr_f32, out: gpu.ptr_f32, n: gpu.u32, alpha: gpu.f32):
    i = gpu.thread_id_x()
    if i < n:
        out[i] = a[i] * alpha + b[i]
"""


def _launch(kernel_source: str) -> GpuKernelLaunch:
    return lower_function_to_launch(ast.parse(kernel_source).body[0])


def _emitted_f32_bits(tmp_path):
    """Import the emitted conversion text as the module source it is."""
    path = tmp_path / "emitted_f32_bits.py"
    path.write_text(gpu_host_launch.F32_BITS_SOURCE)
    spec = importlib.util.spec_from_file_location("emitted_f32_bits", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "__pcc_gpu_f32_bits")


def _c_float_bits(bits: int) -> int:
    value = struct.unpack("<d", struct.pack("<Q", bits))[0]
    return struct.unpack("<I", struct.pack("<f", ctypes.c_float(value).value))[0]


def test_emitted_float_conversion_equals_the_c_conversion(tmp_path):
    convert = _emitted_f32_bits(tmp_path)
    cases = [
        0, 1 << 63, 1, 0x000FFFFFFFFFFFFF,
        0x7FF0000000000000, 0xFFF0000000000000,
    ]
    mantissas = (
        0, 1, 0x8000000000000, 0x7FFFFFFFFFFFF, 0xFFFFFFFFFFFFF,
        0x10000000, 0x30000000, 0x1FFFFFFF, 0x20000000, 0xFFFFFE0000000,
        0xFFFFFF0000000, 0xFFFFFF0000001,
    )
    # Every exponent around both ends of the binary32 range, with mantissas on
    # and beside the rounding boundaries.
    for power in list(range(-160, -120)) + list(range(120, 135)) + [-1, 0, 1]:
        for mantissa in mantissas:
            for sign in (0, 1):
                cases.append((sign << 63) | ((power + 1023) << 52) | mantissa)
    generator = random.Random(20261112)
    cases += [generator.getrandbits(64) for _ in range(20000)]
    for bits in cases:
        value = struct.unpack("<d", struct.pack("<Q", bits))[0]
        got = convert(bits)
        if value != value:
            assert got & 0x7F800000 == 0x7F800000 and got & 0x7FFFFF != 0, hex(bits)
        else:
            assert got == _c_float_bits(bits), hex(bits)
    assert convert(struct.unpack("<Q", struct.pack("<d", 2.5))[0]) == 0x40200000
    # f64_bits hands the pattern over as a signed lane.
    assert convert(-(1 << 63)) == 0x80000000


def test_kernel_bindings_follow_the_emitted_buffer_table():
    add = ast.parse(ADD_KERNEL).body[0]
    module = lower_function_to_kernel_ir(add)
    records = metal_kernel_bindings(module, "add")
    assert [
        (r["name"], r["kind"], r["dtype"], r["index"], r["written"]) for r in records
    ] == [
        ("a", "buffer", "f32", 0, False),
        ("b", "buffer", "f32", 1, False),
        ("out", "buffer", "f32", 2, True),
        ("n", "scalar", "u32", 3, False),
    ]
    launch = _launch(SAXPY_KERNEL)
    assert launch.entry == "saxpy"
    assert launch.threads == 256
    assert [b.dtype for b in launch.bindings] == ["f32", "f32", "f32", "u32", "f32"]
    assert "constant float& alpha [[buffer(4)]]" in launch.source
    for record in launch.bindings:
        assert f"[[buffer({record.index})]]" in launch.source


def test_prelude_is_python_with_runtime_offsets_and_one_launcher_per_kernel():
    prelude = render_launch_prelude([_launch(ADD_KERNEL), _launch(SAXPY_KERNEL)])
    module = ast.parse(prelude)
    functions = {
        node.name: node for node in module.body if isinstance(node, ast.FunctionDef)
    }
    assert {
        "__pcc_gpu_f32_bits", "__pcc_gpu_launch",
        "__pcc_gpu_launch_add", "__pcc_gpu_launch_saxpy",
    } <= set(functions)
    assert "__pcc_gpu_run_add_f32_demo" not in functions
    assert [a.arg for a in functions["__pcc_gpu_launch_saxpy"].args.args] == [
        "__pcc_a0", "__pcc_a1", "__pcc_a2", "__pcc_a3", "__pcc_a4", "count",
    ]
    assert "@" not in prelude
    assert f"store_i64(packet, {abi.PACKET_MAGIC_AT}, {abi.PACKET_MAGIC})" in prelude
    assert f"load_i64(result, {abi.RESULT_STAGE})" in prelude
    assert f"cursor = {abi.RESULT_FIXED_BYTES}" in prelude
    # Raw addresses are declared as raw: an object-pointer parameter would
    # hand the runtime a boxed integer instead of the packet.
    assert "(c_rawptr, c_int64, c_rawptr, c_int64)" in prelude
    assert "c_ptr" not in prelude
    imported = {
        node.module for node in module.body if isinstance(node, ast.ImportFrom)
    }
    assert imported == {"pcc.extern", "pcc.unsafe"}
    assert not any(isinstance(node, ast.Import) for node in module.body)
    add = ast.unparse(functions["__pcc_gpu_launch_add"])
    assert "isinstance(__pcc_a2, bytearray)" in add
    assert "isinstance(__pcc_a0, bytearray)" not in add
    assert "[0, 0, 1, 0]" in add
    assert "__pcc_gpu_u32_bytes(__pcc_a3)" in add
    assert "__pcc_gpu_f32_bytes(__pcc_a4)" in ast.unparse(
        functions["__pcc_gpu_launch_saxpy"]
    )


def test_add_demo_needs_a_matching_add_kernel():
    prelude = render_launch_prelude([_launch(ADD_KERNEL)], add_demo=True)
    assert "def __pcc_gpu_run_add_f32_demo() -> int:" in prelude
    assert "OK Metal add kernel via owned device runtime" in prelude
    expected = struct.pack("<4f", 11.0, 22.0, 33.0, 44.0)
    assert repr(expected) in prelude
    with pytest.raises(GpuHostLaunchError, match="kernel named 'add'"):
        render_launch_prelude([_launch(SAXPY_KERNEL)], add_demo=True)
    renamed = SAXPY_KERNEL.replace("def saxpy", "def add")
    with pytest.raises(GpuHostLaunchError, match="reads two ptr_f32 buffers"):
        render_launch_prelude([_launch(renamed)], add_demo=True)


def test_unsupported_launch_shapes_fail_closed():
    def launch(binding: GpuLaunchBinding, threads: int = 256) -> GpuKernelLaunch:
        return GpuKernelLaunch("k", "k", "kernel void k() {}", (binding,), threads)

    with pytest.raises(GpuHostLaunchError, match="unsupported element type"):
        render_launch_prelude([launch(GpuLaunchBinding("a", "buffer", "f16", 0))])
    with pytest.raises(GpuHostLaunchError, match="unsupported type"):
        render_launch_prelude([launch(GpuLaunchBinding("a", "scalar", "f64", 0))])
    with pytest.raises(GpuHostLaunchError, match="unsupported kind"):
        render_launch_prelude([launch(GpuLaunchBinding("a", "texture", "f32", 0))])
    with pytest.raises(GpuHostLaunchError, match="threads must be positive"):
        render_launch_prelude(
            [launch(GpuLaunchBinding("a", "buffer", "f32", 0), threads=0)]
        )
    twice = _launch(ADD_KERNEL)
    with pytest.raises(GpuHostLaunchError, match="must be unique"):
        render_launch_prelude([twice, twice])


HOST = """\
from pcc import gpu

{kernel}
def main():
    out = bytearray(16)
    gpu.launch(
        add,
        A, B, out, 4,
        count=4,
    )
    print(out.hex())

main()
"""


def test_launch_calls_become_launcher_calls_and_keep_their_lines(tmp_path):
    source = HOST.format(kernel=ADD_KERNEL)
    host, artifacts = prepare_gpu_kernels_for_source(
        source, "host.py", backend="metal", artifact_dir=tmp_path / "gpu",
    )
    assert [artifact.name for artifact in artifacts] == ["add"]
    assert artifacts[0].metal_path.read_text() == artifacts[0].launch.source
    assert len(artifacts[0].source_sha256) == 64
    assert host_source_launches_gpu(host)
    assert "gpu.launch" not in host and "@gpu.kernel" not in host
    assert "from pcc import gpu" not in host
    ast.parse(host)
    tail = host.split("# --- end of generated GPU host launch ---\n", 1)[1]
    removed = len(ADD_KERNEL.splitlines()) + 1
    # Only the kernel and the import were removed; the launch call still spans
    # the same lines.
    assert len(tail.splitlines()) == len(source.splitlines()) - removed
    assert (
        "__pcc_gpu_launch_add(\n\n        A, B, out, 4,\n        count=4,\n    )"
        in tail
    )


def test_host_source_without_launches_gets_no_prelude(tmp_path):
    source = "from pcc import gpu\n\n" + ADD_KERNEL + "\ndef main():\n    print(0)\n"
    host, artifacts = prepare_gpu_kernels_for_source(
        source, "host.py", backend="metal", artifact_dir=tmp_path / "gpu",
    )
    assert len(artifacts) == 1
    assert not host_source_launches_gpu(host)
    assert "pcc.extern" not in host
    assert host.strip() == "def main():\n    print(0)"
    untouched, none = prepare_gpu_kernels_for_source(
        source, "host.py", backend="none", artifact_dir=tmp_path / "unused",
    )
    assert untouched == source and none == []
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize("call,message", [
    ("gpu.launch(missing, A, B, out, 4, count=4)", "is not a @gpu.kernel function"),
    ("gpu.launch(add, A, B, out, 4)", "only keyword, count="),
    ("gpu.launch(add, A, B, out, 4, count=4, threads=8)", "only keyword, count="),
    ("gpu.launch(add, A, B, out, count=4)", "takes 4 arguments, 3 were given"),
    ("gpu.launch(add, *values, count=4)", "starred arguments"),
    ("gpu.launch(kernels[0], A, B, out, 4, count=4)", "must name a @gpu.kernel"),
    ("gpu.launch(count=4)", "must name a @gpu.kernel"),
], ids=["unknown", "no-count", "extra-keyword", "arity", "starred", "expression", "empty"])
def test_malformed_launch_calls_are_compile_errors(call, message):
    host = "def main():\n    " + call + "\n"
    with pytest.raises(GpuKernelError, match=message):
        rewrite_gpu_host_launch_calls(host, [_launch(ADD_KERNEL)])


def test_compiler_reports_a_malformed_launch_before_linking(tmp_path):
    source = tmp_path / "bad_launch.py"
    source.write_text(
        "from pcc import gpu\n\n" + ADD_KERNEL
        + "\ndef main():\n    gpu.launch(add, b'', b'', bytearray(4), count=1)\n"
    )
    with pytest.raises(pipeline.PyPipelineError, match="takes 4 arguments, 3 were given"):
        pipeline.compile_python(
            str(source), str(tmp_path / "bad_launch.ll"), emit_llvm_only=True,
            gpu_backend="metal",
        )
