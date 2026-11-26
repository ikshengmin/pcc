"""Authenticate the exact AMD64 unwind section type delta for old oracles.

This source-only inverse preserves the existing ODR/host-export/packed
reference chain; current ELF behavior is still compared by the live owner.
"""
import hashlib

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


def without_unwind_section(source):
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest == BASE_OWNER_SHA256:
        return source
    assert digest == OWNER_SHA256
    for changed, original in reversed(OWNER_REVERSE_EDITS):
        assert source.count(changed) == 1
        source = source.replace(changed, original)
    assert hashlib.sha256(source.encode()).hexdigest() == BASE_OWNER_SHA256
    return source
