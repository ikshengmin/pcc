"""Authenticate the exact AMD64 unwind section type delta for old oracles.

This source-only inverse preserves the existing ODR/host-export/packed
reference chain; current ELF behavior is still compared by the live owner.
"""
import hashlib

from tests.elf_function_map_preimage import OWNER_SHA256 as FUNCTION_MAP_OWNER_SHA256, without_function_map

OWNER_SHA256 = "da5749873cfb59d6ee69212f8b433df0359bedc391f96cbc6baf6fcda6ed1f86"
BASE_OWNER_SHA256 = "a99b05a0a491a2c48ab9dedb53d01544aec6fa8cb752bbd3e0afd77e88b4d3a6"
OWNER_REVERSE_EDITS = (
    (
        '''SHT_NOBITS = 8
# AMD64 ABI processor-specific type used for read-only DWARF .eh_frame.
SHT_X86_64_UNWIND = 0x70000001
''',
        '''SHT_NOBITS = 8
''',
    ),
    (
        '''        if section.type not in (SHT_PROGBITS, SHT_NOBITS, SHT_NOTE):
            if not (obj.machine == EM_X86_64
                    and section.type == SHT_X86_64_UNWIND
                    and section.name == ".eh_frame" and section.flags == SHF_ALLOC):
                raise ElfError(
                    f"section {section.name!r} has unsupported type {section.type}"
                )
''',
        '''        if section.type not in (SHT_PROGBITS, SHT_NOBITS, SHT_NOTE):
            raise ElfError(
                f"section {section.name!r} has unsupported type {section.type}"
            )
''',
    ),
    (
        '''        if section_type not in (SHT_PROGBITS, SHT_NOBITS, SHT_NOTE):
            # Preserve the AMD64 unwind type and ordinary payload/relocation
            # ownership. Do not admit unrelated processor-specific sections.
            if not (header[2] == EM_X86_64
                    and section_type == SHT_X86_64_UNWIND
                    and raw_names[source_index] == ".eh_frame" and raw[2] == SHF_ALLOC):
                raise ElfError(
                    f"section {raw_names[source_index]!r} has unsupported type {section_type}"
                )
''',
        '''        if section_type not in (SHT_PROGBITS, SHT_NOBITS, SHT_NOTE):
            raise ElfError(
                f"section {raw_names[source_index]!r} has unsupported type {section_type}"
            )
''',
    ),
)



# The complete later STT_FILE delta, production commit 184f51f0. This
# independent layer restores the exact unwind-section owner above, not an
# approximation or a new admissible historical hash.
FILE_OWNER_SHA256 = "1d3ae5dc5a987d6a2a60b8332a1cfb2672d83ee1a15c87785847851cb75462d4"
FILE_REVERSE_EDITS = (
    (
        'STT_SECTION = 3\nSTT_FILE = 4\nSTT_TLS = 6\n',
        'STT_SECTION = 3\nSTT_TLS = 6\n',
    ),
    (
        '                elif relocation.type in (283, 282, 311, 312, 541, 542):\n                    classification_candidate[0] = False\n    file_symbol_indices = None\n    for index, symbol in enumerate(obj.symbols):\n        if index == 0:\n            continue\n        if symbol.binding not in (STB_LOCAL, STB_GLOBAL, STB_WEAK):\n            raise ElfError(f"symbol {symbol.name!r} has unsupported binding")\n        if symbol.type not in (STT_NOTYPE, STT_OBJECT, STT_FUNC, STT_SECTION, STT_FILE, STT_TLS):\n            raise ElfError(f"symbol {symbol.name!r} has unsupported type")\n        if symbol.visibility not in (STV_DEFAULT, STV_HIDDEN):\n            raise ElfError(f"symbol {symbol.name!r} has unsupported visibility")\n        if symbol.type == STT_FILE:\n            # A source-file symbol is local provenance, not an address or\n            # an externally bindable identity. Keep its table index intact.\n            if (symbol.binding != STB_LOCAL or symbol.section_index != SHN_ABS\n                    or symbol.value != 0 or symbol.size != 0\n                    or symbol.visibility != STV_DEFAULT\n                    or not symbol.name or "\\0" in symbol.name):\n                raise ElfError(f"invalid local absolute file symbol {symbol.name!r}")\n            if file_symbol_indices is None:\n                file_symbol_indices = set()\n            file_symbol_indices.add(index)\n        if symbol.binding == STB_LOCAL:\n',
        '                elif relocation.type in (283, 282, 311, 312, 541, 542):\n                    classification_candidate[0] = False\n    for index, symbol in enumerate(obj.symbols):\n        if index == 0:\n            continue\n        if symbol.binding not in (STB_LOCAL, STB_GLOBAL, STB_WEAK):\n            raise ElfError(f"symbol {symbol.name!r} has unsupported binding")\n        if symbol.type not in (STT_NOTYPE, STT_OBJECT, STT_FUNC, STT_SECTION, STT_TLS):\n            raise ElfError(f"symbol {symbol.name!r} has unsupported type")\n        if symbol.visibility not in (STV_DEFAULT, STV_HIDDEN):\n            raise ElfError(f"symbol {symbol.name!r} has unsupported visibility")\n        if symbol.binding == STB_LOCAL:\n',
    ),
    (
        '    # The ordinary owned object route emits no FILE symbols; do not add a\n    # second relocation walk there. Reference inputs which do name files\n    # must never resolve them as ordinary local absolute addresses.\n    if file_symbol_indices is not None:\n        for section in obj.sections:\n            for relocation in section.relocations:\n                if relocation.symbol_index in file_symbol_indices:\n                    raise ElfError(f"section {section.name!r} relocation targets file symbol metadata")\n\n    for section in obj.sections:\n        if section.name in (".eh_frame", ".debug_frame", ".pcc_stackmaps"):\n',
        '    for section in obj.sections:\n        if section.name in (".eh_frame", ".debug_frame", ".pcc_stackmaps"):\n',
    ),
)


def without_file_symbols(source):
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest == FUNCTION_MAP_OWNER_SHA256:
        source = without_function_map(source)
        digest = hashlib.sha256(source.encode()).hexdigest()
    if digest == OWNER_SHA256:
        return source
    assert digest == FILE_OWNER_SHA256
    for changed, original in reversed(FILE_REVERSE_EDITS):
        assert source.count(changed) == 1
        source = source.replace(changed, original)
    assert hashlib.sha256(source.encode()).hexdigest() == OWNER_SHA256
    return source

def without_unwind_section(source):
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest in (FILE_OWNER_SHA256, FUNCTION_MAP_OWNER_SHA256):
        source = without_file_symbols(source)
        digest = hashlib.sha256(source.encode()).hexdigest()
    if digest == BASE_OWNER_SHA256:
        return source
    assert digest == OWNER_SHA256
    for changed, original in reversed(OWNER_REVERSE_EDITS):
        assert source.count(changed) == 1
        source = source.replace(changed, original)
    assert hashlib.sha256(source.encode()).hexdigest() == BASE_OWNER_SHA256
    return source
