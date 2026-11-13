"""Emitter-origin operand provenance for exact typed AArch64 rewrites.

This module never tokenizes or renders assembly. Producers supply their opcode
and operands before encoding. Machine-word aliases do not recover source form.
Headers are indexed by encoded-record ID; operands contain only scalar tokens.
"""

from __future__ import annotations

from .arm64_encode import EncodeError, _validate_text_fixup_word
from .self_backend_value_arena import CompilerInt2, CompilerInt4, CompilerIntArena

# Append-only IDs. Keep aliases distinct: matching is the existing emitter's
# source-form contract, not a broader interpretation of equivalent ISA words.
OPCODE_NAMES = (
    "", "mov", "movz", "movn", "movk", "add", "adds", "sub", "subs",
    "cmp", "cmn", "tst", "and", "ands", "orr", "orn", "eor", "eon",
    "bic", "bics", "mul", "madd", "msub", "mneg", "smulh", "umulh",
    "sdiv", "udiv", "lsl", "lsr", "asr", "ror", "lslv", "lsrv", "asrv",
    "rorv", "neg", "negs", "mvn", "sxtw", "sxth", "sxtb", "uxtb", "uxth",
    "ubfx", "sbfx", "ubfiz", "sbfiz", "ubfm", "sbfm", "bfm", "bfi", "bfxil",
    "csel", "csinc", "csinv", "csneg", "cset", "csetm", "cinc", "cinv", "cneg",
    "ldr", "ldur", "ldrb", "ldurb", "ldrh", "ldurh", "ldrsw", "ldursw",
    "ldrsb", "ldursb", "ldrsh", "ldursh", "str", "stur", "strb", "sturb",
    "strh", "sturh", "ldp", "stp", "ldnp", "stnp", "ldpsw",
    "ldar", "stlr", "ldarb", "ldarh", "stlrb", "stlrh", "ldxr", "ldaxr",
    "stxr", "stlxr", "ldxp", "ldaxp", "stxp", "stlxp",
    "adr", "adrp", "b", "bl", "br", "blr", "cbz", "cbnz", "tbz", "tbnz",
    "ret", "retaa", "retab", "brk", "nop", "bti", "dmb", "dsb", "isb",
    "hint", "yield", "mrs", "msr", "paciasp", "pacibsp", "autiasp", "autibsp",
    "xpaclri", "paciaz", "pacibz", "autiaz", "autibz",
    "fmov", "fcmp", "fcmpe", "fcsel", "fcvt", "scvtf", "ucvtf", "fcvtzs",
    "fcvtzu", "fadd", "fsub", "fmul", "fdiv", "fneg", "fabs", "fsqrt",
    "fmadd", "fmsub", "fnmadd", "fnmsub", "fnmul", "fmax", "fmin",
    "fmaxnm", "fminnm", "frintm", "frintp", "frintz", "frinta", "frintn",
    "clz", "cls", "rbit", "rev", "rev16", "rev32", "extr", "adc", "adcs",
    "sbc", "sbcs", "ngc", "ngcs", "ccmp", "ccmn", "fccmp", "fccmpe",
    "b.eq", "b.ne", "b.cs", "b.hs", "b.cc", "b.lo", "b.mi", "b.pl",
    "b.vs", "b.vc", "b.hi", "b.ls", "b.ge", "b.lt", "b.gt", "b.le",
    "b.al", "b.nv", "svc",
    'ldadd', 'ldaddb', 'ldaddh', 'ldadda', 'ldaddab', 'ldaddah',
    'ldaddl', 'ldaddlb', 'ldaddlh', 'ldaddal', 'ldaddalb', 'ldaddalh',
    'ldclr', 'ldclrb', 'ldclrh', 'ldclra', 'ldclrab', 'ldclrah',
    'ldclrl', 'ldclrlb', 'ldclrlh', 'ldclral', 'ldclralb', 'ldclralh',
    'ldeor', 'ldeorb', 'ldeorh', 'ldeora', 'ldeorab', 'ldeorah',
    'ldeorl', 'ldeorlb', 'ldeorlh', 'ldeoral', 'ldeoralb', 'ldeoralh',
    'ldset', 'ldsetb', 'ldseth', 'ldseta', 'ldsetab', 'ldsetah',
    'ldsetl', 'ldsetlb', 'ldsetlh', 'ldsetal', 'ldsetalb', 'ldsetalh',
    'ldsmax', 'ldsmaxb', 'ldsmaxh', 'ldsmaxa', 'ldsmaxab', 'ldsmaxah',
    'ldsmaxl', 'ldsmaxlb', 'ldsmaxlh', 'ldsmaxal', 'ldsmaxalb', 'ldsmaxalh',
    'ldsmin', 'ldsminb', 'ldsminh', 'ldsmina', 'ldsminab', 'ldsminah',
    'ldsminl', 'ldsminlb', 'ldsminlh', 'ldsminal', 'ldsminalb', 'ldsminalh',
    'ldumax', 'ldumaxb', 'ldumaxh', 'ldumaxa', 'ldumaxab', 'ldumaxah',
    'ldumaxl', 'ldumaxlb', 'ldumaxlh', 'ldumaxal', 'ldumaxalb', 'ldumaxalh',
    'ldumin', 'lduminb', 'lduminh', 'ldumina', 'lduminab', 'lduminah',
    'lduminl', 'lduminlb', 'lduminlh', 'lduminal', 'lduminalb', 'lduminalh',
    'swp', 'swpb', 'swph', 'swpa', 'swpab', 'swpah',
    'swpl', 'swplb', 'swplh', 'swpal', 'swpalb', 'swpalh',
    'cas', 'casb', 'cash', 'casa', 'casab', 'casah',
    'casl', 'caslb', 'caslh', 'casal', 'casalb', 'casalh',
    'clrex', 'ldxrb', 'ldaxrb', 'ldxrh', 'ldaxrh', 'stxrb',
    'stlxrb', 'stxrh', 'stlxrh', 'stlur', 'stlurb', 'stlurh',
    'ldapr', 'ldaprb', 'ldaprh', 'smull', 'umull', 'smaddl',
    'umaddl', 'smsubl', 'umsubl', 'fcvtas', 'fcvtau', 'fcvtms',
    'fcvtmu', 'fcvtns', 'fcvtnu', 'fcvtps', 'fcvtpu', 'frintx',
    'frinti',
    "cnt", "addv", "umov", "movi",
    "uxtw",
)
_OPCODE_IDS = {name: index for index, name in enumerate(OPCODE_NAMES)}

