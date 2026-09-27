"""AArch64 ELF relocation arithmetic (AAELF64, static small code model).

The reference implementation is LLVM's lld/ELF/Arch/AArch64.cpp.  No LLVM
code or tool is used at runtime.  Instruction addends are explicit RELA
addends; preserving unrelated instruction bits is essential for register and
load-width encodings.
"""

import struct

R_AARCH64_ABS64 = 257
R_AARCH64_ABS32 = 258
R_AARCH64_PREL64 = 260
R_AARCH64_PREL32 = 261
R_AARCH64_ADR_PREL_LO21 = 274
R_AARCH64_ADR_PREL_PG_HI21 = 275
R_AARCH64_ADD_ABS_LO12_NC = 277
R_AARCH64_LDST8_ABS_LO12_NC = 278
R_AARCH64_JUMP26 = 282
R_AARCH64_CALL26 = 283
R_AARCH64_LDST16_ABS_LO12_NC = 284
R_AARCH64_LDST32_ABS_LO12_NC = 285
R_AARCH64_LDST64_ABS_LO12_NC = 286
R_AARCH64_LDST128_ABS_LO12_NC = 299
R_AARCH64_ADR_GOT_PAGE = 311
R_AARCH64_LD64_GOT_LO12_NC = 312
R_AARCH64_TLSIE_ADR_GOTTPREL_PAGE21 = 541
R_AARCH64_TLSIE_LD64_GOTTPREL_LO12_NC = 542
R_AARCH64_TLSLE_ADD_TPREL_HI12 = 549
R_AARCH64_TLSLE_ADD_TPREL_LO12_NC = 551

GOT_ADDRESS_RELOCATIONS = (311, 312)
GOT_TLS_RELOCATIONS = (541, 542)


def relocation_width(kind: int) -> int:
    if kind == 0:
        return 0
    if kind in (257, 260):
        return 8
    if kind in (258, 261, 274, 275, 277, 278, 282, 283, 284, 285,
                286, 299, 311, 312, 541, 542, 549, 551):
        return 4
    raise ValueError("unsupported AArch64 ELF relocation: " + str(kind))


def _signed(value: int, bits: int) -> int:
    if value < -(1 << (bits - 1)) or value >= (1 << (bits - 1)):
        raise ValueError("AArch64 relocation out of range: " + str(value))
    return value & ((1 << bits) - 1)


def apply_relocation(image: bytearray, offset: int, kind: int,
                     target: int, place: int) -> None:
    """Apply S+A (or GOT/TP offset+A) to a validated instruction/data site."""
    width = relocation_width(kind)
    if not width:
        return
    if offset < 0 or offset + width > len(image):
        raise ValueError("AArch64 relocation outside image")
    if kind in (257, 258, 260, 261):
        value = target - place if kind in (260, 261) else target
        if kind in (260, 261):
            value = _signed(value, width * 8)
        elif value < 0 or value >= 1 << (width * 8):
            raise ValueError("AArch64 absolute relocation overflow")
        image[offset:offset + width] = value.to_bytes(width, "little")
        return
    if offset & 3:
        raise ValueError("unaligned AArch64 instruction relocation")
    word = struct.unpack_from("<I", image, offset)[0]
    if kind in (282, 283):
        delta = target - place
        if delta & 3:
            raise ValueError("unaligned AArch64 branch target")
        immediate = _signed(delta >> 2, 26)
        word = (word & 0xFC000000) | immediate
    elif kind in (274, 275, 311, 541):
        delta = (target - place) if kind == 274 else (
            (target >> 12) - (place >> 12)
        )
        immediate = _signed(delta, 21)
        word = (word & ~0x60FFFFE0) | ((immediate & 3) << 29)
        word |= ((immediate >> 2) & 0x7FFFF) << 5
    elif kind in (277, 549, 551):
        if kind in (549, 551) and (target < 0 or target >= 1 << 24):
            raise ValueError("AArch64 local-exec TLS offset exceeds 24 bits")
        immediate = ((target >> 12) if kind == 549 else target) & 0xFFF
        word = (word & ~0x003FFC00) | (immediate << 10)
    else:
        scale = {278: 0, 284: 1, 285: 2, 286: 3, 299: 4,
                 312: 3, 542: 3}[kind]
        low = target & 0xFFF
        if low & ((1 << scale) - 1):
            raise ValueError("misaligned AArch64 load/store relocation")
        word = (word & ~0x003FFC00) | ((low >> scale) << 10)
    struct.pack_into("<I", image, offset, word)
