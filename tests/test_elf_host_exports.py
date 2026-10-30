"""Host-table object format checks, not native extension-loader acceptance."""

import struct

import pytest

from pcc.backend import elf_host_exports as exports
from pcc.backend import elf_x86_64 as elf


def _round_trip(names):
    obj = exports.host_export_object(names)
    data = elf.emit_relocatable(obj)
    return obj, data, elf.parse_relocatable(data)


def test_table_uses_read_only_sections_and_exact_header_and_pointer_rows():
    original, data, decoded = _round_trip(("host_add", "PyLong_FromLong"))
    assert elf._ELF_HEADER.unpack_from(data)[1] == elf.ET_REL
    assert decoded == original
    assert decoded.machine == elf.EM_X86_64
    assert len(decoded.sections) == 2
    table, names = decoded.sections
    assert (table.type, table.flags, table.align) == (elf.SHT_PROGBITS, elf.SHF_ALLOC, 8)
    assert (names.type, names.flags, names.align) == (elf.SHT_PROGBITS, elf.SHF_ALLOC, 1)
    assert table.data == b"PCCEXP01" + struct.pack("<Q", 2) + bytes(32)
    assert names.data == b"host_add\0PyLong_FromLong\0"
    assert names.relocations == ()


def test_every_pointer_uses_an_exact_owned_absolute_relocation():
    inventory = ("z", "host_function", "A")
    _, _, decoded = _round_trip(inventory)
    table, names = decoded.sections
    assert len(table.relocations) == 2 * len(inventory)
    offset = 0
    for index, name in enumerate(inventory):
        name_row, address_row = table.relocations[index * 2:index * 2 + 2]
        assert (name_row.offset, name_row.type, name_row.addend) == (
            16 + 16 * index, elf.R_X86_64_64, offset,
        )
        storage = decoded.symbols[name_row.symbol_index]
        assert (storage.binding, storage.type, storage.section_index,
                storage.value, storage.size) == (
            elf.STB_LOCAL, elf.STT_OBJECT, 2, 0, len(names.data),
        )
        assert names.data[offset:offset + len(name) + 1] == name.encode("ascii") + b"\0"
        assert (address_row.offset, address_row.type, address_row.addend) == (
            24 + 16 * index, elf.R_X86_64_64, 0,
        )
        target = decoded.symbols[address_row.symbol_index]
        assert target == elf.ElfSymbol(
            name, elf.SHN_UNDEF, 0, 0, elf.STB_GLOBAL, elf.STT_NOTYPE,
        )
        offset += len(name) + 1


def test_anchor_and_undefined_roots_use_the_existing_elf_symbol_model():
    inventory = ("not_a_capi_prefix", "PyLong_FromLong")
    _, _, decoded = _round_trip(inventory)
    assert decoded.symbols[0] == elf.ElfSymbol.null()
    anchor = next(symbol for symbol in decoded.symbols
                  if symbol.name == exports.HOST_EXPORT_SYMBOL)
    assert anchor == elf.ElfSymbol(
        "__pcc_elf_host_exports", 1, 0, 48, elf.STB_GLOBAL, elf.STT_OBJECT,
    )
    undefined = [symbol.name for symbol in decoded.symbols[1:]
                 if symbol.section_index == elf.SHN_UNDEF]
    assert undefined == list(inventory)
    assert all(symbol.binding == elf.STB_GLOBAL for symbol in decoded.symbols[2:])


def test_empty_inventory_has_a_valid_zero_count_table_and_no_undefined_roots():
    original, _, decoded = _round_trip(())
    assert decoded == original
    table, names = decoded.sections
    assert table.data == b"PCCEXP01" + bytes(8)
    assert table.relocations == ()
    assert names.data == b"" and names.relocations == ()
    assert all(symbol.section_index != elf.SHN_UNDEF for symbol in decoded.symbols[1:])
    anchor = decoded.symbols[2]
    assert anchor.name == exports.HOST_EXPORT_SYMBOL and anchor.size == 16


def test_inventory_order_and_input_are_preserved_and_bytes_are_deterministic():
    inventory = ["z_last_lexically", "A_first_lexically", "middle"]
    before = list(inventory)
    _, first, decoded = _round_trip(inventory)
    _, second, _ = _round_trip(tuple(inventory))
    assert inventory == before
    assert first == second
    assert decoded.sections[1].data == b"z_last_lexically\0A_first_lexically\0middle\0"
    assert [symbol.name for symbol in decoded.symbols[3:]] == inventory


@pytest.mark.parametrize("inventory", [
    None, "entry", b"entry", {"entry"}, {"entry": 1}, 1,
    ("",), ("a\0b",), ("nonascii_é",), (b"entry",), (1,),
    ("entry", "entry"),
])
def test_malformed_or_unordered_inventory_is_rejected(inventory):
    with pytest.raises(elf.ElfError):
        exports.host_export_object(inventory)


def test_reserved_anchor_cannot_also_be_an_export_root():
    with pytest.raises(elf.ElfError, match="reserved table anchor"):
        exports.host_export_object((exports.HOST_EXPORT_SYMBOL,))


@pytest.mark.parametrize("machine", [0, "62", 62.0, True, None])
def test_unsupported_machine_is_rejected(machine):
    with pytest.raises(elf.ElfError, match="unsupported host export table machine"):
        exports.host_export_object(("entry",), machine=machine)


def test_aarch64_empty_table_is_only_a_relocation_free_header():
    obj = exports.host_export_object((), machine=elf.EM_AARCH64)
    decoded = elf.parse_relocatable(elf.emit_relocatable(obj))
    assert decoded == obj and decoded.machine == elf.EM_AARCH64
    assert decoded.sections[0].data == b"PCCEXP01" + bytes(8)
    assert decoded.sections[1].data == b""
    assert all(section.relocations == () for section in decoded.sections)
    assert all(symbol.section_index != elf.SHN_UNDEF for symbol in decoded.symbols[1:])


def test_aarch64_nonempty_table_is_not_an_implemented_export_capability():
    with pytest.raises(elf.ElfError, match="nonempty.*Linux x86_64"):
        exports.host_export_object(("entry",), machine=elf.EM_AARCH64)
