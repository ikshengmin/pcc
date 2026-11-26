"""AMD64 typed unwind section preserves payload, symbols and relocations."""
import struct

import pytest

from pcc.backend import elf_x86_64 as elf
from tests.python.test_elf_x86_64 import _exit_42_object

# Standard AMD64 DWARF CIE and a single FDE with PC-relative start address.
# The FDE range is the first function of the owned _exit_42_object fixture.
UNWIND = 0x70000001
FRAME = bytes.fromhex(
    "14000000 00000000 017a5200 01781001 1b0c0708 90010000 "
    "14000000 1c000000 00000000 0e000000 00410e10 4a0e0800"
)


def _object(kind):
    source = _exit_42_object()
    frame = elf.ElfSection(
        ".eh_frame", kind, elf.SHF_ALLOC, 8, FRAME,
        relocations=(elf.ElfRelocation(32, 1, elf.R_X86_64_PC32, 0),),
    )
    return elf.ElfObject(source.sections + (frame,), source.symbols)


@pytest.mark.parametrize("compact", (False, True))
def test_x86_64_unwind_section_roundtrip_and_link_preserves_relocation(compact):
    source = _object(UNWIND)
    encoded = elf.emit_relocatable(source)
    parsed = elf.parse_relocatable(encoded, compact_relocations=compact)
    frame = parsed.sections[1]
    assert frame.name == ".eh_frame" and frame.type == UNWIND
    assert (frame.flags, frame.align, frame.data) == (elf.SHF_ALLOC, 8, FRAME)
    assert tuple(frame.relocations) == source.sections[1].relocations
    assert parsed.symbols == source.symbols
    assert elf.emit_relocatable(parsed) == encoded
    # The existing PROGBITS classification of the same frame has identical
    # static image layout; the typed frame must receive its PC32 relocation.
    assert elf.link_static_executable([parsed]) == elf.link_static_executable([_object(elf.SHT_PROGBITS)])


@pytest.mark.parametrize("machine,name,flags", (
    (elf.EM_AARCH64, ".eh_frame", elf.SHF_ALLOC),
    (elf.EM_X86_64, ".other", elf.SHF_ALLOC),
    (elf.EM_X86_64, ".eh_frame", 0),
    (elf.EM_X86_64, ".eh_frame", elf.SHF_ALLOC | elf.SHF_WRITE),
    (elf.EM_X86_64, ".eh_frame", elf.SHF_ALLOC | elf.SHF_EXECINSTR),
    (elf.EM_X86_64, ".eh_frame", elf.SHF_ALLOC | elf.SHF_TLS),
))
def test_unwind_admission_is_machine_name_and_readonly_alloc_specific(machine, name, flags):
    section = elf.ElfSection(name, UNWIND, flags, 8, FRAME)
    with pytest.raises(elf.ElfError, match="unsupported type"):
        elf.ElfObject((section,), (elf.ElfSymbol.null(),), machine)
    # Test raw-header parsing independently of model construction: the public
    # writer makes a regular supported section, then the fixture changes the
    # single processor-specific type marker to the unsupported combination.
    normal = elf.ElfObject((elf.ElfSection(name, elf.SHT_PROGBITS, flags, 8, FRAME),),
                           (elf.ElfSymbol.null(),), machine)
    encoded = bytearray(elf.emit_relocatable(normal))
    shoff = struct.unpack_from("<Q", encoded, 40)[0]
    struct.pack_into("<I", encoded, shoff + 64 + 4, UNWIND)
    with pytest.raises(elf.ElfError, match="unsupported type"):
        elf.parse_relocatable(bytes(encoded))
