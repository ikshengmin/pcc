"""Owned ET_REL extension publication; not ET_DYN or native loader acceptance."""

from dataclasses import replace
import builtins
import struct
import subprocess

import pytest

from pcc.backend import elf_extension as extension
from pcc.backend import elf_x86_64 as elf


def _unit(name="extension_entry", *, binding=elf.STB_GLOBAL, visibility=elf.STV_DEFAULT):
    return elf.ElfObject((
        elf.ElfSection(".text", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_EXECINSTR,
                       16, b"\xc3"),
    ), (
        elf.ElfSymbol.null(),
        elf.ElfSymbol(name, 1, 0, 1, binding, elf.STT_FUNC, visibility),
    ))


def _importing_unit(name="extension_entry", host="host_api", binding=elf.STB_GLOBAL):
    return elf.ElfObject((
        elf.ElfSection(".text", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_EXECINSTR,
                       16, b"\xe8\0\0\0\0\xc3",
                       relocations=(elf.ElfRelocation(1, 2, elf.R_X86_64_PLT32, -4),)),
    ), (
        elf.ElfSymbol.null(),
        elf.ElfSymbol(name, 1, 0, 6, elf.STB_GLOBAL, elf.STT_FUNC),
        elf.ElfSymbol(host, elf.SHN_UNDEF, 0, 0, binding, elf.STT_FUNC),
    ))


def _link(objects, exports=("extension_entry",), hosts=()):
    return extension.link_relocatable_extension(
        objects, export_symbols=exports, host_import_symbols=hosts,
    )


def _parse(data, hosts=()):
    return extension.parse_relocatable_extension(data, host_import_symbols=hosts)


def _change_note(data, change):
    obj = elf.parse_relocatable(data)
    note = obj.sections[-1]
    payload = bytearray(note.data)
    change(payload)
    return elf.emit_relocatable(replace(
        obj, sections=obj.sections[:-1] + (replace(note, data=bytes(payload)),),
    ))


def test_publication_keeps_et_rel_and_exact_unresolved_rela_contract():
    original = _importing_unit()
    before = elf.emit_relocatable(original)
    data = _link([original], hosts=("unused_host", "host_api"))
    assert elf._ELF_HEADER.unpack_from(data)[1] == elf.ET_REL
    decoded = _parse(data, ("unused_host", "host_api"))
    assert decoded.export_symbols == ("extension_entry",)
    assert decoded.host_import_symbols == ("host_api",)
    text = decoded.object.sections[0]
    assert text.data == original.sections[0].data
    row = text.relocations[0]
    assert (row.offset, row.type, row.addend) == (1, elf.R_X86_64_PLT32, -4)
    assert decoded.object.symbols[row.symbol_index].name == "host_api"
    assert decoded.object.symbols[row.symbol_index].section_index == elf.SHN_UNDEF
    assert _link([original], hosts=("unused_host", "host_api")) == data
    assert elf.emit_relocatable(original) == before
    with pytest.raises(elf.ElfError, match="not ET_EXEC"):
        elf.parse_static_executable(data)


def test_translation_unit_locals_with_the_same_name_keep_distinct_identities():
    def local_unit(entry, value):
        return elf.ElfObject((
            elf.ElfSection(".data", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_WRITE,
                           8, bytes(8), relocations=(elf.ElfRelocation(0, 1, elf.R_X86_64_64),)),
            elf.ElfSection(".local", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_WRITE,
                           4, struct.pack("<I", value)),
        ), (
            elf.ElfSymbol.null(),
            elf.ElfSymbol("same_local", 2, 0, 4, elf.STB_LOCAL, elf.STT_OBJECT),
            elf.ElfSymbol(entry, 1, 0, 8, elf.STB_GLOBAL, elf.STT_OBJECT),
        ))

    decoded = _parse(_link([local_unit("first", 11), local_unit("second", 23)],
                          exports=("second", "first")))
    assert decoded.export_symbols == ("second", "first")
    targets = [section.relocations[0].symbol_index for section in decoded.object.sections
               if section.relocations]
    assert len(targets) == 2 and targets[0] != targets[1]
    values = []
    for index in targets:
        symbol = decoded.object.symbols[index]
        assert symbol.name == "same_local" and symbol.binding == elf.STB_LOCAL
        values.append(struct.unpack("<I", decoded.object.sections[symbol.section_index - 1].data)[0])
    assert values == [11, 23]


