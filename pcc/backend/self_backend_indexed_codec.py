from __future__ import annotations

"""Versioned process-boundary codec for direct indexed self-backend modules."""

import json
import os
import struct
from typing import cast

from .self_backend_ir import ArgInfo, GlobalDef, ParsedFunction, ParsedModule, TypeDesc
from .self_backend_kernel import (
    IndexedFunctionSeed,
    IndexedFunctionKernel,
    get_indexed_function_kernel,
)
from .self_backend_value_arena import CompilerIntArena


# Corrupt sidecars are an input/value contract failure local to this codec.
# Keeping the builtin exception local prevents a cross-module exception-class
# lookup from replacing every strict no-libpython codec function with a stub.
BackendUnavailable = ValueError


def _wire_str(value, name: str) -> str:
    if not isinstance(value, str):
        raise BackendUnavailable("indexed module has invalid " + name)
    return value


def _wire_int(value, name: str) -> int:
    if not isinstance(value, int):
        raise BackendUnavailable("indexed module has invalid " + name)
    return value


def _wire_bool(value, name: str) -> bool:
    if not isinstance(value, bool):
        raise BackendUnavailable("indexed module has invalid " + name)
    return value


_SCHEMA = "pcc.self-backend.indexed-module.v1"
_DEBUG_SCHEMA = "pcc.self-backend.indexed-module.v2"
_ODR_SCHEMA = "pcc.self-backend.indexed-module.v3"
_MAGIC = b"PCCIDXMOD1\n"
_MAX_HEADER_BYTES = 512 * 1024 * 1024

# (wire name, final-kernel field, reconstructed-seed field).  The order is
# part of v1 and is the raw payload order after the JSON header. Atomic memory
# payload IDs address two adjacent instruction_record_scalars rows: operands,
# then (ordering text ID, explicit alignment or 0, 0, 0). Legacy cold atomic
# payloads keep their five-field diagnostic form; new projections append align.
_ARENA_FIELDS = (
    ("call_records", "call_scalars", "records"),
    ("call_args", "call_arg_scalars", "args"),
    ("block_facts", "block_facts", "block_facts"),
    ("instruction_facts", "instruction_facts", "instruction_facts"),
    ("instruction_kinds", "instruction_kind_ids", "instruction_kind_ids"),
    ("instruction_metadata", "instruction_metadata", "instruction_metadata"),
    (
        "instruction_record_dests",
        "instruction_record_dest_ids",
        "instruction_record_dest_ids",
    ),
    (
        "instruction_record_scalars",
        "instruction_record_scalars",
        "instruction_record_scalars",
    ),
    ("gep_indices", "gep_index_scalars", "gep_index_scalars"),
    ("gep_scalars", "gep_scalars", "gep_scalars"),
    (
        "instruction_overflow_uses",
        "instruction_overflow_use_ids",
        "instruction_overflow_use_ids",
    ),
    ("terminator_cases", "terminator_case_scalars", "terminator_case_scalars"),
    ("terminators", "terminator_scalars", "terminator_scalars"),
    ("block_phi_facts", "block_phi_facts", "block_phi_facts"),
    ("phi_incoming", "phi_incoming_scalars", "phi_incoming_scalars"),
    ("phi_scalars", "phi_scalars", "phi_scalars"),
    ("error_edges", "error_edge_scalars", "error_edge_scalars"),
    ("error_edge_spans", "error_edge_spans", "error_edge_spans"),
    ("error_landings", "error_landing_scalars", "error_landing_scalars"),
    ("value_scalars", "value_scalars", ""),
    ("definition_positions", "definition_positions", ""),
    ("used_value_ids", "used_value_ids", ""),
)


def _type_to_wire(value: TypeDesc | None):
    if value is None:
        return None
    return [
        value.kind,
        value.width,
        _type_to_wire(value.pointee),
        value.count,
        _type_to_wire(value.elem),
        value.name,
        [_type_to_wire(field) for field in value.fields],
    ]


def _type_from_wire(value) -> TypeDesc | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 7:
        raise BackendUnavailable("indexed module has an invalid type record")
    fields = value[6]
    if not isinstance(fields, list):
        raise BackendUnavailable("indexed module has invalid type fields")
    pointee = _type_from_wire(value[2])
    elem = _type_from_wire(value[4])
    return TypeDesc(
        kind=_wire_str(value[0], "type kind"),
        width=_wire_int(value[1], "type width"),
        pointee=pointee,
        count=_wire_int(value[3], "type count"),
        elem=elem,
        name=_wire_str(value[5], "type name"),
        fields=tuple(_type_from_wire(field) for field in fields),
    )


def _cold_to_wire(value):
    if isinstance(value, TypeDesc):
        return {"t": _type_to_wire(value)}
    if isinstance(value, tuple):
        return {"q": [_cold_to_wire(item) for item in value]}
    if isinstance(value, list):
        return {"l": [_cold_to_wire(item) for item in value]}
    if isinstance(value, dict):
        return {
            "d": [
                [_cold_to_wire(key), _cold_to_wire(item)]
                for key, item in value.items()
            ]
        }
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise BackendUnavailable(
        "indexed module cold payload is not serializable: "
        + type(value).__name__
    )


