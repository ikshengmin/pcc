"""Archive framing is independent of the enclosed object format."""

import pytest

from pcc.backend.ar import ArchiveFormatError, read_members


def _member(name: bytes, payload: bytes) -> bytes:
    return (
        name.ljust(16) + b"0".ljust(12) + b"0".ljust(6) + b"0".ljust(6)
        + b"100644".ljust(8) + str(len(payload)).encode().ljust(10) + b"`\n"
        + payload + (b"\n" if len(payload) % 2 else b"")
    )


def test_sysv_members_preserve_order_and_padding():
    data = b"!<arch>\n" + _member(b"first.o/", b"abc") + _member(b"second.o/", b"defg")
    assert read_members(data) == [("first.o", b"abc"), ("second.o", b"defg")]


def test_bsd_extended_names_and_symbol_indexes():
    name = b"a_long_object_name.o\0\0"
    data = b"!<arch>\n" + _member(b"#1/20", b"__.SYMDEF SORTED\0\0\0\0\0" + b"index")
    data += _member(b"#1/" + str(len(name)).encode(), name + b"object")
    assert read_members(data) == [("a_long_object_name.o", b"object")]


def test_gnu_filename_table_and_symbol_indexes():
    table = b"first_long_object_name.o/\nsecond_long_object_name.o/\n"
    offset = table.index(b"second")
    data = b"!<arch>\n" + _member(b"/", b"index") + _member(b"/SYM64/", b"wide")
    data += _member(b"//", table)
    data += _member(b"/0", b"first") + _member(b"/" + str(offset).encode(), b"second")
    assert read_members(data) == [
        ("first_long_object_name.o", b"first"),
        ("second_long_object_name.o", b"second"),
    ]


@pytest.mark.parametrize("data", [
    b"!<thin>\n",
    b"!<arch>\nshort",
    b"!<arch>\n" + _member(b"x.o/", b"abc")[:-1],
    b"!<arch>\n" + _member(b"#1/20", b"short"),
    b"!<arch>\n" + _member(b"/0", b"object"),
])
def test_malformed_or_external_payloads_fail_closed(data):
    with pytest.raises(ArchiveFormatError):
        read_members(data)


def _streamed_parts(data, max_read):
    import hashlib
    import io
    from pcc.backend.ar import read_member_digests

    class ForwardOnly:
        def __init__(self):
            self.reader = io.BytesIO(data)
            self.total = 0

        def read(self, size):
            assert 0 < size <= 65536
            part = self.reader.read(min(size, max_read))
            self.total += len(part)
            return part

    source = ForwardOnly()
    archive_hash = hashlib.sha256()
    measured = read_member_digests(source, hashlib.sha256, archive_hash)
    assert source.total == len(data)
    assert archive_hash.hexdigest() == hashlib.sha256(data).hexdigest()
    return measured


@pytest.mark.parametrize("max_read", [1, 63, 109, 65536])
def test_streamed_archive_hashes_identical_owned_objects_and_raw_file(max_read):
    import hashlib

    # Two GNU tables: even a forward reference uses the last table, exactly as
    # the existing bytes reader. Each BSD prefix is metadata, not object data.
    bsd = b"long-other.o\0\0"
    data = b"!<arch>\n" + _member(b"/0", b"a" * 65537)
    data += _member(b"/", b"INDEX") + _member(b"//", b"superseded.o/\n")
    data += _member(b"#1/" + str(len(bsd)).encode(), bsd + b"object")
    data += _member(b"#1/9", b"__.SYMDEF" + b"index for BSD") + _member(b"/SYM64/", b"index")
    data += _member(b"first/", b"") + _member(b"//", b"last-name.o/\n")
    expected = [(name, hashlib.sha256(payload).hexdigest()) for name, payload in read_members(data)]
    assert [name for name, _ in expected] == ["last-name.o", "long-other.o", "first"]
    assert _streamed_parts(data, max_read) == expected


@pytest.mark.parametrize("data", [
    b"short", b"!<thin>\n", b"!<arch>\n" + b"truncated",
    b"!<arch>\n" + _member(b"a/", b"odd")[:-1],
    b"!<arch>\n" + _member(b"a/", b"odd")[:-2],
    b"!<arch>\n" + _member(b"A/", b"")[:48] + b"9999999999" + b"`\n",
    b"!<arch>\n" + _member(b"/bad", b"payload"),
    b"!<arch>\n" + _member(b"/0", b"") + _member(b"//", b"no newline"),
    b"!<arch>\n" + _member(b"#1/x", b""), b"!<arch>\n" + _member(b"#1/0", b""),
    b"!<arch>\n" + _member(b"#1/7", b"s"),
    b"!<arch>\n" + _member(b"#1/1", b"\0"),
    b"!<arch>\n" + _member(b"#1/x", b"") + b"later invalid frame",
    b"!<arch>\n" + _member(b"/x", b"") + _member(b"#1/x", b"") + b"short",
])
def test_streamed_archive_refuses_same_bad_frame_or_filename_in_same_order(data):
    with pytest.raises(ArchiveFormatError) as old:
        read_members(data)
    with pytest.raises(ArchiveFormatError) as new:
        _streamed_parts(data, 7)
    assert str(new.value) == str(old.value)
