"""Version 3 runtime stack-map tables (stack-map step 1).

A merged version 2 table is transcoded into the compact runtime form for a
final executable. These tests check that the form preserves every frame's root
set, pc and exception/continuation fields, that the validators and scanners
accept it, and that malformed tables are refused.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from pcc.backend import precise_stackmap as wire
from pcc.backend.precise_stackmap import (
    ARCH_AARCH64,
    ARCH_X86_64,
    LOCATION_MANAGED,
    LOCATION_OWNED,
    LOCATION_STACK_INDIRECT,
    NO_OFFSET,
    RECORD_HAS_EXCEPTION_EDGE,
    RECORD_SUSPENDED,
    SAFEPOINT_CALL,
    SAFEPOINT_CONTINUATION,
    SAFEPOINT_ENTRY,
    SAFEPOINT_EXCEPTION,
    SAFEPOINT_LOOP,
    FunctionStackMap,
    PreciseStackMap,
    PreciseStackMapError,
    SafepointRecord,
    StackMapLocation,
    decode_runtime_table,
    decode_stack_map,
    encode_stack_map,
    function_address_offsets,
    transcode_runtime_table,
    validate_runtime_table,
    validate_stack_map_payload,
)


def _slot(offset, owned=False, register=29):
    flags = LOCATION_MANAGED | (LOCATION_OWNED if owned else 0)
    return StackMapLocation(LOCATION_STACK_INDIRECT, flags, register=register, offset=offset)


def _map(arch=ARCH_AARCH64, register=29):
    shared = (_slot(-16, register=register), _slot(-24, owned=True, register=register))
    first = FunctionStackMap(
        function_id=11, function_address=0, code_size=200, frame_size=48,
        records=(
            SafepointRecord(1, 0, SAFEPOINT_ENTRY, ()),
            SafepointRecord(2, 20, SAFEPOINT_CALL, shared),
            SafepointRecord(3, 40, SAFEPOINT_LOOP, shared),
            SafepointRecord(
                4, 60, SAFEPOINT_EXCEPTION, (_slot(-8, register=register),),
                flags=RECORD_HAS_EXCEPTION_EDGE, exceptional_offset=120,
            ),
        ),
    )
    second = FunctionStackMap(
        function_id=29, function_address=0, code_size=96, frame_size=32,
        records=(
            SafepointRecord(5, 12, SAFEPOINT_CALL, shared),
            SafepointRecord(
                6, 44, SAFEPOINT_CONTINUATION, (_slot(-32, owned=True, register=register),),
                flags=RECORD_SUSPENDED, continuation_id=7,
            ),
        ),
    )
    return PreciseStackMap(arch=arch, functions=(first, second))


def _address_offsets(payload):
    decoded = decode_stack_map(payload, expected_arch=None, final_image=False)
    return tuple(zip((function.function_id for function in decoded.functions),
                     function_address_offsets(payload)))


@pytest.mark.parametrize("arch, register", [(ARCH_AARCH64, 29), (ARCH_X86_64, 6)])
def test_transcoding_preserves_every_frames_roots_and_fields(arch, register):
    value = _map(arch, register)
    payload = encode_stack_map(value)
    runtime, remapped = transcode_runtime_table(payload, _address_offsets(payload))
    functions = decode_runtime_table(runtime, expected_arch=arch)
    assert [function[0] for function in functions] == [11, 29]
    for original, (function_id, address, code_size, frame_size, records) in zip(value.functions, functions):
        assert (function_id, address, code_size, frame_size) == (
            original.function_id, 0, original.code_size, original.frame_size,
        )
        assert len(records) == len(original.records)
        for record, (pc, roots, kind, flags, exceptional, continuation) in zip(original.records, records):
            assert pc == record.instruction_offset
            assert roots == tuple((loc.offset, bool(loc.flags & LOCATION_OWNED)) for loc in record.locations)
            plain = record.kind in (SAFEPOINT_ENTRY, SAFEPOINT_LOOP, SAFEPOINT_CALL) and not record.flags
            assert kind == (0 if plain else record.kind)
            assert (flags, exceptional, continuation) == (
                record.flags, record.exceptional_offset, record.continuation_id,
            )
    # The address relocation still sits at function entry start + 8.
    assert remapped == tuple(zip((11, 29), function_address_offsets(runtime)))
    assert function_address_offsets(runtime) == (40 + 8, 40 + 32 + 8)


def test_runtime_table_is_smaller_and_deduplicates_live_sets():
    payload = encode_stack_map(_map())
    runtime, _remapped = transcode_runtime_table(payload, _address_offsets(payload))
    assert len(runtime) < len(payload)
    header = struct.unpack_from("<8sHBBIIIIIII", runtime, 0)
    function_count, record_count, live_sets, locations, rare = header[4:9]
    assert (function_count, record_count) == (2, 6)
    # (), shared, (-8,) and (-32 owned,): the shared pair is stored once.
    assert (live_sets, locations, rare) == (4, 4, 2)


def test_publication_validators_accept_a_final_runtime_table():
    payload = encode_stack_map(_map())
    runtime, remapped = transcode_runtime_table(payload, _address_offsets(payload))
    final = bytearray(runtime)
    for _function_id, offset in remapped:
        struct.pack_into("<Q", final, offset, 0x100004000 + offset)
    validate_stack_map_payload(bytes(final), expected_arch=ARCH_AARCH64, final_image=True)
    with pytest.raises(PreciseStackMapError, match="unresolved function address"):
        validate_stack_map_payload(runtime, expected_arch=ARCH_AARCH64, final_image=True)
    count, scanned, _start, _table = wire._scan_stack_map_payload(runtime)
    assert count == 2 and [fid for fid, _s, _e in scanned] == [11, 29]


def test_transcoding_refuses_a_location_the_runtime_form_cannot_carry():
    # Valid version 2 (stack-pointer relative), but not the frame-pointer
    # slot every producer emits.
    sp_root = _slot(-16, register=31)
    value = PreciseStackMap(arch=ARCH_AARCH64, functions=(FunctionStackMap(
        function_id=3, function_address=0, code_size=64, frame_size=16,
        records=(SafepointRecord(1, 8, SAFEPOINT_CALL, (sp_root,)),),
    ),))
    payload = encode_stack_map(value)
    with pytest.raises(PreciseStackMapError, match="managed frame-relative slots"):
        transcode_runtime_table(payload, _address_offsets(payload))


def _runtime():
    payload = encode_stack_map(_map())
    runtime, _remapped = transcode_runtime_table(payload, _address_offsets(payload))
    return bytearray(runtime)


def test_runtime_validator_refuses_malformed_tables():
    records_base = 40 + 2 * 32
    unordered = _runtime()
    struct.pack_into("<I", unordered, records_base + 8 * 2, 10)  # pc 40 -> 10 after 20
    with pytest.raises(PreciseStackMapError, match="invalid runtime safepoint record"):
        validate_runtime_table(bytes(unordered))
    bad_live_set = _runtime()
    struct.pack_into("<I", bad_live_set, records_base + 4, 99)
    with pytest.raises(PreciseStackMapError, match="invalid runtime safepoint record"):
        validate_runtime_table(bytes(bad_live_set))
    with pytest.raises(PreciseStackMapError, match="size disagrees"):
        validate_runtime_table(bytes(_runtime() + b"\0\0\0\0"))
    wrong_first = _runtime()
    struct.pack_into("<I", wrong_first, 40 + 32 + 24, 3)  # second function's first record
    with pytest.raises(PreciseStackMapError, match="invalid runtime function entry"):
        validate_runtime_table(bytes(wrong_first))


def test_format_policy_accepts_only_two_and_three(monkeypatch):
    monkeypatch.delenv(wire.PRECISE_STACKMAP_FORMAT_ENV, raising=False)
    assert wire.precise_stackmap_runtime_format() == 3
    monkeypatch.setenv(wire.PRECISE_STACKMAP_FORMAT_ENV, "2")
    assert wire.precise_stackmap_runtime_format() == 2
    monkeypatch.setenv(wire.PRECISE_STACKMAP_FORMAT_ENV, "3")
    assert wire.precise_stackmap_runtime_format() == 3
    monkeypatch.setenv(wire.PRECISE_STACKMAP_FORMAT_ENV, "4")
    with pytest.raises(PreciseStackMapError):
        wire.precise_stackmap_runtime_format()


@pytest.mark.parametrize("arch, register", [(ARCH_AARCH64, 29), (ARCH_X86_64, 6)])
def test_transcoding_matches_fixed_wire_bytes_and_requested_remap_order(arch, register):
    payload = encode_stack_map(_map(arch, register))
    offsets = _address_offsets(payload)
    runtime, remapped = transcode_runtime_table(payload, tuple(reversed(offsets)))
    # Independent wire oracle: all six sections, including both rare rows.
    expected = b"".join((
        struct.pack("<8sHBBIIIIIII", wire.MAGIC, 3, arch, 8, 2, 6, 4, 4, 2, 0, 0),
        struct.pack("<QQIIII", 11, 0, 200, 48, 0, 4),
        struct.pack("<QQIIII", 29, 0, 96, 32, 4, 2),
        *(struct.pack("<II", *row) for row in ((0, 0), (20, 1), (40, 1), (60, 2), (12, 1), (44, 3))),
        *(struct.pack("<II", *row) for row in ((0, 0), (0, 2), (2, 1), (3, 1))),
        *(struct.pack("<i", word) for word in (-16, -23, -8, -31)),
        struct.pack("<IBBHII", 3, SAFEPOINT_EXCEPTION, RECORD_HAS_EXCEPTION_EDGE, 0, 120, 0),
        struct.pack("<IBBHII", 5, SAFEPOINT_CONTINUATION, RECORD_SUSPENDED, 0, NO_OFFSET, 7),
    ))
    assert runtime == expected
    assert remapped == ((29, 80), (11, 48))
    assert type(runtime) is bytes


@pytest.mark.parametrize("record_count", (0, 64, 256))
def test_transcoding_allocates_once_and_writes_only_disjoint_fixed_spans(monkeypatch, record_count):
    roots = (_slot(-16), _slot(-24, owned=True))
    value = PreciseStackMap(arch=ARCH_AARCH64, functions=(FunctionStackMap(
        function_id=(1 << 64) - 1, function_address=(1 << 63) + 8,
        code_size=4 * record_count + 4, frame_size=32,
        records=tuple(SafepointRecord(i + 1, 4 * i, SAFEPOINT_CALL, roots)
                      for i in range(record_count)),
    ),))
    payload = encode_stack_map(value)
    offsets = _address_offsets(payload)
    allocations = []
    spans = []

    class FixedBuffer(bytearray):
        def __init__(self, size):
            assert type(size) is int
            super().__init__(size)
            allocations.append(self)

        def __iadd__(self, value):
            raise AssertionError("runtime table must not copy a growing prefix")

        def __setitem__(self, key, value):
            assert isinstance(key, slice) and key.step is None
            start = 0 if key.start is None else key.start
            assert key.stop - start == len(value)
            assert start == (spans[-1][1] if spans else 0)
            assert key.stop <= len(self)
            spans.append((start, key.stop))
            old_length = len(self)
            super().__setitem__(key, value)
            assert len(self) == old_length

    monkeypatch.setattr(wire, "bytearray", FixedBuffer, raising=False)
    runtime, remapped = transcode_runtime_table(payload, offsets)
    assert len(allocations) == 1
    assert bytes(allocations[0]) == runtime
    assert sum(end - start for start, end in spans) == len(runtime)
    assert spans[-1][1] == len(runtime)
    assert remapped == (((1 << 64) - 1, 48),)
    decoded = decode_runtime_table(runtime)
    assert decoded[0][1] == (1 << 63) + 8
    assert len(decoded[0][4]) == record_count


def test_transcoding_empty_table_and_invalid_input_before_output_allocation(monkeypatch):
    empty = struct.pack("<8sHBBIII", wire.MAGIC, 2, ARCH_AARCH64, 8, 0, 0, 0)
    runtime, offsets = transcode_runtime_table(empty, ())
    assert runtime == struct.pack("<8sHBBIIIIIII", wire.MAGIC, 3, ARCH_AARCH64, 8, 0, 0, 0, 0, 0, 0, 0)
    assert offsets == ()

    def forbidden_output(*args):
        raise AssertionError("invalid input must be rejected before output allocation")

    monkeypatch.setattr(wire, "bytearray", forbidden_output, raising=False)
    with pytest.raises(PreciseStackMapError, match="stack-map header"):
        transcode_runtime_table(empty[:10], ())
    with pytest.raises(PreciseStackMapError, match="version 2"):
        transcode_runtime_table(runtime, ())
    with pytest.raises(PreciseStackMapError, match="size disagrees"):
        transcode_runtime_table(empty + b"\0", ())


def test_actual_transcoder_ir_uses_bounded_slice_writes(tmp_path):
    # Host lowering of the actual complete source module, not a rewritten
    # serializer toy. This checks dispatch, not native execution or timing.
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.type_infer import infer_module
    from pcc.ir.optimization.ir_mutator import MutableModule

    path = Path(wire.__file__)
    owner = "pcc.backend.precise_stackmap"
    typed = infer_module(parse_and_lift(path.read_text(), str(path), owner))
    codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="off")
    text = str(codegen.generate(typed))
    module = MutableModule.parse(text)
    function = module.function("user_pcc_backend_precise_stackmap_transcode_runtime_table")
    assert function is not None and function.blocks
    assert all(block.terminator is not None and block.terminator.is_terminator()
               for block in function.blocks)
    instructions = [instruction for block in function.blocks for instruction in block.instructions]
    assert any(instruction.opcode == "ret" for instruction in instructions)
    body = "\n".join(instruction.text for instruction in instructions)
    (tmp_path / "transcode_runtime_table.body.ll").write_text(body)
    assert "strict.nolib.stub" not in body
    calls = [instruction.text for instruction in instructions if instruction.opcode == "call"]
    writes = [call for call in calls if "@py_bytearray_set_slice(" in call or "@py_obj_set_slice(" in call]
    assert len(writes) == 6, writes  # header and five fixed-width tables
    assert not any("@py_bytes_concat(" in call or "@py_bytearray_extend(" in call for call in calls)
    assert any("@py_bytes_from_obj(" in call for call in calls)
