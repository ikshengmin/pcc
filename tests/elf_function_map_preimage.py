"""Authenticate the complete function-map delta before older ELF reference layers.

The two additive hunks are from production owner 81b49ee01e2dbec3a0f635dcc87d75260ce7f0f2.
Only source restoration runs here; current comparison still uses the live owner.
"""

import hashlib

OWNER_SHA256 = "4a1bcf676875128a38c2148dbacc757b7e520996e0deb3cb04f9161226b8a91a"
BASE_OWNER_SHA256 = "1d3ae5dc5a987d6a2a60b8332a1cfb2672d83ee1a15c87785847851cb75462d4"

OWNER_ADDITIONS = (
    '    function_ranges: list[tuple] | None = None,\n',
    '    if function_ranges is not None:\n        # Only the final link still owns both the physical placement and the\n        # original symbol tables. Never resolve a losing weak definition to\n        # a different object or infer a size from its neighboring symbol.\n        from .owned_elf_inputs import _InputSection\n        for object_index, obj in enumerate(objects):\n            for symbol_index, symbol in enumerate(obj.symbols[1:], start=1):\n                if (symbol.type != STT_FUNC or not symbol.name or symbol.size <= 0\n                        or not 1 <= symbol.section_index <= len(obj.sections)):\n                    continue\n                section_index = symbol.section_index\n                section = obj.sections[section_index - 1]\n                if (not section.flags & SHF_ALLOC or not section.flags & SHF_EXECINSTR\n                        or section.type == SHT_NOBITS):\n                    continue\n                placement = placements[(object_index, section_index)]\n                file_offset = placement.file_offset + symbol.value\n                if (symbol.value < 0 or symbol.value + symbol.size > section.size\n                        or file_offset < 0 or file_offset + symbol.size > rx_file_end):\n                    raise ElfError("function map range outside executable placement")\n                source_index = -1\n                if type(section) is _InputSection:\n                    if section.store is not _input_store:\n                        raise ElfError("function map staged source owner mismatch")\n                    source_index = section.source_index\n                address = placement.address + symbol.value\n                function_ranges.append((address, object_index, symbol_index,\n                                        address + symbol.size, file_offset,\n                                        section_index, symbol.binding, symbol.name,\n                                        section.name, source_index))\n',
)


def without_function_map(source):
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest == BASE_OWNER_SHA256:
        return source
    assert digest == OWNER_SHA256
    for added in reversed(OWNER_ADDITIONS):
        assert source.count(added) == 1
        source = source.replace(added, "")
    assert hashlib.sha256(source.encode()).hexdigest() == BASE_OWNER_SHA256
    return source
