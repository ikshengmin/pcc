"""Focused contracts for pcc's indexed object/link fast path."""

from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys

import pytest

from pcc.backend import macho_spec as spec
from pcc.backend import native_object as native_object_module
from pcc.backend.macho_exec import link_executable
from pcc.backend.macho_link import (
    LinkError,
    link_relocatable,
    link_relocatable_native,
)
from pcc.backend.macho_obj import (
    DATA_SECTION_FLAGS,
    TEXT_SECTION_FLAGS,
    ZEROFILL_SECTION_FLAGS,
    DataInCodeRegion,
    Relocation,
    Section,
    TextSymbol,
    emit_object,
)
from pcc.backend.native_object import (
    MAGIC,
    NativeObject,
    NativeObjectError,
    NativeRelocation,
    NativeSection,
    NativeSymbol,
    decode_native_object,
    decode_packed_native_object,
    encode_native_object,
    encode_native_object_from_sections,
)
from pcc.backend.self_backend_value_arena import CompilerIntArena
from pcc.backend.macho_assemble_worker import (
    assemble_asm_path_to_encoded,
    assemble_asm_text_to_encoded,
)


_RET = b"\xc0\x03\x5f\xd6"
_BL_PLACEHOLDER = b"\x00\x00\x00\x94"


@pytest.mark.parametrize("names", [[], ["_main"], ["abc", "defg"], ["_a", "_z", "_a"], ["é", "long_name"]])
def test_string_table_preserves_encoded_offsets_and_padding(names):
    from pcc.backend.macho_obj import _build_string_table

    expected = bytearray(b"\0")
    offsets = {}
    for name in names:
        offsets[name] = len(expected)
        expected += name.encode() + b"\0"
    while len(expected) % 8:
        expected += b"\0"
    assert _build_string_table(names) == (offsets, bytes(expected))


def test_large_string_table_executes_natively(
    tmp_path, monkeypatch, pcc_py_runtime_archive, python_program_compiler,
):
    import inspect
    import os
    from pcc.backend.macho_obj import _build_string_table

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "strings.py"
    source.write_text(inspect.getsource(_build_string_table) + '''
def main():
    names = ["_symbol_" + str(index) for index in range(40000)]
    offsets, data = _build_string_table(names)
    assert len(data) % 8 == 0
    cursor = 1
    for name in names:
        encoded = name.encode() + b"\\0"
        assert offsets[name] == cursor
        assert data[cursor:cursor + len(encoded)] == encoded
        cursor += len(encoded)
    assert data[cursor:] == b"\\0" * (len(data) - cursor)
    print(len(offsets), len(data))
main()
''')
    binary = tmp_path / "strings"
    python_program_compiler(str(source), str(binary), backend="self", libpython_mode="off",
                            runtime_archive=str(pcc_py_runtime_archive))
    expected = str(len(_build_string_table(["_symbol_" + str(i) for i in range(40000)])[1]))
    for backend in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                                env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert result.returncode == 0, (backend, result.stdout, result.stderr)
        assert result.stdout.strip() == "40000 " + expected


@pytest.mark.parametrize("kind", ["native", "packed", "macho"])
def test_merge_rebases_each_relocation_without_an_intermediate_copy(tmp_path, monkeypatch, kind):
    from pcc.backend import macho_link
    from pcc.backend.macho_exec import link_prepared_executable

    helper = NativeObject.from_sections([Section(
        sectname="__text", segname="__TEXT", flags=TEXT_SECTION_FLAGS,
        align_log2=2, data=b"\x40\x05\x80\x52" + _RET,
        symbols=(TextSymbol("_helper", 0),),
    )])
    caller = NativeObject.from_sections([Section(
        sectname="__text", segname="__TEXT", flags=TEXT_SECTION_FLAGS,
        align_log2=2, data=b"\x00\x00\x00\x14",
        symbols=(TextSymbol("_main", 0),),
        relocations=(Relocation(0, "_helper", spec.ARM64_RELOC_BRANCH26, True),),
    )], undefined=("_helper",))
    objects = [helper, caller]
    if kind == "packed":
        objects = [decode_packed_native_object(encode_native_object(obj)) for obj in objects]
    elif kind == "macho":
        objects = [obj.to_macho() for obj in objects]
    constructed = []

    def relocation(*args, **kwargs):
        result = Relocation(*args, **kwargs)
        constructed.append(result)
        return result

    monkeypatch.setattr(macho_link, "Relocation", relocation)
    merged = link_relocatable_native(objects)
    assert len(constructed) == 1
    assert constructed[0].offset == 8
    binary = tmp_path / "rebased-call"
    binary.write_bytes(link_prepared_executable(merged))
    binary.chmod(0o755)
    ran = subprocess.run([str(binary)], capture_output=True, timeout=10)
    assert ran.returncode == 42, ran.stderr