TOKEN_REGISTER = 1
TOKEN_IMMEDIATE = 2
TOKEN_SYMBOL = 3
TOKEN_ADDRESS = 4
TOKEN_SHIFT = 5
TOKEN_CONDITION = 6
TOKEN_SYSTEM = 7
# Canonical floating immediate #0.0, all payloads zero. Other float forms
# need an explicit provenance extension rather than an integer-zero alias.
TOKEN_FLOAT_ZERO = 8
TOKEN_WIDE_IMMEDIATE = 9
TOKEN_VECTOR = 10
TOKEN_FLOAT_IMMEDIATE = 11
VECTOR_ARRANGEMENTS = ("8b", "16b", "4h", "8h", "2s", "4s", "1d", "2d", "b", "h", "s", "d")
FLOAT_IMMEDIATE_LITERALS = ("1.0", "2.0")
FLOAT_IMMEDIATE_ENCODINGS = (0x70, 0)

# Register tokens: first payload is exact spelling ID; other payloads are zero.
# X/W/S/D numeric classes are distinct. SP, ZR, FP and LR must not collapse.
REGISTER_X = 0
REGISTER_W = 32
REGISTER_D = 64
REGISTER_S = 96
REGISTER_SP = 128
REGISTER_WSP = 129
REGISTER_XZR = 130
REGISTER_WZR = 131
REGISTER_FP = 132
REGISTER_LR = 133
REGISTER_Q = 160
REGISTER_B = 192
REGISTER_H = 224

