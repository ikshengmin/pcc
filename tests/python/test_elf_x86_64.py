"""Focused contracts for pcc's finite static x86_64 ELF toolchain."""

from __future__ import annotations

import builtins
import hashlib
import stat
import struct

import pytest

from pcc.backend import owned_elf_link
from pcc.backend.elf_x86_64 import (
    ET_EXEC,
    R_X86_64_64,
    R_X86_64_GOTTPOFF,
    R_X86_64_PLT32,
    SHF_ALLOC,
    SHF_EXECINSTR,
    SHF_TLS,
    SHF_WRITE,
    SHT_NOBITS,
    SHT_PROGBITS,
    STB_GLOBAL,
    STT_FUNC,
    STT_OBJECT,
    STT_TLS,
    ElfError,
    ElfObject,
    ElfRelocation,
    ElfSection,
    ElfSymbol,
    emit_relocatable,
    link_static_executable,
    parse_relocatable,
    parse_static_executable,
)
from pcc.backend.x86_64_asm_driver import assemble_file
from pcc.backend.x86_64_encode import X86EncodeError, encode_instruction


def _exit_42_object() -> ElfObject:
    # _start: call answer; mov edi,eax; mov eax,60; syscall
    # answer: mov eax,42; ret
    text = bytes.fromhex(
        "e8 00 00 00 00 89 c7 b8 3c 00 00 00 0f 05 "
        "b8 2a 00 00 00 c3"
    )
    return ElfObject(
        sections=(ElfSection(
            ".text",
            SHT_PROGBITS,
            SHF_ALLOC | SHF_EXECINSTR,
            16,
            text,
            relocations=(ElfRelocation(1, 2, R_X86_64_PLT32, -4),),
        ),),
        symbols=(
            ElfSymbol.null(),
            ElfSymbol("_start", 1, 0, 14, STB_GLOBAL, STT_FUNC),
            ElfSymbol("answer", 1, 14, 6, STB_GLOBAL, STT_FUNC),
        ),
    )


def _ar_member(name: str, payload: bytes) -> bytes:
    encoded_name = (name + "/").encode("ascii")
    assert len(encoded_name) <= 16
    header = (
        encoded_name.ljust(16, b" ")
        + b"0".ljust(12, b" ")
        + b"0".ljust(6, b" ")
        + b"0".ljust(6, b" ")
        + b"100644".ljust(8, b" ")
        + str(len(payload)).encode("ascii").ljust(10, b" ")
        + b"`\n"
    )
    assert len(header) == 60
    return header + payload + (b"\n" if len(payload) & 1 else b"")


@pytest.mark.parametrize("compact", [False, True])
def test_owned_relocatable_writer_roundtrips_symbols_and_relocations(compact):
    source = _exit_42_object()
    sections = source.sections
    relocations = sections[0].relocations
    relocation = relocations[0]
    encoded = emit_relocatable(source)
    assert encoded.startswith(b"\x7fELF")
    assert parse_relocatable(encoded) == source
    parsed = parse_relocatable(encoded, compact_relocations=compact)
    assert tuple(parsed.sections[0].relocations) == relocations
    assert parsed.sections[0].relocations[-1] == relocation
    assert emit_relocatable(parsed) == encoded
    inputs = [source]
    expected = link_static_executable(inputs)
    assert type(expected) is bytes
    assert link_static_executable([parsed]) == expected
    assert inputs == [source] and inputs[0] is source
    assert source.sections is sections
    assert sections[0].relocations is relocations
    assert relocations[0] is relocation
    if not compact:
        assert isinstance(parsed.sections[0].relocations, tuple)
        assert parsed.sections[0].relocations[0] is parsed.sections[0].relocations[0]
    else:
        assert not isinstance(parsed.sections[0].relocations, tuple)


@pytest.mark.parametrize("field,value,diagnostic", [
    (0, 1000, "relocation is outside its payload"),
    (1, (3 << 32) | R_X86_64_PLT32, "relocation has invalid symbol index"),
    (1, (2 << 32) | 0xFFFFFFFF, "relocation type 4294967295 is not supported"),
])
def test_compact_relocation_parser_preserves_strict_errors(field, value, diagnostic):
    encoded = bytearray(emit_relocatable(_exit_42_object()))
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", encoded)
    headers = [struct.unpack_from("<IIQQQQIIQQ", encoded, header[6] + index * 64)
               for index in range(header[12])]
    rela = next(row for row in headers if row[1] == 4)
    record = list(struct.unpack_from("<QQq", encoded, rela[4]))
    record[field] = value
    struct.pack_into("<QQq", encoded, rela[4], *record)
    with pytest.raises(ElfError) as ordinary:
        parse_relocatable(bytes(encoded))
    with pytest.raises(ElfError) as compact:
        parse_relocatable(bytes(encoded), compact_relocations=True)
    assert diagnostic in str(ordinary.value)
    assert str(compact.value) == str(ordinary.value)


