"""Actual static-ELF host-table relocation and archive-root contracts."""

from dataclasses import replace
import struct
import subprocess

import pytest

from pcc.backend import elf_x86_64 as elf, owned_elf_link
from pcc.backend.elf_host_exports import HOST_EXPORT_SYMBOL


def _function(name, code=b"\xc3", binding=elf.STB_GLOBAL, visibility=elf.STV_DEFAULT):
    return elf.ElfObject((elf.ElfSection(
        ".text", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_EXECINSTR, 16, code,
    ),), (elf.ElfSymbol.null(), elf.ElfSymbol(name, 1, 0, len(code), binding,
                                           elf.STT_FUNC, visibility)))


def _entry():
    # mov eax,60; xor edi,edi; syscall: a real exit(0) entry, no libc.
    return _function("_start", bytes.fromhex("b8 3c 00 00 00 31 ff 0f 05"))


def _archive(members):
    data = b"!<arch>\n"
    for name, obj in members:
        payload = elf.emit_relocatable(obj)
        header = ((name + "/").encode("ascii").ljust(16, b" ")
                  + b"0".ljust(12, b" ") + b"0".ljust(6, b" ")
                  + b"0".ljust(6, b" ") + b"100644".ljust(8, b" ")
                  + str(len(payload)).encode("ascii").ljust(10, b" ") + b"`\n")
        data += header + payload + (b"\n" if len(payload) % 2 else b"")
    return data


def _library():
    api = _function("host_api", b"\xe8\0\0\0\0\xc3")
    api = replace(api, symbols=api.symbols + (
        elf.ElfSymbol("dependency", elf.SHN_UNDEF, 0, 0, elf.STB_GLOBAL, elf.STT_FUNC),
    ), sections=(replace(api.sections[0], relocations=(
        elf.ElfRelocation(1, 2, elf.R_X86_64_PLT32, -4),
    )),))
    data = elf.ElfObject((elf.ElfSection(
        ".data", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_WRITE, 4,
        struct.pack("<I", 42),
    ),), (elf.ElfSymbol.null(), elf.ElfSymbol("host_counter", 1, 0, 4,
                                           elf.STB_GLOBAL, elf.STT_OBJECT)))
    return _archive([("api.o", api), ("counter.o", data),
                     ("dependency.o", _function("dependency", b"\xb8\x2a\0\0\0\xc3")),
                     ("unused.o", _function("unused"))])


def _segments(image):
    header = elf._ELF_HEADER.unpack_from(image)
    return [elf._PROGRAM_HEADER.unpack_from(image, header[5] + index * header[9])
            for index in range(header[10])]


def _file_offset(image, address, size=1):
    for kind, flags, offset, virtual, physical, file_size, memory_size, align in _segments(image):
        if kind == elf.PT_LOAD and virtual <= address and address + size <= virtual + file_size:
            return offset + address - virtual
    raise AssertionError("address is not file-backed by a LOAD segment")


def _table(image):
    assert image.count(b"PCCEXP01") == 1
    position = image.index(b"PCCEXP01")
    count = struct.unpack_from("<Q", image, position + 8)[0]
    result = []
    for index in range(count):
        name_address, value_address = struct.unpack_from("<QQ", image, position + 16 + 16 * index)
        start = _file_offset(image, name_address)
        end = image.index(b"\0", start)
        result.append((image[start:end].decode("ascii"), value_address))
    return result


def test_explicit_exports_are_archive_roots_and_relocate_function_and_data_addresses():
    selected = []
    image = elf.link_static_executable(
        [_entry()], archives=[_library()], archive_selections=selected,
        export_symbols=("host_counter", "host_api"),
    )
    assert {member for _, member in selected} == {"api.o", "counter.o", "dependency.o"}
    assert all(index == 0 for index, _ in selected)
    rows = _table(image)
    assert [name for name, _ in rows] == ["host_counter", "host_api"]
    addresses = dict(rows)
    assert struct.unpack_from("<I", image, _file_offset(image, addresses["host_counter"], 4))[0] == 42
    call = _file_offset(image, addresses["host_api"], 6)
    assert image[call] == 0xE8 and image[call + 5] == 0xC3
    displacement = struct.unpack_from("<i", image, call + 1)[0]
    dependency = addresses["host_api"] + 5 + displacement
    assert image[_file_offset(image, dependency, 6):][:6] == b"\xb8\x2a\0\0\0\xc3"
    assert elf.parse_static_executable(image)["entry"] != 0
    assert all(row[0] in (elf.PT_LOAD, elf.PT_TLS) for row in _segments(image))


