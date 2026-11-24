"""Owned ELF symbol inspection, independent of the collector."""
from dataclasses import replace
from pathlib import Path
import subprocess

import pytest

from pcc.backend import elf_x86_64 as elf


def _owned_elf_symbol_names(path: Path) -> tuple[set[str], set[str]]:
    # Inspect the emitted bytes without a separately scheduled nm process.
    # Unlike archive resolution, exact closure includes weak undefined names.
    # Only symbol zero is omitted; parse_relocatable validates its null shape.
    symbols = elf.parse_relocatable(path.read_bytes()).symbols[1:]
    undefined = {
        symbol.name for symbol in symbols if symbol.section_index == elf.SHN_UNDEF
    }
    defined = {
        symbol.name for symbol in symbols
        if symbol.binding != elf.STB_LOCAL and symbol.section_index != elf.SHN_UNDEF
    }
    return undefined, defined


def _symbol_fixture():
    return elf.ElfObject(
        sections=(
            elf.ElfSection(".text", elf.SHT_PROGBITS, elf.SHF_ALLOC, 1, b"\xc3"),
            elf.ElfSection(".bss", elf.SHT_NOBITS, elf.SHF_ALLOC | elf.SHF_WRITE, 8,
                           mem_size=8),
        ),
        symbols=(
            elf.ElfSymbol.null(),
            elf.ElfSymbol("", 1, 0, 0, elf.STB_LOCAL, elf.STT_SECTION),
            elf.ElfSymbol("local_definition", 1, 0, 1, elf.STB_LOCAL, elf.STT_FUNC),
            elf.ElfSymbol("local_import", elf.SHN_UNDEF, 0, 0,
                          elf.STB_LOCAL, elf.STT_NOTYPE),
            elf.ElfSymbol("global_import", elf.SHN_UNDEF, 0, 0,
                          elf.STB_GLOBAL, elf.STT_NOTYPE),
            elf.ElfSymbol("weak_import", elf.SHN_UNDEF, 0, 0,
                          elf.STB_WEAK, elf.STT_NOTYPE),
            elf.ElfSymbol("global_definition", 1, 0, 1, elf.STB_GLOBAL, elf.STT_FUNC),
            elf.ElfSymbol("weak_definition", 1, 0, 1, elf.STB_WEAK, elf.STT_FUNC),
            elf.ElfSymbol("hidden_definition", 1, 0, 1, elf.STB_GLOBAL, elf.STT_FUNC,
                          elf.STV_HIDDEN),
            elf.ElfSymbol("absolute_definition", elf.SHN_ABS, 42, 0,
                          elf.STB_GLOBAL, elf.STT_NOTYPE),
            elf.ElfSymbol("bss_definition", 2, 0, 8, elf.STB_GLOBAL, elf.STT_OBJECT),
        ),
    )


def test_owned_symbol_names_preserve_exact_binding_and_section_semantics(tmp_path, monkeypatch):
    path = tmp_path / "symbols.o"
    path.write_bytes(elf.emit_relocatable(_symbol_fixture()))

    def forbid_process(*args, **kwargs):
        raise AssertionError("object-closure inspection must not start an external process")

    monkeypatch.setattr(subprocess, "Popen", forbid_process)
    assert _owned_elf_symbol_names(path) == (
        {"local_import", "global_import", "weak_import"},
        {"global_definition", "weak_definition", "hidden_definition",
         "absolute_definition", "bss_definition"},
    )


@pytest.mark.parametrize("binding", [elf.STB_LOCAL, elf.STB_GLOBAL, elf.STB_WEAK])
def test_owned_symbol_names_include_unreferenced_undefined_symbols(tmp_path, binding):
    original = _symbol_fixture()
    extra = elf.ElfSymbol("unexpected_import", elf.SHN_UNDEF, 0, 0,
                          binding, elf.STT_NOTYPE)
    symbols = list(original.symbols)
    symbols.insert(1 if binding == elf.STB_LOCAL else len(symbols), extra)
    path = tmp_path / "unexpected.o"
    path.write_bytes(elf.emit_relocatable(replace(original, symbols=tuple(symbols))))
    undefined, _defined = _owned_elf_symbol_names(path)
    assert undefined == {
        "local_import", "global_import", "weak_import", "unexpected_import",
    }


@pytest.mark.parametrize("payload", [b"", b"not an ELF object", b"\x7fELF" + b"\0" * 64])
def test_owned_symbol_names_reject_invalid_objects(tmp_path, payload):
    path = tmp_path / "invalid.o"
    path.write_bytes(payload)
    with pytest.raises(elf.ElfError):
        _owned_elf_symbol_names(path)


def test_owned_symbol_names_reject_truncated_symbol_table(tmp_path):
    encoded = elf.emit_relocatable(_symbol_fixture())
    header = elf._ELF_HEADER.unpack_from(encoded)
    section_table = header[6]
    damaged = bytearray(encoded)
    for offset in range(section_table, len(encoded), elf._SECTION_HEADER.size):
        section = list(elf._SECTION_HEADER.unpack_from(encoded, offset))
        if section[1] == elf.SHT_SYMTAB:
            section[5] -= 1
            elf._SECTION_HEADER.pack_into(damaged, offset, *section)
            break
    else:
        raise AssertionError("fixture is missing its symbol table")
    path = tmp_path / "truncated-symbols.o"
    path.write_bytes(damaged)
    with pytest.raises(elf.ElfError, match="symbol table has an invalid entry size"):
        _owned_elf_symbol_names(path)
