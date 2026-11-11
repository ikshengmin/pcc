"""Launch a Kernel IR Metal plan on the owned Metal device runtime.

The Metal source and the validated launch plan are the ones the existing
Kernel IR path produces. Only the launcher differs: the freestanding device
runtime in ``pcc.gpu_runtime`` replaces the Objective-C bridge, its external
compiler and the ``ctypes`` adapter.

A result here is a device result for the named kernel on the machine that ran
it. It is host-pcc0 evidence: the launcher is a separate process built by the
host compiler, not a pcc1-built program, and no collector takes part.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Collection, Mapping

from pcc.gpu_runtime import metal_host
from pcc.kernel_ir.metal_launch import MetalLaunchError, MetalLaunchPlan
from pcc.kernel_ir.metal_runtime_abi import _pack_scalar


KERNEL_SOURCE = "pcc-kernel-ir"


@dataclass(frozen=True)
class KernelDeviceLaunch:
    """One Kernel IR launch on the owned device, with its input identities."""

    plan: MetalLaunchPlan
    result: metal_host.MetalPacketResult
    runner: metal_host.MetalPacketRunner
    source_sha256: str
    packet_sha256: str
    output_names: Mapping[int, str]

    def output(self, name: str) -> bytes:
        for index, bound in self.output_names.items():
            if bound == name:
                return self.result.readback[index]
        raise MetalLaunchError(f"{name!r} was not read back from the device")

    def to_dict(self) -> dict[str, object]:
        return {
            "device_owner": metal_host.DEVICE_OWNER,
            "kernel_source": KERNEL_SOURCE,
            "host_backend": "self",
            "fallback_used": False,
            "evidence": "host-pcc0 launcher process; not pcc1-native, no GC",
            "kernel_entry": self.plan.kernel_entry,
            "dispatch": self.plan.dispatch.to_dict(),
            "metal_source_sha256": self.source_sha256,
            "launch_packet_sha256": self.packet_sha256,
            "runner": self.runner.to_dict(),
            "result": self.result.to_dict(),
            "whole_program_gpu": False,
        }


def launch_packet_from_plan(
    plan: MetalLaunchPlan,
    source: str,
    buffers: Mapping[str, bytes],
    *,
    readback: Collection[str],
) -> bytes:
    """Encode a validated launch plan and its buffer contents as one packet."""
    buffer_names = {arg.name for arg in plan.args if arg.kind == "buffer"}
    if set(buffers) != buffer_names:
        raise MetalLaunchError(
            "buffer contents must be supplied for exactly the plan's buffers: "
            f"expected {sorted(buffer_names)}, got {sorted(buffers)}"
        )
    unknown = set(readback) - buffer_names
    if unknown:
        raise MetalLaunchError(f"cannot read back non-buffer arguments {sorted(unknown)}")
    bindings = []
    for arg in plan.args:
        if arg.kind == "buffer":
            data = bytes(buffers[arg.name])
            if len(data) != arg.provided_nbytes:
                raise MetalLaunchError(
                    f"buffer {arg.name!r} is declared as {arg.provided_nbytes} "
                    f"bytes but {len(data)} were supplied"
                )
            bindings.append(metal_host.MetalBinding(
                index=arg.index, kind=metal_host.KIND_BUFFER, data=data,
                readback=arg.name in readback,
            ))
        elif arg.kind == "scalar":
            payload, _size = _pack_scalar(arg.dtype, arg.scalar_value)
            bindings.append(metal_host.MetalBinding(
                index=arg.index, kind=metal_host.KIND_BYTES, data=payload,
            ))
        else:
            raise MetalLaunchError(
                f"launch argument {arg.name!r} has unsupported kind {arg.kind!r}"
            )
    try:
        return metal_host.encode_launch_packet(
            source, plan.kernel_entry, bindings,
            plan.dispatch.threadgroups_per_grid,
            plan.dispatch.threads_per_threadgroup,
        )
    except metal_host.MetalHostError as exc:
        raise MetalLaunchError(str(exc)) from exc


def launch_plan_on_device(
    runner: metal_host.MetalPacketRunner,
    plan: MetalLaunchPlan,
    source: str,
    buffers: Mapping[str, bytes],
    *,
    readback: Collection[str],
    timeout: float = 120.0,
) -> KernelDeviceLaunch:
    """Run one validated launch plan on the device and return its result.

    The result is returned whatever its stage; callers decide whether an
    unavailable device is acceptable. Nothing here falls back to another
    launcher or to the CPU.
    """
    packet = launch_packet_from_plan(plan, source, buffers, readback=readback)
    result = metal_host.run_launch_packet(runner, packet, timeout=timeout)
    return KernelDeviceLaunch(
        plan=plan,
        result=result,
        runner=runner,
        source_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        packet_sha256=hashlib.sha256(packet).hexdigest(),
        output_names={
            arg.index: arg.name
            for arg in plan.args
            if arg.kind == "buffer" and arg.name in readback
        },
    )
