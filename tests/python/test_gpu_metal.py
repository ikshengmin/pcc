"""Annotated Metal kernels and separately labeled offline toolchain references."""

import hashlib
import os
import subprocess
import sys

import pytest

from pcc.kernel_ir.entry import (
    GpuKernelError,
    lower_function_to_kernel_ir,
    lower_function_to_metal,
    prepare_gpu_kernels_for_source,
    strip_gpu_kernel_host_source,
)
from pcc.backend.metal import (
    MetalCompileError,
    MetalToolchainUnavailable,
    compile_metal_source_to_air,
    find_metal_compiler,
)


MINIMAL_METAL_KERNEL = """\
#include <metal_stdlib>
using namespace metal;

kernel void pcc_empty(
    device float* out [[buffer(0)]],
    uint gid [[thread_position_in_grid]]
) {
    out[gid] = out[gid];
}
"""


PCC_GPU_VECTOR_ADD = """\
from pcc import gpu

@gpu.kernel
def add(a: gpu.ptr_f32, b: gpu.ptr_f32, out: gpu.ptr_f32, n: gpu.u32):
    i = gpu.thread_id_x()
    if i < n:
        out[i] = a[i] + b[i]

def main():
    print(0)
"""


PCC_GPU_VECTOR_ADD_LAUNCH = """\
from pcc import gpu

@gpu.kernel
def add(a: gpu.ptr_f32, b: gpu.ptr_f32, out: gpu.ptr_f32, n: gpu.u32):
    i = gpu.thread_id_x()
    if i < n:
        out[i] = a[i] + b[i]

def main():
    rc: int = gpu.run_add_f32_demo()
    print(rc)

main()
"""


PCC_GPU_STRUCTURED_SAXPY = """\
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
"""


def test_find_metal_compiler_returns_path_or_none():
    metal = find_metal_compiler()

    assert metal is None or metal.endswith("metal")


@pytest.mark.pcc_gate(probe="metal")
def test_compile_minimal_metal_kernel_when_toolchain_available(tmp_path):
    # Offline external-toolchain reference, not the owned route. The "metal"
    # gate deselects it where `xcrun --find metal` fails, for example when
    # xcode-select names the Command Line Tools, which carry no Metal compiler.
    out = tmp_path / "pcc_empty.air"

    try:
        compile_metal_source_to_air(MINIMAL_METAL_KERNEL, out)
    except (MetalToolchainUnavailable, MetalCompileError) as exc:
        pytest.fail(f"Metal compiler unavailable: {exc}")

    assert out.is_file()
    assert out.stat().st_size > 0


def test_gpu_kernel_source_stripping_removes_device_only_surface():
    import ast

    module = ast.parse(PCC_GPU_VECTOR_ADD_LAUNCH)
    stripped = strip_gpu_kernel_host_source(PCC_GPU_VECTOR_ADD_LAUNCH, module)

    assert "@gpu.kernel" not in stripped
    assert "from pcc import gpu" not in stripped
    assert "def main" in stripped
    # Extraction only removes device definitions. Launch rewriting is a
    # separate step in prepare_gpu_kernels_for_source.
    assert "pcc.extern" not in stripped
    assert "gpu.run_add_f32_demo()" in stripped
    assert "__pcc_gpu_run_add_f32_demo()" not in stripped


def test_gpu_kernel_vector_add_imports_to_kernel_ir_before_metal():
    import ast

    module = ast.parse(PCC_GPU_VECTOR_ADD)
    kernel = next(node for node in module.body if isinstance(node, ast.FunctionDef))
    kernel_ir = lower_function_to_kernel_ir(kernel)
    data = kernel_ir.to_dict()

    assert data["module"] == "add_gpu_kernel_ir"
    assert data["funcs"][0]["name"] == "add"
    assert [param["kind"] for param in data["funcs"][0]["params"]] == [
        "buffer",
        "buffer",
        "buffer",
        "scalar",
    ]
    assert [op["op"] for op in data["funcs"][0]["body"]] == [
        "parallel",
        "elementwise_add",
    ]

    metal_source = lower_function_to_metal(kernel)
    assert "pcc route: Kernel IR -> TIRx -> Metal" in metal_source
    assert "kernel void add" in metal_source
    assert "out[i] = (a[i] + b[i]);" in metal_source


def test_gpu_kernel_structured_subset_has_no_direct_ast_to_metal_route():
    import ast

    module = ast.parse(PCC_GPU_STRUCTURED_SAXPY)
    kernel = next(node for node in module.body if isinstance(node, ast.FunctionDef))
    kernel_ir = lower_function_to_kernel_ir(kernel)
    ops = kernel_ir.to_dict()["funcs"][0]["body"]

    assert [op["op"] for op in ops] == [
        "scalar_assign",
        "if_begin",
        "scalar_assign",
        "if_begin",
        "indexed_store",
        "else",
        "indexed_store",
        "if_end",
        "if_end",
    ]
    assert ops[0]["attrs"]["expr"] == {"kind": "thread_id_x"}
    assert ops[2]["attrs"]["dtype"] == "f32"

    metal_source = lower_function_to_metal(kernel)
    assert "pcc route: Kernel IR -> TIRx -> Metal" in metal_source
    assert "uint i = gid;" in metal_source
    assert "float value = ((a[i] * alpha) + b[i]);" in metal_source
    assert "out[i] = value;" in metal_source
    assert "out[i] = (b[i] - alpha);" in metal_source


