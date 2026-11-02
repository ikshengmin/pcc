"""Authenticate the complete ELF ODR delta before older source-proof layers.

The two hunks are the complete ``elf_x86_64.py`` change from
c2ac2459a117f89a64dc78648e9fddd4624b255e to
91658d8f1fddce2bcba3ee3f942419adb9cee06a. Both whole LF owners are pinned.
This is test-reference source restoration, not a production linker adapter:
current link comparisons still call the unchanged current ELF implementation.
No compiler, linker or production module is imported here.
"""

import hashlib


OWNER_SHA256 = "a99b05a0a491a2c48ab9dedb53d01544aec6fa8cb752bbd3e0afd77e88b4d3a6"
BASE_OWNER_SHA256 = "74d0f5db1fb1507526fe460f66416dd2a6fd9fd4c704a5bb2e2bb03aa0e2f864"
OWNER_REVERSE_EDITS = (
    (
        '''    for section in obj.sections:
        if section.name in (".eh_frame", ".debug_frame", ".pcc_stackmaps"):
            for relocation in section.relocations:
                target = obj.symbols[relocation.symbol_index]
                if (target.binding == STB_WEAK
                        and target.section_index != SHN_UNDEF):
                    raise ElfError("weak function metadata requires a physical local anchor")
        if section.name != ".pcc_stackmaps":
            continue
''',
        '''    for section in obj.sections:
        if section.name != ".pcc_stackmaps":
            continue
''',
    ),
    (
        '''    symbol = objects[object_index].symbols[symbol_index]
    if symbol.binding == STB_LOCAL or (symbol.section_index != SHN_UNDEF and symbol.binding != STB_WEAK):
        return ("local", (object_index, symbol_index))
    if symbol.name in definitions:
''',
        '''    symbol = objects[object_index].symbols[symbol_index]
    if symbol.binding == STB_LOCAL or symbol.section_index != SHN_UNDEF:
        return ("local", (object_index, symbol_index))
    if symbol.name in definitions:
''',
    ),
)


def without_external_odr(source):
    """Restore only the authenticated parent; reject partial or unknown owners."""
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest == BASE_OWNER_SHA256:
        return source
    assert digest == OWNER_SHA256
    for changed, original in reversed(OWNER_REVERSE_EDITS):
        assert source.count(changed) == 1
        source = source.replace(changed, original)
    assert hashlib.sha256(source.encode()).hexdigest() == BASE_OWNER_SHA256
    return source
