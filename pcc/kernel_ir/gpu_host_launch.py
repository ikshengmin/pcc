"""Generate the host-side launch code for ``@gpu.kernel`` functions.

A program that launches a kernel is ordinary pcc-compiled Python. For each
kernel the compiler emits a small Python wrapper into the host module; the
wrapper packs its arguments and calls one shared helper, which builds a launch
packet in native memory and hands it to the owned Metal device runtime
(``pcc_metal_device_launch_packet``) linked into the same executable.

Everything generated here is plain Python plus ``pcc.extern`` declarations and
``pcc.unsafe`` intrinsics. No Objective-C bridge, metallib or external tool is
involved, and nothing is imported from another module, so the host program
stays a single-file compile.

The packet and result offsets are taken from ``pcc.gpu_runtime.metal_device``
at generation time; that module remains their only definition.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Sequence

from pcc.gpu_runtime import metal_device as abi


class GpuHostLaunchError(ValueError):
    """A kernel cannot be given a host launcher."""


@dataclass(frozen=True)
class GpuLaunchBinding:
    """One kernel parameter as the host launcher sees it."""

    name: str
    kind: str
    dtype: str
    index: int
    written: bool = False


@dataclass(frozen=True)
class GpuKernelLaunch:
    """What the host needs to launch one kernel."""

    name: str
    entry: str
    source: str
    bindings: tuple[GpuLaunchBinding, ...]
    threads: int


_ELEMENT_BYTES = {"f32": 4, "i32": 4, "u32": 4}
_SCALAR_PACKERS = {
    "f32": "__pcc_gpu_f32_bytes",
    "i32": "__pcc_gpu_i32_bytes",
    "u32": "__pcc_gpu_u32_bytes",
}


# Kept as text: it is emitted into the host program, and the compiler must
# not depend on reading its own source at run time. Tests execute this text
# under CPython against the C conversion.
F32_BITS_SOURCE = '''def __pcc_gpu_f32_bits(bits: int) -> int:
    """Binary32 bits of the binary64 value ``bits``, rounded to nearest even.

    This is the C conversion from ``double`` to ``float``: out-of-range values
    become infinity and tiny ones become a subnormal or zero. Only integer
    operations are used, so the same text runs in a pcc-compiled program.
    """
    bits = bits & 0xFFFFFFFFFFFFFFFF
    sign: int = (bits >> 63) << 31
    exponent: int = (bits >> 52) & 0x7FF
    fraction: int = bits & 0xFFFFFFFFFFFFF
    if exponent == 0x7FF:
        if fraction == 0:
            return sign | 0x7F800000
        return sign | 0x7FC00000 | (fraction >> 29)
    if exponent == 0:
        # Zero, or a binary64 subnormal far below the smallest binary32 value.
        return sign
    mantissa: int = fraction | 0x10000000000000
    power: int = exponent - 1023
    if power > 127:
        return sign | 0x7F800000
    shift: int = 29
    if power < -126:
        shift = 29 + (-126 - power)
        if shift > 53:
            return sign
    quotient: int = mantissa >> shift
    remainder: int = mantissa & ((1 << shift) - 1)
    half: int = 1 << (shift - 1)
    if remainder > half or (remainder == half and (quotient & 1) == 1):
        quotient = quotient + 1
    if power < -126:
        # A subnormal; a carry into bit 23 is exactly the smallest normal.
        return sign | quotient
    if quotient == 0x1000000:
        quotient = 0x800000
        power = power + 1
        if power > 127:
            return sign | 0x7F800000
    return sign | ((power + 127) << 23) | (quotient & 0x7FFFFF)
'''


_PRELUDE_IMPORTS = '''from pcc.extern import extern, c_int64, c_obj, c_rawptr
from pcc.unsafe import (
    f64_bits,
    free,
    load_i8,
    load_i64,
    malloc,
    memcpy,
    memset,
    ptr_add,
    ptr_is_null,
    store_i64,
)

__pcc_gpu_launch_packet = extern(
    "pcc_metal_device_launch_packet",
    (c_rawptr, c_int64, c_rawptr, c_int64),
    c_int64,
)
__pcc_gpu_packet_result_bytes = extern(
    "pcc_metal_device_packet_result_bytes", (c_rawptr, c_int64), c_int64
)
__pcc_gpu_bytes_new = extern("py_bytes_new", (c_rawptr, c_int64), c_obj)
'''

# ``@NAME@`` placeholders are replaced with offsets from the device runtime.
# Raw addresses stay local integers; every managed value is copied into the
# packet before the device call, so the call itself touches native memory only.
_LAUNCH_HELPER = '''
def __pcc_gpu_word_bytes(value: int) -> bytes:
    return bytes([
        value & 255, (value >> 8) & 255, (value >> 16) & 255, (value >> 24) & 255,
    ])


def __pcc_gpu_u32_bytes(value) -> bytes:
    number: int = int(value)
    if number < 0 or number > 4294967295:
        raise ValueError("gpu.launch: u32 argument is out of range")
    return __pcc_gpu_word_bytes(number)


def __pcc_gpu_i32_bytes(value) -> bytes:
    number: int = int(value)
    if number < -2147483648 or number > 2147483647:
        raise ValueError("gpu.launch: i32 argument is out of range")
    return __pcc_gpu_word_bytes(number & 4294967295)


def __pcc_gpu_f32_bytes(value) -> bytes:
    return __pcc_gpu_word_bytes(__pcc_gpu_f32_bits(f64_bits(float(value))))


def __pcc_gpu_buffer_bytes(value, element_bytes: int, name: str) -> bytes:
    if not isinstance(value, bytes) and not isinstance(value, bytearray):
        raise TypeError(
            "gpu.launch: buffer '" + name + "' must be bytes or bytearray"
        )
    data = bytes(value)
    size: int = len(data)
    if size == 0:
        raise ValueError("gpu.launch: buffer '" + name + "' is empty")
    if size % element_bytes != 0:
        raise ValueError(
            "gpu.launch: buffer '" + name + "' is not a whole number of elements"
        )
    return data


def __pcc_gpu_launch(
    source, entry, kinds, indexes, payloads, readbacks, threads: int, count: int
):
    if count < 1:
        raise ValueError("gpu.launch: count must be at least 1")
    bindings: int = len(payloads)
    source_len: int = len(source)
    entry_len: int = len(entry)
    source_at: int = @PACKET_HEADER_BYTES@ + bindings * @PACKET_BINDING_BYTES@
    entry_at: int = source_at + (source_len + 7) // 8 * 8
    data_at: int = entry_at + (entry_len + 7) // 8 * 8
    total: int = data_at
    index: int = 0
    while index < bindings:
        total = total + (len(payloads[index]) + 7) // 8 * 8
        index = index + 1
    packet = malloc(total)
    if ptr_is_null(packet):
        raise MemoryError("gpu.launch: launch packet allocation failed")
    memset(packet, 0, total)
    store_i64(packet, @PACKET_MAGIC_AT@, @PACKET_MAGIC@)
    store_i64(packet, @PACKET_SOURCE_BYTES@, source_len)
    store_i64(packet, @PACKET_FUNCTION_BYTES@, entry_len)
    store_i64(packet, @PACKET_BINDING_COUNT@, bindings)
    store_i64(packet, @PACKET_GRID@, (count + threads - 1) // threads)
    store_i64(packet, @PACKET_GRID@ + 8, 1)
    store_i64(packet, @PACKET_GRID@ + 16, 1)
    store_i64(packet, @PACKET_THREADS@, threads)
    store_i64(packet, @PACKET_THREADS@ + 8, 1)
    store_i64(packet, @PACKET_THREADS@ + 16, 1)
    memcpy(ptr_add(packet, source_at), ptr_add(source, 24), source_len)
    memcpy(ptr_add(packet, entry_at), ptr_add(entry, 24), entry_len)
    cursor: int = data_at
    index = 0
    while index < bindings:
        payload = payloads[index]
        size: int = len(payload)
        record: int = @PACKET_HEADER_BYTES@ + index * @PACKET_BINDING_BYTES@
        store_i64(packet, record + @PACKET_BINDING_KIND@, kinds[index])
        store_i64(packet, record + @PACKET_BINDING_INDEX@, indexes[index])
        store_i64(packet, record + @PACKET_BINDING_NBYTES@, size)
        store_i64(packet, record + @PACKET_BINDING_READBACK@, readbacks[index])
        memcpy(ptr_add(packet, cursor), ptr_add(payload, 24), size)
        cursor = cursor + (size + 7) // 8 * 8
        index = index + 1
    capacity: int = __pcc_gpu_packet_result_bytes(packet, total)
    if capacity == 0:
        free(packet)
        raise RuntimeError("pcc gpu: the launch packet was rejected as malformed")
    result = malloc(capacity)
    if ptr_is_null(result):
        free(packet)
        raise MemoryError("gpu.launch: launch result allocation failed")
    written: int = __pcc_gpu_launch_packet(packet, total, result, capacity)
    free(packet)
    stage: int = load_i64(result, @RESULT_STAGE@)
    status: int = load_i64(result, @RESULT_STATUS@)
    if written == 0 or stage != @STAGE_DONE@:
        length: int = 0
        while length < @DIAGNOSTIC_LIMIT@ and load_i8(result, @RESULT_HEADER_BYTES@ + length) != 0:
            length = length + 1
        detail = __pcc_gpu_bytes_new(ptr_add(result, @RESULT_HEADER_BYTES@), length)
        free(result)
        if stage == @STAGE_OPEN@:
            raise RuntimeError(
                "pcc gpu: Metal device unavailable (status " + str(status) + ")"
            )
        raise RuntimeError(
            "pcc gpu: Metal launch failed at stage " + str(stage)
            + " with status " + str(status) + ": "
            + detail.decode("utf-8", "replace")
        )
    outputs = []
    records: int = load_i64(result, @RESULT_READBACK_COUNT@)
    cursor = @RESULT_FIXED_BYTES@
    index = 0
    while index < records:
        size = load_i64(result, cursor + @RESULT_RECORD_NBYTES@)
        outputs.append(
            __pcc_gpu_bytes_new(
                ptr_add(result, cursor + @RESULT_RECORD_HEADER_BYTES@), size
            )
        )
        cursor = cursor + @RESULT_RECORD_HEADER_BYTES@ + (size + 7) // 8 * 8
        index = index + 1
    free(result)
    return outputs
'''


def _substituted(template: str) -> str:
    values = {
        name: getattr(abi, name)
        for name in (
            "PACKET_MAGIC", "PACKET_MAGIC_AT", "PACKET_SOURCE_BYTES",
            "PACKET_FUNCTION_BYTES", "PACKET_BINDING_COUNT", "PACKET_GRID",
            "PACKET_THREADS", "PACKET_HEADER_BYTES", "PACKET_BINDING_KIND",
            "PACKET_BINDING_INDEX", "PACKET_BINDING_NBYTES",
            "PACKET_BINDING_READBACK", "PACKET_BINDING_BYTES", "RESULT_STAGE",
            "RESULT_STATUS", "RESULT_READBACK_COUNT", "RESULT_HEADER_BYTES",
            "RESULT_FIXED_BYTES", "RESULT_RECORD_NBYTES",
            "RESULT_RECORD_HEADER_BYTES", "STAGE_DONE", "STAGE_OPEN",
        )
    }
    values["DIAGNOSTIC_LIMIT"] = abi.RESULT_DIAGNOSTIC_BYTES - 1
    for name in sorted(values, key=len, reverse=True):
        template = template.replace("@" + name + "@", str(values[name]))
    if "@" in template:
        raise AssertionError("unreplaced placeholder in the GPU launch helper")
    return template


def launcher_name(kernel: str) -> str:
    return "__pcc_gpu_launch_" + kernel


def _bytes_literal(text: str) -> str:
    return repr(text.encode("utf-8") + b"\0")


def _kernel_launcher_source(kernel: GpuKernelLaunch) -> str:
    if kernel.threads < 1:
        raise GpuHostLaunchError(f"{kernel.name}: threads must be positive")
    if len(kernel.bindings) > abi.MAX_BINDING_INDEX + 1:
        raise GpuHostLaunchError(
            f"{kernel.name}: at most {abi.MAX_BINDING_INDEX + 1} parameters "
            "fit a Metal launch"
        )
    names = [f"__pcc_a{position}" for position in range(len(kernel.bindings))]
    lines = [
        "",
        f"def {launcher_name(kernel.name)}({', '.join(names + ['count'])}) -> None:",
    ]
    kinds: list[str] = []
    indexes: list[str] = []
    payloads: list[str] = []
    readbacks: list[str] = []
    written: list[str] = []
    for position, binding in enumerate(kernel.bindings):
        argument = names[position]
        indexes.append(str(binding.index))
        if binding.kind == "buffer":
            element = _ELEMENT_BYTES.get(binding.dtype)
            if element is None:
                raise GpuHostLaunchError(
                    f"{kernel.name}: buffer {binding.name!r} has unsupported "
                    f"element type {binding.dtype!r}"
                )
            if binding.written:
                lines += [
                    f"    if not isinstance({argument}, bytearray):",
                    "        raise TypeError("
                    + repr(
                        f"gpu.launch: kernel '{kernel.name}' writes buffer "
                        f"'{binding.name}'; pass a bytearray"
                    )
                    + ")",
                ]
                written.append(argument)
            payloads.append(
                f"__pcc_gpu_buffer_bytes({argument}, {element}, {binding.name!r})"
            )
            kinds.append(str(abi.BINDING_KIND_BUFFER))
            readbacks.append("1" if binding.written else "0")
        elif binding.kind == "scalar":
            packer = _SCALAR_PACKERS.get(binding.dtype)
            if packer is None:
                raise GpuHostLaunchError(
                    f"{kernel.name}: scalar {binding.name!r} has unsupported "
                    f"type {binding.dtype!r}"
                )
            if binding.written:
                raise GpuHostLaunchError(
                    f"{kernel.name}: scalar {binding.name!r} cannot be written"
                )
            payloads.append(f"{packer}({argument})")
            kinds.append(str(abi.BINDING_KIND_BYTES))
            readbacks.append("0")
        else:
            raise GpuHostLaunchError(
                f"{kernel.name}: parameter {binding.name!r} has unsupported "
                f"kind {binding.kind!r}"
            )
    # The packet owns a separate copy per binding, not a shared-buffer table.
    # Preserving a writable alias needs that table; never silently break the
    # kernel's pointer identity by giving the same host buffer two copies.
    for left, first in enumerate(kernel.bindings):
        if first.kind != "buffer":
            continue
        for right in range(left + 1, len(kernel.bindings)):
            second = kernel.bindings[right]
            if second.kind != "buffer" or not (first.written or second.written):
                continue
            lines += [
                f"    if {names[left]} is {names[right]}:",
                "        raise ValueError("
                + repr(
                    f"gpu.launch: aliased buffers '{first.name}' and "
                    f"'{second.name}' are not supported when either is written"
                )
                + ")",
            ]
    lines += [
        "    __pcc_outputs = __pcc_gpu_launch(",
        f"        {_bytes_literal(kernel.source)},",
        f"        {_bytes_literal(kernel.entry)},",
        f"        [{', '.join(kinds)}],",
        f"        [{', '.join(indexes)}],",
        f"        [{', '.join(payloads)}],",
        f"        [{', '.join(readbacks)}],",
        f"        {kernel.threads},",
        "        count,",
        "    )",
        f"    if len(__pcc_outputs) != {len(written)}:",
        "        raise RuntimeError("
        "\"pcc gpu: the launch result has the wrong number of buffers\")",
    ]
    for position, argument in enumerate(written):
        lines += [
            f"    __pcc_data{position} = __pcc_outputs[{position}]",
            f"    {argument}[0:len(__pcc_data{position})] = __pcc_data{position}",
        ]
    return "\n".join(lines) + "\n"


def _add_demo_source(kernel: GpuKernelLaunch) -> str:
    shape = [(binding.kind, binding.dtype, binding.written) for binding in kernel.bindings]
    expected_shape = [
        ("buffer", "f32", False), ("buffer", "f32", False), ("buffer", "f32", True),
    ]
    if (
        len(shape) != 4
        or shape[:3] != expected_shape
        or shape[3][0] != "scalar"
        or shape[3][1] not in ("u32", "i32")
    ):
        raise GpuHostLaunchError(
            "gpu.run_add_f32_demo() needs a kernel named 'add' that reads two "
            "ptr_f32 buffers, writes a third and takes one integer count"
        )
    left = (1.0, 2.0, 3.0, 4.0)
    right = (10.0, 20.0, 30.0, 40.0)
    total = tuple(a + b for a, b in zip(left, right))
    return "\n".join([
        "",
        "def __pcc_gpu_run_add_f32_demo() -> int:",
        "    __pcc_out = bytearray(16)",
        f"    {launcher_name(kernel.name)}(",
        f"        {struct.pack('<4f', *left)!r},",
        f"        {struct.pack('<4f', *right)!r},",
        "        __pcc_out, 4, 4,",
        "    )",
        f"    if bytes(__pcc_out) == {struct.pack('<4f', *total)!r}:",
        "        print(\"OK Metal add kernel via owned device runtime\")",
        "        return 0",
        "    print(\"FAIL Metal add kernel via owned device runtime\")",
        "    return 9",
        "",
    ])


def render_launch_prelude(
    kernels: Sequence[GpuKernelLaunch], *, add_demo: bool = False
) -> str:
    """Python source defining the shared helper and one launcher per kernel.

    ``add_demo`` also defines ``__pcc_gpu_run_add_f32_demo`` over the kernel
    named ``add``.
    """
    names = [kernel.name for kernel in kernels]
    if len(set(names)) != len(names):
        raise GpuHostLaunchError("GPU kernel names must be unique in a module")
    parts = [
        "# --- generated by pcc: host launch for @gpu.kernel functions ---\n",
        _PRELUDE_IMPORTS,
        "\n",
        F32_BITS_SOURCE,
        _substituted(_LAUNCH_HELPER),
    ]
    parts += [_kernel_launcher_source(kernel) for kernel in kernels]
    if add_demo:
        by_name = {kernel.name: kernel for kernel in kernels}
        if "add" not in by_name:
            raise GpuHostLaunchError(
                "gpu.run_add_f32_demo() needs a kernel named 'add' in this module"
            )
        parts.append(_add_demo_source(by_name["add"]))
    parts.append("# --- end of generated GPU host launch ---\n")
    return "".join(parts)
