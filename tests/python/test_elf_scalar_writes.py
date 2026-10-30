"""Host contracts for native ELF scalar stores, not native execution or timing.

The intrinsic model enforces the actual signed-i64 operand contract before
modeling store_i32 truncation or store_i64 storage. Small ordinary and compact
ELF objects run through the real host linker; no compiler or executable runs.
"""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcc.backend import elf_x86_64 as elf
from tests.elf_x86_packed_application_reference import (
    _without_aarch64_jump26_veneers,
    _without_native_scalar_writes,
)


def _scalar_owner_source():
    # Restore the scalar-store stage through the exact later exports/JUMP26
    # inverses, rather than letting those deltas invalidate this older proof.
    return _without_aarch64_jump26_veneers(
        Path(elf.__file__).read_text(encoding="utf-8")
    )


def test_fixture_adapter_restores_exact_original_module():
    source = _scalar_owner_source()
    original = _without_native_scalar_writes(source)
    assert hashlib.sha256(original.encode()).hexdigest() == (
        "f2476a2c75257d5a051b5d5af21b82a1d19be53096de28a3267f81cf4680d20f"
    )
    assert _without_native_scalar_writes(original) == original


@pytest.mark.parametrize("change", ["store", "unrelated"])
def test_fixture_adapter_does_not_accept_other_source_changes(change):
    source = _scalar_owner_source()
    if change == "store":
        source = source.replace("and bits in (32, 64)", "and bits in (16, 32, 64)")
    else:
        source += "\n# unrelated owner drift\n"
    with pytest.raises(AssertionError):
        _without_native_scalar_writes(source)


def _original_write_int(image, offset, value, bits, signed):
    # Exact preimage body from 993f8cab, elf_x86_64 blob 9d8232ac.
    minimum = -(1 << (bits - 1)) if signed else 0
    maximum = (1 << (bits - (1 if signed else 0))) - 1
    if value < minimum or value > maximum:
        kind = "signed" if signed else "unsigned"
        raise elf.ElfError(f"relocation result {value} does not fit {kind} i{bits}")
    image[offset:offset + bits // 8] = int(value).to_bytes(
        bits // 8, "little", signed=signed
    )


