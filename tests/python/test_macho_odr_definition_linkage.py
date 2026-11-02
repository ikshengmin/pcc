"""C++17 [basic.def.odr]/[dcl.fct.spec]: bounded owned function coalescing.

These are Darwin object/link-format checks on the host, not Darwin execution.
Mach-O nlist.h N_WEAK_DEF is independent of N_WEAK_REF.
"""
import pytest

from pcc.backend import macho_spec as spec
from pcc.backend.arm64_asm_driver import AArch64ModuleBuilder, assemble_file
from pcc.backend.arm64_encode import EncodeError, STRUCTURED_FIXUP_CALL
from pcc.backend.macho_exec import TEXT_BASE, link_executable
from pcc.backend.macho_link import LinkError, link_relocatable_native
from pcc.backend.macho_obj import (
    Relocation,
    Section,
    TextSymbol,
    TEXT_SECTION_FLAGS,
)
from pcc.backend.macho_parallel import ShardedSymbolDefinitions, SymbolDefinition
from pcc.backend.native_object import (
    NativeObject,
    NativeObjectError,
    decode_native_object,
    decode_packed_native_object,
    encode_native_object,
    encode_native_object_from_sections,
)


def _object(value=1, weak=True, pointer=None):
    lines = [".section __TEXT,__text,regular,pure_instructions", ".globl _f"]
    if weak:
        lines.append(".weak_definition _f")
    lines += ["_f:", f"mov w0, #{value}", "ret"]
    if pointer:
        lines += [".section __DATA,__data", f".globl {pointer}", f"{pointer}:", ".quad _f"]
    sections, undefined = assemble_file("\n".join(lines))
    return NativeObject.from_sections(sections, undefined=undefined)


def _representation(obj, kind):
    if kind == "native":
        return obj
    if kind == "packed":
        return decode_packed_native_object(encode_native_object(obj))
    return obj.to_macho()


@pytest.mark.parametrize("kind", ["native", "packed", "macho"])
def test_weak_definition_roundtrips_separately_from_weak_reference(kind):
    obj = _object()
    sections, undefined = obj.to_sections()
    encoded = encode_native_object_from_sections(sections, undefined=undefined)
    assert encoded == encode_native_object(obj)
    assert decode_native_object(encoded) == obj
    if kind == "packed":
        symbol = _representation(obj, kind).symbols[0]
        assert symbol.weak_definition and not symbol.weak_reference
    elif kind == "macho":
        symbol = spec.parse_object(_representation(obj, kind)).symbols()[0]
        assert symbol["n_desc"] == spec.N_WEAK_DEF == 0x80
    else:
        assert obj.symbols[0].weak_definition and not obj.symbols[0].weak_reference


@pytest.mark.parametrize("kind", ["native", "packed", "macho"])
@pytest.mark.parametrize("strengths,expected", [((True, True), 0), ((True, False), 8), ((False, True), 0)])
def test_prevailing_definition_and_all_function_addresses(kind, strengths, expected):
    first = _object(1, strengths[0], "_p_first")
    second = _object(2, strengths[1], "_p_second")
    merged = link_relocatable_native([_representation(first, kind), _representation(second, kind)])
    function = next(symbol for symbol in merged.symbols if symbol.name == "_f")
    assert function.offset == expected
    assert function.weak_definition == all(strengths)
    function_index = merged.symbols.index(function)
    data = next(section for section in merged.sections if section.sectname == "__data")
    assert [row.symbol_index for row in data.relocations] == [function_index, function_index]
    assert merged == link_relocatable_native([_representation(first, kind), _representation(second, kind)])


@pytest.mark.parametrize("kind", ["native", "packed", "macho"])
def test_duplicate_strong_definitions_still_fail(kind):
    with pytest.raises(LinkError, match="duplicate definition"):
        link_relocatable_native([_representation(_object(weak=False), kind)] * 2)


