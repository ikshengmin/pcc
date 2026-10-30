"""Finite PCC-native ELF relocatable extensions, not system shared objects.

This publication profile is intended for an owned Linux loader over the existing
ELF64 ET_REL section/symbol/RELA model. A PCC note names the only public exports
and the explicit host imports.
No runtime archive, dynamic linker, or second object format is introduced.
Publication alone does not implement loading: ET_DYN, TLS, constructors, GOT
relocations and dynamically registered stack maps remain explicit gaps.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from . import elf_x86_64 as elf


_NOTE_NAME = ".note.pcc.extension"
_NOTE_OWNER = b"PCC\0"
_NOTE_TYPE = 0x50434301
_ABI_VERSION = 1
_NOTE_HEADER = struct.Struct("<III")
_DESCRIPTOR = struct.Struct("<IIIIII")
_INDEX = struct.Struct("<I")
_SHN_LORESERVE = 0xFF00
_RELOCATIONS = (
    elf.R_X86_64_NONE, elf.R_X86_64_64, elf.R_X86_64_PC32,
    elf.R_X86_64_PLT32, elf.R_X86_64_32, elf.R_X86_64_32S,
)


@dataclass(frozen=True)
class ElfExtension:
    """Decoded payload and its explicit loader-visible symbol inventories."""

    object: elf.ElfObject
    export_symbols: tuple[str, ...]
    host_import_symbols: tuple[str, ...]


def _inventory(values, label):
    if not isinstance(values, (list, tuple)):
        raise elf.ElfError(label + " must be an ordered symbol sequence")
    result = []
    seen = set()
    for name in values:
        if (not isinstance(name, str) or not name or "\0" in name
                or not name.isascii()):
            raise elf.ElfError(label + " contains an invalid ASCII symbol")
        if name in seen:
            raise elf.ElfError(label + " contains a duplicate symbol: " + name)
        seen.add(name)
        result.append(name)
    return tuple(result)


def _validate_inputs(objects):
    if not objects:
        raise elf.ElfError("relocatable extension requires an explicit object")
    for obj in objects:
        elf._validate_object(obj)
        if obj.machine != elf.EM_X86_64:
            raise elf.ElfError("relocatable extensions currently require Linux x86_64")
        for section in obj.sections:
            if section.name == _NOTE_NAME:
                raise elf.ElfError("input already contains an extension ABI note")
            if not section.flags & elf.SHF_ALLOC:
                if (section.name not in (".note.GNU-stack", ".comment")
                        or section.flags or section.type != elf.SHT_PROGBITS or section.relocations
                        or (section.name == ".note.GNU-stack" and section.data)):
                    raise elf.ElfError("unsupported extension metadata: " + section.name)
                continue
            if (section.flags & ~(elf.SHF_ALLOC | elf.SHF_WRITE | elf.SHF_EXECINSTR)
                    or section.align > 4096):
                raise elf.ElfError("unsupported extension section flags/alignment: " + section.name)
            if section.flags & elf.SHF_WRITE and section.flags & elf.SHF_EXECINSTR:
                raise elf.ElfError("writable executable extension section is unsupported")
            if (section.type not in (elf.SHT_PROGBITS, elf.SHT_NOBITS)
                    or (section.type == elf.SHT_NOBITS and not section.flags & elf.SHF_WRITE)):
                raise elf.ElfError("unsupported extension section kind: " + section.name)
            if section.name.endswith((".init", ".fini")) or any(part in section.name for part in (
                ".init_array", ".fini_array", ".preinit_array", ".ctors", ".dtors",
                ".pcc_stackmaps", ".eh_frame", ".gcc_except_table",
            )):
                raise elf.ElfError("extension initialization/unwind/stack-map metadata is unsupported: " + section.name)
            spans = []
            for relocation in section.relocations:
                if relocation.type not in _RELOCATIONS:
                    raise elf.ElfError("unsupported extension relocation: " + str(relocation.type))
                width = elf._relocation_width(relocation.type)
                if width:
                    spans.append((relocation.offset, relocation.offset + width))
            previous_end = 0
            for start, end in sorted(spans):
                if start < previous_end:
                    raise elf.ElfError("overlapping extension relocations are unsupported")
                previous_end = end
        for symbol in obj.symbols[1:]:
            if symbol.type == elf.STT_TLS:
                raise elf.ElfError("extension TLS symbols are unsupported")
            if symbol.binding == elf.STB_LOCAL and symbol.section_index == elf.SHN_UNDEF:
                raise elf.ElfError("undefined local extension symbol")
            if symbol.section_index == elf.SHN_UNDEF and symbol.visibility != elf.STV_DEFAULT:
                raise elf.ElfError("hidden extension import must be defined locally")
            if symbol.section_index not in (elf.SHN_UNDEF, elf.SHN_ABS):
                if not obj.sections[symbol.section_index - 1].flags & elf.SHF_ALLOC:
                    raise elf.ElfError("extension symbol refers to discarded metadata: " + symbol.name)
            if symbol.binding != elf.STB_LOCAL:
                _inventory((symbol.name,), "extension global symbols")


def _admit_symbols(objects, exports, hosts):
    definitions, _ = elf._global_state(objects)
    hidden_names = set()
    for obj in objects:
        for symbol in obj.symbols[1:]:
            if symbol.binding != elf.STB_LOCAL and symbol.visibility != elf.STV_DEFAULT:
                hidden_names.add(symbol.name)
    for name in hosts:
        if name in definitions:
            raise elf.ElfError("extension defines a host-owned symbol: " + name)
    if not exports:
        raise elf.ElfError("relocatable extension requires a public export")
    for name in exports:
        if name not in definitions:
            raise elf.ElfError("extension export is not defined: " + name)
        object_index, symbol_index = definitions[name]
        symbol = objects[object_index].symbols[symbol_index]
        if (symbol.section_index in (elf.SHN_UNDEF, elf.SHN_ABS)
                or symbol.visibility != elf.STV_DEFAULT):
            raise elf.ElfError("extension export is not a public section definition: " + name)
        if name in hidden_names:
            raise elf.ElfError("extension export has a hidden declaration: " + name)
    host_set = set(hosts)
    for obj in objects:
        for symbol in obj.symbols[1:]:
            if (symbol.section_index == elf.SHN_UNDEF
                    and symbol.name not in definitions and symbol.name not in host_set):
                raise elf.ElfError("extension import has no explicit host owner: " + symbol.name)
    return definitions


def _note_payload(symbol_count, export_indices, import_indices):
    chunks = [_DESCRIPTOR.pack(
        _ABI_VERSION, 0, symbol_count, len(export_indices), len(import_indices), 0,
    )]
    for index in tuple(export_indices) + tuple(import_indices):
        chunks.append(_INDEX.pack(index))
    descriptor = b"".join(chunks)
    return (_NOTE_HEADER.pack(len(_NOTE_OWNER), len(descriptor), _NOTE_TYPE)
            + _NOTE_OWNER + descriptor)


def _check_section_count(count):
    # SHN_LORESERVE begins the reserved-index and extended-count range, not
    # merely the limit of the 16-bit field. This profile has no SHN_XINDEX.
    if count <= 0 or count >= _SHN_LORESERVE:
        raise elf.ElfError("extension exceeds the ordinary ELF section-index range")


def link_relocatable_extension(objects, *, export_symbols, host_import_symbols=()):
    """Merge explicit objects without resolving host addresses or linking a runtime.

    Symbol and section indices are rewritten, not their payloads or addends.
    Locals retain translation-unit identity. Global winner selection reuses the
    owned ELF weak/strong rule; all nonlocal references, including references
    in a losing weak-definition TU, bind to that winner. Inputs are not mutated.
    """
    objects = list(objects)
    exports = _inventory(export_symbols, "extension exports")
    hosts = _inventory(host_import_symbols, "host imports")
    _validate_inputs(objects)
    definitions = _admit_symbols(objects, exports, hosts)
    section_indices = {}
    source_sections = []
    for object_index, obj in enumerate(objects):
        for section_index, section in enumerate(obj.sections, start=1):
            if section.flags & elf.SHF_ALLOC:
                section_indices[(object_index, section_index)] = len(source_sections) + 1
                source_sections.append((object_index, section_index, section))

    symbols = [elf.ElfSymbol.null()]
    symbol_indices = {}
    global_names = []
    global_sources = {}
    hidden_globals = set()

    def append_symbol(object_index, symbol_index, force_hidden=False):
        symbol = objects[object_index].symbols[symbol_index]
        section_index = symbol.section_index
        if section_index not in (elf.SHN_UNDEF, elf.SHN_ABS):
            section_index = section_indices[(object_index, section_index)]
        symbols.append(elf.ElfSymbol(
            symbol.name, section_index, symbol.value, symbol.size,
            symbol.binding, symbol.type,
            elf.STV_HIDDEN if force_hidden else symbol.visibility,
        ))
        return len(symbols) - 1

    for object_index, obj in enumerate(objects):
        symbol_indices[(object_index, 0)] = 0
        for symbol_index, symbol in enumerate(obj.symbols[1:], start=1):
            if symbol.binding != elf.STB_LOCAL and symbol.visibility == elf.STV_HIDDEN:
                hidden_globals.add(symbol.name)
            if symbol.binding == elf.STB_LOCAL:
                symbol_indices[(object_index, symbol_index)] = append_symbol(object_index, symbol_index)
            elif symbol.name not in global_sources:
                global_names.append(symbol.name)
                global_sources[symbol.name] = (object_index, symbol_index)
            elif symbol.name not in definitions:
                old_object, old_symbol = global_sources[symbol.name]
                old = objects[old_object].symbols[old_symbol]
                if old.binding == elf.STB_WEAK and symbol.binding == elf.STB_GLOBAL:
                    global_sources[symbol.name] = (object_index, symbol_index)

    globals_by_name = {}
    for name in global_names:
        object_index, symbol_index = definitions.get(name, global_sources[name])
        globals_by_name[name] = append_symbol(object_index, symbol_index, name in hidden_globals)
    for object_index, obj in enumerate(objects):
        for symbol_index, symbol in enumerate(obj.symbols[1:], start=1):
            if symbol.binding != elf.STB_LOCAL:
                symbol_indices[(object_index, symbol_index)] = globals_by_name[symbol.name]

    sections = []
    for object_index, section_index, section in source_sections:
        relocations = tuple(elf.ElfRelocation(
            row.offset, symbol_indices[(object_index, row.symbol_index)], row.type, row.addend,
        ) for row in section.relocations)
        name = ".pcc.ext." + str(object_index) + "." + str(section_index) + section.name
        sections.append(elf.ElfSection(
            name, section.type, section.flags, section.align, section.data,
            section.mem_size, relocations,
        ))
    export_indices = tuple(globals_by_name[name] for name in exports)
    import_indices = tuple(
        globals_by_name[name] for name in global_names if name not in definitions
    )
    sections.append(elf.ElfSection(
        _NOTE_NAME, elf.SHT_NOTE, 0, 4,
        _note_payload(len(symbols), export_indices, import_indices),
    ))
    # Count the complete emitted table: user sections (including our note),
    # RELA sections, then null/symtab/strtab/shstrtab. Every real shndx and
    # shstrndx is smaller than this count, hence below SHN_LORESERVE too.
    _check_section_count(len(sections) + sum(bool(section.relocations) for section in sections) + 4)
    return elf.emit_relocatable(elf.ElfObject(tuple(sections), tuple(symbols)))


def parse_relocatable_extension(data, *, host_import_symbols=()):
    """Validate the owned extension note and its explicit import admission."""
    hosts = _inventory(host_import_symbols, "host imports")
    header = elf._unpack_elf_header(data)
    _check_section_count(header[12])
    if header[0][7:] != bytes(9) or header[4] or header[7]:
        raise elf.ElfError("unsupported PCC extension ELF ABI/header flags")
    obj = elf.parse_relocatable(data)
    notes = [(index, section) for index, section in enumerate(obj.sections)
             if section.name == _NOTE_NAME]
    if len(notes) != 1 or notes[0][0] != len(obj.sections) - 1:
        raise elf.ElfError("extension requires one final PCC ABI note")
    note = notes[0][1]
    if note.type != elf.SHT_NOTE or note.flags or note.align != 4 or note.relocations:
        raise elf.ElfError("invalid extension ABI note section")
    payload = note.data
    start = _NOTE_HEADER.size + len(_NOTE_OWNER)
    if len(payload) < start + _DESCRIPTOR.size:
        raise elf.ElfError("truncated extension ABI note")
    namesz, descsz, kind = _NOTE_HEADER.unpack_from(payload)
    if (namesz != len(_NOTE_OWNER) or kind != _NOTE_TYPE
            or payload[_NOTE_HEADER.size:start] != _NOTE_OWNER
            or descsz != len(payload) - start):
        raise elf.ElfError("invalid extension ABI note header")
    version, flags, symbol_count, export_count, import_count, reserved = _DESCRIPTOR.unpack_from(payload, start)
    if version != _ABI_VERSION or flags or reserved or symbol_count != len(obj.symbols):
        raise elf.ElfError("unsupported extension ABI descriptor")
    if descsz != _DESCRIPTOR.size + (export_count + import_count) * _INDEX.size:
        raise elf.ElfError("invalid extension ABI symbol counts")
    indices = []
    seen_indices = set()
    position = start + _DESCRIPTOR.size
    while position < len(payload):
        index = _INDEX.unpack_from(payload, position)[0]
        if index == 0 or index >= len(obj.symbols) or index in seen_indices:
            raise elf.ElfError("invalid extension ABI symbol index")
        indices.append(index)
        seen_indices.add(index)
        position += _INDEX.size
    export_indices = indices[:export_count]
    import_indices = indices[export_count:]
    actual_imports = [index for index, symbol in enumerate(obj.symbols)
                      if index and symbol.section_index == elf.SHN_UNDEF]
    if import_indices != actual_imports:
        raise elf.ElfError("extension import note does not match undefined symbols")
    exports = _inventory(tuple(obj.symbols[index].name for index in export_indices), "extension exports")
    imports = tuple(obj.symbols[index].name for index in import_indices)
    body = elf.ElfObject(obj.sections[:-1], obj.symbols, obj.machine)
    _validate_inputs([body])
    global_names = set()
    for symbol in body.symbols[1:]:
        if symbol.binding != elf.STB_LOCAL:
            if symbol.name in global_names:
                raise elf.ElfError("extension publication has duplicate nonlocal symbols")
            global_names.add(symbol.name)
    _admit_symbols([body], exports, hosts)
    for index in export_indices:
        symbol = obj.symbols[index]
        if (symbol.binding == elf.STB_LOCAL
                or symbol.section_index in (elf.SHN_UNDEF, elf.SHN_ABS)
                or symbol.visibility != elf.STV_DEFAULT):
            raise elf.ElfError("extension export note does not refer to a public section definition")
    return ElfExtension(body, exports, imports)