def test_static_link_applies_plt32_and_has_no_dynamic_or_section_surface():
    image = link_static_executable([_exit_42_object()])
    shape = parse_static_executable(image)
    assert shape == {
        "entry": 0x401000,
        "program_headers": 1,
        "load_segments": 1,
        "tls_segments": 0,
    }
    assert int.from_bytes(image[0x1001:0x1005], "little", signed=True) == 9
    assert int.from_bytes(image[16:18], "little") == ET_EXEC
    # No section table means no residual SHT_RELA/SHT_DYNSYM can be consumed
    # accidentally by a runtime loader.
    assert image[40:48] == b"\0" * 8


def _archive_selection_fixture():
    start = ElfObject(
        sections=(ElfSection(
            ".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 16,
            bytes.fromhex("e8 00 00 00 00 89 c7 b8 3c 00 00 00 0f 05"),
            relocations=(ElfRelocation(1, 2, R_X86_64_PLT32, -4),),
        ),),
        symbols=(
            ElfSymbol.null(),
            ElfSymbol("_start", 1, 0, 14, STB_GLOBAL, STT_FUNC),
            ElfSymbol("answer", 0, 0, 0, STB_GLOBAL, STT_FUNC),
        ),
    )
    answer = ElfObject(
        sections=(
            ElfSection(
                ".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 16,
                bytes.fromhex("b8 2a 00 00 00 c3"),
            ),
            ElfSection(
                ".data", SHT_PROGBITS, SHF_ALLOC | SHF_WRITE, 8, b"\0" * 8,
                relocations=(ElfRelocation(0, 1, R_X86_64_64),),
            ),
        ),
        symbols=(
            ElfSymbol.null(),
            ElfSymbol("answer", 1, 0, 6, STB_GLOBAL, STT_FUNC),
        ),
    )
    unused = ElfObject(
        sections=(ElfSection(
            ".rodata", SHT_PROGBITS, SHF_ALLOC, 1, b"unused" * 100,
        ),),
        symbols=(
            ElfSymbol.null(),
            ElfSymbol("unused_payload", 1, 0, 600, STB_GLOBAL, STT_OBJECT),
        ),
    )
    archive = (
        b"!<arch>\n"
        + _ar_member("answer.o", emit_relocatable(answer))
        + _ar_member("unused.o", emit_relocatable(unused))
    )
    return start, archive


@pytest.mark.parametrize("compact", [False, True])
def test_archive_member_is_selected_by_unresolved_closure_only(compact):
    start, archive = _archive_selection_fixture()
    image = link_static_executable([start], archives=[archive],
                                   compact_archive_relocations=compact)
    parse_static_executable(image)
    assert b"unusedunusedunused" not in image
    assert image == link_static_executable([start], archives=[archive])


@pytest.fixture
def owned_elf_publication(tmp_path):
    from pcc.backend.linux_thread_intrinsics import assembly

    start, archive = _archive_selection_fixture()
    object_path = tmp_path / "start.o"
    object_path.write_bytes(emit_relocatable(start))
    archive_path = tmp_path / "answer.a"
    archive_path.write_bytes(archive)
    target = "x86_64-unknown-linux-gnu"
    expected = link_static_executable([
        start, owned_elf_link._thread_pointer_object(target),
        owned_elf_link.assemble(assembly(False), target),
    ], archives=[archive])
    output = tmp_path / "program"
    options = dict(target=target, output=str(output), objects=[str(object_path)],
                   archives=[str(archive_path)])
    return options, expected, output, archive_path