@pytest.mark.parametrize("structured", [False, True])
def test_recursive_weak_call_keeps_relocation(structured):
    builder = AArch64ModuleBuilder(["_f"])
    try:
        for line in [".section __TEXT,__text,regular,pure_instructions", ".globl _f", ".weak_definition _f", "_f:"]:
            builder.append_chunk(line)
        if structured:
            builder.append_encoded(0x94000000, STRUCTURED_FIXUP_CALL, 0)
        else:
            builder.append_chunk("bl _f")
        builder.append_chunk("ret")
        sections, _undefined = builder.finish()
    finally:
        builder.close()
    assert len(sections[0].relocations) == 1
    assert sections[0].relocations[0].symbol == "_f"
    assert sections[0].data[:4] == b"\x00\x00\x00\x94"


def test_parallel_definition_collection_chooses_source_order_not_insertion_order():
    definitions = ShardedSymbolDefinitions()
    definitions.add("_f", SymbolDefinition(2, 0, True))
    definitions.add("_f", SymbolDefinition(0, 0, True))
    definitions.add("_g", SymbolDefinition(0, 0, True))
    definitions.add("_g", SymbolDefinition(2, 0, False))
    definitions.freeze()
    assert definitions.owner("_f") == SymbolDefinition(0, 0, True)
    assert definitions.owner("_g") == SymbolDefinition(2, 0, False)


@pytest.mark.parametrize("kind", ["native", "packed", "macho"])
def test_symbolic_weak_metadata_target_fails_closed(kind):
    source = _object()
    sections, undefined = source.to_sections()
    sections.append(Section(
        "__debug_info", "__DWARF", b"\0" * 8, flags=spec.S_ATTR_DEBUG,
        relocations=(Relocation(0, "_f", spec.ARM64_RELOC_UNSIGNED, False, length=3),),
    ))
    obj = NativeObject.from_sections(sections, undefined=undefined)
    with pytest.raises(LinkError, match="metadata requires a physical"):
        link_relocatable_native([_representation(obj, kind)])


@pytest.mark.parametrize("kind", ["native", "packed", "macho"])
def test_physical_metadata_targets_stay_with_each_input_body(kind):
    import struct
    # DWARF4 section 7.5: one childless DW_TAG_compile_unit with low_pc
    # (DW_FORM_addr) and high_pc (DW_FORM_data4, an eight-byte extent).
    abbreviations = b"\x01\x11\x00\x11\x01\x12\x06\x00\x00\x00"
    unit = struct.pack("<IHIB", 20, 4, 0, 8) + b"\x01" + struct.pack("<QI", 0, 8)
    low_pc_offset = 12
    inputs = []
    for value in (1, 2):
        obj = _object(value)
        sections, undefined = obj.to_sections()
        sections.extend([
            Section("__debug_abbrev", "__DWARF", abbreviations,
                    align_log2=0, flags=spec.S_ATTR_DEBUG),
            Section(
                "__debug_info", "__DWARF", unit, align_log2=0, flags=spec.S_ATTR_DEBUG,
                relocations=(Relocation(low_pc_offset, "", spec.ARM64_RELOC_UNSIGNED, False,
                                        length=3, section=("__TEXT", "__text"), target_offset=0),),
            ),
        ])
        inputs.append(_representation(NativeObject.from_sections(sections, undefined=undefined), kind))
    merged = link_relocatable_native(inputs)
    debug = next(section for section in merged.sections if section.sectname == "__debug_info")
    assert [row.target_section_index for row in debug.relocations] == [1, 1]
    assert [row.offset for row in debug.relocations] == [low_pc_offset, len(unit) + low_pc_offset]
    # Native transport deliberately leaves pointer fields zero-filled and
    # carries physical addresses in the typed section-target relocation.
    assert [row.target_offset for row in debug.relocations] == [0, 8]
    for row in debug.relocations:
        assert debug.data[row.offset:row.offset + 8] == b"\0" * 8
    # The Mach-O writer owns final object-address materialization. Verify the
    # actual serialized DWARF addresses as well, not just the native model.
    encoded = merged.to_macho()
    # Independently decode mach_header_64 / segment_command_64 / section_64
    # fixed fields rather than using the writer's companion object parser.
    assert struct.unpack_from("<I", encoded)[0] == 0xFEEDFACF
    command_count = struct.unpack_from("<I", encoded, 16)[0]
    position = 32
    section_addresses = {}
    for _ in range(command_count):
        command, size = struct.unpack_from("<II", encoded, position)
        assert size >= 8 and position + size <= len(encoded)
        if command == 0x19:  # LC_SEGMENT_64
            count = struct.unpack_from("<I", encoded, position + 64)[0]
            assert size == 72 + 80 * count
            for index in range(count):
                section = position + 72 + 80 * index
                name = encoded[section:section + 16].split(b"\0", 1)[0]
                address = struct.unpack_from("<Q", encoded, section + 32)[0]
                file_offset = struct.unpack_from("<I", encoded, section + 48)[0]
                section_addresses[name] = (address, file_offset)
        position += size
    text_address, _ = section_addresses[b"__text"]
    _, debug_file_offset = section_addresses[b"__debug_info"]
    for unit_index, expected_body_offset in enumerate((0, 8)):
        offset = debug_file_offset + len(unit) * unit_index + low_pc_offset
        assert struct.unpack_from("<Q", encoded, offset)[0] == text_address + expected_body_offset