def test_strong_definition_wins_over_weak_for_every_translation_unit_reference():
    caller = _importing_unit(host="implementation")
    weak = _unit("implementation", binding=elf.STB_WEAK)
    weak = replace(weak, sections=weak.sections + (elf.ElfSection(
        ".weak_pointer", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_WRITE, 8,
        bytes(8), relocations=(elf.ElfRelocation(0, 1, elf.R_X86_64_64),),
    ),))
    strong = _unit("implementation")
    for providers in ((weak, strong), (strong, weak)):
        decoded = _parse(_link([caller, *providers]))
        definitions = [s for s in decoded.object.symbols if s.name == "implementation"]
        assert len(definitions) == 1 and definitions[0].binding == elf.STB_GLOBAL
        rows = [row for section in decoded.object.sections for row in section.relocations]
        assert len(rows) == 2  # Includes a reference from the losing weak TU.
        assert all(decoded.object.symbols[row.symbol_index] == definitions[0] for row in rows)
        assert decoded.host_import_symbols == ()


def test_duplicate_strong_definition_is_not_hidden_by_merging():
    with pytest.raises(elf.ElfError, match="duplicate strong"):
        _link([_unit(), _unit()])


def test_global_host_definition_is_rejected_even_when_not_exported_or_imported():
    with pytest.raises(elf.ElfError, match="host-owned"):
        _link([_unit(), _unit("host_api")], hosts=("host_api",))


def test_hidden_declaration_is_not_lost_when_a_public_strong_definition_wins():
    hidden = _unit("implementation", binding=elf.STB_WEAK, visibility=elf.STV_HIDDEN)
    public = _unit("implementation")
    with pytest.raises(elf.ElfError, match="hidden declaration"):
        _link([public, hidden], exports=("implementation",))
    decoded = _parse(_link([_unit(), public, hidden]))
    implementation = next(symbol for symbol in decoded.object.symbols if symbol.name == "implementation")
    assert implementation.visibility == elf.STV_HIDDEN


def test_hidden_undefined_symbol_cannot_bind_to_a_host_owner():
    obj = _importing_unit()
    symbols = obj.symbols[:-1] + (replace(obj.symbols[-1], visibility=elf.STV_HIDDEN),)
    with pytest.raises(elf.ElfError, match="hidden extension import"):
        _link([replace(obj, symbols=symbols)], hosts=("host_api",))


@pytest.mark.parametrize("binding", [elf.STB_GLOBAL, elf.STB_WEAK])
def test_every_unresolved_import_requires_explicit_host_admission(binding):
    with pytest.raises(elf.ElfError, match="explicit host owner"):
        _link([_importing_unit(binding=binding)])
    data = _link([_importing_unit(binding=binding)], hosts=("host_api",))
    with pytest.raises(elf.ElfError, match="explicit host owner"):
        _parse(data)


@pytest.mark.parametrize("inventory", ["entry", b"entry", {"entry"}, ("",),
                                       ("a\0b",), ("nonascii_é",), ("entry", "entry")])
def test_export_inventory_is_ordered_unique_ascii(inventory):
    with pytest.raises(elf.ElfError):
        _link([_unit()], exports=inventory)


@pytest.mark.parametrize("objects,exports", [
    ([], ("extension_entry",)),
    ([_unit()], ()),
    ([_unit()], ("absent",)),
    ([_unit(binding=elf.STB_LOCAL)], ("extension_entry",)),
    ([_unit(visibility=elf.STV_HIDDEN)], ("extension_entry",)),
])
def test_exports_need_actual_public_definitions(objects, exports):
    with pytest.raises(elf.ElfError):
        _link(objects, exports=exports)


def test_absolute_symbol_is_not_an_exported_memory_definition():
    obj = elf.ElfObject((), (elf.ElfSymbol.null(), elf.ElfSymbol(
        "extension_entry", elf.SHN_ABS, 42, 0, elf.STB_GLOBAL, elf.STT_NOTYPE,
    )))
    with pytest.raises(elf.ElfError, match="section definition"):
        _link([obj])


@pytest.mark.parametrize("name", [".init", ".fini", ".init_array", ".fini_array.42", ".preinit_array",
                                  ".ctors", ".dtors", ".eh_frame", ".pcc_stackmaps.extra"])
def test_initialization_unwind_and_dynamic_stackmap_gaps_remain_explicit(name):
    obj = replace(_unit(), sections=(replace(_unit().sections[0], name=name),))
    with pytest.raises(elf.ElfError, match="metadata is unsupported"):
        _link([obj])


@pytest.mark.parametrize("flags,alignment", [
    (elf.SHF_ALLOC | elf.SHF_WRITE | elf.SHF_TLS, 8),
    (elf.SHF_ALLOC | elf.SHF_WRITE | elf.SHF_EXECINSTR, 16),
    (elf.SHF_ALLOC | elf.SHF_EXECINSTR, 8192),
])
def test_unqualified_section_mapping_shapes_are_rejected(flags, alignment):
    obj = replace(_unit(), sections=(replace(_unit().sections[0], flags=flags, align=alignment),))
    with pytest.raises(elf.ElfError):
        _link([obj])