@pytest.mark.parametrize("with_map", [False, True])
def test_owned_elf_chunked_short_writes_publish_exact_image_and_map(
    owned_elf_publication, monkeypatch, with_map,
):
    options, expected, output, archive_path = owned_elf_publication
    map_path = output.with_suffix(".map")
    if with_map:
        options["map_path"] = str(map_path)
    monkeypatch.setattr(owned_elf_link, "_IMAGE_CHUNK_BYTES", 31)
    write_sizes = []

    class ShortWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, data):
            assert type(data) is bytes
            write_sizes.append(len(data))
            return self.stream.write(data[:7])

    def open_file(path, mode="r", **kwargs):
        stream = builtins.open(path, mode, **kwargs)
        return ShortWriter(stream) if mode == "wb" else stream

    monkeypatch.setattr(owned_elf_link, "open", open_file, raising=False)
    owned_elf_link.link_inputs(**options)
    assert output.read_bytes() == expected
    assert stat.S_IMODE(output.stat().st_mode) == 0o755
    assert len(write_sizes) > 1 and max(write_sizes) <= 31
    assert not output.with_name(output.name + ".pcc-link.tmp").exists()
    if with_map:
        lines = map_path.read_text().splitlines()
        assert "# image sha256=" + hashlib.sha256(expected).hexdigest() in lines
        assert any(line.startswith(str(archive_path) + "(answer.o)") for line in lines)
        assert not any("(unused.o)" in line for line in lines)
        assert not map_path.with_name(map_path.name + ".pcc-link.tmp").exists()


@pytest.mark.parametrize("failure", ["write", "zero-write", "replace", "validation"])
def test_owned_elf_chunked_failure_preserves_output_and_cleans_temporary(
    owned_elf_publication, monkeypatch, failure,
):
    options, _expected, output, _archive = owned_elf_publication
    output.write_bytes(b"previous executable")
    map_path = output.with_suffix(".map")
    map_path.write_text("previous map")
    options["map_path"] = str(map_path)
    opened = []

    class FailedWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, data):
            if failure == "zero-write":
                return 0
            self.stream.write(data[:3])
            raise OSError("synthetic write failure")

    def open_file(path, mode="r", **kwargs):
        stream = builtins.open(path, mode, **kwargs)
        if mode == "wb":
            opened.append(path)
            if failure in ("write", "zero-write"):
                return FailedWriter(stream)
        return stream

    def reject_replace(*_args):
        raise OSError("synthetic replace failure")

    def reject_validation(_image):
        raise ElfError("synthetic executable validation failure")

    monkeypatch.setattr(owned_elf_link, "open", open_file, raising=False)
    if failure == "replace":
        monkeypatch.setattr(owned_elf_link.os, "replace", reject_replace)
    if failure == "validation":
        monkeypatch.setattr(owned_elf_link, "parse_static_executable", reject_validation)
    with pytest.raises(ElfError if failure == "validation" else OSError):
        owned_elf_link.link_inputs(**options)
    assert output.read_bytes() == b"previous executable"
    assert map_path.read_text() == "previous map"
    assert not output.with_name(output.name + ".pcc-link.tmp").exists()
    assert not map_path.with_name(map_path.name + ".pcc-link.tmp").exists()
    assert bool(opened) is (failure != "validation")


def test_initial_exec_tls_relocation_builds_got_and_pt_tls():
    obj = ElfObject(
        sections=(
            ElfSection(
                ".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 16,
                b"\0" * 4,
                relocations=(ElfRelocation(0, 2, R_X86_64_GOTTPOFF, -4),),
            ),
            ElfSection(
                ".tdata", SHT_PROGBITS, SHF_ALLOC | SHF_WRITE | SHF_TLS,
                4, (37).to_bytes(4, "little"),
            ),
            ElfSection(
                ".tbss", SHT_NOBITS, SHF_ALLOC | SHF_WRITE | SHF_TLS,
                8, mem_size=8,
            ),
        ),
        symbols=(
            ElfSymbol.null(),
            ElfSymbol("_start", 1, 0, 4, STB_GLOBAL, STT_FUNC),
            ElfSymbol("tls_init", 2, 0, 4, STB_GLOBAL, STT_TLS),
            ElfSymbol("tls_zero", 3, 0, 8, STB_GLOBAL, STT_TLS),
        ),
    )
    shape = parse_static_executable(link_static_executable([obj]))
    assert shape["load_segments"] == 2
    assert shape["tls_segments"] == 1