# Immediate token: (value, radix, flags). Radix is 10 or 16. A minus sign is
# normally implied by value<0; NEGATIVE_ZERO preserves an explicit #-0.
IMMEDIATE_HASH = 1
IMMEDIATE_NEGATIVE_ZERO = 2
IMMEDIATE_HEX_UPPER = 4
IMMEDIATE_EXPLICIT_PLUS = 8
IMMEDIATE_NEGATIVE = 16

# Address token: (mode, following child-token count, 0). Children retain
# spelling/order: base register, optional offset/index, optional shift.
ADDRESS_BASE = 0
ADDRESS_OFFSET = 1
ADDRESS_PREINDEX = 2
ADDRESS_POSTINDEX = 3
ADDRESS_REGISTER = 4

# Shift token: (kind, amount, explicit_amount). Absence of the whole token
# differs from an explicit LSL #0. Extend uses the same structural token.
SHIFT_NAMES = ("lsl", "lsr", "asr", "ror", "uxtb", "uxth", "uxtw", "uxtx", "sxtb", "sxth", "sxtw", "sxtx")
CONDITION_NAMES = ("eq", "ne", "cs", "hs", "cc", "lo", "mi", "pl", "vs", "vc", "hi", "ls", "ge", "lt", "gt", "le", "al", "nv")
SYSTEM_NAMES = ("nzcv", "tpidr_el0", "tpidrro_el0", "c", "j", "jc", "sy", "ish", "ishld", "ishst", "osh", "oshld", "oshst", "nsh", "nshld", "nshst")
# Symbol token: (owned symbol ID, decoration, signed addend).
SYMBOL_PLAIN = 0
SYMBOL_PAGE = 1
SYMBOL_PAGEOFF = 2
SYMBOL_GOTPAGE = 3
SYMBOL_GOTPAGEOFF = 4

# All supported producer spellings use two-space instruction indentation,
# comma-space operand separators and canonical token spelling. Other forms
# must not be guessed from their word. Empty/directive events are NOT headers.
FORM_CANONICAL = 1


def semantic_opcode_id(name: str) -> int:
    opcode = _OPCODE_IDS.get(name, 0)
    if opcode == 0:
        raise EncodeError("AArch64 semantic opcode is unsupported: " + name)
    return opcode


def semantic_register_id(name: str) -> int:
    if name == "sp":
        return REGISTER_SP
    if name == "wsp":
        return REGISTER_WSP
    if name == "xzr":
        return REGISTER_XZR
    if name == "wzr":
        return REGISTER_WZR
    if name == "fp":
        return REGISTER_FP
    if name == "lr":
        return REGISTER_LR
    if len(name) < 2 or name[0] not in ("x", "w", "d", "s", "q", "b", "h"):
        raise EncodeError("AArch64 semantic register is unsupported")
    # This validates a producer register operand, never an assembly line.
    number = name[1:]
    if not number.isdigit():
        raise EncodeError("AArch64 semantic register is unsupported")
    index = int(number)
    limit = 30 if name[0] in ("x", "w") else 31
    if index > limit or str(index) != number:
        raise EncodeError("AArch64 semantic register spelling is unsupported")
    base = REGISTER_X
    if name[0] == "w":
        base = REGISTER_W
    elif name[0] == "d":
        base = REGISTER_D
    elif name[0] == "s":
        base = REGISTER_S
    elif name[0] == "q":
        base = REGISTER_Q
    elif name[0] == "b":
        base = REGISTER_B
    elif name[0] == "h":
        base = REGISTER_H
    return base + index


def semantic_register_spelling(register: int) -> str:
    if 0 <= register <= 30:
        return "x" + str(register)
    if 32 <= register <= 62:
        return "w" + str(register - REGISTER_W)
    if 64 <= register <= 95:
        return "d" + str(register - REGISTER_D)
    if 96 <= register <= 127:
        return "s" + str(register - REGISTER_S)
    if REGISTER_Q <= register <= REGISTER_Q + 31:
        return "q" + str(register - REGISTER_Q)
    if REGISTER_B <= register <= REGISTER_B + 31:
        return "b" + str(register - REGISTER_B)
    if REGISTER_H <= register <= REGISTER_H + 31:
        return "h" + str(register - REGISTER_H)
    if register == REGISTER_SP:
        return "sp"
    if register == REGISTER_WSP:
        return "wsp"
    if register == REGISTER_XZR:
        return "xzr"
    if register == REGISTER_WZR:
        return "wzr"
    if register == REGISTER_FP:
        return "fp"
    if register == REGISTER_LR:
        return "lr"
    raise EncodeError("AArch64 semantic register ID is invalid")