def test_weak_definition_cannot_be_local_data_or_undefined():
    with pytest.raises(EncodeError, match="defined code symbol"):
        assemble_file(".section __TEXT,__text,regular,pure_instructions\n.weak_definition _missing\n_main:\nret")
    for section in [
        Section("__text", "__TEXT", b"\xc0\x03\x5f\xd6", flags=TEXT_SECTION_FLAGS,
                symbols=(TextSymbol("_f", 0, external=False, weak_definition=True),)),
        Section("__data", "__DATA", b"\0" * 8,
                symbols=(TextSymbol("_f", 0, weak_definition=True),)),
    ]:
        with pytest.raises(NativeObjectError, match="external code symbol"):
            NativeObject.from_sections([section])


def test_closed_executable_function_addresses_are_one_address():
    main_sections, undefined = assemble_file(
        ".section __TEXT,__text,regular,pure_instructions\n.globl _main\n_main:\nmov w0, #0\nret"
    )
    main = NativeObject.from_sections(main_sections, undefined=undefined)
    image = link_executable([main, _object(1, pointer="_p_first"), _object(2, pointer="_p_second")])
    executable = spec.parse_object(image)
    symbols = {symbol["name"]: symbol for symbol in executable.symbols()}
    assert symbols["_f"]["n_value"] > 0
    for name in ("_p_first", "_p_second"):
        symbol = symbols[name]
        section = executable.sections()[symbol["n_sect"] - 1]
        offset = section["offset"] + symbol["n_value"] - section["addr"]
        word = int.from_bytes(image[offset:offset + 8], "little")
        assert word >> 63 == 0  # DYLD_CHAINED_PTR_64_OFFSET rebase, not bind.
        assert (word & ((1 << 36) - 1)) + TEXT_BASE == symbols["_f"]["n_value"]


def test_dynamic_export_and_shared_publication_fail_closed():
    from pcc.backend import BackendUnavailable
    from pcc.backend.macho_shared import _check_imports
    with pytest.raises(LinkError, match="unsupported dynamic coalescing"):
        link_executable([_object()], entry="_f", export_symbols=("_f",))
    with pytest.raises(BackendUnavailable, match="weak-definition coalescing"):
        _check_imports(_object())


def test_windowed_packed_merge_has_same_coalescing_and_metadata_guard(tmp_path):
    from pcc.backend import macho_host_prepare as host
    from pcc.backend.macho_exec import link_prepared_executable
    from tests.python.test_macho_windowed_prepare import _budget
    paths = []
    inputs = [_object(1, pointer="_p_first"), _object(2, pointer="_p_second")]
    for index, obj in enumerate(inputs):
        path = tmp_path / f"{index}.pco"
        path.write_bytes(encode_native_object(obj))
        paths.append(str(path))
    budget = _budget(10_000_000)
    prepared = host.prepare_executable_from_paths(paths, compact_budget=budget)
    actual = link_prepared_executable(prepared, entry="_f", compact_budget=budget)
    assert actual == link_executable(inputs, entry="_f")
    sections, undefined = inputs[0].to_sections()
    sections.append(Section(
        "__debug_info", "__DWARF", b"\0" * 8, flags=spec.S_ATTR_DEBUG,
        relocations=(Relocation(0, "_f", spec.ARM64_RELOC_UNSIGNED, False, length=3),),
    ))
    bad = NativeObject.from_sections(sections, undefined=undefined)
    path = tmp_path / "bad.pco"
    path.write_bytes(encode_native_object(bad))
    with pytest.raises(LinkError, match="metadata requires a physical"):
        host.prepare_executable_from_paths([str(path)], compact_budget=_budget(10_000_000))