def _cold_from_wire(value):
    if not isinstance(value, dict):
        return value
    if len(value) != 1:
        raise BackendUnavailable("indexed module has an invalid cold payload")
    if "t" in value:
        result = _type_from_wire(value["t"])
        if result is None:
            raise BackendUnavailable("indexed module cold type is null")
        return result
    if "q" in value:
        items = value["q"]
        if not isinstance(items, list):
            raise BackendUnavailable("indexed module cold tuple is invalid")
        return tuple(_cold_from_wire(item) for item in items)
    if "l" in value:
        items = value["l"]
        if not isinstance(items, list):
            raise BackendUnavailable("indexed module cold list is invalid")
        return [_cold_from_wire(item) for item in items]
    if "d" in value:
        rows = value["d"]
        if not isinstance(rows, list):
            raise BackendUnavailable("indexed module cold dict is invalid")
        out = {}
        for row in rows:
            if not isinstance(row, list) or len(row) != 2:
                raise BackendUnavailable("indexed module cold dict row is invalid")
            out[_cold_from_wire(row[0])] = _cold_from_wire(row[1])
        return out
    raise BackendUnavailable("indexed module cold payload tag is invalid")


def _global_to_wire(value: GlobalDef):
    return [
        value.name,
        _type_to_wire(value.type),
        value.initializer,
        value.is_constant,
        value.is_internal,
        value.tls_model,
        value.alignment,
        value.ir_prefix,
        [item for item in value.trailing_attributes],
    ]


def _global_from_wire(value, type_context=None) -> GlobalDef:
    if not isinstance(value, list) or len(value) != 9:
        raise BackendUnavailable("indexed module has an invalid global record")
    value_type = _type_from_wire(value[1])
    if value_type is None:
        raise BackendUnavailable("indexed module global type is null")
    trailing = value[8]
    if not isinstance(trailing, list):
        raise BackendUnavailable("indexed module global attributes are invalid")
    return GlobalDef(
        name=_wire_str(value[0], "global name"),
        type_context=type_context,
        type=value_type,
        initializer=_wire_str(value[2], "global initializer"),
        is_constant=_wire_bool(value[3], "global constant flag"),
        is_internal=_wire_bool(value[4], "global internal flag"),
        tls_model=_wire_str(value[5], "global TLS model"),
        alignment=_wire_int(value[6], "global alignment"),
        ir_prefix=_wire_str(value[7], "global IR prefix"),
        trailing_attributes=tuple(
            _wire_str(item, "global trailing attribute") for item in trailing
        ),
    )


def _kernel_arenas(
    kernel: IndexedFunctionKernel,
) -> tuple[tuple[str, CompilerIntArena], ...]:
    out: list[tuple[str, CompilerIntArena]] = []
    for wire_name, kernel_field, _seed_field in _ARENA_FIELDS:
        arena = cast(CompilerIntArena, getattr(kernel, kernel_field))
        if not isinstance(arena, CompilerIntArena):
            raise BackendUnavailable(
                "indexed module kernel arena is invalid: " + wire_name
            )
        out.append((wire_name, arena))
    return tuple(out)


def _function_to_wire(function: ParsedFunction, kernel: IndexedFunctionKernel):
    value_count = len(kernel.value_names)
    if len(kernel.value_scalars) != value_count * 8:
        raise BackendUnavailable(
            "indexed module requires complete value scalar columns"
        )
    if len(kernel.definition_positions) != value_count:
        raise BackendUnavailable(
            "indexed module requires complete definition positions"
        )
    if len(kernel.slot_scalars) != 0 or len(kernel.block_layout_ids) != 0:
        raise BackendUnavailable(
            "indexed module sidecar must be written before stack preparation"
        )
    value_id = 0
    while value_id < value_count:
        if (
            kernel.value_scalars.get_unchecked(value_id * 8 + 2) != -1
            or kernel.value_scalars.get_unchecked(value_id * 8 + 3) != -1
            or kernel.value_scalars.get_unchecked(value_id * 8 + 5) != -1
        ):
            raise BackendUnavailable(
                "indexed module sidecar contains prepared value state"
            )
        value_id += 1
    arithmetic_flags = []
    for instruction_id, flags in kernel.instruction_arithmetic_flag_values.items():
        arithmetic_flags.append(
            [instruction_id, [flag for flag in flags]]
        )
    arena_lengths = []
    for wire_name, arena in _kernel_arenas(kernel):
        arena_lengths.append([wire_name, len(arena)])
    value = {
        "name": function.name,
        "ret_type": _type_to_wire(function.ret_type),
        "args": [
            [arg.name, _type_to_wire(arg.type)] for arg in function.args
        ],
        "is_global": function.is_global,
        "is_vararg": function.is_vararg,
        "block_names": [name for name in kernel.block_names],
        "value_names": [name for name in kernel.value_names],
        "first_duplicate_definition_value_id": (
            kernel.first_duplicate_definition_value_id
        ),
        "instruction_use_total": kernel.instruction_use_total,
        "instruction_arithmetic_flags": arithmetic_flags,
        "cold_instruction_data": [
            _cold_to_wire(item) for item in kernel.cold_instruction_data
        ],
        "call_texts": [text for text in kernel.call_texts],
        "types": [_type_to_wire(item) for item in kernel.types],
        "arenas": arena_lengths,
    }
    # Target validation belongs to emission; validate the linkage/global pair
    # here without silently dropping any non-legacy value during transport.
    linkage = function.definition_linkage
    if linkage == "":
        linkage = "external" if function.is_global else "internal"
    if linkage not in ("external", "internal", "external_odr"):
        raise BackendUnavailable("unsupported indexed function definition linkage")
    if function.is_global != (linkage != "internal"):
        raise BackendUnavailable("inconsistent indexed function definition linkage")
    if linkage == "external_odr":
        value["definition_linkage"] = linkage
    return value