def semantic_symbol_uses_register(symbol: str, register: int) -> bool:
    """Exact original token-scan spelling, not an ISA read-effect inference.

    Symbol names already belong to the existing intern table. A name such as
    x9.part contains token x9; L_x9 does not. W/X alias selection is the caller's
    original pass-specific policy (TLV and scratch scans differ for LR/FP).
    """
    spelling = semantic_register_spelling(register)
    token = ""
    for char in symbol:
        if char.isalnum() or char == "_":
            token += char
        else:
            if token == spelling:
                return True
            token = ""
    return token == spelling


def validate_semantic_token(kind: int, first: int, second: int, third: int) -> None:
    valid = False
    if kind == TOKEN_REGISTER:
        valid = (0 <= first <= 30 or 32 <= first <= 62 or 64 <= first <= REGISTER_LR or REGISTER_Q <= first <= REGISTER_H + 31) and second == 0 and third == 0
    elif kind == TOKEN_IMMEDIATE:
        valid = second in (10, 16) and 0 <= third <= 15
        if third & IMMEDIATE_NEGATIVE_ZERO:
            valid = valid and first == 0
        if third & IMMEDIATE_HEX_UPPER:
            valid = valid and second == 16
        if third & IMMEDIATE_EXPLICIT_PLUS:
            valid = valid and first >= 0 and not (third & IMMEDIATE_NEGATIVE_ZERO)
    elif kind == TOKEN_SYMBOL:
        valid = first >= 0 and SYMBOL_PLAIN <= second <= SYMBOL_GOTPAGEOFF
    elif kind == TOKEN_ADDRESS:
        valid = third == 0 and (
            (first == ADDRESS_BASE and second == 1)
            or (first in (ADDRESS_OFFSET, ADDRESS_PREINDEX, ADDRESS_POSTINDEX) and second == 2)
            or (first == ADDRESS_REGISTER and second in (2, 3))
        )
    elif kind == TOKEN_SHIFT:
        valid = 0 <= first < len(SHIFT_NAMES) and 0 <= second <= 63 and third in (0, 1)
    elif kind == TOKEN_CONDITION:
        valid = 0 <= first < len(CONDITION_NAMES) and second == 0 and third == 0
    elif kind == TOKEN_SYSTEM:
        valid = 0 <= first < len(SYSTEM_NAMES) and second == 0 and third == 0
    elif kind == TOKEN_FLOAT_ZERO:
        valid = first == 0 and second == 0 and third == 0
    elif kind == TOKEN_WIDE_IMMEDIATE:
        radix = third >> 8
        flags = third & 255
        valid = 0 <= first <= 0xFFFFFFFF and 0 <= second <= 0xFFFFFFFF
        valid = valid and radix in (10, 16) and flags <= 31
        if flags & IMMEDIATE_HEX_UPPER:
            valid = valid and radix == 16
        if flags & IMMEDIATE_NEGATIVE_ZERO:
            valid = valid and first == 0 and second == 0
        if flags & IMMEDIATE_EXPLICIT_PLUS:
            valid = valid and not (flags & (IMMEDIATE_NEGATIVE | IMMEDIATE_NEGATIVE_ZERO))
    elif kind == TOKEN_VECTOR:
        valid = 0 <= first <= 31 and 0 <= second < len(VECTOR_ARRANGEMENTS)
        if valid:
            arrangement = VECTOR_ARRANGEMENTS[second]
            if arrangement in ("b", "h", "s", "d"):
                lanes = 16 if arrangement == "b" else 8 if arrangement == "h" else 4 if arrangement == "s" else 2
                valid = 0 <= third < lanes
            else:
                valid = third == -1
    elif kind == TOKEN_FLOAT_IMMEDIATE:
        valid = 0 <= second < len(FLOAT_IMMEDIATE_LITERALS) and third == IMMEDIATE_HASH
        if valid:
            valid = first == FLOAT_IMMEDIATE_ENCODINGS[second]
    if not valid:
        raise EncodeError("AArch64 semantic operand is invalid or unsupported")


