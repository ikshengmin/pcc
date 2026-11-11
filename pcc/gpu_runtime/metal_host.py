"""Host-side launch packet codec and launcher builder for ``metal_device``.

The freestanding device runtime accepts one self-describing launch packet and
answers with one result. This module encodes and decodes those byte layouts,
builds the launcher executable with the pcc frontend, the self backend, the
owned object writer and the owned Mach-O linker, and runs it as a child
process.

No ``ctypes`` adapter, Objective-C bridge or external compiler takes part. The
layout offsets are read from ``metal_device`` so the runtime source remains
their only definition.
"""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from pcc.gpu_runtime import metal_device as abi


TARGET = "arm64-apple-darwin"
RUNNER_ENTRY = "_pcc_metal_device_packet_main"
RUNNER_NAME = "pcc-metal-device-launch"
DEVICE_OWNER = "pcc-metal-device"

KIND_BUFFER = "buffer"
KIND_BYTES = "bytes"
_KIND_CODES = {
    KIND_BUFFER: abi.BINDING_KIND_BUFFER,
    KIND_BYTES: abi.BINDING_KIND_BYTES,
}


class MetalHostError(ValueError):
    """A launch packet, a result or the launcher process is unusable."""


@dataclass(frozen=True)
class MetalBinding:
    """One launch argument: a device buffer or inline bytes at a buffer index."""

    index: int
    kind: str
    data: bytes
    readback: bool = False


@dataclass(frozen=True)
class MetalPacketResult:
    """The decoded answer to one launch packet."""

    stage: int
    status: int
    max_threads: int
    execution_width: int
    diagnostic: str
    readback: Mapping[int, bytes] = field(default_factory=dict)
    language_version: int = 0

    @property
    def completed(self) -> bool:
        """True only when the command buffer completed on a device."""
        return self.stage == abi.STAGE_DONE and self.status == abi.STATUS_OK

    @property
    def device_unavailable(self) -> bool:
        return self.stage == abi.STAGE_OPEN

    def to_dict(self) -> dict[str, object]:
        return {
            "device_owner": DEVICE_OWNER,
            "stage": self.stage,
            "status": self.status,
            "completed": self.completed,
            "max_threads_per_threadgroup": self.max_threads,
            "thread_execution_width": self.execution_width,
            "metal_language_version": self.language_version,
            "diagnostic": self.diagnostic,
            "readback": {
                str(index): hashlib.sha256(data).hexdigest()
                for index, data in sorted(self.readback.items())
            },
        }


@dataclass(frozen=True)
class MetalPacketRunner:
    """A launcher executable and the identities of what produced it."""

    path: Path
    target: str
    source_sha256: str
    object_sha256: str
    image_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "device_owner": DEVICE_OWNER,
            "path": str(self.path),
            "target": self.target,
            "entry": RUNNER_ENTRY,
            "source_sha256": self.source_sha256,
            "object_sha256": self.object_sha256,
            "image_sha256": self.image_sha256,
            "toolchain": "pcc frontend, self backend, owned Mach-O writer and linker",
        }


def _pad8(data: bytes) -> bytes:
    return data + bytes(-len(data) % 8)


def _extent(name: str, values: Sequence[int]) -> tuple[int, int, int]:
    extent = tuple(int(value) for value in values)
    if len(extent) != 3 or any(value < 1 for value in extent):
        raise MetalHostError(f"{name} must be three positive integers, got {values!r}")
    return extent  # type: ignore[return-value]


def _text(name: str, value: str) -> bytes:
    if not isinstance(value, str) or value == "":
        raise MetalHostError(f"{name} must be a non-empty string")
    encoded = value.encode("utf-8")
    if b"\0" in encoded:
        raise MetalHostError(f"{name} must not contain a NUL byte")
    return encoded + b"\0"


def encode_launch_packet(
    source: str,
    function: str,
    bindings: Sequence[MetalBinding],
    grid: Sequence[int],
    threads: Sequence[int],
    *,
    language_version: int = 0,
) -> bytes:
    """Encode one launch as the packet ``pcc_metal_device_launch_packet`` reads.

    ``language_version`` is a ``MTLLanguageVersion`` value; zero selects the
    runtime's default.
    """
    if language_version < 0:
        raise MetalHostError("Metal language version must not be negative")
    source_bytes = _text("Metal source", source)
    function_bytes = _text("kernel entry name", function)
    grid_extent = _extent("grid", grid)
    threads_extent = _extent("threads", threads)
    if len(bindings) > abi.MAX_BINDING_INDEX + 1:
        raise MetalHostError(
            f"at most {abi.MAX_BINDING_INDEX + 1} bindings fit a Metal launch"
        )
    seen: set[int] = set()
    descriptors = b""
    payload = b""
    for binding in bindings:
        if binding.kind not in _KIND_CODES:
            raise MetalHostError(f"unknown binding kind {binding.kind!r}")
        if not 0 <= binding.index <= abi.MAX_BINDING_INDEX:
            raise MetalHostError(f"binding index {binding.index} is out of range")
        if binding.index in seen:
            raise MetalHostError(f"binding index {binding.index} is bound twice")
        seen.add(binding.index)
        data = bytes(binding.data)
        if not data:
            raise MetalHostError(f"binding {binding.index} carries no bytes")
        if binding.readback and binding.kind != KIND_BUFFER:
            raise MetalHostError(
                f"binding {binding.index} is inline bytes and cannot be read back"
            )
        descriptors += struct.pack(
            "<4q", _KIND_CODES[binding.kind], binding.index, len(data),
            1 if binding.readback else 0,
        )
        payload += _pad8(data)
    header = struct.pack(
        "<11q", abi.PACKET_MAGIC, len(source_bytes), len(function_bytes),
        len(bindings), *grid_extent, *threads_extent, language_version,
    )
    assert len(header) == abi.PACKET_HEADER_BYTES
    assert len(descriptors) == len(bindings) * abi.PACKET_BINDING_BYTES
    packet = (
        header + descriptors + _pad8(source_bytes) + _pad8(function_bytes) + payload
    )
    if len(packet) > abi.PACKET_MAX_BYTES:
        raise MetalHostError("launch packet exceeds the runtime's size limit")
    return packet