def test_static_link_fails_closed_on_undefined_and_duplicate_symbols():
    source = _exit_42_object()
    duplicate = ElfObject(
        sections=(ElfSection(
            ".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 1, b"\xc3",
        ),),
        symbols=(
            ElfSymbol.null(),
            ElfSymbol("answer", 1, 0, 1, STB_GLOBAL, STT_FUNC),
        ),
    )
    try:
        link_static_executable([source, duplicate])
    except ElfError as exc:
        assert "duplicate strong definition" in str(exc)
    else:
        raise AssertionError("duplicate strong definition was accepted")

    undefined = ElfObject(
        sections=(ElfSection(
            ".text", SHT_PROGBITS, SHF_ALLOC | SHF_EXECINSTR, 1, b"\0" * 4,
            relocations=(ElfRelocation(0, 2, R_X86_64_PLT32, -4),),
        ),),
        symbols=(
            ElfSymbol.null(),
            ElfSymbol("_start", 1, 0, 4, STB_GLOBAL, STT_FUNC),
            ElfSymbol("missing", 0, 0, 0, STB_GLOBAL, STT_FUNC),
        ),
    )
    try:
        link_static_executable([undefined])
    except ElfError as exc:
        assert "undefined static ELF symbols: missing" in str(exc)
    else:
        raise AssertionError("undefined static symbol was accepted")


def test_owned_x86_encoder_covers_core_integer_memory_branch_and_atomic_forms():
    labels = {".Ldone": (".text", 256)}
    lines = (
        "push rbp",
        "mov rbp, rsp",
        "sub rsp, 32",
        "mov r10, 42",
        "mov QWORD PTR [rbp - 8], r10",
        "mov r11, QWORD PTR [rbp - 8]",
        "lea r10, [r11 + r10*8]",
        "imul r10, r11, 9",
        "cmp r10, r11",
        "setne al",
        "movzx r10, al",
        "lock xadd QWORD PTR [r11], r10",
        "lock cmpxchg QWORD PTR [r11], r10",
        "jne .Ldone",
        "add rsp, 32",
        "pop rbp",
        "ret",
    )
    offset = 0
    payload = bytearray()
    for line in lines:
        encoded = encode_instruction(
            line, pc=offset, labels=labels, section_name=".text"
        )
        assert encoded.code
        assert not encoded.relocations
        payload.extend(encoded.code)
        offset += len(encoded.code)
    assert payload.startswith(bytes.fromhex("55 48 89 e5"))
    assert payload.endswith(bytes.fromhex("48 83 c4 20 5d c3"))


def test_owned_asm_driver_emits_external_call_data_and_tls_relocations():
    source = """
.intel_syntax noprefix
.section .tdata,"awT",@progbits
.p2align 3
.globl tls_value
.type tls_value, @object
.size tls_value, 8
tls_value:
  .quad 7
.data
.p2align 3
.globl callback_slot
.type callback_slot, @object
.size callback_slot, 8
callback_slot:
  .quad external_callback+4
.text
.p2align 4, 0x90
.globl _start
.type _start, @function
_start:
  mov r10, QWORD PTR tls_value@gottpoff[rip]
  add r10, QWORD PTR fs:0
  call external_callback
  ret
.size _start, .-_start
.section .note.GNU-stack,"",@progbits
"""
    obj = assemble_file(source)
    encoded = emit_relocatable(obj)
    parsed = parse_relocatable(encoded)
    relocation_types = {
        relocation.type
        for section in parsed.sections
        for relocation in section.relocations
    }
    assert R_X86_64_GOTTPOFF in relocation_types
    assert R_X86_64_PLT32 in relocation_types
    assert R_X86_64_64 in relocation_types
    tls_symbol = next(symbol for symbol in parsed.symbols if symbol.name == "tls_value")
    assert tls_symbol.type == STT_TLS


def test_owned_asm_driver_resolves_temporary_labels_without_publishing_them():
    source = """
.intel_syntax noprefix
.text
.globl probe
.type probe, @function
probe:
  xor eax, eax
.Lagain:
  add eax, 1
  cmp eax, 2
  jne .Lagain
  ret
.size probe, .-probe
.section .note.GNU-stack,"",@progbits
"""
    obj = assemble_file(source)
    text = next(section for section in obj.sections if section.name == ".text")
    assert not text.relocations
    assert "probe" in {symbol.name for symbol in obj.symbols}
    assert ".Lagain" not in {symbol.name for symbol in obj.symbols}


def test_owned_x86_assembler_fails_closed_on_dialect_growth():
    rejected = (
        (
            ".intel_syntax noprefix\n.text\n.globl _start\n_start:\n"
            "  vzeroupper\n"
        ),
        ".text\n.globl _start\n_start:\n  ret\n",
        (
            ".intel_syntax noprefix\n.text\n"
            ".intel_syntax noprefix\n_start:\n  ret\n"
        ),
    )
    for source in rejected:
        try:
            assemble_file(source)
        except X86EncodeError as exc:
            assert "not proven" in str(exc) or ".intel_syntax" in str(exc)
        else:
            raise AssertionError(
                "unknown x86 assembly shape was silently accepted"
            )