@pytest.mark.parametrize("kind", [elf.R_X86_64_GOTPCREL, elf.R_X86_64_GOTTPOFF,
                                 elf.R_X86_64_TPOFF32, elf.R_X86_64_GOTPCRELX,
                                 elf.R_X86_64_REX_GOTPCRELX])
def test_got_and_tls_relocations_are_not_silently_downgraded(kind):
    obj = _importing_unit()
    section = replace(obj.sections[0], relocations=(elf.ElfRelocation(1, 2, kind, -4),))
    with pytest.raises(elf.ElfError, match="unsupported extension relocation"):
        _link([replace(obj, sections=(section,))], hosts=("host_api",))


@pytest.mark.parametrize("second_offset", [0, 4])
def test_overlapping_relocations_are_not_given_an_unimplemented_composition_rule(second_offset):
    obj = elf.ElfObject((elf.ElfSection(
        ".data", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_WRITE, 8, bytes(16),
        relocations=(elf.ElfRelocation(0, 1, elf.R_X86_64_64),
                     elf.ElfRelocation(second_offset, 1, elf.R_X86_64_64)),
    ),), (elf.ElfSymbol.null(), elf.ElfSymbol(
        "extension_entry", 1, 0, 16, elf.STB_GLOBAL, elf.STT_OBJECT,
    )))
    with pytest.raises(elf.ElfError, match="overlapping"):
        _link([obj])


def test_zero_fill_data_has_no_payload_and_preserves_its_size():
    obj = elf.ElfObject((elf.ElfSection(
        ".bss", elf.SHT_NOBITS, elf.SHF_ALLOC | elf.SHF_WRITE, 32, mem_size=8192,
    ),), (elf.ElfSymbol.null(), elf.ElfSymbol(
        "storage", 1, 0, 8192, elf.STB_GLOBAL, elf.STT_OBJECT,
    )))
    data = _link([obj], exports=("storage",))
    section = _parse(data).object.sections[0]
    assert section.data == b"" and section.mem_size == 8192 and section.align == 32
    assert len(data) < 8192


def test_non_x86_target_is_not_admitted_by_elf_format_alone():
    with pytest.raises(elf.ElfError, match="Linux x86_64"):
        _link([replace(_unit(), machine=elf.EM_AARCH64)])


def test_executable_stack_request_is_not_discarded_as_metadata():
    obj = _unit()
    stack = elf.ElfSection(".note.GNU-stack", elf.SHT_PROGBITS, elf.SHF_EXECINSTR, 1)
    with pytest.raises(elf.ElfError, match="unsupported extension metadata"):
        _link([replace(obj, sections=obj.sections + (stack,))])


def test_a_plain_relocatable_is_not_implicitly_an_extension():
    with pytest.raises(elf.ElfError, match="PCC ABI note"):
        _parse(elf.emit_relocatable(_unit()))


@pytest.mark.parametrize("offset,format,value", [(7, "B", 3), (8, "B", 1),
                                               (24, "Q", 1), (48, "I", 1)])
def test_unknown_elf_abi_entry_or_flags_are_not_admitted(offset, format, value):
    data = bytearray(_link([_unit()]))
    struct.pack_into("<" + format, data, offset, value)
    with pytest.raises(elf.ElfError, match="ELF ABI/header"):
        _parse(bytes(data))


def test_ordinary_section_count_stops_before_reserved_indices():
    extension._check_section_count(0xFEFF)
    for count in (0, 0xFF00, elf.SHN_ABS, 0xFFFF, 0x10000):
        with pytest.raises(elf.ElfError, match="section-index range"):
            extension._check_section_count(count)


def test_publisher_applies_count_check_to_the_complete_wire_section_table(monkeypatch):
    seen = []

    def reject(count):
        seen.append(count)
        raise elf.ElfError("finite section boundary")

    monkeypatch.setattr(extension, "_check_section_count", reject)
    with pytest.raises(elf.ElfError, match="finite section boundary"):
        _link([_importing_unit()], hosts=("host_api",))
    # text, PCC note, RELA, plus null/symtab/strtab/shstrtab.
    assert seen == [7]


def test_rela_and_note_metadata_count_toward_the_publication_boundary(monkeypatch):
    monkeypatch.setattr(extension, "_SHN_LORESERVE", 7)
    without_rela = _link([_unit()])
    header = elf._ELF_HEADER.unpack_from(without_rela)
    assert header[12] == 6 and header[13] == 5
    with pytest.raises(elf.ElfError, match="section-index range"):
        _link([_importing_unit()], hosts=("host_api",))