def decode_launch_result(data: bytes) -> MetalPacketResult:
    """Decode a launch result, rejecting anything that is not exactly one."""
    if len(data) < abi.RESULT_FIXED_BYTES:
        raise MetalHostError(
            f"launch result is {len(data)} bytes, shorter than its fixed part"
        )
    magic, stage, status, max_threads, width, records, language = (
        struct.unpack_from("<7q", data)
    )
    if magic != abi.RESULT_MAGIC:
        raise MetalHostError("launch result has the wrong magic")
    region = data[abi.RESULT_HEADER_BYTES:abi.RESULT_FIXED_BYTES]
    diagnostic = region.split(b"\0", 1)[0].decode("utf-8", "replace")
    readback: dict[int, bytes] = {}
    offset = abi.RESULT_FIXED_BYTES
    if stage != abi.STAGE_DONE and (records != 0 or len(data) != offset):
        raise MetalHostError("a failed launch result carries read-back records")
    for _ in range(records):
        if offset + abi.RESULT_RECORD_HEADER_BYTES > len(data):
            raise MetalHostError("launch result ends inside a read-back record")
        index, nbytes = struct.unpack_from("<2q", data, offset)
        offset += abi.RESULT_RECORD_HEADER_BYTES
        if nbytes <= 0 or offset + nbytes > len(data) or index in readback:
            raise MetalHostError("launch result has a malformed read-back record")
        readback[index] = data[offset:offset + nbytes]
        offset += nbytes + (-nbytes % 8)
    if offset != len(data):
        raise MetalHostError("launch result has trailing bytes")
    return MetalPacketResult(
        stage=stage, status=status, max_threads=max_threads,
        execution_width=width, diagnostic=diagnostic, readback=readback,
        language_version=language,
    )


def build_device_runtime_object(
    out_dir: str | Path, *, target_triple: str = TARGET
) -> Path:
    """Compile the device runtime to one object file and return its path.

    A program that launches kernels links this object; it is also the whole
    launcher executable. Only Darwin arm64 is a Metal target.
    """
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.self_backend_target_match import is_aarch64_darwin_triple
    from pcc.frontends.python import pipeline

    if not is_aarch64_darwin_triple(target_triple):
        raise MetalHostError(
            "the Metal device runtime targets Darwin arm64, not " + target_triple
        )
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    ir_path = directory / "metal_device.ll"
    pipeline.compile_python(
        str(Path(abi.__file__)), str(ir_path), emit_llvm_only=True,
        python_library=True, backend="self", target_triple=target_triple,
    )
    path = directory / "metal_device.o"
    path.write_bytes(emit_owned_object(ir_path.read_text(), target_triple))
    return path


def build_packet_runner(out_dir: str | Path) -> MetalPacketRunner:
    """Build the launcher executable from the device runtime source.

    The image is a Darwin arm64 executable whatever the build host is; it can
    only run on such a machine.
    """
    from pcc.backend.macho_exec import link_executable

    directory = Path(out_dir)
    source = Path(abi.__file__).read_bytes()
    obj = build_device_runtime_object(directory).read_bytes()
    image = link_executable(
        [obj], entry=RUNNER_ENTRY, identifier=RUNNER_NAME.encode("ascii"),
    )
    path = directory / RUNNER_NAME
    path.write_bytes(image)
    path.chmod(0o755)
    runner = MetalPacketRunner(
        path=path,
        target=TARGET,
        source_sha256=hashlib.sha256(source).hexdigest(),
        object_sha256=hashlib.sha256(obj).hexdigest(),
        image_sha256=hashlib.sha256(image).hexdigest(),
    )
    (directory / (RUNNER_NAME + ".json")).write_text(
        json.dumps(runner.to_dict(), indent=2, sort_keys=True) + "\n"
    )
    return runner


def run_launch_packet(
    runner: MetalPacketRunner,
    packet: bytes,
    *,
    timeout: float = 120.0,
) -> MetalPacketResult:
    """Run one packet in a launcher process and decode its result."""
    try:
        ran = subprocess.run(
            [str(runner.path)], input=packet, capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise MetalHostError(f"Metal launcher timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise MetalHostError(f"Metal launcher could not be executed: {exc}") from exc
    if ran.returncode != abi.PROCESS_OK:
        raise MetalHostError(
            f"Metal launcher exited with status {ran.returncode}: "
            + ran.stderr[-400:].decode("utf-8", "replace")
        )
    return decode_launch_result(ran.stdout)
