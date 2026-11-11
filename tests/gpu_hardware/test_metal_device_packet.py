"""Gates for the Metal launch packet and the owned launcher process.

The launcher is one freestanding object linked with the owned Mach-O linker;
its entry point reads a launch packet and writes the result. The Kernel IR
gates feed it the Metal source and the validated launch plan that the existing
Kernel IR path produces, and compare the device output with the CPU oracle.

Host Python builds the launcher and encodes the packets, so this is host-pcc0
evidence on the machine that ran it: not pcc1-native and not five-GC evidence.
An unavailable device is reported in band and rejected when
``PCC_GPU_HARDWARE_STRICT=1``.
"""

from __future__ import annotations

import os
import struct

import pytest

from pcc.gpu_runtime import metal_device as abi
from pcc.gpu_runtime import metal_host
from pcc.gpu_runtime.metal_host import MetalBinding, MetalHostError
from pcc.kernel_ir.cpu_reference import execute_scalar_tiled_gemm_reference
from pcc.kernel_ir.hmm_fence import PccBufferHandle, PccPackedArgs
from pcc.kernel_ir.ir import (
    BufferParam,
    KernelFunc,
    KernelModule,
    KernelOp,
    MemoryScope,
    ScalarParam,
    ScalarType,
)
from pcc.kernel_ir.metal_device_launch import (
    KERNEL_SOURCE,
    launch_packet_from_plan,
    launch_plan_on_device,
)
from pcc.kernel_ir.metal_finalize import emit_metal_source
from pcc.kernel_ir.metal_launch import MetalLaunchError, plan_metal_launch
from pcc.kernel_ir.tilelang_import import import_tilelang_source
from tests.gpu_hardware.test_metal_claim_levels import TILELANG_METAL_MATMUL