def semantic_immediate_token(value, radix: int = 10, flags: int = IMMEDIATE_HASH) -> CompilerInt4:
    if -(1 << 63) <= value < (1 << 63):
        validate_semantic_token(TOKEN_IMMEDIATE, value, radix, flags)
        return CompilerInt4(TOKEN_IMMEDIATE, value, radix, flags)
    magnitude = -value if value < 0 else value
    if magnitude >= 1 << 64:
        raise EncodeError("AArch64 semantic immediate exceeds its integer domain")
    wide_flags = flags | (IMMEDIATE_NEGATIVE if value < 0 else 0)
    form = (radix << 8) | wide_flags
    low = magnitude & 0xFFFFFFFF
    high = magnitude >> 32
    validate_semantic_token(TOKEN_WIDE_IMMEDIATE, low, high, form)
    return CompilerInt4(TOKEN_WIDE_IMMEDIATE, low, high, form)


def semantic_immediate_value(token: CompilerInt4):
    validate_semantic_token(token.first, token.second, token.third, token.fourth)
    if token.first == TOKEN_IMMEDIATE:
        return token.second
    if token.first != TOKEN_WIDE_IMMEDIATE:
        raise EncodeError("AArch64 semantic operand is not an integer immediate")
    magnitude = (token.third << 32) | token.second
    return -magnitude if token.fourth & IMMEDIATE_NEGATIVE else magnitude


def semantic_immediate_radix(token: CompilerInt4) -> int:
    validate_semantic_token(token.first, token.second, token.third, token.fourth)
    if token.first == TOKEN_IMMEDIATE:
        return token.third
    if token.first == TOKEN_WIDE_IMMEDIATE:
        return token.fourth >> 8
    raise EncodeError("AArch64 semantic operand is not an integer immediate")


def semantic_immediate_flags(token: CompilerInt4) -> int:
    validate_semantic_token(token.first, token.second, token.third, token.fourth)
    if token.first == TOKEN_IMMEDIATE:
        return token.fourth
    if token.first == TOKEN_WIDE_IMMEDIATE:
        return token.fourth & 255
    raise EncodeError("AArch64 semantic operand is not an integer immediate")