def test_ordinary_links_do_not_gain_a_table_or_select_unreferenced_archive_members():
    selected = []
    image = elf.link_static_executable([_entry()], archives=[_library()], archive_selections=selected)
    assert b"PCCEXP01" not in image and selected == []
    assert elf.link_static_executable([_entry()], archives=[_library()], export_symbols=()) == image


def test_a_reserved_anchor_reference_without_export_intent_gets_only_an_empty_table():
    obj = _entry()
    obj = replace(obj, sections=obj.sections + (elf.ElfSection(
        ".data", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_WRITE, 8,
        bytes(8), relocations=(elf.ElfRelocation(0, 2, elf.R_X86_64_64),),
    ),), symbols=obj.symbols + (elf.ElfSymbol(
        HOST_EXPORT_SYMBOL, elf.SHN_UNDEF, 0, 0, elf.STB_GLOBAL, elf.STT_OBJECT,
    ),))
    selected = []
    image = elf.link_static_executable([obj], archives=[_library()], archive_selections=selected)
    assert _table(image) == [] and selected == []


def test_aarch64_reserved_anchor_gets_only_the_empty_data_table():
    obj = elf.ElfObject((
        elf.ElfSection(".text", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_EXECINSTR,
                       4, struct.pack("<III", 0xD2800000, 0xD2800BA8, 0xD4000001)),
        elf.ElfSection(".data", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_WRITE,
                       8, bytes(8), relocations=(elf.ElfRelocation(0, 2, 257),)),
    ), (elf.ElfSymbol.null(), elf.ElfSymbol("_start", 1, 0, 12, elf.STB_GLOBAL, elf.STT_FUNC),
        elf.ElfSymbol(HOST_EXPORT_SYMBOL, elf.SHN_UNDEF, 0, 0, elf.STB_GLOBAL, elf.STT_OBJECT)),
        elf.EM_AARCH64)
    image = elf.link_static_executable([obj])
    assert elf._ELF_HEADER.unpack_from(image)[2] == elf.EM_AARCH64
    assert _table(image) == []
    # Format/closure check only; this cloud does not execute AArch64 code.


@pytest.mark.parametrize("provider", [
    _function("host_api", visibility=elf.STV_HIDDEN),
    _function("host_api", binding=elf.STB_LOCAL),
    elf.ElfObject((), (elf.ElfSymbol.null(), elf.ElfSymbol(
        "host_api", elf.SHN_ABS, 42, 0, elf.STB_GLOBAL, elf.STT_NOTYPE,
    ))),
])
def test_export_inventory_cannot_expose_hidden_local_or_absolute_providers(provider):
    with pytest.raises(elf.ElfError):
        elf.link_static_executable([_entry(), provider], export_symbols=("host_api",))


def test_missing_requested_export_does_not_become_an_unresolved_runtime_lookup():
    with pytest.raises(elf.ElfError, match="undefined static ELF symbols.*absent"):
        elf.link_static_executable([_entry()], export_symbols=("absent",))


def test_hidden_weak_declaration_cannot_be_published_through_a_public_winner():
    hidden = _function("host_api", binding=elf.STB_WEAK, visibility=elf.STV_HIDDEN)
    with pytest.raises(elf.ElfError, match="public section definition"):
        elf.link_static_executable([_entry(), _function("host_api"), hidden],
                                   export_symbols=("host_api",))


def test_reserved_anchor_cannot_be_redefined_by_a_requesting_program():
    with pytest.raises(elf.ElfError, match="duplicate strong"):
        elf.link_static_executable([_entry(), _function(HOST_EXPORT_SYMBOL), _function("host_api")],
                                   export_symbols=("host_api",))


@pytest.mark.parametrize("symbol_type", [elf.STT_TLS, elf.STT_NOTYPE, elf.STT_OBJECT])
def test_export_inventory_cannot_publish_thread_local_storage_addresses(symbol_type):
    provider = elf.ElfObject((elf.ElfSection(
        ".tdata", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_WRITE | elf.SHF_TLS,
        8, struct.pack("<Q", 42),
    ),), (elf.ElfSymbol.null(), elf.ElfSymbol(
        "host_tls", 1, 0, 8, elf.STB_GLOBAL, symbol_type,
    )))
    with pytest.raises(elf.ElfError, match="host TLS exports are not supported"):
        elf.link_static_executable([_entry(), provider], export_symbols=("host_tls",))