_IS_ARM64_DARWIN = (
    hasattr(os, "uname")
    and os.uname().sysname == "Darwin"
    and os.uname().machine == "arm64"
)
_needs_arm64_darwin = pytest.mark.pcc_gate(
    unavailable=None if _IS_ARM64_DARWIN else "needs Darwin arm64"
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

def _probe_packet(count: int, threads: int, *, source: str = PROBE_SOURCE) -> bytes:
    values = struct.pack("<%dI" % count, *(i * 7 + 5 for i in range(count)))
    return metal_host.encode_launch_packet(
        source,
        "pcc_probe",
        [
            MetalBinding(0, metal_host.KIND_BUFFER, values),
            MetalBinding(1, metal_host.KIND_BUFFER, bytes(count * 4), readback=True),
            MetalBinding(2, metal_host.KIND_BYTES, struct.pack("<I", count)),
        ],
        grid=((count + threads - 1) // threads, 1, 1),
        threads=(threads, 1, 1),
    )


def _probe_oracle(count: int) -> bytes:
    return struct.pack(
        "<%dI" % count, *(((i * 7 + 5) * 3 + 1) & 0xFFFFFFFF for i in range(count))
    )


def _executed(result: metal_host.MetalPacketResult) -> bool:
    """Return False for an accepted in-band unavailable-device result."""
    if not result.device_unavailable:
        return True
    if os.environ.get("PCC_GPU_HARDWARE_STRICT") == "1":
        pytest.fail(
            "PCC_GPU_HARDWARE_STRICT=1 requires a Metal device; "
            f"device open returned {result.status}"
        )
    print(f"SKIPPED_WITH_REASON: Metal device open returned {result.status}")
    return False


@pytest.fixture(scope="module")
def runner(tmp_path_factory):
    return metal_host.build_packet_runner(tmp_path_factory.mktemp("metal-runner"))


# --- byte layout and host-side rejection: no hardware needed ---------------


def test_packet_layout_matches_the_runtime_constants():
    packet = _probe_packet(8, 4)
    header = struct.unpack_from("<10q", packet)
    assert header[0] == abi.PACKET_MAGIC
    assert header[abi.PACKET_BINDING_COUNT // 8] == 3
    assert header[abi.PACKET_GRID // 8:abi.PACKET_GRID // 8 + 3] == (2, 1, 1)
    assert header[abi.PACKET_THREADS // 8:abi.PACKET_THREADS // 8 + 3] == (4, 1, 1)
    source_bytes = header[abi.PACKET_SOURCE_BYTES // 8]
    function_bytes = header[abi.PACKET_FUNCTION_BYTES // 8]
    assert source_bytes == len(PROBE_SOURCE.encode()) + 1
    assert function_bytes == len(b"pcc_probe") + 1
    descriptors = [
        struct.unpack_from(
            "<4q", packet, abi.PACKET_HEADER_BYTES + i * abi.PACKET_BINDING_BYTES
        )
        for i in range(3)
    ]
    assert descriptors == [
        (abi.BINDING_KIND_BUFFER, 0, 32, 0),
        (abi.BINDING_KIND_BUFFER, 1, 32, 1),
        (abi.BINDING_KIND_BYTES, 2, 4, 0),
    ]
    source_at = abi.PACKET_HEADER_BYTES + 3 * abi.PACKET_BINDING_BYTES
    assert packet[source_at:source_at + source_bytes] == PROBE_SOURCE.encode() + b"\0"
    assert len(packet) % 8 == 0
    assert packet.endswith(struct.pack("<I", 8) + bytes(4))
    assert abi.RESULT_FIXED_BYTES == (
        abi.RESULT_HEADER_BYTES + abi.RESULT_DIAGNOSTIC_BYTES
    )


@pytest.mark.parametrize("bindings,grid,threads,message", [
    ([MetalBinding(0, "texture", b"1234")], (1, 1, 1), (1, 1, 1), "unknown binding kind"),
    ([MetalBinding(31, "buffer", b"1234")], (1, 1, 1), (1, 1, 1), "out of range"),
    ([MetalBinding(0, "buffer", b"1234"), MetalBinding(0, "bytes", b"1234")],
     (1, 1, 1), (1, 1, 1), "bound twice"),
    ([MetalBinding(0, "buffer", b"")], (1, 1, 1), (1, 1, 1), "carries no bytes"),
    ([MetalBinding(0, "bytes", b"1234", readback=True)], (1, 1, 1), (1, 1, 1),
     "cannot be read back"),
    ([], (1, 0, 1), (1, 1, 1), "grid must be three positive"),
    ([], (1, 1, 1), (1, 1), "threads must be three positive"),
], ids=["kind", "index", "duplicate", "empty", "readback-bytes", "grid", "threads"])
def test_encoder_rejects_malformed_launches(bindings, grid, threads, message):
    with pytest.raises(MetalHostError, match=message):
        metal_host.encode_launch_packet(PROBE_SOURCE, "pcc_probe", bindings, grid, threads)


def test_result_decoder_rejects_everything_but_one_exact_result():
    fixed = struct.pack("<6q", abi.RESULT_MAGIC, abi.STAGE_DONE, 0, 1024, 32, 1)
    fixed += bytes(abi.RESULT_DIAGNOSTIC_BYTES)
    record = struct.pack("<2q", 1, 5) + b"hello" + bytes(3)
    decoded = metal_host.decode_launch_result(fixed + record)
    assert decoded.completed and decoded.readback == {1: b"hello"}
    assert (decoded.max_threads, decoded.execution_width) == (1024, 32)
    for broken, message in (
        (fixed[:100], "shorter than its fixed part"),
        (b"\0" * 8 + fixed[8:] + record, "wrong magic"),
        (fixed + record + bytes(8), "trailing bytes"),
        (fixed + record[:12], "ends inside a read-back record"),
        (fixed + struct.pack("<2q", 1, 500) + b"hello", "malformed read-back record"),
    ):
        with pytest.raises(MetalHostError, match=message):
            metal_host.decode_launch_result(broken)
    failed = struct.pack("<6q", abi.RESULT_MAGIC, abi.STAGE_DISPATCH, 41, 0, 0, 0)
    failed += bytes(abi.RESULT_DIAGNOSTIC_BYTES)
    assert not metal_host.decode_launch_result(failed).completed
    with pytest.raises(MetalHostError, match="carries read-back records"):
        metal_host.decode_launch_result(failed + record)


# --- the launcher process ----------------------------------------------------


@_needs_arm64_darwin
def test_runner_identity_names_the_owned_toolchain(runner):
    described = runner.to_dict()
    assert described["device_owner"] == "pcc-metal-device"
    assert described["entry"] == "_pcc_metal_device_packet_main"
    assert described["target"] == "arm64-apple-darwin"
    for key in ("source_sha256", "object_sha256", "image_sha256"):
        assert len(described[key]) == 64
    assert runner.path.read_bytes()[:4] == b"\xcf\xfa\xed\xfe"


@_needs_arm64_darwin
def test_runner_rejects_malformed_packets_before_opening_a_device(runner):
    good = _probe_packet(16, 8)
    count_at = abi.PACKET_BINDING_COUNT
    readback_bytes = bytearray(good)
    struct.pack_into(
        "<q", readback_bytes,
        abi.PACKET_HEADER_BYTES + 2 * abi.PACKET_BINDING_BYTES
        + abi.PACKET_BINDING_READBACK, 1,
    )
    unterminated = bytearray(good)
    source_at = abi.PACKET_HEADER_BYTES + 3 * abi.PACKET_BINDING_BYTES
    unterminated[source_at + len(PROBE_SOURCE.encode())] = ord("x")
    malformed = {
        "empty": b"",
        "truncated-header": good[:40],
        "magic": bytes(8) + good[8:],
        "short": good[:-8],
        "long": good + bytes(8),
        "count": good[:count_at] + struct.pack("<q", 40) + good[count_at + 8:],
        "readback-bytes": bytes(readback_bytes),
        "unterminated-source": bytes(unterminated),
    }
    for name, packet in malformed.items():
        result = metal_host.run_launch_packet(runner, packet)
        assert (result.stage, result.status) == (
            abi.STAGE_PACKET, abi.STATUS_BAD_ARGUMENT,
        ), name
        assert result.readback == {}


@_needs_arm64_darwin
def test_runner_executes_a_packet_and_matches_the_cpu_oracle(runner):
    count = 1000
    probe = metal_host.run_launch_packet(runner, _probe_packet(count, 1))
    if not _executed(probe):
        return
    assert probe.completed
    width = probe.execution_width
    assert 1 <= width <= probe.max_threads
    result = metal_host.run_launch_packet(runner, _probe_packet(count, width))
    assert result.completed, (result.stage, result.status, result.diagnostic)
    assert result.readback == {1: _probe_oracle(count)}
    assert probe.readback == result.readback


@_needs_arm64_darwin
def test_runner_reports_failures_in_band_without_readback(runner):
    probe = metal_host.run_launch_packet(runner, _probe_packet(8, 1))
    if not _executed(probe):
        return
    broken = metal_host.run_launch_packet(
        runner, _probe_packet(8, 1, source="kernel void pcc_probe( { nope }\n"),
    )
    assert (broken.stage, broken.status) == (
        abi.STAGE_PIPELINE, abi.STATUS_SOURCE_REJECTED,
    )
    assert "error" in broken.diagnostic
    assert broken.readback == {}

    missing = metal_host.run_launch_packet(
        runner,
        metal_host.encode_launch_packet(
            PROBE_SOURCE, "absent_kernel",
            [MetalBinding(0, metal_host.KIND_BUFFER, bytes(32))], (1, 1, 1), (1, 1, 1),
        ),
    )
    assert (missing.stage, missing.status) == (
        abi.STAGE_PIPELINE, abi.STATUS_FUNCTION_NOT_FOUND,
    )

    too_wide = metal_host.run_launch_packet(
        runner, _probe_packet(8, probe.max_threads + 1),
    )
    assert (too_wide.stage, too_wide.status) == (
        abi.STAGE_DISPATCH, abi.STATUS_DISPATCH_EXCEEDS_PIPELINE,
    )
    assert too_wide.readback == {}
    assert too_wide.max_threads == probe.max_threads


# --- Kernel IR through the owned device --------------------------------------


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
    return KernelModule("device_packet_copy_mod", funcs=(func,))


def _copy_args() -> PccPackedArgs:
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=16, dtype="f32", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=16, dtype="f32", device="metal:0"))
    args.add_scalar("u32", 4)
    return args


def test_plan_packet_requires_exactly_the_plan_buffers():
    module = _copy_module()
    plan = plan_metal_launch(module, _copy_args())
    source = emit_metal_source(module)
    packet = launch_packet_from_plan(
        plan, source, {"src": bytes(16), "dst": bytes(16)}, readback=("dst",),
    )
    header = struct.unpack_from("<10q", packet)
    assert header[abi.PACKET_BINDING_COUNT // 8] == 3
    assert header[abi.PACKET_THREADS // 8] == 16
    with pytest.raises(MetalLaunchError, match="exactly the plan's buffers"):
        launch_packet_from_plan(plan, source, {"src": bytes(16)}, readback=())
    with pytest.raises(MetalLaunchError, match="declared as 16 bytes"):
        launch_packet_from_plan(
            plan, source, {"src": bytes(8), "dst": bytes(16)}, readback=(),
        )
    with pytest.raises(MetalLaunchError, match="non-buffer arguments"):
        launch_packet_from_plan(
            plan, source, {"src": bytes(16), "dst": bytes(16)}, readback=("n",),
        )


@_needs_arm64_darwin
def test_kernel_ir_copy_plan_executes_on_the_owned_device(runner):
    module = _copy_module()
    values = (1.5, -2.25, 3.0, 4.75)
    launch = launch_plan_on_device(
        runner,
        plan_metal_launch(module, _copy_args()),
        emit_metal_source(module),
        {"src": struct.pack("<4f", *values), "dst": bytes(16)},
        readback=("dst",),
    )
    if not _executed(launch.result):
        return
    assert launch.result.completed, launch.result.to_dict()
    assert struct.unpack("<4f", launch.output("dst")) == values
    manifest = launch.to_dict()
    assert manifest["device_owner"] == "pcc-metal-device"
    assert manifest["kernel_source"] == KERNEL_SOURCE
    assert manifest["fallback_used"] is False
    assert manifest["whole_program_gpu"] is False
    assert manifest["kernel_entry"] == "copy_kernel"


@_needs_arm64_darwin
@pytest.mark.parametrize("m,n,k", [(5, 7, 3), (4, 4, 6)])
def test_tilelang_scalar_gemm_plan_matches_the_cpu_reference(runner, m, n, k):
    module = import_tilelang_source(
        TILELANG_METAL_MATMUL,
        outer_function="matmul_simdgroup",
        prim_func="gemm_kernel",
        constants={"M": m, "N": n, "K": k},
        module_name="device_packet_tilelang_matmul",
    )
    a = tuple(tuple(float(((i * 3) + kk) % 7 - 3) for kk in range(k)) for i in range(m))
    b = tuple(tuple(float(((kk * 2) - j) % 9 - 4) for j in range(n)) for kk in range(k))
    cpu = execute_scalar_tiled_gemm_reference(module, {"A": a, "B": b})
    args = PccPackedArgs(launch_device="metal:0")
    args.add_buffer(PccBufferHandle(nbytes=m * k * 2, dtype="f16", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=k * n * 2, dtype="f16", device="metal:0"))
    args.add_buffer(PccBufferHandle(nbytes=m * n * 4, dtype="f32", device="metal:0"))
    launch = launch_plan_on_device(
        runner,
        plan_metal_launch(module, args),
        emit_metal_source(module),
        {
            "A": struct.pack("<%de" % (m * k), *(v for row in a for v in row)),
            "B": struct.pack("<%de" % (k * n), *(v for row in b for v in row)),
            "C": bytes(m * n * 4),
        },
        readback=("C",),
    )
    if not _executed(launch.result):
        return
    assert launch.result.completed, launch.result.to_dict()
    device = struct.unpack("<%df" % (m * n), launch.output("C"))
    oracle = tuple(float(v) for row in cpu.outputs["C"] for v in row)
    assert device == pytest.approx(oracle, abs=1e-5)
    assert any(value != 0.0 for value in oracle)
