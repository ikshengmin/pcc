"""Exact 8ba4e13fc ELF owner inverse, before the older source-proof chain.

The four hunks are the complete elf_x86_64.py delta in
8ba4e13fc29b4f2133e37af0a8683f5c55b2c799, whose parent is
69175165fba8687d00a00fda2764272327fc9832. Both complete LF owners are
authenticated; no old source-proof hash or linker implementation is changed.
This module imports no production code and performs no link or compiler run.
"""

import hashlib


OWNER_SHA256 = "74d0f5db1fb1507526fe460f66416dd2a6fd9fd4c704a5bb2e2bb03aa0e2f864"
BASE_OWNER_SHA256 = "4e4c195e57dd0cc668589b8c7e5156c4563a1bb9f309a508ff663147c564e54a"
OWNER_REVERSE_EDITS = (
    (
        '''    base_address: int = _BASE,
    archive_selections: list[tuple[int, str]] | None = None,
    compact_archive_relocations: bool = False,
    export_symbols: tuple[str, ...] = (),
) -> bytes:
    """Public immutable ELF image; private publication may retain the builder."""
    if not isinstance(export_symbols, (list, tuple)):
        raise ElfError("host exports must be an ordered symbol sequence")
    if export_symbols:
        return bytes(_link_static_executable_image(
            objects, archives=archives, entry=entry, base_address=base_address,
            archive_selections=archive_selections,
            compact_archive_relocations=compact_archive_relocations,
            export_symbols=export_symbols,
        ))
    return bytes(_link_static_executable_image(
        objects, archives=archives, entry=entry, base_address=base_address,
        archive_selections=archive_selections,
''',
        '''    base_address: int = _BASE,
    archive_selections: list[tuple[int, str]] | None = None,
    compact_archive_relocations: bool = False,
) -> bytes:
    """Public immutable ELF image; private publication may retain the builder."""
    return bytes(_link_static_executable_image(
        objects, archives=archives, entry=entry, base_address=base_address,
        archive_selections=archive_selections,
''',
    ),
    (
        '''    compact_archive_relocations: bool = False,
    _input_store=None,
    coordinator_timing=None,
    export_symbols: tuple[str, ...] = (),
) -> bytearray:
    """Build a private mutable image, fully validated before publication."""
    image_phase_started = 0
''',
        '''    compact_archive_relocations: bool = False,
    _input_store=None,
    coordinator_timing=None,
) -> bytearray:
    """Build a private mutable image, fully validated before publication."""
    image_phase_started = 0
''',
    ),
    (
        '''        raise ElfError("static ELF base address must be page-aligned and >= 0x10000")
    objects = list(objects)
    machine = objects[0].machine
    if not isinstance(export_symbols, (list, tuple)):
        raise ElfError("host exports must be an ordered symbol sequence")
    export_symbols = tuple(export_symbols)
    host_export_object_index = -1
    if export_symbols:
        from .elf_host_exports import host_export_object
        # Undefined address fields are ordinary archive roots. No separate
        # symbol scanner or post-link host-tool export pass is involved.
        host_export_object_index = len(objects)
        objects.append(host_export_object(export_symbols, machine=machine))
    boundary_names = ("__init_array_start", "__init_array_end", "__fini_array_start", "__fini_array_end")
    boundary_symbols = [ElfSymbol.null()]
    for name in boundary_names:
''',
        '''        raise ElfError("static ELF base address must be page-aligned and >= 0x10000")
    objects = list(objects)
    machine = objects[0].machine
    boundary_names = ("__init_array_start", "__init_array_end", "__fini_array_start", "__fini_array_end")
    boundary_symbols = [ElfSymbol.null()]
    for name in boundary_names:
''',
    ),
    (
        '''        definitions, undefined = _global_state(objects)
    if any(obj.machine != machine for obj in objects):
        raise ElfError("cannot mix ELF machines in a link")
    if "__pcc_elf_host_exports" in definitions:
        for object_index, obj in enumerate(objects):
            if object_index == host_export_object_index:
                continue
            for symbol in obj.symbols[1:]:
                if (symbol.name == "__pcc_elf_host_exports"
                        and symbol.binding != STB_LOCAL
                        and symbol.section_index != SHN_UNDEF):
                    raise ElfError("reserved host export table anchor must be linker-owned")
    if not export_symbols and "__pcc_elf_host_exports" in undefined:
        from .elf_host_exports import host_export_object
        # A loader linked without export intent gets an explicit empty table,
        # not implicit visibility of every main-image definition. Older links
        # that never reference this reserved anchor remain byte-identical.
        objects.append(host_export_object((), machine=machine))
        definitions, undefined = _global_state(objects)
    if undefined:
        raise ElfError("undefined static ELF symbols: " + ", ".join(sorted(undefined)))
    if export_symbols:
        selected_exports = set(export_symbols)
        hidden_exports = set()
        for obj in objects:
            for symbol in obj.symbols[1:]:
                if (symbol.binding != STB_LOCAL and symbol.name in selected_exports
                        and symbol.visibility != STV_DEFAULT):
                    hidden_exports.add(symbol.name)
        for name in export_symbols:
            object_index, symbol_index = definitions[name]
            symbol = objects[object_index].symbols[symbol_index]
            if (symbol.section_index in (SHN_UNDEF, SHN_ABS)
                    or name in hidden_exports):
                raise ElfError("host export is not a public section definition: " + name)
            section = objects[object_index].sections[symbol.section_index - 1]
            if symbol.type == STT_TLS or section.flags & SHF_TLS:
                raise ElfError("host TLS exports are not supported: " + name)
    if entry not in definitions:
        raise ElfError(f"entry symbol {entry!r} is not defined")

''',
        '''        definitions, undefined = _global_state(objects)
    if any(obj.machine != machine for obj in objects):
        raise ElfError("cannot mix ELF machines in a link")
    if undefined:
        raise ElfError("undefined static ELF symbols: " + ", ".join(sorted(undefined)))
    if entry not in definitions:
        raise ElfError(f"entry symbol {entry!r} is not defined")

''',
    ),
)


def without_host_exports(source):
    digest = hashlib.sha256(source.encode()).hexdigest()
    if digest == BASE_OWNER_SHA256:
        return source
    assert digest == OWNER_SHA256
    for changed, original in reversed(OWNER_REVERSE_EDITS):
        assert source.count(changed) == 1
        source = source.replace(changed, original)
    assert hashlib.sha256(source.encode()).hexdigest() == BASE_OWNER_SHA256
    return source
