"""Host-only archive name projection; no compiler or executable invocation."""

import struct

import pytest

from pcc.backend import macho_archive, macho_spec as spec


def _object(records=(), strings=b"\0_symbol\0", *, count=None, symtab=True):
    # Put the string table before the records, so truncation cannot be hidden
    # by string bytes after the advertised final nlist.
    header_size = spec.MACH_HEADER_64.size
    command_size = spec.SYMTAB_COMMAND.size if symtab else 0
    header = spec.MACH_HEADER_64.pack({
        "magic": spec.MH_MAGIC_64, "cputype": spec.CPU_TYPE_ARM64,
        "cpusubtype": spec.CPU_SUBTYPE_ARM64_ALL, "filetype": spec.MH_OBJECT,
        "ncmds": int(symtab), "sizeofcmds": command_size,
        "flags": 0, "reserved": 0,
    })
    if not symtab:
        return header
    stroff = header_size + command_size
    command = spec.SYMTAB_COMMAND.pack({
        "cmd": spec.LC_SYMTAB, "cmdsize": command_size,
        "symoff": stroff + len(strings),
        "nsyms": len(records) if count is None else count,
        "stroff": stroff, "strsize": len(strings),
    })
    return header + command + strings + b"".join(
        spec.NLIST_64_STRUCT.pack(*record) for record in records
    )


def _legacy_member_symbols(data):
    obj = spec.parse_object(data)
    defines, undefined = set(), set()
    for sym in obj.symbols():
        kind = sym["n_type"] & spec.N_TYPE
        if not sym["n_type"] & spec.N_EXT:
            continue
        if kind == spec.N_SECT:
            defines.add(sym["name"])
        elif kind == spec.N_UNDF and not (sym["n_desc"] & spec.N_WEAK_REF):
            undefined.add(sym["name"])
    return frozenset(defines), frozenset(undefined)


def _archive(members):
    parts = [macho_archive.ARMAG]
    for name, data in members:
        fields = (name.encode() + b"/", b"0", b"0", b"0", b"100644", str(len(data)).encode())
        widths = (16, 12, 6, 6, 8, 10)
        parts.append(b"".join(value.ljust(width) for value, width in zip(fields, widths)) + b"`\n")
        parts.append(data)
        if len(data) % 2:
            parts.append(b"\n")
    return b"".join(parts)


def _named(definitions=(), undefined=(), weak=()):
    strings = b"\0"
    records = []
    for names, kind, desc in (
        (definitions, spec.N_SECT | spec.N_EXT, 0),
        (undefined, spec.N_UNDF | spec.N_EXT, 0),
        (weak, spec.N_UNDF | spec.N_EXT, spec.N_WEAK_REF),
    ):
        for name in names:
            records.append((len(strings), kind, 1 if kind & spec.N_TYPE else 0, desc, 0))
            strings += name.encode() + b"\0"
    return _object(records, strings)


@pytest.mark.parametrize("desc", (0, spec.N_WEAK_REF, 0x80, 0xFFFF))
@pytest.mark.parametrize("section,value", ((0, 0), (255, (1 << 64) - 1)))
def test_all_symbol_type_bits_keep_legacy_classification(desc, section, value):
    # Covers local, external, private external, every N_TYPE, and STAB bits.
    # n_sect and n_value are not archive-state validation, even for undefineds.
    for flags in range(256):
        data = _object([(1, flags, section, desc, value)])
        actual = macho_archive._member_symbols(data)
        assert actual == _legacy_member_symbols(data), (flags, desc, section, value)
        expected_defined = {"_symbol"} if flags & spec.N_EXT and flags & spec.N_TYPE == spec.N_SECT else set()
        expected_undefined = {"_symbol"} if flags & spec.N_EXT and flags & spec.N_TYPE == spec.N_UNDF and not desc & spec.N_WEAK_REF else set()
        assert actual == (frozenset(expected_defined), frozenset(expected_undefined))


