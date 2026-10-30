"""Describe an explicit main-image export table with the owned ELF object model.

This object supplies archive roots and address relocations, not proof that an
export is public or belongs to a verified runtime. The link owner must validate
the selected definitions before publishing the image. No runtime, archive or
image writer is introduced here.
"""

import struct

from . import elf_x86_64 as elf


HOST_EXPORT_SYMBOL = "__pcc_elf_host_exports"

_MAGIC = b"PCCEXP01"
_HEADER = struct.Struct("<8sQ")
_ROW_SIZE = 16
_NAMES_SYMBOL = ".Lpcc_host_export_names"


def host_export_object(export_symbols, *, machine=elf.EM_X86_64):
    """Return a read-only table whose explicit undefined symbols root exports.

    Inventory order is preserved. Each row is ``(name_pointer, address_pointer)``;
    both pointers remain ordinary R_X86_64_64 relocations until the existing ELF
    linker assigns addresses. An empty inventory still publishes a valid header;
    AArch64 permits only that empty, relocation-free form, not extension loading.
    """
    if type(machine) is not int or machine not in (elf.EM_X86_64, elf.EM_AARCH64):
        raise elf.ElfError("unsupported host export table machine")
    if not isinstance(export_symbols, (list, tuple)):
        raise elf.ElfError("host exports must be an ordered symbol sequence")
    names = []
    seen = set()
    for name in export_symbols:
        if (not isinstance(name, str) or not name or "\0" in name
                or not name.isascii()):
            raise elf.ElfError("host exports contain an invalid ASCII symbol")
        if name == HOST_EXPORT_SYMBOL:
            raise elf.ElfError("host exports contain the reserved table anchor")
        if name in seen:
            raise elf.ElfError("host exports contain a duplicate symbol: " + name)
        seen.add(name)
        names.append(name)
    if names and machine != elf.EM_X86_64:
        raise elf.ElfError("nonempty host export tables currently require Linux x86_64")

    name_chunks = []
    name_offsets = []
    names_size = 0
    for name in names:
        encoded = name.encode("ascii") + b"\0"
        name_offsets.append(names_size)
        name_chunks.append(encoded)
        names_size += len(encoded)
    names_data = b"".join(name_chunks)
    table_data = _HEADER.pack(_MAGIC, len(names)) + bytes(_ROW_SIZE * len(names))
    symbols = [
        elf.ElfSymbol.null(),
        elf.ElfSymbol(_NAMES_SYMBOL, 2, 0, names_size,
                      elf.STB_LOCAL, elf.STT_OBJECT),
        elf.ElfSymbol(HOST_EXPORT_SYMBOL, 1, 0, len(table_data),
                      elf.STB_GLOBAL, elf.STT_OBJECT),
    ]
    relocations = []
    for index, name in enumerate(names):
        symbols.append(elf.ElfSymbol(
            name, elf.SHN_UNDEF, 0, 0, elf.STB_GLOBAL, elf.STT_NOTYPE,
        ))
        row_offset = _HEADER.size + index * _ROW_SIZE
        relocations.append(elf.ElfRelocation(
            row_offset, 1, elf.R_X86_64_64, name_offsets[index],
        ))
        relocations.append(elf.ElfRelocation(
            row_offset + 8, len(symbols) - 1, elf.R_X86_64_64, 0,
        ))
    sections = (
        elf.ElfSection(".rodata.pcc.host_exports", elf.SHT_PROGBITS,
                       elf.SHF_ALLOC, 8, table_data,
                       relocations=tuple(relocations)),
        elf.ElfSection(".rodata.pcc.host_export_names", elf.SHT_PROGBITS,
                       elf.SHF_ALLOC, 1, names_data),
    )
    return elf.ElfObject(sections, tuple(symbols), machine)
