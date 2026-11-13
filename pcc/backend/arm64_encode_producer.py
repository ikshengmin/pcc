"""Encode scalar producer operands without constructing or parsing assembly."""
from __future__ import annotations

from .arm64_encode import (
    EncodeError,
    _cond,
    _enc_addsub_reg,
    _enc_logical_reg,
    _enc_ldstp,
    lse_atomic_mnemonic_parts,
    _LSE_RMW_OPCODES,
    _enc_mov_immediate,
    _freg,
    _logical_imm,
    _is_sp_token,
    _reg,
    _reg_kind,
    _sf,
)


def encode_producer_registers(
    mnemonic: str, destination: str, left: str,
    right: str = "", fourth: str = "", shift: int = 0,
) -> int:
    """Original register/condition operands, never an assembly line."""
    unary = mnemonic in ("neg", "sxtb", "sxth", "sxtw", "fneg", "fcmp",
                         "fmov", "fcvt", "scvtf", "ucvtf", "fcvtzs", "fcvtzu", "clz", "rbit", "rev", "rev16",
                         "uxtw", "mvn", "tst", "fabs", "fsqrt",
                         "frintn", "frintp", "frintm", "frintz")
    if unary and right:
        raise EncodeError("unexpected producer right operand")
    if mnemonic not in ("madd", "msub", "csel", "fcsel", "csinv") and fourth:
        raise EncodeError("unexpected producer fourth operand")
    if mnemonic not in ("add", "sub", "adds", "subs", "orr") and shift != 0:
        raise EncodeError("unexpected producer register shift")
    if _is_sp_token(destination) or _is_sp_token(left):
        raise EncodeError("producer scalar operation cannot use SP")
    if mnemonic in ("fadd", "fsub", "fmul", "fdiv", "fneg", "fcmp", "fcsel", "fabs", "fsqrt",
                    "frintn", "frintp", "frintm", "frintz"):
        rd, kind = _freg(destination)
        rn, source_kind = _freg(left)
        if kind != source_kind or kind not in ("s", "d"):
            raise EncodeError("producer FP operands must have matching scalar widths")
        base = 0x1E600000 if kind == "d" else 0x1E200000
        if mnemonic in ("fneg", "fabs", "fsqrt", "frintn", "frintp", "frintm", "frintz"):
            opc = {"fneg": 0x14000, "fabs": 0xC000, "fsqrt": 0x1C000,
                   "frintn": 0x44000, "frintp": 0x4C000,
                   "frintm": 0x54000, "frintz": 0x5C000}[mnemonic]
            return base | opc | (rn << 5) | rd
        if mnemonic == "fcmp":
            return base | 0x2000 | (rn << 16) | (rd << 5)
        rm, other_kind = _freg(right)
        if other_kind != kind:
            raise EncodeError("producer FP operands must have matching scalar widths")
        if mnemonic == "fcsel":
            return base | 0xC00 | (rm << 16) | (_cond(fourth) << 12) | (rn << 5) | rd
        opc = {"fmul": 0x0800, "fdiv": 0x1800, "fadd": 0x2800, "fsub": 0x3800}[mnemonic]
        return base | opc | (rm << 16) | (rn << 5) | rd
    if mnemonic in ("fmov", "fcvt", "scvtf", "ucvtf", "fcvtzs", "fcvtzu"):
        kd = _reg_kind(destination)
        ks = _reg_kind(left)
        if kd in ("s", "d"):
            rd, _ = _freg(destination)
        else:
            rd, _ = _reg(destination)
        if ks in ("s", "d"):
            rn, _ = _freg(left)
        else:
            rn, _ = _reg(left)
        if mnemonic == "fmov":
            bases = {"dd": 0x1E604000, "ss": 0x1E204000,
                     "sw": 0x1E270000, "ws": 0x1E260000,
                     "dx": 0x9E670000, "xd": 0x9E660000}
            base = bases.get(kd + ks, -1)
        elif mnemonic == "fcvt":
            base = {"ds": 0x1E22C000, "sd": 0x1E624000}.get(kd + ks, -1)
        elif mnemonic in ("scvtf", "ucvtf"):
            if kd not in ("s", "d") or ks not in ("w", "x"):
                raise EncodeError("invalid producer integer to FP conversion")
            base = 0x1E220000 | ((ks == "x") << 31) | ((kd == "d") << 22)
            if mnemonic == "ucvtf":
                base |= 0x10000
        else:
            if kd not in ("w", "x") or ks not in ("s", "d"):
                raise EncodeError("invalid producer FP to integer conversion")
            base = 0x1E380000 | ((kd == "x") << 31) | ((ks == "d") << 22)
            if mnemonic == "fcvtzu":
                base |= 0x10000
        if base < 0:
            raise EncodeError("unsupported producer FP conversion")
        return base | (rn << 5) | rd
    rd, d64 = _reg(destination)
    rn, n64 = _reg(left)
    if _is_sp_token(destination) or _is_sp_token(left):
        raise EncodeError("producer scalar operation cannot use SP")
    if mnemonic in ("sxtb", "sxth", "sxtw"):
        if (mnemonic == "sxtw" and (not d64 or n64)) or (not d64 and n64):
            raise EncodeError("invalid producer sign-extension widths")
        imms = {"sxtb": 7, "sxth": 15, "sxtw": 31}[mnemonic]
        return _sf(d64) | 0x13000000 | (int(d64) << 22) | (imms << 10) | (rn << 5) | rd
    if mnemonic == "uxtw":
        if not d64 or n64:
            raise EncodeError("producer uxtw requires x,w registers")
        return 0xD3407C00 | (rn << 5) | rd
    if mnemonic in ("smull", "umull"):
        rm, m64 = _reg(right)
        if not d64 or n64 or m64 or _is_sp_token(right):
            raise EncodeError("producer widening multiply requires x,w,w")
        return 0x9B207C00 | (int(mnemonic == "umull") << 23) | (rm << 16) | (rn << 5) | rd
    if d64 != n64:
        raise EncodeError("producer integer register widths differ")
    if mnemonic == "neg":
        return _enc_addsub_reg(1, rd, 31, rn, d64)
    if mnemonic in ("clz", "rbit", "rev", "rev16"):
        op2 = {"rbit": 0, "rev16": 1, "rev": 3 if d64 else 2, "clz": 4}[mnemonic]
        return _sf(d64) | 0x5AC00000 | (op2 << 10) | (rn << 5) | rd
    if mnemonic == "mvn":
        return _sf(d64) | 0x2A2003E0 | (rn << 16) | rd
    if mnemonic == "tst":
        return _enc_logical_reg(3, 31, rd, rn, d64)
    if mnemonic == "cneg":
        return _sf(d64) | 0x5A800400 | (rn << 16) | ((_cond(right) ^ 1) << 12) | (rn << 5) | rd
    rm, m64 = _reg(right)
    if m64 != d64 or _is_sp_token(right):
        raise EncodeError("producer integer register widths differ")
    if mnemonic in ("add", "sub", "adds", "subs"):
        return _enc_addsub_reg(int(mnemonic.startswith("sub")), rd, rn, rm, d64,
                               int(mnemonic in ("adds", "subs")), shift=shift)
    if mnemonic in ("and", "orr", "eor"):
        return _enc_logical_reg({"and": 0, "orr": 1, "eor": 2}[mnemonic], rd, rn, rm, d64, shift=shift)
    if mnemonic in ("lslv", "lsrv", "asrv"):
        op2 = {"lslv": 8, "lsrv": 9, "asrv": 10}[mnemonic]
        return _sf(d64) | 0x1AC00000 | (rm << 16) | (op2 << 10) | (rn << 5) | rd
    if mnemonic in ("smulh", "umulh"):
        if not d64:
            raise EncodeError("producer high multiply requires x registers")
        return 0x9B407C00 | (int(mnemonic == "umulh") << 23) | (rm << 16) | (rn << 5) | rd
    if mnemonic == "csinv":
        return _sf(d64) | 0x5A800000 | (rm << 16) | (_cond(fourth) << 12) | (rn << 5) | rd
    if mnemonic == "mul":
        return _sf(d64) | 0x1B007C00 | (rm << 16) | (rn << 5) | rd
    if mnemonic in ("sdiv", "udiv"):
        return _sf(d64) | 0x1AC00800 | (rm << 16) | (int(mnemonic == "sdiv") << 10) | (rn << 5) | rd
    if mnemonic == "csel":
        return _sf(d64) | 0x1A800000 | (rm << 16) | (_cond(fourth) << 12) | (rn << 5) | rd
    if mnemonic in ("madd", "msub"):
        ra, a64 = _reg(fourth)
        if a64 != d64 or _is_sp_token(fourth) or (mnemonic == "madd" and not d64):
            raise EncodeError("invalid producer multiply-add operands")
        return _sf(d64) | 0x1B000000 | (int(mnemonic == "msub") << 15) | (rm << 16) | (ra << 10) | (rn << 5) | rd
    raise EncodeError("unsupported producer register opcode " + mnemonic)