@pytest.mark.parametrize("kind", ["native", "packed", "macho"])
def test_section_rebases_do_not_rescan_symbol_relocations(tmp_path, monkeypatch, kind):
    """A large symbol prefix must not be visited for every section target."""
    from dataclasses import replace
    from pcc.backend.macho_exec import link_prepared_executable

    symbol_count, section_count = 1024, 128
    prefix = NativeObject.from_sections([Section(
        sectname="__data", segname="__DATA", flags=DATA_SECTION_FLAGS,
        align_log2=3, data=b"\0" * 16,
        symbols=(TextSymbol("_prefix", 0),),
    )])
    relocations = [Relocation(
        offset=8 * index, symbol="", type=spec.ARM64_RELOC_UNSIGNED,
        pcrel=False, length=3, section=("__DATA", "__data"),
        target_offset=4 if index % 2 else 0,
    ) for index in range(section_count)]
    relocations.extend(Relocation(
        offset=8 * (section_count + index), symbol="_dest",
        type=spec.ARM64_RELOC_UNSIGNED, pcrel=False, length=3,
    ) for index in range(symbol_count))
    caller = NativeObject.from_sections([
        Section(sectname="__text", segname="__TEXT", flags=TEXT_SECTION_FLAGS,
                align_log2=2, data=b"\x40\x05\x80\x52" + _RET,
                symbols=(TextSymbol("_main", 0),)),
        Section(sectname="__data", segname="__DATA", flags=DATA_SECTION_FLAGS,
                align_log2=3, data=b"\0" * 8, symbols=(TextSymbol("_dest", 0),)),
        Section(sectname="__ptrs", segname="__DATA", flags=DATA_SECTION_FLAGS,
                align_log2=3, data=b"\0" * (8 * len(relocations)),
                relocations=tuple(relocations)),
    ])
    objects = [prefix, caller]
    if kind == "packed":
        objects = [decode_packed_native_object(encode_native_object(obj)) for obj in objects]
    elif kind == "macho":
        objects = [obj.to_macho() for obj in objects]
    offset_reads = 0
    original_getattribute = Relocation.__getattribute__

    def count_offset_reads(self, name):
        nonlocal offset_reads
        if name == "offset":
            offset_reads += 1
        return original_getattribute(self, name)

    with monkeypatch.context() as patch:
        patch.setattr(Relocation, "__getattribute__", count_offset_reads)
        merged = link_relocatable_native(objects)
    assert offset_reads < 40 * (symbol_count + section_count), offset_reads
    pointers = next(section for section in merged.sections if section.sectname == "__ptrs")
    assert [entry.offset for entry in pointers.relocations] == list(
        range(8 * (len(relocations) - 1), -1, -8)
    )
    for entry in pointers.relocations:
        if entry.offset < section_count * 8 and (entry.offset // 8) % 2:
            assert entry.target_offset == 20
            assert merged.sections[entry.target_section_index - 1].sectname == "__data"
        else:
            assert merged.symbols[entry.symbol_index].name == "_dest"
    with pytest.raises(LinkError, match="normalized to a defined symbol"):
        link_prepared_executable(merged)
    # Defining the second destination makes the same relocation workload a
    # supported executable. The anonymous form above remains relocatable-only.
    sections, undefined = caller.to_sections()
    sections = [replace(section, symbols=section.symbols + (TextSymbol("_anon", 4),))
                if section.sectname == "__data" else section for section in sections]
    executable_inputs = [prefix, NativeObject.from_sections(sections, undefined=undefined)]
    if kind == "packed":
        executable_inputs = [decode_packed_native_object(encode_native_object(obj))
                             for obj in executable_inputs]
    elif kind == "macho":
        executable_inputs = [obj.to_macho() for obj in executable_inputs]
    binary = tmp_path / "section-rebased"
    binary.write_bytes(link_executable(executable_inputs))
    binary.chmod(0o755)
    run = subprocess.run([str(binary)], capture_output=True, timeout=10)
    assert run.returncode == 42, run.stderr


def test_worker_assembly_text_and_path_publish_identical_native_object(
    tmp_path: Path,
) -> None:
    assembly = (
        ".section __TEXT,__text,regular,pure_instructions\n"
        ".globl _main\n.p2align 2\n_main:\n  ret\n"
    )
    path = tmp_path / "input.s"
    path.write_text(assembly, encoding="utf-8")

    from_text = assemble_asm_text_to_encoded(assembly)
    from_path = assemble_asm_path_to_encoded(str(path))

    assert from_text == from_path
    packed = decode_packed_native_object(from_text)
    assert [symbol.name for symbol in packed.symbols] == ["_main"]


def test_macho_driver_links_ordered_mixed_asm_and_pco_inputs(
    tmp_path: Path,
) -> None:
    assembly = tmp_path / "main.s"
    assembly.write_text(
        ".section __TEXT,__text,regular,pure_instructions\n"
        ".globl _main\n.p2align 2\n_main:\n  movz x0, #0\n  ret\n",
        encoding="utf-8",
    )
    helper = tmp_path / "helper.pco"
    helper.write_bytes(
        encode_native_object(NativeObject.from_sections(_helper_sections()))
    )
    manifest = tmp_path / "inputs.txt"
    manifest.write_text(
        "pcc.macho-internal-inputs.v1\n"
        "2\n"
        "PCO\t"
        + str(helper)
        + "\nASM\t"
        + str(assembly)
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "program"
    profile = tmp_path / "link-profile.json"
    driver = Path(__file__).resolve().parents[2] / "scripts" / "pcc_link_macho.py"

    linked = subprocess.run(
        [
            sys.executable,
            str(driver),
            "--internal-input-manifest",
            str(manifest),
            "--out",
            str(output),
            "--profile-json",
            str(profile),
        ],
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert linked.returncode == 0, linked.stderr
    link_profile = json.loads(profile.read_text(encoding="utf-8"))
    assert link_profile["inputs"]["asm"] == 1
    assert link_profile["inputs"]["native_object"] == 1
    executed = subprocess.run(
        [str(output)],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert executed.returncode == 0, executed.stderr


def _caller_sections() -> list[Section]:
    return [Section(
        sectname="__text",
        segname="__TEXT",
        data=_BL_PLACEHOLDER + _RET,
        align_log2=2,
        flags=TEXT_SECTION_FLAGS,
        symbols=(TextSymbol("_main", 0),),
        relocations=(Relocation(
            offset=0,
            symbol="_helper",
            type=spec.ARM64_RELOC_BRANCH26,
            pcrel=True,
        ),),
    )]


def _helper_sections() -> list[Section]:
    return [Section(
        sectname="__text",
        segname="__TEXT",
        data=_RET,
        align_log2=2,
        flags=TEXT_SECTION_FLAGS,
        symbols=(TextSymbol("_helper", 0),),
    )]


def test_final_link_streams_relocation_rows_without_materializing_tables(monkeypatch):
    caller = NativeObject.from_sections(_caller_sections(), undefined=["_helper"])
    helper = NativeObject.from_sections(_helper_sections())
    expected = link_executable([caller, helper])

    def forbid_materialized_rows(_section):
        raise AssertionError("final linking must not retain a dictionary per relocation")

    monkeypatch.setattr(native_object_module, "_raw_relocations", forbid_materialized_rows)
    assert link_executable([caller, helper]) == expected


def test_final_link_does_not_materialize_flat_native_payload(monkeypatch):
    caller = NativeObject.from_sections(_caller_sections(), undefined=["_helper"])
    helper = NativeObject.from_sections(_helper_sections())
    expected = link_executable([caller, helper])

    def forbidden(_self):
        raise AssertionError("final linking must read section payloads directly")

    monkeypatch.setattr(native_object_module.NativeObjectView, "data", property(forbidden))
    assert link_executable([caller, helper]) == expected


def test_native_view_payload_identity_and_legacy_flat_data():
    payload = b"\0" * 32
    native = NativeObject.from_sections([Section(
        sectname="__data", segname="__DATA", flags=DATA_SECTION_FLAGS,
        align_log2=3, data=payload, symbols=(TextSymbol("_value", 0),),
    )])
    view = native.link_view()
    section = view.sections()[0]
    assert view._data_cache is None
    assert view.section_data(section) is payload
    assert view.data == payload
    view.data = b"x" * 32
    assert view.section_data(section) == b"x" * 32


def test_explicit_owned_inputs_retire_before_executable_layout(monkeypatch):
    import weakref
    from pcc.backend import macho_exec

    inputs = [NativeObject.from_sections(_caller_sections(), undefined=["_helper"]),
              NativeObject.from_sections(_helper_sections())]
    expected = link_executable(inputs)
    assert len(inputs) == 2
    references = [weakref.ref(value) for value in inputs]
    original = macho_exec._prepare_executable_image
    checked = []

    def check(*args, **kwargs):
        assert inputs == []
        assert all(reference() is None for reference in references)
        checked.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(macho_exec, "_prepare_executable_image", check)
    assert link_executable(inputs, _consume_inputs=True) == expected
    assert checked == [True]


def test_relocation_order_arena_closes_when_iteration_stops(monkeypatch):
    original = native_object_module.CompilerIntArena
    closed = []

    class Arena(original):
        def close(self):
            closed.append(True)
            super().close()

    monkeypatch.setattr(native_object_module, "CompilerIntArena", Arena)
    native = NativeObject.from_sections(_caller_sections(), undefined=["_helper"])
    iterator = native_object_module._iter_raw_relocations(native.sections[0])
    next(iterator)
    iterator.close()
    assert closed == [True]


def test_native_validation_streams_without_full_source_projection(monkeypatch):
    def forbid_projection(_self):
        raise AssertionError("validation must not reconstruct the full source object graph")

    monkeypatch.setattr(NativeObject, "to_sections", forbid_projection)
    native = NativeObject.from_sections(_caller_sections(), undefined=["_helper"])
    encoded = encode_native_object(native)
    assert decode_native_object(encoded) == native


def test_final_image_allocation_does_not_retain_prepared_object(monkeypatch):
    import weakref
    from pcc.backend import macho_exec

    original_prepare = macho_exec.prepare_executable_object
    original_materialize = macho_exec.materialize_output
    prepared_refs = []

    def prepare(*args, **kwargs):
        prepared = original_prepare(*args, **kwargs)
        prepared_refs.append(weakref.ref(prepared))
        return prepared

    def materialize(*args, **kwargs):
        assert prepared_refs and prepared_refs[-1]() is None
        return original_materialize(*args, **kwargs)

    monkeypatch.setattr(macho_exec, "prepare_executable_object", prepare)
    monkeypatch.setattr(macho_exec, "materialize_output", materialize)
    caller = NativeObject.from_sections(_caller_sections(), undefined=["_helper"])
    helper = NativeObject.from_sections(_helper_sections())
    assert link_executable([caller, helper])


def test_signing_releases_output_region_owners(monkeypatch):
    import weakref
    from pcc.backend import macho_exec

    materialize = macho_exec.materialize_output
    sign = macho_exec.build_signature
    references = []

    def capture(size, regions):
        references.extend(weakref.ref(region) for region in regions)
        return materialize(size, regions)

    def check(*args, **kwargs):
        assert references and all(ref() is None for ref in references)
        return sign(*args, **kwargs)

    monkeypatch.setattr(macho_exec, "materialize_output", capture)
    monkeypatch.setattr(macho_exec, "build_signature", check)
    assert link_executable([NativeObject.from_sections(_helper_sections())], entry="_helper")


def test_private_link_plan_retires_graph_before_image_allocation_natively(
    tmp_path, monkeypatch, pcc_py_runtime_archive, python_program_compiler,
):
    import inspect
    import os
    from pcc.backend import macho_exec

    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "link_lifetime.py"
    source.write_text('''import gc
class LinkInput:
    pass
released = 0
class Graph:
    def __del__(self):
        global released
        released += 1
def _validate_minos(value):
    return value
def prepare_executable_object(objects, *, archives, semantic_manifest, _consume_inputs):
    return Graph()
def _prepare_executable_image(merged, *, entry, minos, identifier):
    return (b"prepared", identifier)
def _finish_executable_image(plan, identifier, phase_callback):
    gc.collect()
    assert released == 1
    assert plan[1] == identifier
    return b"image"
''' + inspect.getsource(macho_exec.link_executable) + "\n" +
                      inspect.getsource(macho_exec._prepare_executable_inputs) + '''
def main():
    assert link_executable([], identifier=b"custom") == b"image"
    print(released)
main()
''')
    binary = tmp_path / "link_lifetime"
    python_program_compiler(str(source), str(binary), backend="self", libpython_mode="off",
                            runtime_archive=str(pcc_py_runtime_archive))
    for backend in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                                env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert result.returncode == 0, (backend, result.stdout, result.stderr)
        assert result.stdout.strip() == "1"


def test_streamed_validation_matches_packed_decoder_on_byte_mutations():
    import random

    encoded = encode_native_object(
        NativeObject.from_sections(_caller_sections(), undefined=["_helper"])
    )
    randomizer = random.Random(20260909)
    for _ in range(2000):
        changed = bytearray(encoded)
        changed[randomizer.randrange(len(changed))] ^= randomizer.randrange(1, 256)
        payload = bytes(changed)
        accepted = []
        for decoder in (decode_native_object, decode_packed_native_object):
            try:
                decoder(payload)
            except NativeObjectError:
                accepted.append(False)
            else:
                accepted.append(True)
        assert accepted[0] == accepted[1], payload.hex()


def test_native_codec_stores_each_symbol_once_and_relocations_by_index() -> None:
    native = NativeObject.from_sections(
        _caller_sections(), undefined=["_helper"],
    )
    payload = encode_native_object(native)
    restored = decode_native_object(payload)

    assert payload.startswith(MAGIC)
    assert payload.count(b"_helper") == 1
    assert restored == native
    relocation = restored.sections[0].relocations[0]
    assert relocation.symbol_index == 1
    assert restored.symbols[relocation.symbol_index].name == "_helper"


def test_direct_section_codec_matches_materialized_object_without_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sections = _caller_sections()
    expected = encode_native_object(NativeObject.from_sections(
        sections,
        undefined=["_helper"],
    ))

    def unexpected_materialization(*_args, **_kwargs):
        raise AssertionError("direct section codec materialized native records")

    monkeypatch.setattr(
        native_object_module,
        "NativeSymbol",
        unexpected_materialization,
    )
    monkeypatch.setattr(
        native_object_module,
        "NativeSection",
        unexpected_materialization,
    )
    monkeypatch.setattr(
        native_object_module,
        "NativeRelocation",
        unexpected_materialization,
    )

    actual = encode_native_object_from_sections(
        sections,
        undefined=["_helper"],
    )
    assert actual == expected
    assert decode_packed_native_object(actual).relocation_target_indices == (
        frozenset({1})
    )


def test_packed_relocation_scalar_arena_matches_codec_layout() -> None:
    records = CompilerIntArena()
    fields = (
        24,
        0xFFFFFFFF,
        spec.ARM64_RELOC_PAGE21,
        1,
        2,
        -17,
        3,
        0xFFFFFFFF,
        -1,
    )
    records.append4(*fields[:4])
    records.append4(*fields[4:8])
    records.append(fields[8])

    assert native_object_module._pack_native_relocation_records(records) == (
        native_object_module._RELOCATION.pack(*fields)
    )
    records.close()


def test_direct_section_codec_preserves_canonical_symbol_order() -> None:
    sections = [Section(
        sectname="__text",
        segname="__TEXT",
        data=_RET + _RET,
        align_log2=2,
        flags=TEXT_SECTION_FLAGS,
        symbols=(
            TextSymbol("_later_local", 4, external=False),
            TextSymbol("_entry", 0, external=True),
            TextSymbol("_first_local", 0, external=False),
        ),
    )]

    expected = encode_native_object(NativeObject.from_sections(
        sections,
        undefined=["_z", "_a"],
    ))
    actual = encode_native_object_from_sections(
        sections,
        undefined=["_z", "_a"],
    )

    assert actual == expected
    packed = decode_packed_native_object(actual)
    assert [symbol.name for symbol in packed.symbols] == [
        "_first_local",
        "_later_local",
        "_entry",
        "_a",
        "_z",
    ]


def test_direct_section_codec_revalidates_the_final_packed_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_final_bytes(_payload):
        raise NativeObjectError("packed boundary reached")

    monkeypatch.setattr(
        native_object_module,
        "decode_packed_native_object",
        reject_final_bytes,
    )
    with pytest.raises(NativeObjectError, match="packed boundary reached"):
        encode_native_object_from_sections(_helper_sections())


def test_native_wire_ascii_names_use_the_owned_utf8_subset() -> None:
    assert native_object_module._decode_ascii_name(
        b"_entry",
        "symbol",
    ) == "_entry"
    with pytest.raises(NativeObjectError, match="non-ASCII symbol name"):
        native_object_module._decode_ascii_name(b"\xc3\xa9", "symbol")
    source = Path(native_object_module.__file__).read_text(encoding="utf-8")
    assert '.decode("ascii")' not in source
    special_start = source.index("def _validate_packed_special_section(")
    special_end = source.index("\ndef is_native_object_bytes", special_start)
    assert "tuple(_packed_relocations_in_storage_order" not in source[
        special_start:special_end
    ]


def test_packed_codec_validates_without_native_relocation_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = encode_native_object(NativeObject.from_sections(
        _caller_sections(), undefined=["_helper"],
    ))

    def unexpected_relocation(*_args, **_kwargs):
        raise AssertionError("packed decode materialized NativeRelocation")

    monkeypatch.setattr(
        native_object_module, "NativeRelocation", unexpected_relocation,
    )
    packed = decode_packed_native_object(payload)

    assert isinstance(packed.section_data(0), memoryview)
    assert [symbol.name for symbol in packed.symbols] == ["_main", "_helper"]
    assert packed.relocation_target_indices == frozenset({1})
    fields = list(packed.relocation_fields(0))
    assert len(fields) == 1
    assert fields[0][1] == 1
    assert bytes(packed.section_data(0)) == _BL_PLACEHOLDER + _RET


def test_packed_codec_rejects_bad_framing_and_relocation_flags() -> None:
    payload = encode_native_object(NativeObject.from_sections(
        _caller_sections(), undefined=["_helper"],
    ))
    packed = decode_packed_native_object(payload)
    damaged = bytearray(payload)
    # <Q offset, I symbol, I type, B pcrel, ...>
    damaged[packed.sections[0].relocation_offset + 16] = 2

    with pytest.raises(NativeObjectError, match="pcrel byte"):
        decode_packed_native_object(bytes(damaged))
    with pytest.raises(NativeObjectError, match="trailing bytes"):
        decode_packed_native_object(payload + b"unexpected")
    with pytest.raises(NativeObjectError, match="truncated"):
        decode_packed_native_object(payload[:-1])


def test_packed_codec_rejects_symbols_defined_in_stackmap_section() -> None:
    from pcc.backend.precise_stackmap import (
        ARCH_AARCH64,
        FunctionStackMap,
        PreciseStackMap,
        SAFEPOINT_ENTRY,
        SafepointRecord,
        encode_stack_map,
        function_address_offsets,
        function_id,
        safepoint_id,
    )

    symbol = "_main"
    stack_map = PreciseStackMap(
        arch=ARCH_AARCH64,
        functions=(FunctionStackMap(
            function_id=function_id(symbol),
            function_address=0,
            code_size=4,
            frame_size=0,
            records=(SafepointRecord(
                safepoint_id=safepoint_id(symbol, 0, SAFEPOINT_ENTRY),
                instruction_offset=0,
                kind=SAFEPOINT_ENTRY,
                locations=(),
            ),),
        ),),
    )
    stack_payload = encode_stack_map(stack_map)
    address_offset = function_address_offsets(stack_payload)[0]
    valid = encode_native_object(NativeObject.from_sections([
        Section(
            "__text", "__TEXT", _RET, 2, TEXT_SECTION_FLAGS,
            (TextSymbol(symbol, 0),),
        ),
        Section(
            "__pcc_stackmaps", "__DATA", stack_payload, 3,
            spec.S_REGULAR,
            relocations=(Relocation(
                address_offset,
                symbol,
                spec.ARM64_RELOC_UNSIGNED,
                False,
                length=3,
            ),),
        ),
    ]))
    damaged = bytearray(valid)
    symbol_record = (
        native_object_module._HEADER.size
        + native_object_module._U32.size
        + len(symbol)
    )
    damaged[symbol_record:symbol_record + 4] = (2).to_bytes(4, "little")

    with pytest.raises(NativeObjectError, match="cannot define data symbols"):
        decode_packed_native_object(bytes(damaged))
    with pytest.raises(NativeObjectError, match="cannot define data symbols"):
        decode_native_object(bytes(damaged))


def test_native_final_link_never_parses_an_internal_macho_string_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller = NativeObject.from_sections(
        _caller_sections(), undefined=["_helper"],
    )
    helper = NativeObject.from_sections(_helper_sections())

    def unexpected_parse(_data):
        raise AssertionError("pcc-native input entered the Mach-O parser")

    with monkeypatch.context() as patch:
        patch.setattr(spec, "parse_object", unexpected_parse)
        image = link_executable([
            caller,
            helper,
        ])

    parsed = spec.parse_object(image)
    assert parsed.header["filetype"] == spec.MH_EXECUTE
    assert {symbol["name"] for symbol in parsed.symbols()} >= {
        "_main", "_helper",
    }


def test_native_link_does_not_expand_inputs_to_macho_shaped_views(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller = NativeObject.from_sections(
        _caller_sections(), undefined=["_helper"],
    )
    helper = NativeObject.from_sections(_helper_sections())

    def unexpected_view(_self):
        raise AssertionError("indexed input expanded to NativeObjectView")

    monkeypatch.setattr(NativeObject, "link_view", unexpected_view)
    merged = link_relocatable_native([caller, helper])

    assert [symbol.name for symbol in merged.symbols] == ["_main", "_helper"]
    relocation = merged.sections[0].relocations[0]
    assert merged.symbols[relocation.symbol_index].name == "_helper"


def test_internal_and_external_object_boundaries_produce_the_same_image() -> None:
    caller_sections = _caller_sections()
    helper_sections = _helper_sections()
    internal = link_executable([
        NativeObject.from_sections(caller_sections, undefined=["_helper"]),
        NativeObject.from_sections(helper_sections),
    ])
    external = link_executable([
        emit_object(caller_sections, undefined=["_helper"]),
        emit_object(helper_sections),
    ])

    assert internal == external


def test_packed_and_materialized_native_inputs_produce_the_same_image() -> None:
    caller = NativeObject.from_sections(
        _caller_sections(), undefined=["_helper"],
    )
    helper = NativeObject.from_sections(_helper_sections())
    materialized = link_executable([caller, helper])
    packed = link_executable([
        decode_packed_native_object(encode_native_object(caller)),
        decode_packed_native_object(encode_native_object(helper)),
    ])

    assert packed == materialized


@pytest.mark.parametrize("kind", ["native", "packed", "macho"])
def test_merge_keeps_unmodified_payload_immutable(tmp_path, monkeypatch, kind):
    from pcc.backend import macho_link

    text = b"\x40\x05\x80\x52" + _RET * 2047
    source = NativeObject.from_sections([
        Section(sectname="__text", segname="__TEXT", flags=TEXT_SECTION_FLAGS,
                align_log2=2, data=text, symbols=(TextSymbol("_main", 0),)),
        Section(sectname="__data", segname="__DATA", flags=DATA_SECTION_FLAGS,
                align_log2=3, data=b"\0" * 8, symbols=(TextSymbol("_value", 0),)),
        Section(sectname="__ptrs", segname="__DATA", flags=DATA_SECTION_FLAGS,
                align_log2=3, data=b"\0" * 8,
                relocations=(Relocation(0, "", spec.ARM64_RELOC_UNSIGNED, False,
                                        length=3, section=("__DATA", "__data"),
                                        target_offset=0),)),
    ])
    original = source.to_macho()
    item = source
    if kind == "packed":
        item = decode_packed_native_object(encode_native_object(source))
    elif kind == "macho":
        item = original
    allocations = []

    class TrackedBytearray(bytearray):
        def __init__(self, value=b""):
            super().__init__(value)
            allocations.append(len(self))

    with monkeypatch.context() as patch:
        patch.setattr(macho_link, "bytearray", TrackedBytearray, raising=False)
        merged = link_relocatable_native([item])
    assert not any(size >= len(text) for size in allocations), allocations
    assert any(size == 8 for size in allocations)  # Section-target writes still copy.
    assert next(section.data for section in merged.sections if section.sectname == "__text") == text
    assert source.to_macho() == original
    output = tmp_path / "immutable-payload"
    output.write_bytes(link_executable([merged]))
    output.chmod(0o755)
    run = subprocess.run([str(output)], capture_output=True, timeout=10)
    assert run.returncode == 42, run.stderr


def test_indexed_view_matches_macho_for_sections_symbols_and_relocations() -> None:
    sections = [
        Section(
            sectname="__text",
            segname="__TEXT",
            data=b"\0" * 8 + b"data",
            align_log2=2,
            flags=TEXT_SECTION_FLAGS,
            symbols=(TextSymbol("_main", 0),),
            relocations=(
                Relocation(
                    0,
                    "_global",
                    spec.ARM64_RELOC_PAGE21,
                    True,
                    addend=8,
                ),
                Relocation(
                    4,
                    "_global",
                    spec.ARM64_RELOC_PAGEOFF12,
                    False,
                    addend=8,
                ),
            ),
            data_in_code=(DataInCodeRegion(8, 4),),
        ),
        Section(
            sectname="__data",
            segname="__DATA",
            data=b"\0" * 16,
            align_log2=3,
            flags=DATA_SECTION_FLAGS,
            symbols=(TextSymbol("_global", 0),),
            relocations=(Relocation(
                8,
                "",
                spec.ARM64_RELOC_UNSIGNED,
                False,
                length=3,
                section=("__DATA", "__bss"),
                target_offset=0,
            ),),
        ),
        Section(
            sectname="__bss",
            segname="__DATA",
            align_log2=3,
            flags=ZEROFILL_SECTION_FLAGS,
            symbols=(TextSymbol("_scratch", 0),),
            zerofill_size=8,
        ),
    ]
    native = decode_native_object(encode_native_object(
        NativeObject.from_sections(sections)
    )).link_view()
    macho = spec.parse_object(emit_object(sections))

    native_sections = native.sections()
    macho_sections = macho.sections()
    section_fields = (
        "segname_str",
        "sectname_str",
        "flags",
        "align",
        "addr",
        "size",
        "nreloc",
    )
    assert [
        tuple(section[field] for field in section_fields)
        for section in native_sections
    ] == [
        tuple(section[field] for field in section_fields)
        for section in macho_sections
    ]
    for native_section, macho_section in zip(
        native_sections,
        macho_sections,
        strict=True,
    ):
        if (
            native_section["flags"] & spec.SECTION_TYPE
        ) == spec.S_ZEROFILL:
            continue
        native_payload = native.data[
            native_section["offset"]:
            native_section["offset"] + native_section["size"]
        ]
        macho_payload = macho.data[
            macho_section["offset"]:
            macho_section["offset"] + macho_section["size"]
        ]
        assert native_payload == macho_payload
        assert native.relocations(native_section) == macho.relocations(
            macho_section
        )

    symbol_fields = ("name", "n_type", "n_sect", "n_desc", "n_value")
    assert [
        tuple(symbol[field] for field in symbol_fields)
        for symbol in native.symbols()
    ] == [
        tuple(symbol[field] for field in symbol_fields)
        for symbol in macho.symbols()
    ]
    assert native.data_in_code() == macho.data_in_code()


def test_relocatable_public_boundary_still_materialises_standard_macho() -> None:
    native = NativeObject.from_sections(_helper_sections())
    merged_native = link_relocatable_native([native])
    external = link_relocatable([native])

    assert isinstance(merged_native, NativeObject)
    assert not external.startswith(MAGIC)
    parsed = spec.parse_object(external)
    assert parsed.header["filetype"] == spec.MH_OBJECT
    assert [symbol["name"] for symbol in parsed.symbols()] == ["_helper"]


def test_encoded_native_transport_requires_explicit_decode_before_link() -> None:
    payload = encode_native_object(
        NativeObject.from_sections(_helper_sections())
    )

    with pytest.raises(LinkError, match="decode it explicitly"):
        link_relocatable([payload])

    external = link_relocatable([decode_native_object(payload)])
    assert not external.startswith(MAGIC)
    assert spec.parse_object(external).header["filetype"] == spec.MH_OBJECT


def test_native_object_rejects_out_of_range_indices_and_bad_framing() -> None:
    with pytest.raises(NativeObjectError, match="symbol index.*out of range"):
        NativeObject(
            sections=(NativeSection(
                segname="__TEXT",
                sectname="__text",
                flags=(
                    spec.S_REGULAR
                    | spec.S_ATTR_PURE_INSTRUCTIONS
                    | spec.S_ATTR_SOME_INSTRUCTIONS
                ),
                align_log2=2,
                data=_BL_PLACEHOLDER + _RET,
                relocations=(NativeRelocation(
                    offset=0,
                    symbol_index=99,
                    type=spec.ARM64_RELOC_BRANCH26,
                    pcrel=True,
                ),),
            ),),
            symbols=(NativeSymbol("_main", 1, 0, True),),
        )

    valid = encode_native_object(
        NativeObject.from_sections(_helper_sections())
    )
    with pytest.raises(NativeObjectError, match="trailing bytes"):
        decode_native_object(valid + b"unexpected")
    with pytest.raises(NativeObjectError, match="truncated"):
        decode_native_object(valid[:-1])