def test_incremental_shape_binds_weak_definition_and_stays_on_cold_merge():
    import hashlib
    from pcc.backend.macho_incremental import (
        _hash_native_shape,
        _hash_packed_native_shape,
        _native_shape_is_patchable,
        _packed_native_shape_is_patchable,
    )
    digests = []
    for weak in (False, True):
        obj = _object(weak=weak)
        packed = decode_packed_native_object(encode_native_object(obj))
        native_digest, packed_digest = hashlib.sha256(), hashlib.sha256()
        _hash_native_shape(native_digest, obj)
        _hash_packed_native_shape(packed_digest, packed)
        assert native_digest.digest() == packed_digest.digest()
        digests.append(native_digest.digest())
        assert _native_shape_is_patchable(obj) == (not weak)
        assert _packed_native_shape_is_patchable(packed) == (not weak)
    assert digests[0] != digests[1]


def test_semantic_layout_preserves_weak_definition_on_symbol_reconstruction():
    from pcc.backend.macho_semantic_layout import (
        SemanticAtom,
        SemanticLayoutManifest,
        apply_semantic_layout,
        native_object_sha256,
    )
    obj = _object()
    manifest = SemanticLayoutManifest.create(
        object_sha256=native_object_sha256(obj), entry="_f", roots=(),
        atoms=(SemanticAtom.create(name="_f", segname="__TEXT", sectname="__text",
                                   offset=0, size=8, align_log2=2, temperature="normal"),),
    )
    result = apply_semantic_layout(obj, manifest).native_object
    assert result.symbols[0].weak_definition
    assert result == obj


def test_guard_model_accounts_for_the_typed_symbol_field():
    from pcc.backend.macho_host_relocations import _MODEL_FIELDS
    assert "weak_definition" in _MODEL_FIELDS[TextSymbol]


@pytest.mark.parametrize("branch", ["b.eq _f", "cbz x0, _f", "cbnz x0, _f", "structured"])
@pytest.mark.parametrize("far", [False, True])
@pytest.mark.parametrize("early_layout", [False, True])
def test_conditional_weak_branch_without_external_relocation_fails_closed(
    branch, far, early_layout, monkeypatch,
):
    from pcc.backend import arm64_encode
    # Exercise the actual far-relaxation rule with a small branch range,
    # rather than allocating hundreds of thousands of test instructions.
    monkeypatch.setattr(arm64_encode, "_CONDITIONAL_BRANCH_LIMIT", 2 if far else 1 << 18)
    builder = AArch64ModuleBuilder(["_f"])
    try:
        builder.append_chunk(
            ".section __TEXT,__text,regular,pure_instructions\n.globl _f\n.weak_definition _f\n_caller:"
        )
        if branch == "structured":
            builder.append_encoded(0x54000000, -19, 0)
        else:
            builder.append_chunk(branch)
        builder.append_chunk("nop\nnop\nnop\n_f:\nret")
        with pytest.raises(EncodeError, match="weak definition branch requires a linker relocation"):
            if early_layout:
                builder.finalize_text_layout()
            else:
                builder.finish()
    finally:
        builder.close()


def test_late_weak_definition_cannot_reinterpret_already_relaxed_layout(monkeypatch):
    from pcc.backend import arm64_encode
    monkeypatch.setattr(arm64_encode, "_CONDITIONAL_BRANCH_LIMIT", 2)
    builder = arm64_encode.PackedAArch64TextBuilder(["_f"])
    try:
        builder.append_encoded(0x54000000, -19, 0)
        builder.append_line("nop")
        builder.append_line("nop")
        builder.append_line("nop")
        builder.append_label("_f")
        builder.append_line("ret")
        builder.finalize_layout()
        assert builder.pc == 24  # five original words plus the relaxed branch.
        with pytest.raises(EncodeError, match="weak definitions changed"):
            builder.finish(weak_definitions={"_f"})
    finally:
        builder.close()
