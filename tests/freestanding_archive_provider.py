"""Actual ELF archive-provider availability, independent of member extraction.

The container reader understands GNU/BSD/COFF ar framing; this function still
requires an ELF member of the caller's explicit machine. It makes no Mach-O or
COFF object-support claim and never builds, links, or executes a program.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pcc.backend import elf_x86_64 as elf


def require_elf_archive_function(archive_path, *, member_name, symbol_name, machine):
    data = Path(archive_path).read_bytes()
    archive_sha256 = hashlib.sha256(data).hexdigest()
    members = elf.read_archive_payloads(data)
    del data
    matches = [payload for name, payload in members if name == member_name]
    assert len(matches) == 1, "archive must contain exactly one required provider member"
    payload = matches[0]
    del members, matches
    member_sha256 = hashlib.sha256(payload).hexdigest()
    obj = elf.parse_relocatable(payload)
    assert obj.machine == machine, "provider ELF machine differs"
    symbols = [symbol for symbol in obj.symbols if symbol.name == symbol_name]
    assert len(symbols) == 1, "provider must contain exactly one required symbol"
    symbol = symbols[0]
    assert symbol.binding == elf.STB_GLOBAL, "provider must be a strong global definition"
    assert symbol.type == elf.STT_FUNC, "provider must be a function"
    assert 1 <= symbol.section_index <= len(obj.sections), "provider must be section-defined"
    section = obj.sections[symbol.section_index - 1]
    assert section.type == elf.SHT_PROGBITS and section.flags & elf.SHF_EXECINSTR, (
        "provider function must belong to executable object code"
    )
    return {
        "archive_sha256": archive_sha256,
        "member_sha256": member_sha256,
        "member": member_name,
        "symbol": symbol_name,
        "machine": machine,
    }