class AArch64InstructionSemantics:
    """One optional scope-owned provenance sidecar, indexed by record ID.

    Header=(opcode_id, form_flags, first_token, token_count). Token offsets and
    counts are in four-int records, not bytes. Opcode zero means UNPROVEN and
    is never eligible for an optimized pass. Construction is transactional at
    the producer: snapshot lengths before tokens/header/encoded record append,
    then truncate both arenas if the encoded record cannot be published.
    """

    def __init__(self) -> None:
        self.headers: CompilerIntArena = CompilerIntArena()
        try:
            self.tokens: CompilerIntArena = CompilerIntArena()
        except Exception:
            self.headers.close()
            raise
        self.closed: bool = False

    def token_count(self) -> int:
        return len(self.tokens) // 4

    def append_token(self, kind: int, first: int, second: int = 0, third: int = 0) -> None:
        if self.closed:
            raise RuntimeError("AArch64 instruction semantics are closed")
        validate_semantic_token(kind, first, second, third)
        self.tokens.append4(kind, first, second, third)

    def append_header(self, opcode_id: int, form_flags: int, first_token: int, token_count: int) -> None:
        if self.closed:
            raise RuntimeError("AArch64 instruction semantics are closed")
        if opcode_id <= 0 or opcode_id >= len(OPCODE_NAMES) or form_flags != FORM_CANONICAL:
            raise EncodeError("AArch64 instruction provenance is unproven")
        if first_token < 0 or token_count < 0 or first_token + token_count > self.token_count():
            raise EncodeError("AArch64 semantic operand span is invalid")
        self._validate_tokens(first_token, token_count)
        self.headers.append4(opcode_id, form_flags, first_token, token_count)

    def append_unproven(self) -> None:
        if self.closed:
            raise RuntimeError("AArch64 instruction semantics are closed")
        self.headers.append4(0, 0, 0, 0)

    def require_header(self, record_id: int) -> CompilerInt4:
        if self.closed:
            raise RuntimeError("AArch64 instruction semantics are closed")
        if len(self.headers) % 4 or len(self.tokens) % 4:
            raise EncodeError("AArch64 semantic arenas are truncated")
        if record_id < 0 or record_id >= len(self.headers) // 4:
            raise EncodeError("AArch64 instruction provenance is missing")
        header: CompilerInt4 = self.headers.get4_unchecked(record_id)
        if header.first <= 0 or header.first >= len(OPCODE_NAMES) or header.second != FORM_CANONICAL:
            raise EncodeError("AArch64 instruction provenance is unproven")
        if header.third < 0 or header.fourth < 0 or header.third + header.fourth > self.token_count():
            raise EncodeError("AArch64 semantic operand span is invalid")
        self._validate_tokens(header.third, header.fourth)
        return header

    def _validate_tokens(self, first: int, count: int) -> None:
        index = first
        end = first + count
        while index < end:
            token: CompilerInt4 = self.tokens.get4_unchecked(index)
            validate_semantic_token(token.first, token.second, token.third, token.fourth)
            if token.first == TOKEN_ADDRESS:
                children = token.third
                if index + children >= end:
                    raise EncodeError("AArch64 semantic address is truncated")
                base: CompilerInt4 = self.tokens.get4_unchecked(index + 1)
                if base.first != TOKEN_REGISTER or not (
                    0 <= base.second <= 30 or base.second in (REGISTER_SP, REGISTER_FP, REGISTER_LR)
                ):
                    raise EncodeError("AArch64 semantic address base is not an X/SP register")
                if children >= 2:
                    offset: CompilerInt4 = self.tokens.get4_unchecked(index + 2)
                    if token.second == ADDRESS_REGISTER:
                        if offset.first != TOKEN_REGISTER or not (
                            0 <= offset.second <= 30 or 32 <= offset.second <= 62
                            or offset.second in (REGISTER_XZR, REGISTER_WZR, REGISTER_FP, REGISTER_LR)
                        ):
                            raise EncodeError("AArch64 semantic address index is invalid")
                    elif offset.first not in (TOKEN_IMMEDIATE, TOKEN_WIDE_IMMEDIATE) and not (
                        token.second == ADDRESS_OFFSET and offset.first == TOKEN_SYMBOL
                    ):
                        raise EncodeError("AArch64 semantic address offset is invalid")
                if children == 3:
                    shift: CompilerInt4 = self.tokens.get4_unchecked(index + 3)
                    if shift.first != TOKEN_SHIFT:
                        raise EncodeError("AArch64 semantic address shift is invalid")
                child_index = index + 1
                while child_index <= index + children:
                    child: CompilerInt4 = self.tokens.get4_unchecked(child_index)
                    if child.first not in (TOKEN_REGISTER, TOKEN_IMMEDIATE, TOKEN_WIDE_IMMEDIATE, TOKEN_SYMBOL, TOKEN_SHIFT):
                        raise EncodeError("AArch64 semantic address operand is unsupported")
                    child_index += 1
            index += 1

    def operand(self, record_id: int, index: int) -> CompilerInt4:
        header: CompilerInt4 = self.require_header(record_id)
        if index < 0 or index >= header.fourth:
            raise EncodeError("AArch64 semantic operand index is invalid")
        return self.tokens.get4_unchecked(header.third + index)

    def require_complete(self, records: CompilerIntArena, symbol_count: int) -> None:
        """Check one-to-one record IDs before any optimizing pass is entered.

        Noninstruction event records occupy header slots too, marked unproven;
        their event family, payload and replay semantics belong to fragments.
        Instruction producers own word/provenance agreement and must encode
        both from the same selected operands, never recover tags from a word.
        """
        if self.closed:
            raise RuntimeError("AArch64 instruction semantics are closed")
        if len(records) % 4 or len(self.tokens) % 4 or len(self.headers) != len(records) or symbol_count < 0:
            raise EncodeError("AArch64 provenance record IDs do not match encoded records")
        record_id = 0
        while record_id < len(records) // 4:
            encoded: CompilerInt4 = records.get4_unchecked(record_id)
            if encoded.second >= 0:
                header: CompilerInt4 = self.require_header(record_id)
                index = 0
                while index < header.fourth:
                    token: CompilerInt4 = self.tokens.get4_unchecked(header.third + index)
                    if token.first == TOKEN_SYMBOL and token.second >= symbol_count:
                        raise EncodeError("AArch64 semantic symbol ID is invalid")
                    index += 1
            else:
                header: CompilerInt4 = self.headers.get4_unchecked(record_id)
                if header.first != 0 or header.second != 0 or header.third != 0 or header.fourth != 0:
                    raise EncodeError("AArch64 event record has instruction provenance")
            record_id += 1

    def reset(self) -> None:
        if self.closed:
            raise RuntimeError("AArch64 instruction semantics are closed")
        self.headers.clear()
        self.tokens.clear()

    def close(self) -> None:
        if self.closed:
            return
        self.tokens.close()
        self.headers.close()
        self.closed = True


