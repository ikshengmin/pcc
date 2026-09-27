"""Project pcc's owned AArch64 instruction/section transport into ELF64.

The instruction encoder's internal @PAGE spelling is shared with Darwin;
the serialized object has ELF sections, ELF symbols and AArch64 RELA fixups.
No Mach-O object is serialized or linked on this path.
"""

import struct

from . import macho_spec as ms
from .arm64_asm_driver import assemble_file as assemble_sections
from .elf_x86_64 import (
    EM_AARCH64, ElfError, ElfObject, ElfRelocation, ElfSection, ElfSymbol,
    SHF_ALLOC, SHF_EXECINSTR, SHF_TLS, SHF_WRITE, SHN_UNDEF,
    SHT_NOBITS, SHT_PROGBITS, STB_GLOBAL, STB_LOCAL, STT_FUNC,
    STT_NOTYPE, STT_OBJECT, STT_TLS, STV_DEFAULT, STV_HIDDEN,
)

_SECTIONS = {
    ("__TEXT", "__text"): (".text", SHF_ALLOC | SHF_EXECINSTR),
    ("__TEXT", "__cstring"): (".rodata.str", SHF_ALLOC),
    ("__TEXT", "__const"): (".rodata", SHF_ALLOC),
    ("__DATA", "__const"): (".data.rel.ro", SHF_ALLOC),
    ("__DATA", "__data"): (".data", SHF_ALLOC | SHF_WRITE),
    ("__DATA", "__bss"): (".bss", SHF_ALLOC | SHF_WRITE),
    ("__DATA", "__thread_data"): (".tdata", SHF_ALLOC | SHF_WRITE | SHF_TLS),
    ("__DATA", "__thread_bss"): (".tbss", SHF_ALLOC | SHF_WRITE | SHF_TLS),
    ("__DATA", "__mod_init_func"): (".init_array", SHF_ALLOC | SHF_WRITE),
    ("__DATA", "__pcc_stackmaps"): (".pcc_stackmaps", SHF_ALLOC),
}


def _section_spec(section):
    if section.segname == "__PCC" and section.sectname.startswith(("__init", "__fini")):
        family = "init" if section.sectname.startswith("__init") else "fini"
        return ("." + family + "_array." + section.sectname[6:], SHF_ALLOC | SHF_WRITE)
    key = (section.segname, section.sectname)
    if key not in _SECTIONS:
        raise ElfError("unsupported AArch64 ELF section: " + str(key))
    return _SECTIONS[key]


def from_sections(sections, undefined=()) -> ElfObject:
    local = []
    public = []
    tls_names = set()
    storage_sections = []
    for section in sections:
        if (section.segname, section.sectname) == ("__PCC", "__tls_refs"):
            if section.symbols or section.relocations or section.is_zerofill:
                raise ElfError("invalid AArch64 external TLS metadata")
            if section.data and not section.data.endswith(b"\0"):
                raise ElfError("unterminated AArch64 external TLS metadata")
            for raw_name in section.data.split(b"\0"):
                if raw_name:
                    tls_names.add(raw_name.decode("utf-8"))
        else:
            storage_sections.append(section)
    sections = storage_sections
    for index, section in enumerate(sections, 1):
        name, flags = _section_spec(section)
        for symbol in section.symbols:
            kind = STT_TLS if flags & SHF_TLS else (
                STT_FUNC if flags & SHF_EXECINSTR else STT_OBJECT
            )
            record = ElfSymbol(symbol.name, index, symbol.offset, 0,
                               STB_GLOBAL if symbol.external else STB_LOCAL,
                               kind, STV_HIDDEN if symbol.private_external else STV_DEFAULT)
            (public if symbol.external else local).append(record)
            if kind == STT_TLS:
                tls_names.add(symbol.name)
    defined = {symbol.name for symbol in local + public}
    missing = set(undefined)
    for section in sections:
        for relocation in section.relocations:
            if relocation.section is not None or relocation.minuend is not None:
                raise ElfError("AArch64 ELF requires named, non-subtractor relocations")
            if relocation.symbol not in defined:
                missing.add(relocation.symbol)
    public.extend(ElfSymbol(name, SHN_UNDEF, 0, 0, STB_GLOBAL,
                           STT_TLS if name in tls_names else STT_NOTYPE)
                  for name in sorted(missing - defined))
    symbols = [ElfSymbol.null()] + local + public
    indices = {symbol.name: index for index, symbol in enumerate(symbols)}
    output = []
    for section in sections:
        name, flags = _section_spec(section)
        data = bytearray(section.data)
        relocs = []
        for relocation in section.relocations:
            offset = relocation.offset
            addend = relocation.addend
            kind = relocation.type
            if kind == ms.ARM64_RELOC_UNSIGNED:
                width = 1 << relocation.length
                if width not in (4, 8):
                    raise ElfError("unsupported AArch64 absolute fixup width")
                addend += int.from_bytes(data[offset:offset + width], "little", signed=True)
                data[offset:offset + width] = b"\0" * width
                elf_kind = 257 if width == 8 else 258
            elif kind == ms.ARM64_RELOC_BRANCH26:
                word = struct.unpack_from("<I", data, offset)[0]
                elf_kind = 283 if word & 0x80000000 else 282
            elif kind == ms.ARM64_RELOC_PAGE21:
                elf_kind = 275
            elif kind == ms.ARM64_RELOC_PAGEOFF12:
                word = struct.unpack_from("<I", data, offset)[0]
                if word & 0x3B000000 == 0x39000000:
                    scale = (word >> 30) & 3
                    if word & 0x04000000 and word & 0x00800000:
                        scale = 4
                    elf_kind = (278, 284, 285, 286, 299)[scale]
                else:
                    elf_kind = 277
            elif kind == ms.ARM64_RELOC_GOT_LOAD_PAGE21:
                elf_kind = 541 if relocation.symbol in tls_names else 311
            elif kind == ms.ARM64_RELOC_GOT_LOAD_PAGEOFF12:
                elf_kind = 542 if relocation.symbol in tls_names else 312
            else:
                raise ElfError("unsupported AArch64 transport relocation: " + str(kind))
            relocs.append(ElfRelocation(offset, indices[relocation.symbol], elf_kind, addend))
        output.append(ElfSection(name, SHT_NOBITS if section.is_zerofill else SHT_PROGBITS,
                                 flags, 1 << section.align_log2, bytes(data),
                                 section.zerofill_size, tuple(relocs)))
    return ElfObject(tuple(output), tuple(symbols), EM_AARCH64)


def assemble_file(text: str) -> ElfObject:
    sections, undefined = assemble_sections(text)
    return from_sections(sections, undefined)
