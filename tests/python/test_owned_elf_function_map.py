"""Opt-in exact physical function ranges from the actual owned ELF link."""

import hashlib
import json
import os
import struct

import pytest

from pcc.backend import elf_x86_64 as elf
from pcc.backend import owned_elf_link
from tests.python.test_elf_x86_64 import _archive_selection_fixture

TARGET = "x86_64-unknown-linux-gnu"


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _fixture(tmp_path):
    start, archive = _archive_selection_fixture()
    # Two independently placed local function names, equal-range aliases,
    # a zero-size symbol, and two physical definitions of a weak/global name.
    def extra(binding, instruction):
        return elf.ElfObject((elf.ElfSection(
            ".text", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_EXECINSTR,
            16, instruction + b"\xc3\x90\x90",
        ),), (
            elf.ElfSymbol.null(),
            elf.ElfSymbol("local_duplicate", 1, 0, 2, elf.STB_LOCAL, elf.STT_FUNC),
            elf.ElfSymbol("local_alias", 1, 0, 2, elf.STB_LOCAL, elf.STT_FUNC),
            elf.ElfSymbol("zero_size", 1, 2, 0, elf.STB_LOCAL, elf.STT_FUNC),
            elf.ElfSymbol("notype", 1, 2, 2, elf.STB_LOCAL, elf.STT_NOTYPE),
            elf.ElfSymbol("shadowed", 1, 0, 2, binding, elf.STT_FUNC),
        ))
    paths = []
    for index, obj in enumerate((start, extra(elf.STB_WEAK, b"\x90"),
                                 extra(elf.STB_GLOBAL, b"\x91"))):
        path = tmp_path / (str(index) + ".o")
        path.write_bytes(elf.emit_relocatable(obj))
        paths.append(str(path))
    archive_path = tmp_path / "answer.a"
    archive_path.write_bytes(archive)
    return paths, archive_path


def _read_map(path):
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    return lines[0], lines[1:]


def test_actual_map_binds_image_and_physical_ranges(tmp_path):
    objects, archive = _fixture(tmp_path)
    off = tmp_path / "off"
    on = tmp_path / "on"
    ownership_off = tmp_path / "off.owners"
    ownership_on = tmp_path / "on.owners"
    function_map = tmp_path / "on.functions"
    options = dict(target=TARGET, objects=objects, archives=[str(archive)])
    owned_elf_link.link_inputs(**options, output=str(off), map_path=str(ownership_off))
    assert not function_map.exists()
    owned_elf_link.link_inputs(**options, output=str(on), map_path=str(ownership_on),
                              function_map_path=str(function_map))
    image = off.read_bytes()
    assert on.read_bytes() == image
    assert ownership_off.read_bytes() == ownership_on.read_bytes()
    header, rows = _read_map(function_map)
    assert header["schema"] == "pcc.owned-elf-function-map.v1"
    assert header["target"] == TARGET
    assert header["image_sha256"] == _sha(image)
    assert header["image_bytes"] == len(image)
    assert header["function_count"] == len(rows)
    assert rows == sorted(rows, key=lambda row: (row["start"], row["object_index"], row["symbol_index"]))
    assert not any(row["name"] in ("zero_size", "notype", "unused_payload") for row in rows)
    for row in rows:
        assert row["start"] < row["end"]
        assert row["file_offset"] >= 0
        assert row["file_offset"] + row["end"] - row["start"] <= len(image)
        # ET_EXEC RX PT_LOAD maps file offsets 1:1 from its base.
        base = struct.unpack_from("<Q", image, 64 + 16)[0]
        assert row["start"] == base + row["file_offset"]
    names = {}
    for row in rows:
        names.setdefault(row["name"], []).append(row)
    first = names["_start"][0]
    answer = names["answer"][0]
    assert first["origin_sha256"] == _sha(open(objects[0], "rb").read())
    assert answer["origin_sha256"] == _sha(dict(elf.read_archive_payloads(archive.read_bytes()))["answer.o"])
    assert answer["origin_label"] == str(archive) + "(answer.o)"
    rel32 = struct.unpack_from("<i", image, first["file_offset"] + 1)[0]
    assert first["start"] + 5 + rel32 == answer["start"]
    assert len(names["local_duplicate"]) == len(names["local_alias"]) == len(names["shadowed"]) == 2
    assert [r["start"] for r in names["local_duplicate"]] == [r["start"] for r in names["local_alias"]]
    weak, strong = names["shadowed"]
    assert weak["binding"] == elf.STB_WEAK and strong["binding"] == elf.STB_GLOBAL
    assert weak["start"] != strong["start"]
    assert image[weak["file_offset"]:weak["file_offset"] + 2] == b"\x90\xc3"
    assert image[strong["file_offset"]:strong["file_offset"] + 2] == b"\x91\xc3"
    another = tmp_path / "another.functions"
    owned_elf_link.link_inputs(**options, output=str(on), function_map_path=str(another))
    assert another.read_bytes() == function_map.read_bytes()