@pytest.mark.parametrize("strings", (b"", b"\0", b"\0bad\xff\xfe\0end\0", b"unterminated\xff"))
@pytest.mark.parametrize("strx", (0, 1, 4, 100, (1 << 32) - 1))
def test_name_projection_keeps_surrogateescape_and_existing_index_edges(strings, strx):
    records = [
        (strx, spec.N_SECT | spec.N_EXT, 1, 0, 0),
        (strx, spec.N_UNDF | spec.N_EXT, 0, 0, 0),
        (strx, spec.N_UNDF | spec.N_EXT, 0, spec.N_WEAK_REF, 0),
        (strx, spec.N_SECT, 1, 0, 0),
    ]
    data = _object(records, strings)
    assert macho_archive._member_symbols(data) == _legacy_member_symbols(data)


@pytest.mark.parametrize("symtab", (False, True))
def test_no_symbol_table_or_empty_table_keeps_empty_state(symtab):
    data = _object(symtab=symtab)
    assert macho_archive._member_symbols(data) == _legacy_member_symbols(data) == (frozenset(), frozenset())


@pytest.mark.parametrize("flags,desc", (
    (spec.N_SECT, 0), (spec.N_UNDF | spec.N_EXT, spec.N_WEAK_REF),
    (spec.N_ABS | spec.N_EXT, 0), (spec.N_SECT | spec.N_EXT, 0),
))
@pytest.mark.parametrize("missing", (1, 4, 8, 15, 16))
def test_truncated_ignored_records_keep_same_exception_and_offset(flags, desc, missing):
    data = _object([
        (1, spec.N_SECT | spec.N_EXT, 1, 0, 0),
        (1, flags, 255, desc, (1 << 64) - 1),
    ])[:-missing]
    with pytest.raises(struct.error) as old:
        _legacy_member_symbols(data)
    with pytest.raises(struct.error) as new:
        macho_archive._member_symbols(data)
    assert str(new.value) == str(old.value)


def test_archive_backreferences_weak_refs_and_duplicate_avoidance_keep_order():
    sources = [
        ("early.o", _named(("_early",), ("_main",))),
        ("late.o", _named(("_late",), ("_early", "_next"), ("_optional",))),
        ("duplicate.o", _named(("_main",))),
        ("optional.o", _named(("_optional",), ("_unprovided",))),
        ("unused.o", _named(("_unused",))),
    ]
    archive = _archive(sources)
    members = macho_archive.read_archive(archive)
    expected = [_legacy_member_symbols(data) for _name, data in sources]
    assert [(member.defines, member.undefined) for member in members] == expected
    assert [(member.name, member.data) for member in members] == sources
    selected = []
    pulled, pending = macho_archive.select_members(
        members, {"_late"}, already_defined={"_main"}, selected_members=selected,
    )
    assert [member.name for member in selected] == ["early.o", "late.o"]
    assert pulled == [data for _name, data in sources[:2]] and pending == {"_next"}


def test_unused_member_still_checks_ignored_truncated_symbol():
    good = _named(("_main",))
    bad = _object([(1, spec.N_SECT, 1, 0, 0)])[:-1]
    with pytest.raises(struct.error) as old:
        _legacy_member_symbols(bad)
    with pytest.raises(struct.error) as new:
        macho_archive.read_archive(_archive([("main.o", good), ("unused.o", bad)]))
    assert str(new.value) == str(old.value)


def test_framing_validation_still_precedes_symbol_inspection(monkeypatch):
    good = _named(("_main",))

    def forbidden(data):
        pytest.fail("symbol inspection ran before archive framing finished")

    monkeypatch.setattr(macho_archive, "_member_symbols", forbidden)
    with pytest.raises(macho_archive.ArchiveError, match="truncated member header"):
        macho_archive.read_archive(_archive([("main.o", good)]) + b"bad")