def encode_producer_immediate(
    mnemonic: str, destination: str, left: str, immediate,
) -> int:
    rd, d64 = _reg(destination)
    if _is_sp_token(destination):
        raise EncodeError("producer immediate operation cannot use SP")
    if mnemonic == "mov":
        if left:
            raise EncodeError("unexpected producer MOV source operand")
        return _enc_mov_immediate(rd, immediate, d64, "producer MOV immediate")
    rn, n64 = _reg(left)
    if d64 != n64 or _is_sp_token(left):
        raise EncodeError("producer immediate register widths differ")
    if mnemonic == "and":
        n_bit, immr, imms = _logical_imm(immediate, d64)
        return _sf(d64) | 0x12000000 | (n_bit << 22) | (immr << 16) | (imms << 10) | (rn << 5) | rd
    if mnemonic in ("lsl", "lsr", "asr"):
        width = 64 if d64 else 32
        if immediate < 0 or immediate >= width:
            raise EncodeError("producer shift out of range")
        immr = (width - immediate) % width if mnemonic == "lsl" else immediate
        imms = width - 1 - immediate if mnemonic == "lsl" else width - 1
        base = 0x13000000 if mnemonic == "asr" else 0x53000000
        return _sf(d64) | base | (int(d64) << 22) | (immr << 16) | (imms << 10) | (rn << 5) | rd
    raise EncodeError("unsupported producer immediate opcode " + mnemonic)


