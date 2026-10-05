"""Validated, rereadable ELF inputs with one resident parsed payload.

Symbols and section descriptors are retained for resolution/layout. Object,
assembly and archive payloads are hashed, validated, then released; the linker
reloads at most one parsed input at a time. This is bounded payload retention,
not constant-space linking: symbol tuples and the final image remain resident.
"""

from dataclasses import dataclass
import hashlib
import os

from . import elf_x86_64 as elf

_HASH_CHUNK_BYTES = 1024 * 1024


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(_HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class _InputSource:
    path: str
    offset: int
    size: int
    sha256: str
    label: str
    kind: str
    whole_file: bool


@dataclass(frozen=True)
class _InputSection:
    store: object
    source_index: int
    section_index: int
    name: str
    type: int
    flags: int
    align: int
    file_size: int
    mem_size: int
    relocation_count: int

    @property
    def size(self) -> int:
        return self.mem_size if self.type == elf.SHT_NOBITS else self.file_size

    @property
    def data(self):
        if not self.file_size:
            return b""
        return self.store.load(self.source_index).sections[self.section_index].data

    @property
    def relocations(self):
        if not self.relocation_count:
            return ()
        return self.store.load(self.source_index).sections[self.section_index].relocations


@dataclass(frozen=True)
class _InputObject:
    # Phase metadata from an already validated ElfObject, never an unchecked
    # alternative public ElfObject constructor. Reload uses the same parser.
    sections: tuple
    symbols: tuple
    machine: int


def _archive_ranges(path: str):
    """Index owned GNU/BSD/COFF ar names without retaining member payloads."""
    size = os.path.getsize(path)
    rows = []
    gnu_names = b""
    with open(path, "rb") as stream:
        if stream.read(8) != elf._AR_MAGIC:
            raise elf.ElfError("not an ar archive")
        offset = 8
        while offset < size:
            if offset + elf._AR_HEADER_SIZE > size:
                raise elf.ElfError(f"truncated ar header at {offset}")
            stream.seek(offset)
            header = stream.read(elf._AR_HEADER_SIZE)
            if len(header) != elf._AR_HEADER_SIZE:
                raise elf.ElfError(f"truncated ar header at {offset}")
            if header[58:60] != b"`\n":
                raise elf.ElfError(f"bad ar member magic at {offset}")
            try:
                length = int(header[48:58].decode("ascii").strip())
            except (UnicodeDecodeError, ValueError) as exc:
                raise elf.ElfError(f"bad ar member size at {offset}") from exc
            body = offset + elf._AR_HEADER_SIZE
            end = body + length
            if length < 0 or end > size:
                raise elf.ElfError(f"ar member at {offset} runs past end of archive")
            raw_name = header[:16].rstrip()
            offset = end + (end & 1)
            if raw_name == b"//":
                gnu_names = stream.read(length)
                if len(gnu_names) != length:
                    raise elf.ElfError("staged ELF archive string table became truncated")
                continue
            if raw_name in (b"/", b"/SYM64/") or raw_name.startswith(b"__.SYMDEF"):
                continue
            if raw_name.startswith(b"#1/"):
                try:
                    name_length = int(raw_name[3:])
                except ValueError as exc:
                    raise elf.ElfError("bad BSD ar extended name") from exc
                if name_length < 0 or name_length > length:
                    raise elf.ElfError("BSD ar extended name exceeds member")
                name_bytes = stream.read(name_length)
                if len(name_bytes) != name_length:
                    raise elf.ElfError("staged ELF archive member name became truncated")
                name = name_bytes.rstrip(b"\0").decode("utf-8", "surrogateescape")
                body += name_length
                length -= name_length
            elif raw_name.startswith(b"/") and raw_name[1:].isdigit():
                if not gnu_names:
                    raise elf.ElfError("GNU ar member uses a missing string table")
                name_offset = int(raw_name[1:])
                if name_offset >= len(gnu_names):
                    raise elf.ElfError("GNU ar member name offset is out of range")
                name_end = gnu_names.find(b"/\n", name_offset)
                if name_end < 0:
                    name_end = gnu_names.find(b"\0", name_offset)
                if name_end < 0:
                    raise elf.ElfError("ar long member name is unterminated")
                name = gnu_names[name_offset:name_end].decode("utf-8", "surrogateescape")
            else:
                name = raw_name.rstrip(b"/").decode("utf-8", "surrogateescape")
            if not name.startswith("__.SYMDEF"):
                rows.append((name, body, length))
    return rows


class ElfInputStore:
    """One link scope; at most one reload cache, plus validated metadata."""

    def __init__(self, target: str, archives=()) -> None:
        self.target = target
        self.archives = list(archives)
        self.sources = []
        self.archive_hashes = []
        self.member_hashes = []
        self.duplicate_member_names = []
        self.current_index = -1
        self.current = None

    def clear(self) -> None:
        self.current = None
        self.current_index = -1

    def _read(self, source):
        with open(source.path, "rb") as stream:
            stream.seek(source.offset)
            data = stream.read(source.size)
            if len(data) != source.size:
                raise elf.ElfError("staged ELF input became truncated: " + source.label)
            # For a direct object/ASM, an append also changes the input. Archive
            # members have bounded ranges; the complete archive is hashed too.
            if source.whole_file and stream.read(1):
                raise elf.ElfError("staged ELF input size changed: " + source.label)
        digest = hashlib.sha256(data).hexdigest()
        if source.sha256 and digest != source.sha256:
            raise elf.ElfError("staged ELF input identity changed: " + source.label)
        return data, digest

    def _parse(self, data, kind):
        if kind == "ASM":
            from .owned_elf_link import assemble
            # Match open(..., encoding='utf-8') universal-newline semantics.
            text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            return assemble(text, self.target)
        return elf.parse_relocatable(data, compact_relocations=True)

    def stage(self, path: str, *, kind: str = "PCO", offset: int = 0,
              size=None, label: str = ""):
        self.clear()
        whole_file = size is None
        if whole_file:
            size = os.path.getsize(path)
        source = _InputSource(path, offset, size, "", label or path, kind, whole_file)
        data, digest = self._read(source)
        obj = self._parse(data, kind)
        del data
        index = len(self.sources)
        self.sources.append(_InputSource(path, offset, size, digest, source.label, kind, whole_file))
        sections = tuple(
            _InputSection(self, index, si, section.name, section.type, section.flags,
                          section.align, len(section.data), section.mem_size,
                          len(section.relocations))
            for si, section in enumerate(obj.sections)
        )
        return _InputObject(sections, obj.symbols, obj.machine)

    def load(self, index: int):
        if self.current_index == index:
            return self.current
        self.clear()
        source = self.sources[index]
        data, _digest = self._read(source)
        obj = self._parse(data, source.kind)
        del data
        self.current = obj
        self.current_index = index
        return obj

    def read_archive(self, archive_index: int):
        path = self.archives[archive_index]
        digest = file_sha256(path)
        members = []
        hashes = {}
        duplicate_names = False
        for name, offset, size in _archive_ranges(path):
            try:
                obj = self.stage(path, offset=offset, size=size, label=path + "(" + name + ")")
            except elf.ElfError as exc:
                raise elf.ElfError(f"archive member {name!r} is not a proven ELF object: {exc}") from exc
            if name in hashes:
                duplicate_names = True
            hashes[name] = self.sources[-1].sha256
            defined, undefined = elf._object_symbol_sets(obj)
            members.append(elf.ElfArchiveMember(name, obj, defined, undefined))
        if file_sha256(path) != digest:
            raise elf.ElfError("staged ELF archive identity changed during index: " + path)
        self.archive_hashes.append(digest)
        self.member_hashes.append(hashes)
        self.duplicate_member_names.append(duplicate_names)
        return tuple(members)

    def verify(self) -> None:
        # The current cache is unnecessary during final validation/publication.
        self.clear()
        for source in self.sources:
            if not source.whole_file:
                continue  # Complete archive identity also covers unused members.
            digest = hashlib.sha256()
            with open(source.path, "rb") as stream:
                remaining = source.size
                while remaining:
                    chunk = stream.read(min(_HASH_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise elf.ElfError("staged ELF input became truncated: " + source.label)
                    digest.update(chunk)
                    remaining -= len(chunk)
                if stream.read(1):
                    raise elf.ElfError("staged ELF input size changed: " + source.label)
            if digest.hexdigest() != source.sha256:
                raise elf.ElfError("staged ELF input identity changed: " + source.label)
        for path, digest in zip(self.archives, self.archive_hashes):
            if file_sha256(path) != digest:
                raise elf.ElfError("staged ELF archive identity changed: " + path)