def test_gpu_kernel_unsupported_syntax_fails_closed_without_metal_fallback():
    import ast

    module = ast.parse(
        "@gpu.kernel\n"
        "def bad(out: gpu.ptr_f32, n: gpu.u32):\n"
        "    for i in range(n):\n"
        "        out[i] = 0.0\n"
    )
    kernel = module.body[0]
    assert isinstance(kernel, ast.FunctionDef)
    with pytest.raises(GpuKernelError, match="no direct Metal fallback exists"):
        lower_function_to_metal(kernel)


def test_prepare_gpu_kernel_embeds_source_and_keeps_inspectable_metal(tmp_path):
    host_source, artifacts = prepare_gpu_kernels_for_source(
        PCC_GPU_VECTOR_ADD_LAUNCH,
        "kernel_host.py",
        backend="metal",
        artifact_dir=tmp_path / "gpu",
    )

    assert "def main" in host_source
    assert "gpu.run_add_f32_demo()" not in host_source
    assert "__pcc_gpu_run_add_f32_demo()" in host_source
    assert "pcc_metal_device_launch_packet" in host_source
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.name == "add"
    metal_text = artifact.metal_path.read_text(encoding="utf-8")
    assert metal_text == artifact.launch.source
    assert artifact.source_sha256 == hashlib.sha256(metal_text.encode()).hexdigest()
    assert "kernel void add" in metal_text
    assert "thread_position_in_grid" in metal_text
    assert "out[i] = (a[i] + b[i]);" in metal_text
    assert sorted(path.name for path in (tmp_path / "gpu").iterdir()) == ["add.metal"]
    assert not list(tmp_path.glob("*.metallib"))


def test_pcc_emit_llvm_with_gpu_kernel_embeds_source_without_metal_toolchain(tmp_path):
    src = tmp_path / "kernel_host.py"
    ll = tmp_path / "kernel_host.ll"
    src.write_text(PCC_GPU_VECTOR_ADD_LAUNCH, encoding="utf-8")

    result = subprocess.run(
        [
            "uv",
            "run",
            "pcc",
            "--gpu-backend=metal",
            "--emit-llvm",
            str(ll),
            str(src),
        ],
        text=True,
        capture_output=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert ll.is_file()
    assert "pcc_metal_device_launch_packet" in ll.read_text(encoding="utf-8")
    assert (tmp_path / "kernel_host.ll.gpu" / "add.metal").is_file()
    assert not list(tmp_path.rglob("*.metallib"))
    assert not list(tmp_path.rglob("*.air"))
    assert not list(tmp_path.rglob("*.o"))


@pytest.mark.pcc_gate(
    unavailable=None if sys.platform == "darwin" and os.uname().machine == "arm64"
    else "needs Darwin arm64 Metal"
)
def test_pcc_metal_gpu_kernel_binary_launches_embedded_source(
    tmp_path, pcc_runtime_archive,
):
    src = tmp_path / "kernel_host.py"
    exe = tmp_path / "kernel_host"
    src.write_text(PCC_GPU_VECTOR_ADD_LAUNCH, encoding="utf-8")

    compile_result = subprocess.run(
        [
            "uv", "run", "pcc", "--gpu-backend=metal",
            str(src), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=90,
        env=dict(os.environ, PCC_RUNTIME_ARCHIVE=str(pcc_runtime_archive)),
    )

    assert compile_result.returncode == 0, compile_result.stderr
    assert exe.is_file()
    sidecars = tmp_path / "kernel_host.gpu"
    assert (sidecars / "add.metal").is_file()
    assert (sidecars / "metal_device.o").is_file()
    assert not list(tmp_path.rglob("*.metallib"))
    assert not list(tmp_path.rglob("*.air"))
    sidecars.rename(tmp_path / "kernel_host.hidden.gpu")

    run_result = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30,
    )
    combined = run_result.stdout + run_result.stderr
    assert run_result.returncode == 0, combined
    assert combined.strip() == "OK Metal add kernel via owned device runtime\n0"


@pytest.mark.pcc_gate(
    unavailable=None if sys.platform == "darwin" and os.uname().machine == "arm64"
    else "needs Darwin arm64 Metal"
)
def test_pcc_metal_gpu_kernel_launches_with_self_backend(
    tmp_path, pcc_runtime_archive,
):
    src = tmp_path / "kernel_host.py"
    exe = tmp_path / "kernel_host_self"
    src.write_text(PCC_GPU_VECTOR_ADD_LAUNCH, encoding="utf-8")

    compile_result = subprocess.run(
        [
            "uv", "run", "pcc", "--backend", "self", "--gpu-backend=metal",
            str(src), "-o", str(exe),
        ],
        text=True, capture_output=True, timeout=90,
        env=dict(os.environ, PCC_RUNTIME_ARCHIVE=str(pcc_runtime_archive)),
    )

    assert compile_result.returncode == 0, compile_result.stderr
    assert exe.is_file()
    sidecars = tmp_path / "kernel_host_self.gpu"
    assert (sidecars / "metal_device.o").is_file()
    assert not list(tmp_path.rglob("*.metallib"))
    assert not list(tmp_path.rglob("*.air"))
    sidecars.rename(tmp_path / "kernel_host_self.hidden.gpu")

    run_result = subprocess.run(
        [str(exe)], text=True, capture_output=True, timeout=30,
    )
    combined = run_result.stdout + run_result.stderr
    assert run_result.returncode == 0, combined
    assert combined.strip() == "OK Metal add kernel via owned device runtime\n0"