def _producer_address_register(base: str) -> int:
    rn, is64 = _reg(base)
    if not is64 or (rn == 31 and not _is_sp_token(base)):
        raise EncodeError("producer address requires Xn or SP")
    return rn


def encode_producer_atomic(
    mnemonic: str, first: str, base: str, second: str = "",
) -> int:
    rn = _producer_address_register(base)
    rs, s64 = _reg(first)
    if _is_sp_token(first) or _is_sp_token(second):
        raise EncodeError("producer atomic data cannot use SP")
    parts = lse_atomic_mnemonic_parts(mnemonic)
    if parts is not None:
        rt, t64 = _reg(second)
        operation, ordering, suffix = parts
        if s64 != t64 or (suffix and s64):
            raise EncodeError("producer LSE data widths differ")
        size = (0 if suffix == "b" else 1) if suffix else (3 if s64 else 2)
        acquire = int(ordering in ("a", "al"))
        release = int(ordering in ("l", "al"))
        if operation == "cas":
            return ((size << 30) | 0x08A07C00 | (acquire << 22) | (rs << 16)
                    | (release << 15) | (rn << 5) | rt)
        o3, opc = _LSE_RMW_OPCODES[operation]
        return ((size << 30) | 0x38200000 | (acquire << 23) | (release << 22)
                | (rs << 16) | (o3 << 15) | (opc << 12) | (rn << 5) | rt)
    if mnemonic in ("stxr", "stxrb", "stxrh", "stlxr", "stlxrb", "stlxrh"):
        rt, t64 = _reg(second)
        narrow = mnemonic.endswith("b") or mnemonic.endswith("h")
        if s64 or (narrow and t64):
            raise EncodeError("producer store-exclusive register width invalid")
        base_op = (0x08007C00 if mnemonic.endswith("b") else 0x48007C00) if narrow else (0xC8007C00 if t64 else 0x88007C00)
        return base_op | (int(mnemonic.startswith("stlxr")) << 15) | (rs << 16) | (rn << 5) | rt
    if second:
        raise EncodeError("unexpected producer atomic second register")
    bases = {"ldar": 0x88DFFC00, "ldaxr": 0x885FFC00,
             "ldxr": 0x885F7C00, "stlr": 0x889FFC00,
             "ldarb": 0x08DFFC00, "ldarh": 0x48DFFC00,
             "ldaxrb": 0x085FFC00, "ldaxrh": 0x485FFC00,
             "ldxrb": 0x085F7C00, "ldxrh": 0x485F7C00,
             "stlrb": 0x089FFC00, "stlrh": 0x489FFC00}
    if mnemonic not in bases:
        raise EncodeError("unsupported producer atomic opcode")
    narrow = mnemonic.endswith("b") or mnemonic.endswith("h")
    if narrow and s64:
        raise EncodeError("producer narrow atomic requires W data")
    return bases[mnemonic] | (int(s64) << 30) | (rn << 5) | rs


