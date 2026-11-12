"""Fail-closed direct dependency evidence for owned regression executables.

This inspects bytes, never executes a target or searches PATH. It does not
prove transitive dependencies, dynamically loaded code, or static source
ownership. Unsupported formats are errors, not empty dependency inventories.
"""

from __future__ import annotations

import hashlib
import struct


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _pe_libraries(data):
    _require(len(data) >= 64, "truncated DOS header")
    pe = struct.unpack_from("<I", data, 60)[0]
    _require(pe >= 64 and pe + 24 <= len(data), "truncated PE header")
    _require(data[pe:pe + 4] == b"PE\0\0", "invalid PE signature")
    count = struct.unpack_from("<H", data, pe + 6)[0]
    size = struct.unpack_from("<H", data, pe + 20)[0]
    optional = pe + 24
    _require(size >= 240 and optional + size + count * 40 <= len(data), "truncated PE tables")
    _require(struct.unpack_from("<H", data, optional)[0] == 0x20B, "expected PE32+")
    _require(struct.unpack_from("<I", data, optional + 108)[0] == 16, "unsupported PE directories")
    # This reader handles normal imports only. Do not certify an
    # uninspected delay-import or CLR runtime route as no-libpython.
    for index in (13, 14):
        _require(struct.unpack_from("<II", data, optional + 112 + index * 8) == (0, 0),
                 "unsupported PE delay imports or CLR directory")
    ranges = []
    for index in range(count):
        section = optional + size + index * 40
        _, rva, raw_size, raw = struct.unpack_from("<IIII", data, section + 8)
        _require(raw + raw_size <= len(data), "truncated PE section")
        if raw_size:
            _require(not any(length and rva < start + length and start < rva + raw_size
                             for start, length, _ in ranges),
                     "overlapping PE section RVA mappings")
        ranges.append((rva, raw_size, raw))

    def span(rva, size):
        matches = [(raw + rva - start, raw + length)
                   for start, length, raw in ranges
                   if start <= rva and rva + size <= start + length]
        _require(len(matches) == 1, "ambiguous or unmapped PE import RVA")
        return matches[0]

    rva, size = struct.unpack_from("<II", data, optional + 120)
    _require(bool(rva) == bool(size), "inconsistent PE import directory")
    libraries = []
    if rva:
        at, _ = span(rva, size)
        terminated = False
        for index in range(size // 20):
            descriptor = struct.unpack_from("<IIIII", data, at + index * 20)
            if descriptor == (0, 0, 0, 0, 0):
                terminated = True
                break
            name, end = span(descriptor[3], 1)
            stop = data.find(b"\0", name, end)
            _require(stop > name, "unterminated PE import name")
            libraries.append(data[name:stop].decode("ascii").lower())
        _require(terminated, "unterminated PE import directory")
    return libraries


def inspect_owned_image(data):
    """Return hash-bound direct linkage evidence, or raise without a receipt."""
    if data[:4] == b"\x7fELF":
        from pcc.backend.elf_x86_64 import parse_static_executable

        parse_static_executable(data)
        image_format, libraries = "static-elf64", []
    elif data[:4] == b"\xcf\xfa\xed\xfe":
        from pcc.backend.macho_spec import parse_object

        image = parse_object(data)
        _require(image.header["filetype"] == 2, "expected Mach-O executable")
        end = 32 + image.header["sizeofcmds"]
        _require(end <= len(data) and sum(lc.cmdsize for lc in image.commands) == end - 32,
                 "truncated or inconsistent Mach-O load commands")
        libraries = []
        for lc in image.commands:
            _require(lc.cmd != 0x10, "unsupported prebound Mach-O dependency")
            # Mask LC_REQ_DYLD, including weak/reexport/upward loads.
            if lc.cmd & 0x7FFFFFFF not in (0xC, 0x18, 0x1F, 0x20, 0x23):
                continue
            _require(lc.cmdsize >= 24, "truncated Mach-O dylib command")
            start = struct.unpack_from("<I", lc.raw, 8)[0]
            stop = lc.raw.find(b"\0", start)
            _require(24 <= start < stop < lc.cmdsize, "invalid Mach-O dylib name")
            libraries.append(lc.raw[start:stop].decode("utf-8"))
        image_format = "macho64"
    elif data[:2] == b"MZ":
        image_format, libraries = "pe32+", _pe_libraries(data)
    else:
        raise ValueError("unsupported native image format")
    # Includes Python.framework and Windows python3.dll, not just libpython.
    _require(not any("python" in name.lower() for name in libraries),
             "binary directly links a Python runtime: " + repr(libraries))
    return {
        "schema": "pcc.owned-image-linkage.v1",
        "binary_sha256": hashlib.sha256(data).hexdigest(),
        "format": image_format,
        "libraries": libraries,
        "libpython": "off",
        "proof_scope": "direct image dependencies only; not transitive, dlopen, or static source ownership",
    }
