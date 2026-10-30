#!/usr/bin/env python3
"""Generate canonical ASCII image objects from the runtime's object ABI."""

from __future__ import annotations

import argparse
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pcc.runtime.py import py_abi_constants as abi


def render_table() -> str:
    # One payload byte and its NUL terminator, rounded to an i64 boundary.
    stride = (abi.PYSTROBJECT_DATA_OFFSET + 2 + 7) // 8 * 8
    lines = ['define_global_i64_array(', '    "pcc_ascii_image_chars",']
    for codepoint in range(128):
        data = bytearray(stride)
        for offset, value, width in (
            (abi.PYOBJECTHEADER_REFCOUNT_OFFSET, 1, "q"),
            (abi.PYOBJECTHEADER_TYPE_TAG_OFFSET, abi.PY_TYPE_STR, "I"),
            (abi.PYOBJECTHEADER_FLAGS_OFFSET, abi.PY_FLAG_IMMORTAL, "I"),
            (abi.PYSTROBJECT_BYTE_LEN_OFFSET, 1, "q"),
            (abi.PYSTROBJECT_CP_LEN_OFFSET, 1, "q"),
            (abi.PYSTROBJECT_HASH_OFFSET, -1, "q"),
        ):
            struct.pack_into("<" + width, data, offset, value)
        data[abi.PYSTROBJECT_DATA_OFFSET] = codepoint
        words = struct.unpack("<" + "q" * (stride // 8), data)
        lines.append(
            "    " + ", ".join(map(str, words)) + f",  # U+{codepoint:04X}"
        )
    lines.append(")")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = ROOT / "pcc/runtime/py/freestanding_ascii_chars.py"
    source = path.read_text()
    start = source.index('define_global_i64_array(\n    "pcc_ascii_image_chars",')
    end = source.index("\n)", start) + 2
    rendered = render_table()
    if args.check:
        if source[start:end] != rendered:
            print("stale ASCII image layout; run scripts/gen_ascii_image_chars.py")
            return 1
        print("ASCII image objects match the runtime object ABI")
        return 0
    path.write_text(source[:start] + rendered + source[end:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
