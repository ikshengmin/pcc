"""Read regular Unix archives without an external archiver or extraction."""

from __future__ import annotations


class ArchiveFormatError(ValueError):
    pass


_MAGIC = b"!<arch>\n"
_METADATA_NAMES = frozenset({b"/", b"//", b"/SYM64/"})
_BLOCK_SIZE = 65536


def _member_header(header):
    if len(header) != 60:
        raise ArchiveFormatError("truncated archive member header")
    if header[58:60] != b"`\n":
        raise ArchiveFormatError("bad archive member header magic")
    try:
        size = int(header[48:58].decode("ascii").strip())
    except ValueError as exc:
        raise ArchiveFormatError("invalid archive member size") from exc
    if size < 0:
        raise ArchiveFormatError("negative archive member size")
    return header[:16].rstrip(), size


def _bsd_length(raw_name, size):
    if not raw_name.startswith(b"#1/"):
        return 0
    try:
        length = int(raw_name[3:].decode("ascii"))
    except ValueError as exc:
        raise ArchiveFormatError("invalid BSD archive filename length") from exc
    if length < 1 or length > size:
        raise ArchiveFormatError("BSD archive filename exceeds member")
    return length


def _member_name(raw_name, name_table, bsd_name):
    if raw_name.startswith(b"#1/"):
        name_bytes = bsd_name.rstrip(b"\0")
    elif raw_name.startswith(b"/"):
        try:
            offset = int(raw_name[1:].decode("ascii"))
        except ValueError as exc:
            raise ArchiveFormatError("invalid GNU archive filename offset") from exc
        if offset < 0 or offset >= len(name_table):
            raise ArchiveFormatError("GNU archive filename offset outside table")
        name_end = name_table.find(b"\n", offset)
        if name_end < 0:
            raise ArchiveFormatError("unterminated GNU archive filename")
        name_bytes = name_table[offset:name_end].rstrip(b"/")
    else:
        name_bytes = raw_name.rstrip(b"/")
    name = name_bytes.decode("utf-8", "surrogateescape")
    if not name:
        raise ArchiveFormatError("empty archive member name")
    return name


def read_members(data: bytes) -> list[tuple[str, bytes]]:
    """Return object payloads in archive order, supporting BSD and GNU names.

    Symbol indexes and the GNU filename table are metadata. Member names are
    returned as data, never interpreted as filesystem paths. Thin archives
    are rejected because their payloads live outside the supplied bytes.
    """
    if not data.startswith(_MAGIC):
        raise ArchiveFormatError("not a regular ar archive (bad magic)")
    headers: list[tuple[bytes, int, int]] = []
    name_table = b""
    position = len(_MAGIC)
    while position < len(data):
        raw_name, size = _member_header(data[position:position + 60])
        start = position + 60
        end = start + size
        if end > len(data):
            raise ArchiveFormatError("archive member runs past end of input")
        if raw_name == b"//":
            name_table = data[start:end]
        headers.append((raw_name, start, end))
        position = end + size % 2
        if position > len(data):
            raise ArchiveFormatError("truncated archive member padding")

    members: list[tuple[str, bytes]] = []
    for raw_name, start, end in headers:
        if raw_name in _METADATA_NAMES:
            continue
        name_length = _bsd_length(raw_name, end - start)
        name = _member_name(raw_name, name_table, data[start:start + name_length])
        if not name.startswith("__.SYMDEF"):
            members.append((name, data[start + name_length:end]))
    return members


def _read_block(stream, length, archive_hash):
    data = stream.read(length)
    if archive_hash is not None:
        archive_hash.update(data)
    return data


def _read_header_bytes(stream, length, archive_hash):
    """Permit short reads and EOF without losing any byte of the snapshot."""
    data = _read_block(stream, length, archive_hash)
    while data and len(data) < length:
        more = _read_block(stream, length - len(data), archive_hash)
        if not more:
            break
        data += more
    return data


def read_member_digests(stream, new_hash, archive_hash=None):
    """Hash members and, optionally, their entire enclosing stream in one pass.

    A file can be replaced or modified: never seek or reopen it to hash an
    object after examining its header. Only name metadata and final digests
    survive each member. Raw padding, symbol indexes, naming tables and BSD
    name prefixes belong to the archive hash, not the object hash.
    """
    if _read_header_bytes(stream, len(_MAGIC), archive_hash) != _MAGIC:
        raise ArchiveFormatError("not a regular ar archive (bad magic)")
    headers = []
    name_table = b""
    while True:
        header = _read_header_bytes(stream, 60, archive_hash)
        if not header:
            break
        raw_name, size = _member_header(header)
        metadata = raw_name in _METADATA_NAMES
        if raw_name == b"//":
            prefix = size
        else:
            try:
                prefix = _bsd_length(raw_name, size)
            except ArchiveFormatError:
                # Framing errors anywhere in the stream take precedence over
                # filename errors; validate in the original order below.
                prefix = 0
        name_chunks = []
        member_hash = None if metadata else new_hash()
        remaining = size
        while remaining:
            block = _read_block(stream, min(remaining, _BLOCK_SIZE), archive_hash)
            if not block:
                raise ArchiveFormatError("archive member runs past end of input")
            remaining -= len(block)
            if prefix:
                take = min(prefix, len(block))
                name_chunks.append(block[:take])
                prefix -= take
                block = block[take:]
            if member_hash is not None and block:
                member_hash.update(block)
        if size % 2 and not _read_block(stream, 1, archive_hash):
            raise ArchiveFormatError("truncated archive member padding")
        if raw_name == b"//":
            name_table = b"".join(name_chunks)
        elif member_hash is not None:
            headers.append((raw_name, size, b"".join(name_chunks), member_hash.hexdigest()))

    members = []
    for raw_name, size, bsd_name, digest in headers:
        _bsd_length(raw_name, size)
        name = _member_name(raw_name, name_table, bsd_name)
        if not name.startswith("__.SYMDEF"):
            members.append((name, digest))
    return members