@pytest.mark.parametrize("collision", ["input", "archive", "output", "output-temp",
                                         "owner-map", "owner-temp", "hardlink", "dangling", "temp-hardlink"])
def test_function_map_rejects_alias_before_publication(tmp_path, collision):
    objects, archive = _fixture(tmp_path)
    output = tmp_path / "program"
    owner = tmp_path / "owners"
    function_map = tmp_path / "functions"
    if collision == "input": function_map = objects[0]
    elif collision == "archive": function_map = str(archive)
    elif collision == "output": function_map = str(output)
    elif collision == "output-temp": function_map = str(output) + ".pcc-link.tmp"
    elif collision == "owner-map": function_map = str(owner)
    elif collision == "owner-temp": function_map = str(owner) + ".pcc-link.tmp"
    elif collision == "hardlink": os.link(objects[0], function_map)
    elif collision == "dangling": os.symlink(tmp_path / "missing", function_map)
    else: os.link(objects[0], str(function_map) + ".pcc-link.tmp")
    before = open(objects[0], "rb").read()
    with pytest.raises(elf.ElfError, match="function map"):
        owned_elf_link.link_inputs(target=TARGET, objects=objects, archives=[str(archive)],
                                  output=str(output), map_path=str(owner),
                                  function_map_path=str(function_map))
    assert not output.exists() and not owner.exists()
    assert open(objects[0], "rb").read() == before


def test_failed_link_does_not_publish_function_map(tmp_path):
    objects, archive = _fixture(tmp_path)
    output = tmp_path / "program"
    function_map = tmp_path / "functions"
    with pytest.raises(elf.ElfError, match="undefined"):
        owned_elf_link.link_inputs(target=TARGET, objects=objects, output=str(output),
                                  function_map_path=str(function_map))
    assert not output.exists() and not function_map.exists()
    assert not os.path.lexists(str(function_map) + ".pcc-link.tmp")