def append_semantic_instruction(
    fragments, semantics: AArch64InstructionSemantics, destination: CompilerInt2,
    encoded: CompilerInt4, opcode_id: int, form_flags: int,
    operands: list[CompilerInt4],
) -> int:
    """Publish one selected replacement, with no retained operand objects.

    Header IDs must already cover all earlier records (including event slots).
    Only failures before the final span-root commit are rolled back. No
    instruction is emitted for unselected eagerly constructed alternatives.
    """
    fragments._require_open()
    fragments.spans.length(destination)
    old_records = len(fragments.records)
    old_headers = len(semantics.headers)
    old_tokens = len(semantics.tokens)
    if old_records % 4 or old_headers != old_records:
        raise EncodeError("AArch64 provenance record IDs do not match encoded records")
    if encoded.first < 0 or encoded.first > 0xFFFFFFFF or encoded.second not in (1, 2, 3, 4):
        raise EncodeError("AArch64 semantic replacement is not an encoded instruction")
    if encoded.third not in (0, -1, -19, -26, 1, 2, 3, 4, 5):
        raise EncodeError("AArch64 semantic replacement relocation is invalid")
    if encoded.third < 0:
        _validate_text_fixup_word(encoded.first, encoded.third)
    if encoded.third != 0 and (encoded.fourth < 0 or encoded.fourth >= len(fragments.symbol_names)):
        raise EncodeError("AArch64 semantic replacement symbol ID is invalid")
    if encoded.third == 0 and encoded.fourth != -1:
        raise EncodeError("AArch64 semantic replacement has an unexpected symbol")
    try:
        for token in operands:
            if token.first == TOKEN_SYMBOL and token.second >= len(fragments.symbol_names):
                raise EncodeError("AArch64 semantic operand symbol ID is invalid")
            semantics.append_token(token.first, token.second, token.third, token.fourth)
        semantics.append_header(opcode_id, form_flags, old_tokens // 4, len(operands))
        fragments._append_record(destination, encoded)
    except Exception:
        fragments.records.truncate(old_records)
        semantics.headers.truncate(old_headers)
        semantics.tokens.truncate(old_tokens)
        raise
    return old_records // 4


def append_semantic_event(
    fragments, semantics: AArch64InstructionSemantics, destination: CompilerInt2,
    event: CompilerInt4,
) -> int:
    """Append a zero-width/module event with its aligned noninstruction slot."""
    fragments._require_open()
    fragments.spans.length(destination)
    old_records = len(fragments.records)
    old_headers = len(semantics.headers)
    if old_records % 4 or old_headers != old_records:
        raise EncodeError("AArch64 provenance record IDs do not match encoded records")
    if event.second not in (-1, -2, -3, -4):
        raise EncodeError("AArch64 semantic event family is unsupported")
    try:
        semantics.append_unproven()
        fragments._append_record(destination, event)
    except Exception:
        fragments.records.truncate(old_records)
        semantics.headers.truncate(old_headers)
        raise
    return old_records // 4