def encode_producer_system(mnemonic: str, register: str, system: str) -> int:
    rn, is64 = _reg(register)
    if mnemonic not in ("mrs", "msr") or not is64 or _is_sp_token(register):
        raise EncodeError("invalid producer system transfer")
    if system.lower() not in ("nzcv", "tpidr_el0"):
        raise EncodeError("unsupported producer system register")
    base = 0xD53B4200 if system.lower() == "nzcv" else 0xD53BD040
    return (base if mnemonic == "mrs" else base - 0x200000) | rn


def encode_producer_indirect_branch(mnemonic: str, register: str) -> int:
    rn, is64 = _reg(register)
    if mnemonic not in ("br", "blr") or not is64 or _is_sp_token(register):
        raise EncodeError("invalid producer indirect branch")
    return (0xD63F0000 if mnemonic == "blr" else 0xD61F0000) | (rn << 5)


def encode_producer_pair(
    mnemonic: str, first: str, second: str, base: str, offset: int,
) -> int:
    rt, t64 = _reg(first)
    rt2, second64 = _reg(second)
    rn = _producer_address_register(base)
    if (mnemonic not in ("ldp", "stp") or t64 != second64
            or _is_sp_token(first) or _is_sp_token(second)):
        raise EncodeError("invalid producer pair operands")
    return _enc_ldstp(int(mnemonic == "ldp"), "signed", rt, rt2, rn, offset, t64)


def encode_producer_fp_immediate(register: str, encoding8: int) -> int:
    rd, kind = _freg(register)
    if encoding8 not in (0, 0x70):
        raise EncodeError("unsupported producer direct FP immediate")
    return (0x1E601000 if kind == "d" else 0x1E201000) | (encoding8 << 13) | rd


def encode_producer_vector(
    mnemonic: str, destination_index: int, source_index: int = 0,
) -> int:
    if not 0 <= destination_index <= 31 or not 0 <= source_index <= 31:
        raise EncodeError("producer vector register index out of range")
    if mnemonic == "movi":
        if source_index != 0:
            raise EncodeError("producer MOVI zero takes no source register")
        return 0x4F00E400 | destination_index
    bases = {"cnt": 0x0E205800, "addv": 0x0E31B800, "umov": 0x0E013C00}
    if mnemonic not in bases or (mnemonic == "umov" and destination_index == 31):
        raise EncodeError("unsupported producer vector shape")
    return bases[mnemonic] | (source_index << 5) | destination_index