@pytest.fixture
def native_stores(monkeypatch):
    calls = []
    data_offset = 37  # Deliberately not a baked-in object layout.

    def abi_constant(name):
        assert name == "object.bytearray.data_offset"
        return data_offset

    def store(image, raw_offset, value, bits):
        assert type(image) is bytearray
        assert type(raw_offset) is int and type(value) is int
        # unsafe_lowering._unsafe_i64_arg is the input to both stores. The
        # 32-bit intrinsic then truncates, rather than accepting a u32 ABI.
        assert -(1 << 63) <= value < (1 << 63)
        offset = raw_offset - data_offset
        assert 0 <= offset <= len(image) - bits // 8
        calls.append((bits, offset, value))
        raw = value & ((1 << bits) - 1)
        image[offset:offset + bits // 8] = raw.to_bytes(bits // 8, "little")

    monkeypatch.setattr(elf, "_NATIVE_PATCH_WORDS", True)
    monkeypatch.setattr(elf, "sys", SimpleNamespace(byteorder="little"))
    monkeypatch.setattr(elf, "abi_constant", abi_constant)
    monkeypatch.setattr(elf, "store_i32", lambda b, o, v: store(b, o, v, 32))
    monkeypatch.setattr(elf, "store_i64", lambda b, o, v: store(b, o, v, 64))
    return calls


_SCALARS = [
    (bits, signed, value)
    for bits in (32, 64)
    for signed in (False, True)
    for value in (
        (0, 1, (1 << (bits - 1)) - 1, 1 << (bits - 1), (1 << bits) - 1)
        if not signed else
        (-(1 << (bits - 1)), -1, 0, 1, (1 << (bits - 1)) - 1)
    )
]


@pytest.mark.parametrize("bits,signed,value", _SCALARS)
@pytest.mark.parametrize("position", ["first", "unaligned", "last"])
def test_native_store_matches_original_bytes(native_stores, bits, signed, value, position):
    image = bytearray(b"Z" * 24)
    expected = bytearray(image)
    offset = {"first": 0, "unaligned": 1, "last": len(image) - bits // 8}[position]
    _original_write_int(expected, offset, value, bits, signed)
    elf._write_int(image, offset, value, bits, signed)
    operand = value - (1 << 64) if bits == 64 and value >= (1 << 63) else value
    assert image == expected
    assert native_stores == [(bits, offset, operand)]


@pytest.mark.parametrize("bits,signed,value,operand", [
    (32, False, 0x80000000, 0x80000000),
    (32, False, 0xFFFFFFFF, 0xFFFFFFFF),
    (32, True, -(1 << 31), -(1 << 31)),
    (64, False, 1 << 63, -(1 << 63)),
    (64, False, (1 << 64) - 1, -1),
    (64, True, -(1 << 63), -(1 << 63)),
])
def test_high_bit_operands_follow_intrinsic_contract(native_stores, bits, signed, value, operand):
    image = bytearray(8)
    elf._write_int(image, 0, value, bits, signed)
    assert native_stores == [(bits, 0, operand)]
    assert int.from_bytes(image[:bits // 8], "little", signed=signed) == value


@pytest.mark.parametrize("bits,signed", [(32, False), (32, True), (64, False), (64, True)])
@pytest.mark.parametrize("edge", ["below", "above"])
def test_overflow_precedes_invalid_destination(native_stores, bits, signed, edge):
    lower = -(1 << (bits - 1)) if signed else 0
    upper = (1 << (bits - (1 if signed else 0))) - 1
    value = lower - 1 if edge == "below" else upper + 1
    with pytest.raises(elf.ElfError) as expected:
        _original_write_int(b"", object(), value, bits, signed)
    with pytest.raises(elf.ElfError) as actual:
        elf._write_int(b"", object(), value, bits, signed)
    assert str(actual.value) == str(expected.value)
    assert native_stores == []


@pytest.mark.parametrize("mode", ["host", "big-endian"])
def test_non_native_or_big_endian_uses_original_path(monkeypatch, native_stores, mode):
    if mode == "host":
        monkeypatch.setattr(elf, "_NATIVE_PATCH_WORDS", False)
    else:
        monkeypatch.setattr(elf, "sys", SimpleNamespace(byteorder="big"))
    image = bytearray(9)
    expected = bytearray(image)
    _original_write_int(expected, 1, (1 << 64) - 1, 64, False)
    elf._write_int(image, 1, (1 << 64) - 1, 64, False)
    assert image == expected
    assert native_stores == []


def _fallback_case(shape):
    events = []
    image = bytearray(b"Z" * 16)
    offset, value, bits, signed = 1, 5, 32, False

    class Integer(int):
        def __int__(self):
            events.append("int")
            return super().__int__()

        def __add__(self, other):
            events.append("add")
            return super().__add__(other)

        def __sub__(self, other):
            events.append("sub")
            return super().__sub__(other)

        def __floordiv__(self, other):
            events.append("floordiv")
            return super().__floordiv__(other)

    class Buffer(bytearray):
        def __setitem__(self, key, data):
            events.append(("setitem", key.start, key.stop, bytes(data)))
            return super().__setitem__(key, data)

    class Value:
        def __lt__(self, other):
            events.append(("lt", other))
            if shape == "resize-value":
                image.clear()
            return False

        def __gt__(self, other):
            events.append(("gt", other))
            return False

        def __int__(self):
            events.append("int")
            if shape == "raise-value":
                raise ValueError("conversion failed")
            return 5

    class Signed:
        def __bool__(self):
            events.append("signed")
            if shape == "resize-signed":
                image.clear()
            return True

    if shape == "negative-offset":
        offset = -2
    elif shape == "before-image":
        offset = -100
    elif shape == "after-image":
        offset = 100
    elif shape == "overlap-end":
        offset = 15
    elif shape == "empty-image":
        image.clear()
    elif shape == "float-offset":
        offset = 1.0
    elif shape == "bool-offset":
        offset = True
    elif shape == "offset-subclass":
        offset = Integer(1)
    elif shape == "value-subclass":
        value = Integer(5)
    elif shape == "float-value":
        value = 5.9
    elif shape == "bool-value":
        value = True
    elif shape in ("value-protocol", "resize-value", "raise-value"):
        value = Value()
    elif shape == "bits-subclass":
        bits = Integer(32)
    elif shape == "width-16":
        bits = 16
    elif shape == "width-24":
        bits = 24
    elif shape == "width-128":
        bits = 128
    elif shape == "invalid-width":
        bits = 0
    elif shape in ("signed-protocol", "resize-signed"):
        signed = Signed()
    elif shape == "integer-signed":
        signed = 1
    elif shape == "buffer-subclass":
        image = Buffer(image)
    elif shape == "writable-view":
        image = memoryview(image)
    elif shape == "readonly-view":
        image = memoryview(bytes(image))
    elif shape == "immutable-buffer":
        image = bytes(image)
    elif shape == "list-buffer":
        image = list(image)
    else:
        raise AssertionError(shape)
    return image, offset, value, bits, signed, events


def _fallback_outcome(write, shape):
    image, offset, value, bits, signed, events = _fallback_case(shape)
    try:
        result = ("result", write(image, offset, value, bits, signed))
    except Exception as exc:
        result = ("error", type(exc), str(exc))
    return result, bytes(image), events


@pytest.mark.parametrize("shape", [
    "negative-offset", "before-image", "after-image", "overlap-end", "empty-image",
    "float-offset", "bool-offset", "offset-subclass", "value-subclass",
    "float-value", "bool-value", "value-protocol", "resize-value", "raise-value",
    "bits-subclass", "width-16", "width-24", "width-128", "invalid-width",
    "signed-protocol", "resize-signed", "integer-signed", "buffer-subclass",
    "writable-view", "readonly-view", "immutable-buffer", "list-buffer",
])
def test_fallback_preserves_result_bytes_and_callback_order(native_stores, shape):
    assert _fallback_outcome(elf._write_int, shape) == _fallback_outcome(_original_write_int, shape)
    assert native_stores == []


def _relocation_object():
    rows = (
        (1, 3, elf.R_X86_64_64, 0),
        (9, 4, elf.R_X86_64_32, 0),
        (13, 2, elf.R_X86_64_32S, 0),
        (17, 1, elf.R_X86_64_PC32, -4),
        (21, 1, elf.R_X86_64_PLT32, -4),
        (25, 3, elf.R_X86_64_GOTPCREL, -4),
        (29, 3, elf.R_X86_64_GOTPCRELX, -4),
        (33, 3, elf.R_X86_64_REX_GOTPCRELX, -4),
        (37, 5, elf.R_X86_64_GOTTPOFF, -4),
        (41, 5, elf.R_X86_64_TPOFF32, 0),
    )
    return elf.ElfObject(
        (
            elf.ElfSection(".text", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_EXECINSTR,
                           16, bytes(48), relocations=tuple(elf.ElfRelocation(*row) for row in rows)),
            elf.ElfSection(".tdata", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_WRITE | elf.SHF_TLS,
                           8, bytes(8)),
        ),
        (
            elf.ElfSymbol.null(),
            elf.ElfSymbol("_start", 1, 0, 48, elf.STB_GLOBAL, elf.STT_FUNC),
            elf.ElfSymbol("low", elf.SHN_ABS, 123, 0, elf.STB_GLOBAL, elf.STT_OBJECT),
            elf.ElfSymbol("wide", elf.SHN_ABS, (1 << 64) - 1, 0, elf.STB_GLOBAL, elf.STT_OBJECT),
            elf.ElfSymbol("high32", elf.SHN_ABS, (1 << 32) - 1, 0, elf.STB_GLOBAL, elf.STT_OBJECT),
            elf.ElfSymbol("tls", 2, 0, 8, elf.STB_GLOBAL, elf.STT_TLS),
        ),
    )


@pytest.mark.parametrize("compact", [False, True])
def test_real_elf_link_matches_original_for_all_scalar_families(monkeypatch, native_stores, compact):
    payload = elf.emit_relocatable(_relocation_object())
    obj = elf.parse_relocatable(payload, compact_relocations=compact)
    with monkeypatch.context() as original:
        original.setattr(elf, "_write_int", _original_write_int)
        expected = elf.link_static_executable([obj])
    actual = elf.link_static_executable([obj])
    assert actual == expected
    assert elf.parse_static_executable(actual)["tls_segments"] == 1
    # Ten relocation patches plus two GOT slots. High-bit scalar and GOT
    # operands, signed PC-relative deltas and TLS offsets all reached stores.
    assert len(native_stores) == 12
    operands32 = [value for bits, _offset, value in native_stores if bits == 32]
    operands64 = [value for bits, _offset, value in native_stores if bits == 64]
    assert (1 << 32) - 1 in operands32
    assert any(value < 0 for value in operands32)
    assert operands64.count(-1) == 2
    assert any(value < -1 for value in operands64)
