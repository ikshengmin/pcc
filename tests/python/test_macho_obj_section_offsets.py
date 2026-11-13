"""A Mach-O object's section file offsets mirror their addresses.

macho_obj gives sections addresses from 0 in one unnamed segment, each
aligned to its own alignment. Their file payloads now sit at the section
data start plus that address, as LLVM's Mach object writer lays them out.
When the file cursor was aligned on its own, an over-aligned section (the
128-byte global tables of the runtime) got a different gap in the file than
in the address space, the segment's filesize exceeded its vmsize, and the
system nm rejected the object ("filesize field in LC_SEGMENT_64 greater than
vmsize field"), failing the runtime archive build.
"""
from __future__ import annotations

import pytest

from pcc.backend import macho_obj, macho_spec as spec
from pcc.backend.macho_obj import Section, TextSymbol


def _object(text_size, data_align, data_size=24):
    sections = [
        Section(
            sectname="__text", segname="__TEXT", data=b"\xc0\x03\x5f\xd6" * (text_size // 4) + b"\0" * (text_size % 4),
            flags=macho_obj.TEXT_SECTION_FLAGS, align_log2=2,
            symbols=(TextSymbol("_main", 0),),
        ),
        Section(
            sectname="__data", segname="__DATA", data=bytes(range(data_size)),
            flags=macho_obj.DATA_SECTION_FLAGS, align_log2=data_align,
            symbols=(TextSymbol("_table", 0),),
        ),
    ]
    return macho_obj.emit_object(sections)


@pytest.mark.parametrize("text_size", (4, 12, 20, 132))
@pytest.mark.parametrize("data_align", (0, 3, 4, 7, 12))
def test_payload_offsets_mirror_addresses(text_size, data_align):
    blob = _object(text_size, data_align)
    parsed = spec.parse_object(blob)
    segment = parsed.command(spec.LC_SEGMENT_64)
    sections = parsed.sections()
    fileoff = segment.body["fileoff"]
    for section in sections:
        assert section["offset"] - fileoff == section["addr"], section["sectname"]
        assert section["addr"] % (1 << section["align"]) == 0
    assert segment.body["filesize"] <= segment.body["vmsize"]
    # The data start is eight-byte aligned, so a section aligned to eight
    # bytes or less is also aligned in the file, as before.
    assert fileoff % 8 == 0
    data = next(section for section in sections if section["sectname"].rstrip(b"\0") == b"__data")
    assert blob[data["offset"]:data["offset"] + data["size"]] == bytes(range(24))