@pytest.mark.parametrize("binding", [elf.STB_GLOBAL, elf.STB_WEAK])
@pytest.mark.parametrize("absolute", [False, True])
def test_no_intent_input_cannot_supply_the_reserved_table_anchor(binding, absolute):
    provider = _function(HOST_EXPORT_SYMBOL, binding=binding)
    if absolute:
        provider = replace(provider, sections=(), symbols=(elf.ElfSymbol.null(),
            elf.ElfSymbol(HOST_EXPORT_SYMBOL, elf.SHN_ABS, 42, 0,
                          binding, elf.STT_NOTYPE)))
    with pytest.raises(elf.ElfError, match="reserved host export table anchor"):
        elf.link_static_executable([_entry(), provider])


def test_no_intent_archive_cannot_satisfy_an_undefined_reserved_anchor():
    entry = _entry()
    entry = replace(entry, symbols=entry.symbols + (elf.ElfSymbol(
        HOST_EXPORT_SYMBOL, elf.SHN_UNDEF, 0, 0, elf.STB_GLOBAL, elf.STT_OBJECT,
    ),))
    with pytest.raises(elf.ElfError, match="reserved host export table anchor"):
        elf.link_static_executable([entry], archives=[_archive([
            ("foreign_table.o", _function(HOST_EXPORT_SYMBOL)),
        ])])


@pytest.mark.parametrize("in_archive", [False, True])
def test_weak_defined_anchor_cannot_redirect_references_away_from_generated_table(in_archive):
    provider = _function("host_api")
    provider = replace(provider, sections=provider.sections + (elf.ElfSection(
        ".data", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_WRITE, 8, bytes(16),
        relocations=(elf.ElfRelocation(8, 2, elf.R_X86_64_64),),
    ),), symbols=provider.symbols + (elf.ElfSymbol(
        HOST_EXPORT_SYMBOL, 2, 0, 8, elf.STB_WEAK, elf.STT_OBJECT,
    ),))
    objects = [_entry()] if in_archive else [_entry(), provider]
    archives = [_archive([("foreign_table.o", provider)])] if in_archive else []
    with pytest.raises(elf.ElfError, match="reserved host export table anchor"):
        elf.link_static_executable(objects, archives=archives, export_symbols=("host_api",))


def test_same_spelling_local_anchor_remains_translation_unit_private():
    private = _function(HOST_EXPORT_SYMBOL, binding=elf.STB_LOCAL)
    image = elf.link_static_executable([_entry(), private, _function("host_api")],
                                       export_symbols=("host_api",))
    assert [name for name, _ in _table(image)] == ["host_api"]


@pytest.mark.parametrize("bad", ["", b"", set()])
def test_empty_malformed_inventory_is_not_silently_treated_as_default(bad):
    with pytest.raises(elf.ElfError, match="ordered symbol sequence"):
        elf.link_static_executable([_entry()], export_symbols=bad)


def test_actual_owned_file_owner_publishes_the_same_explicit_inventory(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected host tool")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    object_path = tmp_path / "entry.o"
    archive_path = tmp_path / "runtime.a"
    output = tmp_path / "program"
    object_path.write_bytes(elf.emit_relocatable(_entry()))
    archive_path.write_bytes(_library())
    owned_elf_link.link_inputs(
        target="x86_64-unknown-linux-gnu", output=str(output),
        objects=(str(object_path),), archives=(str(archive_path),),
        export_symbols=("host_api", "host_counter"),
    )
    assert [name for name, _ in _table(output.read_bytes())] == ["host_api", "host_counter"]
    assert output.stat().st_mode & 0o111
    assert not (tmp_path / "program.pcc-link.tmp").exists()
    before = output.read_bytes()
    with pytest.raises(elf.ElfError, match="undefined static ELF symbols"):
        owned_elf_link.link_inputs(
            target="x86_64-unknown-linux-gnu", output=str(output),
            objects=(str(object_path),), archives=(str(archive_path),),
            export_symbols=("absent",),
        )
    assert output.read_bytes() == before
    assert not (tmp_path / "program.pcc-link.tmp").exists()
