"""Encode typed optimizer replacements without instruction text adapters."""

from __future__ import annotations

from . import arm64_encode as encoder
from .arm64_encode_producer import encode_producer_immediate, encode_producer_registers, encode_producer_fp_immediate
from .self_backend_aarch64_semantics import (
    OPCODE_NAMES, CONDITION_NAMES, SYSTEM_NAMES, SHIFT_NAMES,
    TOKEN_REGISTER, TOKEN_IMMEDIATE, TOKEN_WIDE_IMMEDIATE, TOKEN_SYMBOL,
    TOKEN_ADDRESS, TOKEN_SHIFT, TOKEN_CONDITION, TOKEN_SYSTEM, TOKEN_FLOAT_ZERO, TOKEN_FLOAT_IMMEDIATE,
    ADDRESS_BASE, ADDRESS_OFFSET, ADDRESS_PREINDEX, ADDRESS_POSTINDEX,
    SYMBOL_PLAIN, SYMBOL_PAGE, SYMBOL_PAGEOFF, SYMBOL_GOTPAGE, SYMBOL_GOTPAGEOFF,
    semantic_register_spelling, semantic_immediate_value, validate_semantic_token,
)
from .self_backend_value_arena import CompilerInt4


def _register(token: CompilerInt4) -> str:
    if token.first != TOKEN_REGISTER:
        raise encoder.EncodeError("typed encoding requires a register operand")
    return semantic_register_spelling(token.second)


def _gpr(token: CompilerInt4, *, allow_sp: bool = False, allow_zr: bool = False) -> int:
    name = _register(token)
    if (name == "sp" and allow_sp) or (name == "xzr" and allow_zr):
        return 31
    if name == "fp":
        return 29
    if name == "lr":
        return 30
    if name.startswith("x") and name[1:].isdigit():
        return int(name[1:])
    raise encoder.EncodeError("typed encoding requires an X register")