@pytest.mark.parametrize("exports", [(), ("answer",)])
def test_actual_synthesized_helpers_and_host_export_table(tmp_path, exports):
    objects, archive = _fixture(tmp_path)
    # Require the actual linker-owned export anchor even when the inventory
    # is empty; the mapped data pointer proves the empty object was selected.
    anchor = elf.ElfObject((elf.ElfSection(
        ".data", elf.SHT_PROGBITS, elf.SHF_ALLOC | elf.SHF_WRITE, 8,
        b"\0" * 8, relocations=(elf.ElfRelocation(0, 1, elf.R_X86_64_64),),
    ),), (elf.ElfSymbol.null(), elf.ElfSymbol(
        "__pcc_elf_host_exports", elf.SHN_UNDEF, 0, 0, elf.STB_GLOBAL, elf.STT_NOTYPE,
    )))
    anchor_path = tmp_path / "requires-export.o"
    anchor_path.write_bytes(elf.emit_relocatable(anchor))
    objects.append(str(anchor_path))
    output = tmp_path / "program"
    off = tmp_path / "off"
    function_map = tmp_path / "functions"
    options = dict(target=TARGET, objects=objects, archives=[str(archive)], export_symbols=exports)
    owned_elf_link.link_inputs(**options, output=str(off))
    owned_elf_link.link_inputs(**options, output=str(output), function_map_path=str(function_map))
    image = output.read_bytes()
    assert image == off.read_bytes()
    header, rows = _read_map(function_map)
    assert header["image_sha256"] == _sha(image)
    names = {row["name"]: row for row in rows}
    expected = {
        "pcc_linux_set_thread_pointer": (len(objects), "linker-synthesized:thread-pointer"),
        "pcc_linux_rt_sigreturn": (len(objects) + 1, "linker-synthesized:thread-intrinsics"),
        "pcc_linux_thread_spawn": (len(objects) + 1, "linker-synthesized:thread-intrinsics"),
        "pcc_linux_thread_exit_free": (len(objects) + 1, "linker-synthesized:thread-intrinsics"),
    }
    for name, (object_index, label) in expected.items():
        assert names[name]["object_index"] == object_index
        assert names[name]["origin_label"] == label
        assert names[name]["origin_sha256"] == ""
        assert names[name]["origin_offset"] == 0
    assert not any("host_export" in row["name"] or row["name"] in (
        "__init_array_start", "__init_array_end", "__fini_array_start", "__fini_array_end",
    ) for row in rows)
    answer = names["answer"]
    archive_bytes = archive.read_bytes()
    member = dict(elf.read_archive_payloads(archive_bytes))["answer.o"]
    assert archive_bytes[answer["origin_offset"]:answer["origin_offset"] + len(member)] == member
    assert answer["origin_sha256"] == _sha(member)
    # Inspect actual final PT_LOAD bytes, not an inferred next-symbol range.
    table_offset = image.index(b"PCCEXP01")
    assert image.count(b"PCCEXP01") == 1
    assert struct.unpack_from("<Q", image, table_offset + 8)[0] == len(exports)
    if exports:
        name_address, func_address = struct.unpack_from("<QQ", image, table_offset + 16)
        assert func_address == answer["start"]
        assert name_address > 0


@pytest.mark.parametrize("stage", ["map-write", "image-write", "image-replace", "owners-replace", "map-replace"])
def test_failed_publication_never_leaves_function_map(tmp_path, monkeypatch, stage):
    objects, archive = _fixture(tmp_path)
    output = tmp_path / "program"
    owner = tmp_path / "owners"
    function_map = tmp_path / "functions"
    original_image = b"previous executable sentinel"
    output.write_bytes(original_image)
    real_replace = os.replace
    if stage == "map-write":
        def fail_map(stream, *args):
            stream.write("partial map\n")
            raise OSError("injected publication failure")
        monkeypatch.setattr(owned_elf_link, "_write_function_map", fail_map)
    elif stage == "image-write":
        def fail_image(stream, image):
            stream.write(image[:32])
            raise OSError("injected publication failure")
        monkeypatch.setattr(owned_elf_link, "_write_image_chunks", fail_image)
    else:
        failure_destination = {"image-replace": output, "owners-replace": owner,
                               "map-replace": function_map}[stage]
        def fail_replace(src, dest):
            if os.fspath(dest) == str(failure_destination):
                raise OSError("injected publication failure")
            return real_replace(src, dest)
        monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected publication failure"):
        owned_elf_link.link_inputs(target=TARGET, objects=objects, archives=[str(archive)],
                                  output=str(output), map_path=str(owner),
                                  function_map_path=str(function_map))
    # The sidecar is never valid/published on any tested failed path. A later
    # sidecar failure can leave the actual completed executable in place;
    # it must not roll back the image or advertise successful mapping.
    assert not function_map.exists()
    for path in (output, owner, function_map):
        assert not os.path.lexists(str(path) + ".pcc-link.tmp")
    if stage in ("map-write", "image-write", "image-replace"):
        assert output.read_bytes() == original_image
        assert not owner.exists()
    else:
        elf.parse_static_executable(output.read_bytes())
        assert owner.exists() == (stage == "map-replace")