# At most 64 KiB of wire bytes per write, plus 8,192 scalar byte objects.
# Keep packing scratch independent of arena length without changing <q coercion.
_HOST_ARENA_CHUNK_SCALARS = 8192


def _write_host_arena(stream, arena: CompilerIntArena) -> None:
    # As before, the caller supplies a blocking binary writer that writes the
    # complete bytes argument or raises. Earlier chunks can remain written if
    # a later scalar cannot be packed; callers must discard failed sidecars.
    chunks = []
    index = 0
    while index < len(arena):
        chunks.append(struct.pack("<q", arena.get_unchecked(index)))
        index += 1
        if len(chunks) == _HOST_ARENA_CHUNK_SCALARS:
            stream.write(b"".join(chunks))
            chunks = []
    if chunks or index == 0:
        stream.write(b"".join(chunks))


def _bounded_capture_budget(module: ParsedModule, max_bytes: int, diagnostic=None) -> None:
    """Reject oversized diagnostics before constructing wire lists or buffers.

    This is a conservative upper bound, deliberately not a fit guarantee. Raw
    arenas are counted without reading them. Cold cells are traversed without
    copying. Strings charge their exact ensure_ascii JSON representation;
    Type records count their raw JSON layout, retaining the old conservative
    integer bound above 64 bits. Outer scalar/container overhead remains
    conservative; this is not an exact whole-wire size or a fit guarantee.
    The ordinary sidecar path does not enter this diagnostic walk.
    """
    budget = [0]

    def charge(count):
        budget[0] += count
        if budget[0] > max_bytes:
            if diagnostic is not None:
                diagnostic["observed_bytes"] = budget[0]
            raise BackendUnavailable("indexed module diagnostic exceeds the size limit")

    def string(value):
        # Reserve minimum bytes before the added escape inspection.
        # Each such scan is bounded by the remaining capture budget,
        # without materializing JSON or an encoded copy.
        charge(len(value) + 2)
        if value.isascii() and value.isprintable():
            # Ordinary names/IR text need only two allocation-free counts.
            charge(value.count('"') + value.count("\\"))
        else:
            for character in value:
                point = ord(character)
                if (
                    point == 34 or point == 92 or point == 8
                    or point == 12 or point == 10 or point == 13
                    or point == 9
                ):
                    charge(1)
                elif point < 0x20 or 0x7F <= point <= 0xFFFF:
                    # Includes DEL and lone UTF-16 surrogates: \\uXXXX.
                    charge(5)
                elif point > 0xFFFF:
                    # A supplementary scalar is a JSON surrogate pair.
                    charge(11)

    def type_integer(value):
        # bool is an int subclass, but JSON uses true/false rather than 1/0.
        if isinstance(value, bool):
            charge(4 if value else 5)
            return
        bits = value.bit_length()
        if bits > 64:
            # Preserve the existing bound for arbitrary-size integers. Do not
            # allocate a decimal string or add a size-dependent division loop.
            charge(bits + 1)
            return
        if value < 0:
            charge(1)
            value = -value
        # At most 20 digits for a value with at most 64 significant bits.
        charge(1)
        while value >= 10:
            charge(1)
            value //= 10

    def type_record(value):
        # Mirror _type_to_wire, not _cold_to_wire: nested types are raw
        # seven-element arrays, nulls and one raw fields array, with no tags.
        if value is None:
            charge(4)
            return
        charge(14)  # '[' + six ', ' separators + ']'.
        string(value.kind)
        type_integer(value.width)
        type_record(value.pointee)
        type_integer(value.count)
        type_record(value.elem)
        string(value.name)
        field_count = len(value.fields)
        charge(2 if field_count == 0 else 2 * field_count)
        for field in value.fields:
            type_record(field)

    def cold(value, raw_type=False):
        # Exact strings and integers are immutable, untagged JSON scalars.
        # Integer bit_length()+1 still bounds decimal/sign bytes. Keep eight
        # spare bytes per scalar for enclosing separators and pair rows.
        # bool and scalar subclasses retain the old reserve, callback order,
        # and failures; no new conversion or payload copy is introduced.
        value_type = type(value)
        charge(8 if value_type is str or value_type is int
               or (raw_type and value_type is TypeDesc) else 32)
        if isinstance(value, str):
            string(value)
        elif isinstance(value, TypeDesc):
            type_record(value)
        elif isinstance(value, (tuple, list)):
            for item in value:
                cold(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                cold(key)
                cold(item)
        elif isinstance(value, int):
            # Decimal is shorter than its bit count; no giant str(int) first.
            charge(value.bit_length() + 1)
        elif value is None or isinstance(value, (bool, float)):
            charge(32)
        else:
            raise BackendUnavailable("unsupported diagnostic cold payload")

    if max_bytes <= 0:
        raise BackendUnavailable("indexed diagnostic byte bound must be positive")
    charge(4096)
    cold(module.triple)
    if module.type_context is not None:
        if module.type_context.debug_info is not None:
            raise BackendUnavailable("bounded indexed debug capture is not supported")
        cold(module.type_context.named_type_bodies)
    for item in module.globals_:
        # _global_to_wire has 18 bytes of nine-element array syntax. A
        # nonempty enclosing globals array adds two bytes per record for
        # its brackets/separators. Charge both flag values explicitly rather
        # than reserving 512 bytes per global, retaining each cold allowance.
        charge(20)
        cold(item.name)
        cold(item.type, True)
        cold(item.initializer)
        cold(item.is_constant)
        cold(item.is_internal)
        cold(item.tls_model)
        cold(item.alignment)
        cold(item.ir_prefix)
        cold(item.trailing_attributes)
    for function in module.functions:
        kernel = function.indexed_kernel
        if not isinstance(kernel, IndexedFunctionKernel):
            raise BackendUnavailable("indexed module function has no published indexed kernel")
        # Ordinary records have plain fields/slots. Subclasses keep the old
        # reservation and must not gain observable metadata attribute reads.
        # 1536 bounds all fixed JSON keys, punctuation, boolean flags, optional
        # external_odr linkage and the 22 arena name/length rows (len <=
        # sys.maxsize). Variable payloads and both unbounded metadata integers
        # are charged separately. Keep the final actual-wire gate unchanged.
        if type(function) is ParsedFunction and type(kernel) is IndexedFunctionKernel:
            is_global = getattr(function, "is_global", None)
            is_vararg = getattr(function, "is_vararg", None)
            linkage = getattr(function, "definition_linkage", None)
            duplicate_id = getattr(kernel, "first_duplicate_definition_value_id", None)
            use_total = getattr(kernel, "instruction_use_total", None)
            if (type(is_global) is bool and type(is_vararg) is bool
                    and type(duplicate_id) is int and type(use_total) is int
                    and type(linkage) is str):
                charge(1536)
                type_integer(duplicate_id)
                type_integer(use_total)
            else:
                charge(4096)
            # Do not retain unusual fallback metadata across cold callbacks.
            del is_global, is_vararg, duplicate_id, use_total, linkage
        else:
            charge(4096)
        for _wire_name, arena in _kernel_arenas(kernel):
            charge(len(arena) * 8)
        cold(function.name)
        cold(function.ret_type, True)
        for arg in function.args:
            cold(arg.name)
            cold(arg.type, True)
        cold(kernel.block_names)
        cold(kernel.value_names)
        cold(kernel.instruction_arithmetic_flag_values)
        cold(kernel.cold_instruction_data)
        cold(kernel.call_texts)
        types = kernel.types
        if type(types) is tuple:
            # This wire field is a raw type array, not a tagged cold tuple.
            # Retain the original tuple reserve and iteration order.
            charge(32)
            for item in types:
                cold(item, True)
        else:
            cold(types)


def encode_indexed_module_file(
    path: str, module: ParsedModule, *, max_bytes: int = 0, diagnostic=None,
) -> None:
    """Write one direct module with raw scalar arenas and a JSON cold header."""

    if diagnostic is not None:
        diagnostic["stage"] = "codec-preflight"
        diagnostic["remaining_bytes"] = max_bytes
    if max_bytes:
        _bounded_capture_budget(module, max_bytes, diagnostic)
    if diagnostic is not None:
        diagnostic["stage"] = "codec-shape"
    functions: list[dict] = []
    raw_arenas: list[CompilerIntArena] = []
    total_scalars = 0
    module_functions: tuple[ParsedFunction, ...] = module.functions
    for function_value in module_functions:
        function: ParsedFunction = cast(ParsedFunction, function_value)
        # This is a process-boundary codec for an already-published direct
        # module, not another construction path.  Re-entering the lazy kernel
        # builder here both obscures the boundary and makes the pcc1 closure
        # project the cross-module call through py_cpy.  Require the producer's
        # exact final kernel instead.
        kernel_value = function.indexed_kernel
        if not isinstance(kernel_value, IndexedFunctionKernel):
            raise BackendUnavailable(
                "indexed module function has no published indexed kernel"
            )
        kernel: IndexedFunctionKernel = kernel_value
        functions.append(_function_to_wire(function, kernel))
        for _wire_name, arena in _kernel_arenas(kernel):
            raw_arenas.append(arena)
            total_scalars += len(arena)
    header = {
        "schema": _SCHEMA,
        "scalar_width": 8,
        "byte_order": "little",
        "triple": module.triple,
        "named_type_bodies": (
            dict(module.type_context.named_type_bodies)
            if module.type_context is not None else {}
        ),
        "globals": [_global_to_wire(item) for item in module.globals_],
        "functions": functions,
        "total_scalars": total_scalars,
    }
    if module.type_context is not None and module.type_context.debug_info is not None:
        from .self_backend_debug_codec import debug_to_wire
        from . import BackendUnavailable as DebugUnavailable
        try:
            header["source_debug"] = debug_to_wire(
                module.type_context.debug_info, module.functions,
            )
        except DebugUnavailable as error:
            raise BackendUnavailable(str(error)) from error
        # Old v1 readers must reject a debug module rather than ignore its
        # additional header and silently emit a source-less artifact.
        header["schema"] = _DEBUG_SCHEMA
    if any(row.get("definition_linkage") == "external_odr" for row in functions):
        # v1/v2 readers ignore unknown function keys: force them to reject
        # instead of silently strengthening an ODR definition. v3 may also
        # carry source_debug; ordinary v1/v2 bytes remain unchanged.
        header["schema"] = _ODR_SCHEMA
    if diagnostic is not None:
        diagnostic["stage"] = "codec-header"
    header_bytes = json.dumps(header, sort_keys=True).encode("utf-8")
    if diagnostic is not None:
        diagnostic["observed_bytes"] = len(header_bytes)
    if len(header_bytes) > _MAX_HEADER_BYTES:
        raise BackendUnavailable("indexed module header exceeds the size limit")
    wire_size = len(_MAGIC) + len(str(len(header_bytes))) + 1 + len(header_bytes) + total_scalars * 8
    if diagnostic is not None:
        diagnostic["stage"] = "codec-wire-size"
        diagnostic["observed_bytes"] = wire_size
    if max_bytes and wire_size > max_bytes:
        raise BackendUnavailable("indexed module diagnostic exceeds the size limit")
    if diagnostic is not None:
        diagnostic["stage"] = "codec-write"
    with open(path, "wb") as stream:
        stream.write(_MAGIC)
        # Decimal digits plus newline have identical UTF-8 and ASCII bytes;
        # UTF-8 is the codec implemented by the strict pcc1 stdlib surface.
        stream.write((str(len(header_bytes)) + "\n").encode("utf-8"))
        stream.write(header_bytes)
        if not raw_arenas:
            return
        native = raw_arenas[0].uses_native_storage
        if native:
            stream.flush()
            fd = int(stream.fileno())
            for arena in raw_arenas:
                if not arena.uses_native_storage:
                    raise BackendUnavailable(
                        "indexed module mixes arena storage projections"
                    )
                arena.write_raw_fd(fd)
        else:
            for arena in raw_arenas:
                if arena.uses_native_storage:
                    raise BackendUnavailable(
                        "indexed module mixes arena storage projections"
                    )
                _write_host_arena(stream, arena)


def _read_host_arena(stream, count: int) -> CompilerIntArena:
    raw = stream.read(count * 8)
    if len(raw) != count * 8:
        raise BackendUnavailable("indexed module arena payload is truncated")
    arena = CompilerIntArena(count)
    index = 0
    while index < count:
        arena.append(struct.unpack_from("<q", raw, index * 8)[0])
        index += 1
    return arena


def _read_native_arena(fd: int, count: int) -> CompilerIntArena:
    arena = CompilerIntArena(count)
    try:
        arena.read_raw_fd(fd, count)
    except BaseException:
        # The arena is still private until the read succeeds. Keep the read
        # failure authoritative even if releasing its payload also fails.
        try:
            arena.close()
        except BaseException:
            pass
        raise
    return arena


def _replace_seed_arena(
    seed: IndexedFunctionSeed,
    seed_field: str,
    arena: CompilerIntArena,
) -> None:
    previous = getattr(seed, seed_field)
    if isinstance(previous, CompilerIntArena):
        previous.close()
    setattr(seed, seed_field, arena)


def _close_private_seed(seed: IndexedFunctionSeed) -> None:
    # Only a fully constructed, not-yet-returned seed belongs here. Close the
    # current indexes, not the originals that name registration may replace.
    # Keep each attempt independent and use direct fields: no error-time owner
    # list, kernel construction, payload alias, or dynamic attribute dispatch.
    try:
        seed.records.close()
    except BaseException:
        pass
    try:
        seed.args.close()
    except BaseException:
        pass
    try:
        seed.block_name_index.close()
    except BaseException:
        pass
    try:
        seed.value_name_index.close()
    except BaseException:
        pass
    try:
        seed.block_facts.close()
    except BaseException:
        pass
    try:
        seed.instruction_facts.close()
    except BaseException:
        pass
    try:
        seed.instruction_kind_ids.close()
    except BaseException:
        pass
    try:
        seed.instruction_metadata.close()
    except BaseException:
        pass
    try:
        seed.instruction_record_dest_ids.close()
    except BaseException:
        pass
    try:
        seed.instruction_record_scalars.close()
    except BaseException:
        pass
    try:
        seed.gep_index_scalars.close()
    except BaseException:
        pass
    try:
        seed.gep_scalars.close()
    except BaseException:
        pass
    try:
        seed.instruction_overflow_use_ids.close()
    except BaseException:
        pass
    try:
        seed.terminator_case_scalars.close()
    except BaseException:
        pass
    try:
        seed.terminator_scalars.close()
    except BaseException:
        pass
    try:
        seed.block_phi_facts.close()
    except BaseException:
        pass
    try:
        seed.phi_incoming_scalars.close()
    except BaseException:
        pass
    try:
        seed.phi_scalars.close()
    except BaseException:
        pass
    try:
        seed.error_edge_scalars.close()
    except BaseException:
        pass
    try:
        seed.error_edge_spans.close()
    except BaseException:
        pass
    try:
        seed.error_landing_scalars.close()
    except BaseException:
        pass


def _seed_from_wire(value) -> tuple[ParsedFunction, tuple[tuple[str, int], ...]]:
    if not isinstance(value, dict):
        raise BackendUnavailable("indexed module has an invalid function record")
    block_names = value.get("block_names")
    value_names = value.get("value_names")
    if not isinstance(block_names, list) or not isinstance(value_names, list):
        raise BackendUnavailable("indexed module function names are invalid")
    seed = IndexedFunctionSeed(len(block_names), len(value_names))
    try:
        for name in block_names:
            seed.register_block(_wire_str(name, "block name"))
        for name in value_names:
            seed._append_value_columns(_wire_str(name, "value name"))
        seed._ensure_value_name_index()
        seed.first_duplicate_definition_value_id = _wire_int(
            value.get("first_duplicate_definition_value_id", -1),
            "duplicate definition ID",
        )
        seed.instruction_use_total = _wire_int(
            value.get("instruction_use_total", 0), "instruction use total"
        )
        seed.instruction_arithmetic_flag_values = {}
        arithmetic_rows = value.get("instruction_arithmetic_flags")
        if not isinstance(arithmetic_rows, list):
            raise BackendUnavailable("indexed module arithmetic flags are invalid")
        for row in arithmetic_rows:
            if not isinstance(row, list) or len(row) != 2 or not isinstance(row[1], list):
                raise BackendUnavailable("indexed module arithmetic flag row is invalid")
            instruction_id = _wire_int(row[0], "arithmetic instruction ID")
            seed.instruction_arithmetic_flag_values[instruction_id] = tuple(
                _wire_str(item, "arithmetic flag") for item in row[1]
            )
        cold = value.get("cold_instruction_data")
        if not isinstance(cold, list):
            raise BackendUnavailable("indexed module cold data is invalid")
        seed.cold_instruction_data = [_cold_from_wire(item) for item in cold]
        call_texts = value.get("call_texts")
        type_rows = value.get("types")
        if not isinstance(call_texts, list) or not isinstance(type_rows, list):
            raise BackendUnavailable("indexed module call tables are invalid")
        seed.texts = [_wire_str(item, "call text") for item in call_texts]
        seed.text_identity_ids = {
            id(item): index for index, item in enumerate(seed.texts)
        }
        seed.types = []
        for row in type_rows:
            item = _type_from_wire(row)
            if item is None:
                raise BackendUnavailable("indexed module type table contains null")
            seed.types.append(item)
        seed.type_identity_ids = {
            id(item): (index, item) for index, item in enumerate(seed.types)
        }
        seed.terminator_records_complete = True
        seed.phi_records_complete = True
        seed.use_records_complete = True
        seed.complete = True

        args_value = value.get("args")
        if not isinstance(args_value, list):
            raise BackendUnavailable("indexed module function args are invalid")
        args = []
        for row in args_value:
            if not isinstance(row, list) or len(row) != 2:
                raise BackendUnavailable("indexed module function arg is invalid")
            arg_type = _type_from_wire(row[1])
            if arg_type is None:
                raise BackendUnavailable("indexed module function arg type is null")
            args.append(ArgInfo(_wire_str(row[0], "argument name"), arg_type))
        ret_type = _type_from_wire(value.get("ret_type"))
        if ret_type is None:
            raise BackendUnavailable("indexed module function return type is null")
        arena_rows = value.get("arenas")
        if not isinstance(arena_rows, list) or len(arena_rows) != len(_ARENA_FIELDS):
            raise BackendUnavailable("indexed module arena inventory is invalid")
        arena_inventory = []
        index = 0
        while index < len(_ARENA_FIELDS):
            row = arena_rows[index]
            expected_name = _ARENA_FIELDS[index][0]
            if (
                not isinstance(row, list)
                or len(row) != 2
                or row[0] != expected_name
                or not isinstance(row[1], int)
                or row[1] < 0
            ):
                raise BackendUnavailable("indexed module arena order is invalid")
            if expected_name == "block_facts" and row[1] != len(seed.block_names) * 4:
                raise BackendUnavailable(
                    "indexed module block fact columns are inconsistent"
                )
            arena_inventory.append((expected_name, row[1]))
            index += 1
        is_global = _wire_bool(value.get("is_global", False), "global flag")
        definition_linkage = _wire_str(
            value.get("definition_linkage", "external" if is_global else "internal"),
            "function definition linkage",
        )
        if definition_linkage not in ("external", "internal", "external_odr"):
            raise BackendUnavailable("unsupported indexed function definition linkage")
        if is_global != (definition_linkage != "internal"):
            raise BackendUnavailable("inconsistent indexed function definition linkage")
        function = ParsedFunction(
            name=_wire_str(value.get("name", ""), "function name"),
            ret_type=ret_type,
            args=args,
            is_global=is_global,
            definition_linkage=definition_linkage,
            is_vararg=_wire_bool(value.get("is_vararg", False), "vararg flag"),
            blocks=[],
            value_types={},
            value_slots={},
            value_slot_buckets={},
            alloca_slots={},
            alloca_slot_buckets={},
            block_map={},
            used_values=[],
            block_local_last_uses=None,
            value_registers={},
            aarch64_madd_fusions=[],
            aarch64_block_layout=[],
            aarch64_cold_fallthrough_edges=[],
            hidden_sret_slot=None,
            frame_size=0,
            indexed_kernel=None,
            indexed_seed=seed,
            indexed_slot_projection=False,
            aarch64_tail_call_ids=[],
            aarch64_callee_saved=[],
            aarch64_reload_slot_offsets=[],
            aarch64_fused_branch_values={},
            aarch64_frameless=False,
            aarch64_dynamic_stack=False,
        )
        return function, tuple(arena_inventory)
    except BaseException:
        _close_private_seed(seed)
        raise


def _close_private_value_columns(
    value_scalars: CompilerIntArena | None,
    definition_positions: CompilerIntArena | None,
    used_value_ids: CompilerIntArena | None,
) -> None:
    # These wire columns are copied, never adopted by the seed or kernel.
    # This helper runs only while another failure is already authoritative.
    try:
        if value_scalars is not None:
            value_scalars.close()
    except BaseException:
        pass
    try:
        if definition_positions is not None:
            definition_positions.close()
    except BaseException:
        pass
    try:
        if used_value_ids is not None:
            used_value_ids.close()
    except BaseException:
        pass


def _close_private_decoded_kernel(kernel: IndexedFunctionKernel) -> None:
    # Only complete kernels privately owned by this decoder reach this rollback.
    # Current fields cover seed aliases and fresh kernel tables exactly once.
    # Do not revisit historical seeds or construct a kernel while handling errors.
    try:
        kernel.block_name_index.close()
    except BaseException:
        pass
    try:
        kernel.block_facts.close()
    except BaseException:
        pass
    try:
        kernel.call_arg_scalars.close()
    except BaseException:
        pass
    try:
        kernel.call_scalars.close()
    except BaseException:
        pass
    try:
        kernel.instruction_facts.close()
    except BaseException:
        pass
    try:
        kernel.instruction_kind_ids.close()
    except BaseException:
        pass
    try:
        kernel.instruction_metadata.close()
    except BaseException:
        pass
    try:
        kernel.instruction_record_dest_ids.close()
    except BaseException:
        pass
    try:
        kernel.instruction_record_scalars.close()
    except BaseException:
        pass
    try:
        kernel.gep_index_scalars.close()
    except BaseException:
        pass
    try:
        kernel.gep_scalars.close()
    except BaseException:
        pass
    try:
        kernel.type_field_ids.close()
    except BaseException:
        pass
    try:
        kernel.type_scalars.close()
    except BaseException:
        pass
    try:
        kernel.terminator_case_scalars.close()
    except BaseException:
        pass
    try:
        kernel.terminator_scalars.close()
    except BaseException:
        pass
    try:
        kernel.block_phi_facts.close()
    except BaseException:
        pass
    try:
        kernel.phi_incoming_scalars.close()
    except BaseException:
        pass
    try:
        kernel.phi_scalars.close()
    except BaseException:
        pass
    try:
        kernel.error_edge_scalars.close()
    except BaseException:
        pass
    try:
        kernel.error_edge_spans.close()
    except BaseException:
        pass
    try:
        kernel.error_landing_scalars.close()
    except BaseException:
        pass
    try:
        kernel.value_scalars.close()
    except BaseException:
        pass
    try:
        kernel.definition_positions.close()
    except BaseException:
        pass
    try:
        kernel.used_value_ids.close()
    except BaseException:
        pass
    try:
        kernel.slot_scalars.close()
    except BaseException:
        pass
    try:
        kernel.block_layout_ids.close()
    except BaseException:
        pass
    try:
        kernel.instruction_overflow_use_ids.close()
    except BaseException:
        pass
    try:
        kernel.value_name_index.close()
    except BaseException:
        pass


def decode_indexed_module_file(path: str, *, max_bytes: int = 0) -> ParsedModule:
    """Read and fully validate one v1 direct indexed module sidecar."""

    initial_size = os.path.getsize(path)
    if max_bytes and (max_bytes < 0 or initial_size > max_bytes):
        raise BackendUnavailable("indexed module diagnostic exceeds the size limit")
    with open(path, "rb") as stream:
        if stream.readline(32) != _MAGIC:
            raise BackendUnavailable("invalid indexed module file magic")
        size_line = stream.readline(32)
        try:
            header_size = int(size_line.decode("utf-8"))
        except Exception as exc:
            raise BackendUnavailable("invalid indexed module header size") from exc
        if (header_size < 0 or header_size > _MAX_HEADER_BYTES
                or header_size > initial_size
                or (max_bytes and header_size > max_bytes)):
            raise BackendUnavailable("indexed module header exceeds the size limit")
        header_raw = stream.read(header_size)
        if len(header_raw) != header_size:
            raise BackendUnavailable("indexed module header is truncated")
        try:
            header = json.loads(header_raw.decode("utf-8"))
        except Exception as exc:
            raise BackendUnavailable("indexed module header is invalid") from exc
        if (
            not isinstance(header, dict)
            or header.get("schema") not in (_SCHEMA, _DEBUG_SCHEMA, _ODR_SCHEMA)
            or header.get("scalar_width") != 8
            or header.get("byte_order") != "little"
        ):
            raise BackendUnavailable("indexed module header contract mismatch")
        if (header["schema"] != _ODR_SCHEMA
                and ((header["schema"] == _DEBUG_SCHEMA) != ("source_debug" in header))):
            raise BackendUnavailable("indexed module source debug version mismatch")
        function_rows = header.get("functions")
        global_rows = header.get("globals")
        if not isinstance(function_rows, list) or not isinstance(global_rows, list):
            raise BackendUnavailable("indexed module header tables are invalid")
        functions = []
        inventories = []
        total_scalars = 0
        for row in function_rows:
            function, inventory = _seed_from_wire(row)
            functions.append(function)
            inventories.append(inventory)
            for _name, count in inventory:
                total_scalars += count
        has_odr = any(function.definition_linkage == "external_odr" for function in functions)
        if has_odr != (header["schema"] == _ODR_SCHEMA):
            raise BackendUnavailable("indexed module definition linkage version mismatch")
        header_total_scalars = _wire_int(
            header.get("total_scalars", -1), "total scalar count"
        )
        if total_scalars != header_total_scalars:
            raise BackendUnavailable("indexed module scalar total is inconsistent")
        payload_start = int(stream.tell())
        expected_size = payload_start + total_scalars * 8
        if os.path.getsize(path) != expected_size:
            raise BackendUnavailable("indexed module file size is inconsistent")
        probe = CompilerIntArena(0)
        native = probe.uses_native_storage
        probe.close()
        fd = -1
        if native and total_scalars:
            stream.seek(payload_start)
            fd = int(stream.fileno())
        function_index = 0
        while function_index < len(functions):
            function = functions[function_index]
            seed = function.indexed_seed
            if not isinstance(seed, IndexedFunctionSeed):
                raise BackendUnavailable("indexed module seed construction failed")
            inventory = inventories[function_index]
            value_scalars: CompilerIntArena | None = None
            definition_positions: CompilerIntArena | None = None
            used_value_ids: CompilerIntArena | None = None
            try:
                arena_index = 0
                while arena_index < len(_ARENA_FIELDS):
                    wire_name, _kernel_field, seed_field = _ARENA_FIELDS[arena_index]
                    count = inventory[arena_index][1]
                    arena = (
                        _read_native_arena(fd, count)
                        if native
                        else _read_host_arena(stream, count)
                    )
                    if seed_field:
                        _replace_seed_arena(seed, seed_field, arena)
                    elif wire_name == "value_scalars":
                        value_scalars = arena
                    elif wire_name == "definition_positions":
                        definition_positions = arena
                    else:
                        used_value_ids = arena
                    arena_index += 1
                value_count = len(seed.value_names)
                if (
                    not isinstance(value_scalars, CompilerIntArena)
                    or not isinstance(definition_positions, CompilerIntArena)
                    or not isinstance(used_value_ids, CompilerIntArena)
                    or len(value_scalars) != value_count * 8
                    or len(definition_positions) != value_count
                ):
                    raise BackendUnavailable(
                        "indexed module value scalar columns are inconsistent"
                    )
                seed.definition_blocks = []
                seed.definition_positions = []
                seed.value_type_ids = []
                seed.alloca_type_ids = []
                seed.value_is_used_flags = []
                seed.value_last_use_positions = []
                value_id = 0
                while value_id < value_count:
                    seed.definition_blocks.append(
                        value_scalars.get_unchecked(value_id * 8)
                    )
                    seed.value_type_ids.append(
                        value_scalars.get_unchecked(value_id * 8 + 1)
                    )
                    if (
                        value_scalars.get_unchecked(value_id * 8 + 2) != -1
                        or value_scalars.get_unchecked(value_id * 8 + 3) != -1
                        or value_scalars.get_unchecked(value_id * 8 + 5) != -1
                    ):
                        raise BackendUnavailable(
                            "indexed module value state is already prepared"
                        )
                    seed.alloca_type_ids.append(
                        value_scalars.get_unchecked(value_id * 8 + 4)
                    )
                    seed.value_last_use_positions.append(
                        value_scalars.get_unchecked(value_id * 8 + 6)
                    )
                    seed.value_is_used_flags.append(
                        bool(value_scalars.get_unchecked(value_id * 8 + 7))
                    )
                    seed.definition_positions.append(
                        definition_positions.get_unchecked(value_id)
                    )
                    value_id += 1
                seed.used_value_ids_in_order = []
                used_index = 0
                while used_index < len(used_value_ids):
                    seed.used_value_ids_in_order.append(
                        used_value_ids.get_unchecked(used_index)
                    )
                    used_index += 1
                get_indexed_function_kernel(function)
                # Remove each private owner before close so a failing release is
                # not retried by rollback. Later columns still get one attempt.
                arena = value_scalars
                value_scalars = None
                arena.close()
                arena = definition_positions
                definition_positions = None
                arena.close()
                arena = used_value_ids
                used_value_ids = None
                arena.close()
            except BaseException:
                _close_private_value_columns(
                    value_scalars, definition_positions, used_value_ids,
                )
                raise
            function_index += 1
        if os.path.getsize(path) != expected_size or initial_size != expected_size:
            raise BackendUnavailable("indexed module changed during read")
        if not native and stream.read(1):
            raise BackendUnavailable("indexed module has trailing bytes")
    try:
        bodies = header.get("named_type_bodies", {})
        if not isinstance(bodies, dict):
            raise BackendUnavailable("indexed module named type declarations are invalid")
        declarations = []
        for name, body in bodies.items():
            name = _wire_str(name, "named type name")
            body = _wire_str(body, "named type body")
            if not name.startswith("%") or "\n" in name or "\n" in body:
                raise BackendUnavailable("indexed module named type declaration is invalid")
            declarations.append(name + " = type " + body)
        from .self_backend_parse import _parse_named_types
        triple = _wire_str(header.get("triple", ""), "target triple")
        type_context = _parse_named_types("\n".join(declarations))
        type_context.target_triple = triple
        if "source_debug" in header:
            from .self_backend_debug_codec import debug_from_wire
            from . import BackendUnavailable as DebugUnavailable
            try:
                type_context.debug_info = debug_from_wire(header["source_debug"], tuple(functions))
            except DebugUnavailable as error:
                raise BackendUnavailable(str(error)) from error
        for function in functions:
            function.type_context = type_context
        globals_ = tuple(_global_from_wire(row, type_context) for row in global_rows)
        return ParsedModule(
            triple=triple,
            globals_=globals_,
            functions=tuple(functions),
            type_context=type_context,
        )
    except BaseException:
        # All functions have completed private kernel adoption, but no module
        # has been returned. Earlier acquisition rollback contracts stay local.
        for function in functions:
            kernel = function.indexed_kernel
            if isinstance(kernel, IndexedFunctionKernel):
                _close_private_decoded_kernel(kernel)
        raise



__all__ = ["encode_indexed_module_file", "decode_indexed_module_file"]