def encode_semantic_instruction(opcode_id: int, operands: list[CompilerInt4]) -> CompilerInt4:
    """Return (word,family,fixup,symbol ID) in the caller's symbol namespace.

    Symbol IDs are retained verbatim, never interned or remapped here. The
    scope-owned publisher validates their bounds. Tokens are original typed
    operands, not strings produced by rendering/reparsing an instruction.
    """
    if opcode_id <= 0 or opcode_id >= len(OPCODE_NAMES):
        raise encoder.EncodeError("typed encoding opcode is unproven")
    for operand in operands:
        validate_semantic_token(operand.first, operand.second, operand.third, operand.fourth)
    mnemonic = OPCODE_NAMES[opcode_id]
    count = len(operands)
    family = encoder.EMITTED_INSTRUCTION_SCALAR
    kind = 0
    symbol = -1
    if mnemonic in ("b", "bl") or mnemonic.startswith("b.") or mnemonic in ("cbz", "cbnz"):
        register = ""
        if mnemonic in ("cbz", "cbnz"):
            if count != 2:
                raise encoder.EncodeError("typed zero branch operands are invalid")
            register = _register(operands[0])
        elif count != 1:
            raise encoder.EncodeError("typed branch operands are invalid")
        target = operands[-1]
        if target.first != TOKEN_SYMBOL or target.third != SYMBOL_PLAIN or target.fourth != 0:
            raise encoder.EncodeError("typed branch target is invalid")
        symbol = target.second
        if mnemonic == "bl":
            word = 0x94000000
            kind = encoder.STRUCTURED_FIXUP_CALL
            family = encoder.EMITTED_INSTRUCTION_CALL
        else:
            word = encoder.encode_emitted_branch_base_parts(mnemonic, register)
            kind = encoder.STRUCTURED_FIXUP_BRANCH26 if mnemonic == "b" else encoder.STRUCTURED_FIXUP_BRANCH19
    elif mnemonic in ("ldp", "stp"):
        if count not in (4, 5) or operands[2].first != TOKEN_ADDRESS:
            raise encoder.EncodeError("typed pair operands are invalid")
        address = operands[2]
        modes = {ADDRESS_BASE: "signed", ADDRESS_OFFSET: "signed", ADDRESS_PREINDEX: "pre", ADDRESS_POSTINDEX: "post"}
        if address.second not in modes or address.third != count - 3:
            raise encoder.EncodeError("typed pair address is invalid")
        offset = 0 if count == 4 else semantic_immediate_value(operands[4])
        if offset < -512 or offset > 504:
            raise encoder.EncodeError("typed pair offset is out of range")
        word = encoder._enc_ldstp(mnemonic == "ldp", modes[address.second], _gpr(operands[0], allow_zr=True), _gpr(operands[1], allow_zr=True), _gpr(operands[3], allow_sp=True), offset, True)
    elif count >= 3 and operands[1].first == TOKEN_ADDRESS:
        address = operands[1]
        if address.second not in (ADDRESS_BASE, ADDRESS_OFFSET) or address.third != count - 2:
            raise encoder.EncodeError("typed scalar memory address is unsupported")
        register = _register(operands[0])
        base = _register(operands[2])
        if count == 4 and operands[3].first == TOKEN_SYMBOL:
            target = operands[3]
            if mnemonic != "ldr" or target.third != SYMBOL_GOTPAGEOFF or target.fourth != 0:
                raise encoder.EncodeError("typed symbolic load is unsupported")
            word = encoder.encode_emitted_ldr_got_pageoff_parts(register, base)
            kind = encoder.STRUCTURED_RELOCATION_GOT_LOAD_PAGEOFF12
            symbol = target.second
        else:
            if count not in (3, 4):
                raise encoder.EncodeError("typed scalar memory operands are invalid")
            offset = 0 if count == 3 else semantic_immediate_value(operands[3])
            if offset < -256 or offset > 65520:
                raise encoder.EncodeError("typed scalar memory offset is out of range")
            word = encoder.encode_emitted_load_store_parts(mnemonic, register, base, offset)
            if mnemonic in ("ldur", "stur", "ldurb", "sturb", "ldurh", "sturh"):
                family = encoder.EMITTED_INSTRUCTION_UNSCALED
    elif mnemonic == "adrp":
        if count != 2 or operands[1].first != TOKEN_SYMBOL or operands[1].fourth != 0:
            raise encoder.EncodeError("typed page address is invalid")
        target = operands[1]
        if target.third not in (SYMBOL_PAGE, SYMBOL_GOTPAGE):
            raise encoder.EncodeError("typed page decoration is invalid")
        word = encoder.encode_emitted_adrp_parts(_register(operands[0]))
        kind = encoder.STRUCTURED_RELOCATION_PAGE21 if target.third == SYMBOL_PAGE else encoder.STRUCTURED_RELOCATION_GOT_LOAD_PAGE21
        symbol = target.second
    elif mnemonic == "add" and count == 3 and operands[2].first == TOKEN_SYMBOL:
        target = operands[2]
        if target.third != SYMBOL_PAGEOFF or target.fourth != 0:
            raise encoder.EncodeError("typed page-offset address is invalid")
        word = encoder.encode_emitted_add_pageoff_parts(_register(operands[0]), _register(operands[1]))
        kind = encoder.STRUCTURED_RELOCATION_PAGEOFF12
        symbol = target.second
    elif mnemonic == "mov" and count == 2 and operands[1].first == TOKEN_REGISTER:
        word = encoder.encode_emitted_move_register_parts(_register(operands[0]), _register(operands[1]))
        family = encoder.EMITTED_INSTRUCTION_MOVE
    elif mnemonic in ("movz", "movn", "movk"):
        if count not in (2, 3):
            raise encoder.EncodeError("typed move-wide operands are invalid")
        shift = 0
        if count == 3:
            if operands[2].first != TOKEN_SHIFT or SHIFT_NAMES[operands[2].second] != "lsl":
                raise encoder.EncodeError("typed move-wide shift is invalid")
            shift = operands[2].third
        word = encoder.encode_emitted_movewide_parts(mnemonic, _register(operands[0]), semantic_immediate_value(operands[1]), shift)
        family = encoder.EMITTED_INSTRUCTION_MOVE
    elif mnemonic in ("csel", "csinv", "csinc", "csneg", "fcsel"):
        if count != 4 or operands[3].first != TOKEN_CONDITION:
            raise encoder.EncodeError("typed conditional select operands are invalid")
        condition = CONDITION_NAMES[operands[3].second]
        operation = "csel" if mnemonic in ("csinc", "csneg") else mnemonic
        word = encode_producer_registers(operation, _register(operands[0]), _register(operands[1]), _register(operands[2]), condition)
        if mnemonic == "csinc":
            word |= 1 << 10
        elif mnemonic == "csneg":
            word |= (1 << 30) | (1 << 10)
    elif mnemonic == "fmov" and count == 2 and operands[1].first == TOKEN_FLOAT_IMMEDIATE:
        word = encode_producer_fp_immediate(_register(operands[0]), operands[1].second)
    elif mnemonic == "cmp":
        if count != 2:
            raise encoder.EncodeError("typed compare operands are invalid")
        left = _register(operands[0])
        if operands[1].first == TOKEN_REGISTER:
            word = encoder.encode_emitted_compare_register_parts(left, _register(operands[1]))
        else:
            word = encoder.encode_emitted_compare_immediate_parts(left, semantic_immediate_value(operands[1]))
    elif mnemonic == "cset":
        if count != 2 or operands[1].first != TOKEN_CONDITION:
            raise encoder.EncodeError("typed cset operands are invalid")
        word = encoder.encode_emitted_cset_parts(_register(operands[0]), CONDITION_NAMES[operands[1].second])
    elif mnemonic in ("mrs", "msr"):
        if count != 2:
            raise encoder.EncodeError("typed system register operands are invalid")
        register = operands[0] if mnemonic == "mrs" else operands[1]
        system = operands[1] if mnemonic == "mrs" else operands[0]
        if system.first != TOKEN_SYSTEM or SYSTEM_NAMES[system.second] != "nzcv":
            raise encoder.EncodeError("typed system register is unsupported")
        word = (0xD53B4200 if mnemonic == "mrs" else 0xD51B4200) | _gpr(register, allow_zr=True)
    elif mnemonic in ("br", "blr") and count == 1:
        word = (0xD61F0000 if mnemonic == "br" else 0xD63F0000) | (_gpr(operands[0]) << 5)
    elif count == 0 and mnemonic in ("nop", "ret", "paciasp", "autiasp"):
        word = {"nop": 0xD503201F, "ret": 0xD65F03C0, "paciasp": 0xD503233F, "autiasp": 0xD50323BF}[mnemonic]
    elif mnemonic in ("add", "sub") and count == 3 and operands[2].first in (TOKEN_IMMEDIATE, TOKEN_WIDE_IMMEDIATE):
        word = encoder.encode_emitted_addsub_immediate_parts(mnemonic, _register(operands[0]), _register(operands[1]), semantic_immediate_value(operands[2]))
    elif count in (2, 3) and operands[-1].first in (TOKEN_IMMEDIATE, TOKEN_WIDE_IMMEDIATE):
        left = "" if count == 2 else _register(operands[1])
        word = encode_producer_immediate(mnemonic, _register(operands[0]), left, semantic_immediate_value(operands[-1]))
        if mnemonic == "mov":
            family = encoder.EMITTED_INSTRUCTION_MOVE
    else:
        registers = []
        shift = 0
        for operand in operands:
            if operand.first == TOKEN_REGISTER:
                registers.append(_register(operand))
            elif operand.first == TOKEN_SHIFT and SHIFT_NAMES[operand.second] == "lsl":
                shift = operand.third
            else:
                raise encoder.EncodeError("typed replacement operands are unsupported")
        if len(registers) < 2 or len(registers) > 4:
            raise encoder.EncodeError("typed replacement register count is unsupported")
        word = encode_producer_registers(mnemonic, registers[0], registers[1], registers[2] if len(registers) > 2 else "", registers[3] if len(registers) > 3 else "", shift)
    return CompilerInt4(word, family, kind, symbol)