def test_parser_rejects_extended_section_count_before_reading_section_rows():
    data = bytearray(_link([_unit()]))
    struct.pack_into("<H", data, 60, 0xFF00)
    with pytest.raises(elf.ElfError, match="section-index range"):
        _parse(bytes(data))


def test_parser_rejects_reserved_section_name_table_index():
    data = bytearray(_link([_unit()]))
    struct.pack_into("<H", data, 62, 0xFF00)
    with pytest.raises(elf.ElfError, match="section-name table index"):
        _parse(bytes(data))


@pytest.mark.parametrize("offset,value", [
    (0, 8), (8, 0), (16, 2), (20, 1), (24, 999), (28, 999), (36, 1),
    (40, 0), (40, 999),
])
def test_note_header_version_flags_counts_and_indices_fail_closed(offset, value):
    data = _link([_importing_unit()], hosts=("host_api",))
    changed = _change_note(data, lambda payload: struct.pack_into("<I", payload, offset, value))
    with pytest.raises(elf.ElfError):
        _parse(changed, ("host_api",))


def test_note_may_not_omit_or_reclassify_an_undefined_import():
    data = _link([_importing_unit()], hosts=("host_api",))

    def omit(payload):
        struct.pack_into("<I", payload, 4, len(payload) - 16 - 4)
        struct.pack_into("<I", payload, 32, 0)
        del payload[-4:]

    with pytest.raises(elf.ElfError, match="does not match undefined"):
        _parse(_change_note(data, omit), ("host_api",))
    with pytest.raises(elf.ElfError, match="symbol index"):
        _parse(_change_note(data, lambda payload: payload.__setitem__(slice(44, 48), payload[40:44])),
               ("host_api",))


def test_parser_rechecks_host_collision_against_the_consumers_full_inventory():
    data = _link([_unit(), _unit("other_host")])
    with pytest.raises(elf.ElfError, match="host-owned"):
        _parse(data, ("other_host",))


@pytest.mark.parametrize("section_index,visibility", [
    (elf.SHN_ABS, elf.STV_DEFAULT), (1, elf.STV_HIDDEN),
    (elf.SHN_UNDEF, elf.STV_DEFAULT),
])
def test_parser_rejects_duplicate_nonlocal_names_in_merged_publication(section_index, visibility):
    original = elf.parse_relocatable(_link([_unit()]))
    duplicate = elf.ElfSymbol("extension_entry", section_index, 0, 0,
                              elf.STB_WEAK, elf.STT_FUNC, visibility)
    imports = (2,) if section_index == elf.SHN_UNDEF else ()
    exports = (1,) if imports else (2,)
    note = replace(original.sections[-1], data=extension._note_payload(3, exports, imports))
    malformed = elf.emit_relocatable(replace(
        original, symbols=original.symbols + (duplicate,),
        sections=original.sections[:-1] + (note,),
    ))
    with pytest.raises(elf.ElfError, match="duplicate nonlocal"):
        _parse(malformed)


def test_real_c_translation_units_use_owned_publication_without_external_tools(monkeypatch):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.c.evaluator.c_evaluator import _compile_preprocessed_translation_unit_artifact

    original_import = builtins.__import__

    def checked_import(name, *args, **kwargs):
        if name == "llvmlite" or name.startswith("llvmlite."):
            raise AssertionError("external LLVM import")
        return original_import(name, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError("external compiler/linker process")

    monkeypatch.setattr(builtins, "__import__", checked_import)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    target = "x86_64-unknown-linux-gnu"
    units = (
        ("helper.c", "static int local=5; int helper(int x){return x+local;}"),
        ("extension.c", "extern int helper(int); extern int host_api(int); "
         "static int local=7; int extension_entry(void){return host_api(helper(local));}"),
    )
    objects = []
    for name, source in units:
        artifact = _compile_preprocessed_translation_unit_artifact(
            name, source, frontend_opt_level=0, target_triple=target,
        )
        objects.append(elf.parse_relocatable(emit_owned_object(artifact["ir_text"], target)))
    first = _link(objects, hosts=("host_api",))
    assert _link(objects, hosts=("host_api",)) == first
    decoded = _parse(first, ("host_api",))
    assert decoded.host_import_symbols == ("host_api",)
    assert decoded.export_symbols == ("extension_entry",)
    names = {symbol.name: symbol for symbol in decoded.object.symbols}
    assert names["helper"].section_index != elf.SHN_UNDEF
    assert names["host_api"].section_index == elf.SHN_UNDEF
    # This executes host development compilation, not pcc1 or a loaded image.
