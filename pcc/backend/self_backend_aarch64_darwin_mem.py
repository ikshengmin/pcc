from __future__ import annotations

"""AArch64 Darwin memory/opcode helpers for the self backend."""

from . import BackendUnavailable
from .arm64_encode import (
    EMITTED_INSTRUCTION_CALL,
    EMITTED_INSTRUCTION_SCALAR,
    EMITTED_INSTRUCTION_UNSCALED,
    EMITTED_INSTRUCTION_MOVE,
    EncodeError,
    STRUCTURED_RELOCATION_GOT_LOAD_PAGE21,
    STRUCTURED_RELOCATION_GOT_LOAD_PAGEOFF12,
    STRUCTURED_RELOCATION_PAGE21,
    STRUCTURED_RELOCATION_PAGEOFF12,
    encode_emitted_addsub_immediate_parts,
    encode_emitted_addsub_register_parts,
    encode_emitted_branch_base_parts,
    encode_emitted_compare_immediate_parts,
    encode_emitted_compare_register_parts,
    encode_emitted_cset_parts,
    encode_emitted_frame_pair_parts,
    encode_emitted_add_pageoff_parts,
    encode_emitted_adrp_parts,
    encode_emitted_ldr_got_pageoff_parts,
    encode_emitted_load_store_parts,
    encode_emitted_move_register_parts,
    encode_emitted_movewide_parts,
)
from .self_backend_ir import TypeDesc
from .self_backend_value_arena import CompilerInt4, CompilerIntArena
from . import self_backend_aarch64_semantics as semantics
from .aarch64_fp_immediates import direct_fp_immediate_encoding
from .arm64_encode_producer import (
    encode_producer_atomic,
    encode_producer_system,
    encode_producer_indirect_branch,
    encode_producer_pair,
    encode_producer_fp_immediate,
    encode_producer_vector,
    encode_producer_immediate,
    encode_producer_registers,
)


DIRECT_INSTRUCTION_PLACEHOLDER = "  pcc_direct_instruction"
# Preserve call boundaries until the packed words are consumed. Both markers
# still consume exactly one record from the same four-scalar instruction arena.
DIRECT_CALL_INSTRUCTION_PLACEHOLDER = "  pcc_direct_call_instruction"
DIRECT_INDIRECT_CALL_INSTRUCTION_PLACEHOLDER = "  pcc_direct_indirect_call_instruction"
_DIRECT_INSTRUCTION_RECORDS: CompilerIntArena | None = None
_DIRECT_INSTRUCTION_SEMANTICS: semantics.AArch64InstructionSemantics | None = None
_DIRECT_INSTRUCTION_SYMBOL_NAMES: list[str] = []
_DIRECT_INSTRUCTION_SYMBOL_IDS: dict[str, int] = {}


def begin_direct_instruction_capture(*, capture_semantics: bool = False) -> None:
    """Start one process-local structured PCO instruction stream."""

    global _DIRECT_INSTRUCTION_RECORDS
    global _DIRECT_INSTRUCTION_SEMANTICS
    global _DIRECT_INSTRUCTION_SYMBOL_NAMES
    global _DIRECT_INSTRUCTION_SYMBOL_IDS
    if _DIRECT_INSTRUCTION_RECORDS is not None:
        raise BackendUnavailable("direct instruction capture is already active")
    owner = semantics.AArch64InstructionSemantics() if capture_semantics else None
    try:
        _DIRECT_INSTRUCTION_RECORDS = CompilerIntArena()
    except Exception:
        if owner is not None:
            owner.close()
        raise
    _DIRECT_INSTRUCTION_SEMANTICS = owner
    _DIRECT_INSTRUCTION_SYMBOL_NAMES = []
    _DIRECT_INSTRUCTION_SYMBOL_IDS = {}


def borrow_direct_instruction_records() -> CompilerIntArena:
    records = _DIRECT_INSTRUCTION_RECORDS
    if records is None:
        raise RuntimeError("direct instruction capture is not active")
    # Keep the module-global owner live while the caller consumes this
    # borrowed arena.  Clearing the global here frees the arena under pcc1
    # before the borrowed local can be returned; CPython masks that bug by
    # retaining ordinary local assignments.
    return records


def borrow_direct_instruction_symbol_names() -> list[str]:
    if _DIRECT_INSTRUCTION_RECORDS is None:
        raise RuntimeError("direct instruction capture is not active")
    return _DIRECT_INSTRUCTION_SYMBOL_NAMES


def end_direct_instruction_capture() -> None:
    global _DIRECT_INSTRUCTION_RECORDS
    global _DIRECT_INSTRUCTION_SEMANTICS
    global _DIRECT_INSTRUCTION_SYMBOL_NAMES
    global _DIRECT_INSTRUCTION_SYMBOL_IDS
    if _DIRECT_INSTRUCTION_SEMANTICS is not None:
        _DIRECT_INSTRUCTION_SEMANTICS.close()
    _DIRECT_INSTRUCTION_SEMANTICS = None
    if _DIRECT_INSTRUCTION_RECORDS is not None:
        _DIRECT_INSTRUCTION_RECORDS.close()
    _DIRECT_INSTRUCTION_RECORDS = None
    _DIRECT_INSTRUCTION_SYMBOL_NAMES = []
    _DIRECT_INSTRUCTION_SYMBOL_IDS = {}


def require_direct_instruction_capture_idle() -> None:
    if _DIRECT_INSTRUCTION_RECORDS is not None:
        raise BackendUnavailable("direct instruction capture is already active")


def direct_instruction_capture_active() -> bool:
    return _DIRECT_INSTRUCTION_RECORDS is not None


def emitted_memory_instruction_line(
    mnemonic: str,
    register: str,
    base: str,
    offset: int = 0,
    *, explicit_offset: bool = False,
) -> str:
    """Return exact ASM text or one constant placeholder plus a packed word."""

    records = _DIRECT_INSTRUCTION_RECORDS
    if records is not None:
        if mnemonic in ("ldar", "stlr", "ldarb", "ldarh", "stlrb", "stlrh"):
            if offset != 0 or not register.startswith(("w", "x")):
                raise EncodeError("atomic scalar memory requires a bare address and integer register")
            # Reuse the established typed register/address validation.
            # Only Rn/Rt and the W/X size bit survive; acquire/release
            # opcodes come from the same ISA words as the ASM encoder.
            access_op = "ldr"
            if mnemonic.endswith("b"):
                access_op = "ldrb"
            elif mnemonic.endswith("h"):
                access_op = "ldrh"
            ordinary = encode_emitted_load_store_parts(access_op, register, base, 0)
            opcode = {
                "ldar": 0x88DFFC00, "stlr": 0x889FFC00,
                "ldarb": 0x08DFFC00, "ldarh": 0x48DFFC00,
                "stlrb": 0x089FFC00, "stlrh": 0x489FFC00,
            }[mnemonic]
            word = opcode | (ordinary & 0x40000000) | (ordinary & 0x3FF)
        else:
            word = encode_emitted_load_store_parts(
                mnemonic,
                register,
                base,
                offset,
            )
        family = (
            EMITTED_INSTRUCTION_UNSCALED
            if mnemonic in ("ldur", "stur", "ldurb", "sturb", "ldurh", "sturh")
            else EMITTED_INSTRUCTION_SCALAR
        )
        _capture_direct_instruction(
            word, family, 0, -1, mnemonic,
            [
                _semantic_register(register),
                CompilerInt4(
                    semantics.TOKEN_ADDRESS,
                    semantics.ADDRESS_BASE if offset == 0 and not explicit_offset else semantics.ADDRESS_OFFSET,
                    1 if offset == 0 and not explicit_offset else 2, 0,
                ),
                _semantic_register(base),
            ] + ([] if offset == 0 and not explicit_offset else [_semantic_immediate(offset)]),
        )
        return DIRECT_INSTRUCTION_PLACEHOLDER
    suffix = "" if offset == 0 and not explicit_offset else ", #" + str(offset)
    return "  " + mnemonic + " " + register + ", [" + base + suffix + "]"


def emitted_move_register_line(destination: str, source: str) -> str:
    records = _DIRECT_INSTRUCTION_RECORDS
    if records is not None:
        word = encode_emitted_move_register_parts(destination, source)
        _capture_direct_instruction(
            word,
            EMITTED_INSTRUCTION_MOVE,
            0,
            -1,
            "mov",
            [_semantic_register(destination), _semantic_register(source)],
        )
        return DIRECT_INSTRUCTION_PLACEHOLDER
    return "  mov " + destination + ", " + source


def emitted_movewide_instruction_line(
    mnemonic: str,
    destination: str,
    immediate: int,
    shift: int = 0,
    explicit_shift: bool = False,
) -> str:
    records = _DIRECT_INSTRUCTION_RECORDS
    if records is not None:
        word = encode_emitted_movewide_parts(
            mnemonic,
            destination,
            immediate,
            shift,
        )
        _capture_direct_instruction(
            word,
            EMITTED_INSTRUCTION_MOVE,
            0,
            -1,
            mnemonic,
            [_semantic_register(destination), _semantic_immediate(immediate)] + ([CompilerInt4(semantics.TOKEN_SHIFT, 0, shift, 1)] if shift != 0 or explicit_shift else []),
        )
        return DIRECT_INSTRUCTION_PLACEHOLDER
    suffix = "" if shift == 0 and not explicit_shift else ", lsl #" + str(shift)
    return (
        "  "
        + mnemonic
        + " "
        + destination
        + ", #"
        + str(immediate)
        + suffix
    )


def emitted_addsub_register_line(
    mnemonic: str,
    destination: str,
    left: str,
    right: str,
) -> str:
    records = _DIRECT_INSTRUCTION_RECORDS
    if records is not None:
        word = encode_emitted_addsub_register_parts(
            mnemonic,
            destination,
            left,
            right,
        )
        _capture_direct_instruction(
            word,
            EMITTED_INSTRUCTION_SCALAR,
            0,
            -1,
            mnemonic,
            [_semantic_register(destination), _semantic_register(left), _semantic_register(right)],
        )
        return DIRECT_INSTRUCTION_PLACEHOLDER
    return (
        "  "
        + mnemonic
        + " "
        + destination
        + ", "
        + left
        + ", "
        + right
    )


def emitted_addsub_immediate_line(
    mnemonic: str,
    destination: str,
    left: str,
    immediate: int,
) -> str:
    records = _DIRECT_INSTRUCTION_RECORDS
    if records is not None:
        word = encode_emitted_addsub_immediate_parts(
            mnemonic,
            destination,
            left,
            immediate,
        )
        _capture_direct_instruction(
            word,
            EMITTED_INSTRUCTION_SCALAR,
            0,
            -1,
            mnemonic,
            [_semantic_register(destination), _semantic_register(left), _semantic_immediate(immediate)],
        )
        return DIRECT_INSTRUCTION_PLACEHOLDER
    return (
        "  "
        + mnemonic
        + " "
        + destination
        + ", "
        + left
        + ", #"
        + str(immediate)
    )


def emitted_compare_register_line(left: str, right: str) -> str:
    records = _DIRECT_INSTRUCTION_RECORDS
    if records is not None:
        _capture_direct_instruction(
            encode_emitted_compare_register_parts(left, right),
            EMITTED_INSTRUCTION_SCALAR,
            0,
            -1,
            "cmp",
            [_semantic_register(left), _semantic_register(right)],
        )
        return DIRECT_INSTRUCTION_PLACEHOLDER
    return "  cmp " + left + ", " + right


def emitted_compare_immediate_line(left: str, immediate: int) -> str:
    records = _DIRECT_INSTRUCTION_RECORDS
    if records is not None:
        _capture_direct_instruction(
            encode_emitted_compare_immediate_parts(left, immediate),
            EMITTED_INSTRUCTION_SCALAR,
            0,
            -1,
            "cmp",
            [_semantic_register(left), _semantic_immediate(immediate)],
        )
        return DIRECT_INSTRUCTION_PLACEHOLDER
    return "  cmp " + left + ", #" + str(immediate)


def emitted_cset_line(destination: str, condition: str) -> str:
    records = _DIRECT_INSTRUCTION_RECORDS
    if records is not None:
        _capture_direct_instruction(
            encode_emitted_cset_parts(destination, condition),
            EMITTED_INSTRUCTION_SCALAR,
            0,
            -1,
            "cset",
            [_semantic_register(destination), _semantic_condition(condition)],
        )
        return DIRECT_INSTRUCTION_PLACEHOLDER
    return "  cset " + destination + ", " + condition


def emitted_direct_call_line(target: str) -> str:
    records = _DIRECT_INSTRUCTION_RECORDS
    if records is not None:
        symbol_id = _capture_direct_symbol(target)
        _capture_direct_instruction(
            0,
            EMITTED_INSTRUCTION_CALL,
            0,
            symbol_id,
            "bl",
            [CompilerInt4(semantics.TOKEN_SYMBOL, symbol_id, semantics.SYMBOL_PLAIN, 0)],
        )
        return DIRECT_CALL_INSTRUCTION_PLACEHOLDER
    return "  bl " + target


def emitted_branch_line(
    mnemonic: str,
    target: str,
    register: str = "",
) -> str:
    records = _DIRECT_INSTRUCTION_RECORDS
    if records is not None:
        target_id = _capture_direct_symbol(target)
        width = 26 if mnemonic == "b" else 19
        _capture_direct_instruction(
            encode_emitted_branch_base_parts(mnemonic, register),
            EMITTED_INSTRUCTION_SCALAR,
            -width,
            target_id,
            mnemonic,
            ([] if not register else [_semantic_register(register)]) + [CompilerInt4(semantics.TOKEN_SYMBOL, target_id, semantics.SYMBOL_PLAIN, 0)],
        )
        return DIRECT_INSTRUCTION_PLACEHOLDER
    if mnemonic == "b" or mnemonic.startswith("b."):
        return "  " + mnemonic + " " + target
    return "  " + mnemonic + " " + register + ", " + target


def emitted_fixed_instruction_line(mnemonic: str) -> str:
    records = _DIRECT_INSTRUCTION_RECORDS
    if records is not None:
        opcode = mnemonic
        operands: list[CompilerInt4] = []
        if mnemonic == "brk #0":
            word = 0xD4200000
            opcode = "brk"
            operands = [_semantic_immediate(0)]
        elif mnemonic == "clrex":
            word = 0xD5033F5F
        elif mnemonic == "dmb ish":
            word = 0xD5033BBF
            opcode = "dmb"
            operands = [CompilerInt4(semantics.TOKEN_SYSTEM, semantics.SYSTEM_NAMES.index("ish"), 0, 0)]
        elif mnemonic == "ret":
            word = 0xD65F03C0
        elif mnemonic == "nop":
            word = 0xD503201F
        elif mnemonic == "svc #0":
            word = 0xD4000001
            opcode = "svc"
            operands = [_semantic_immediate(0)]
        elif mnemonic == "paciasp":
            word = 0xD503233F
        elif mnemonic == "autiasp":
            word = 0xD50323BF
        elif mnemonic == "bti c":
            word = 0xD503245F
            opcode = "bti"
            operands = [CompilerInt4(semantics.TOKEN_SYSTEM, semantics.SYSTEM_NAMES.index("c"), 0, 0)]
        elif mnemonic == "mrs x17, nzcv":
            word = 0xD53B4211
            opcode = "mrs"
            operands = [_semantic_register("x17"), CompilerInt4(semantics.TOKEN_SYSTEM, 0, 0, 0)]
        elif mnemonic == "msr nzcv, x17":
            word = 0xD51B4211
            opcode = "msr"
            operands = [CompilerInt4(semantics.TOKEN_SYSTEM, 0, 0, 0), _semantic_register("x17")]
        elif mnemonic == "blr x16":
            word = 0xD63F0200
            opcode = "blr"
            operands = [_semantic_register("x16")]
        else:
            raise EncodeError("unsupported emitted fixed instruction " + mnemonic)
        _capture_direct_instruction(
            word,
            EMITTED_INSTRUCTION_SCALAR,
            0,
            -1,
            opcode,
            operands,
        )
        return (DIRECT_INDIRECT_CALL_INSTRUCTION_PLACEHOLDER
                if mnemonic == "blr x16" else DIRECT_INSTRUCTION_PLACEHOLDER)
    return "  " + mnemonic


def emitted_frame_pair_line(
    load: bool, first: int = 29, second: int = 30, frame_bytes: int = 16,
) -> str:
    records = _DIRECT_INSTRUCTION_RECORDS
    if records is not None:
        _capture_direct_instruction(
            encode_emitted_frame_pair_parts(load, first, second, frame_bytes),
            EMITTED_INSTRUCTION_SCALAR,
            0,
            -1,
            "ldp" if load else "stp",
            [_semantic_register("xzr" if first == 31 else "x" + str(first)), _semantic_register("xzr" if second == 31 else "x" + str(second)), CompilerInt4(semantics.TOKEN_ADDRESS, semantics.ADDRESS_POSTINDEX if load else semantics.ADDRESS_PREINDEX, 2, 0), _semantic_register("sp"), _semantic_immediate(frame_bytes if load else -frame_bytes)],
        )
        return DIRECT_INSTRUCTION_PLACEHOLDER
    first_register = "xzr" if first == 31 else "x" + str(first)
    second_register = "xzr" if second == 31 else "x" + str(second)
    registers = first_register + ", " + second_register
    if load:
        return "  ldp " + registers + ", [sp], #" + str(frame_bytes)
    return "  stp " + registers + ", [sp, #-" + str(frame_bytes) + "]!"


def _capture_direct_symbol(symbol: str) -> int:
    if symbol in _DIRECT_INSTRUCTION_SYMBOL_IDS:
        return _DIRECT_INSTRUCTION_SYMBOL_IDS[symbol]
    symbol_id = len(_DIRECT_INSTRUCTION_SYMBOL_NAMES)
    _DIRECT_INSTRUCTION_SYMBOL_IDS[symbol] = symbol_id
    _DIRECT_INSTRUCTION_SYMBOL_NAMES.append(symbol)
    return symbol_id


def emitted_global_address_lines(
    register: str,
    symbol: str,
    got: bool,
) -> list[str]:
    records = _DIRECT_INSTRUCTION_RECORDS
    if records is not None:
        symbol_id = _capture_direct_symbol(symbol)
        _capture_direct_instruction(
            encode_emitted_adrp_parts(register),
            EMITTED_INSTRUCTION_SCALAR,
            STRUCTURED_RELOCATION_GOT_LOAD_PAGE21 if got else STRUCTURED_RELOCATION_PAGE21,
            symbol_id,
            "adrp",
            [_semantic_register(register), CompilerInt4(semantics.TOKEN_SYMBOL, symbol_id, semantics.SYMBOL_GOTPAGE if got else semantics.SYMBOL_PAGE, 0)],
        )
        _capture_direct_instruction(
            encode_emitted_ldr_got_pageoff_parts(register, register) if got else encode_emitted_add_pageoff_parts(register, register),
            EMITTED_INSTRUCTION_SCALAR,
            STRUCTURED_RELOCATION_GOT_LOAD_PAGEOFF12 if got else STRUCTURED_RELOCATION_PAGEOFF12,
            symbol_id,
            "ldr" if got else "add",
            ([_semantic_register(register), CompilerInt4(semantics.TOKEN_ADDRESS, semantics.ADDRESS_OFFSET, 2, 0), _semantic_register(register), CompilerInt4(semantics.TOKEN_SYMBOL, symbol_id, semantics.SYMBOL_GOTPAGEOFF, 0)] if got else [_semantic_register(register), _semantic_register(register), CompilerInt4(semantics.TOKEN_SYMBOL, symbol_id, semantics.SYMBOL_PAGEOFF, 0)]),
        )
        return [
            DIRECT_INSTRUCTION_PLACEHOLDER,
            DIRECT_INSTRUCTION_PLACEHOLDER,
        ]
    if got:
        return [
            "  adrp " + register + ", " + symbol + "@GOTPAGE",
            "  ldr " + register + ", [" + register + ", " + symbol + "@GOTPAGEOFF]",
        ]
    return [
        "  adrp " + register + ", " + symbol + "@PAGE",
        "  add " + register + ", " + register + ", " + symbol + "@PAGEOFF",
    ]


def stack_load_op(value_type: TypeDesc) -> str:
    if value_type.is_ptr or value_type.is_fp or (value_type.is_int and value_type.width > 16):
        return "ldur"
    if value_type.is_int and value_type.width <= 8:
        return "ldurb"
    if value_type.is_int and value_type.width <= 16:
        return "ldurh"
    return "ldur"


def stack_store_op(value_type: TypeDesc) -> str:
    if value_type.is_ptr or value_type.is_fp or (value_type.is_int and value_type.width > 16):
        return "stur"
    if value_type.is_int and value_type.width <= 8:
        return "sturb"
    if value_type.is_int and value_type.width <= 16:
        return "sturh"
    return "stur"


def mem_load_op(value_type: TypeDesc) -> str:
    if value_type.is_ptr or (value_type.is_int and value_type.width > 32):
        return "ldr"
    if value_type.is_int and value_type.width <= 8:
        return "ldrb"
    if value_type.is_int and value_type.width <= 16:
        return "ldrh"
    return "ldr"


def mem_store_op(value_type: TypeDesc) -> str:
    if value_type.is_ptr or (value_type.is_int and value_type.width > 32):
        return "str"
    if value_type.is_int and value_type.width <= 8:
        return "strb"
    if value_type.is_int and value_type.width <= 16:
        return "strh"
    return "str"


def chunk_load_op(size: int, *, stack: bool) -> str:
    if size == 8:
        return "ldur" if stack else "ldr"
    if size == 4:
        return "ldur" if stack else "ldr"
    if size == 2:
        return "ldurh" if stack else "ldrh"
    if size == 1:
        return "ldurb" if stack else "ldrb"
    raise BackendUnavailable(f"self backend does not support aggregate chunk load size {size}")


def chunk_store_op(size: int, *, stack: bool) -> str:
    if size == 8:
        return "stur" if stack else "str"
    if size == 4:
        return "stur" if stack else "str"
    if size == 2:
        return "sturh" if stack else "strh"
    if size == 1:
        return "sturb" if stack else "strb"
    raise BackendUnavailable(f"self backend does not support aggregate chunk store size {size}")


def aggregate_copy_chunks(size: int) -> list[tuple[int, int]]:
    chunks: list[tuple[int, int]] = []
    offset = 0
    remaining = size
    for chunk_size in (8, 4, 2, 1):
        while remaining >= chunk_size:
            chunks.append((offset, chunk_size))
            offset += chunk_size
            remaining -= chunk_size
    return chunks



def borrow_direct_instruction_semantics() -> semantics.AArch64InstructionSemantics:
    owner = _DIRECT_INSTRUCTION_SEMANTICS
    if _DIRECT_INSTRUCTION_RECORDS is None or owner is None:
        raise RuntimeError("direct instruction semantic capture is not active")
    return owner


def _semantic_register(register: str) -> CompilerInt4:
    return CompilerInt4(semantics.TOKEN_REGISTER, semantics.semantic_register_id(register), 0, 0)


def _semantic_immediate(immediate, radix: int = 10) -> CompilerInt4:
    return semantics.semantic_immediate_token(immediate, radix)


def _semantic_condition(condition: str) -> CompilerInt4:
    return CompilerInt4(semantics.TOKEN_CONDITION, semantics.CONDITION_NAMES.index(condition), 0, 0)


def _capture_direct_instruction(
    word: int, family: int, fixup: int, symbol: int,
    mnemonic: str, operands: list[CompilerInt4],
) -> None:
    records = _DIRECT_INSTRUCTION_RECORDS
    if records is None:
        raise RuntimeError("direct instruction capture is not active")
    owner = _DIRECT_INSTRUCTION_SEMANTICS
    if owner is None:
        records.append4(word, family, fixup, symbol)
        return
    record_length = len(records)
    header_length = len(owner.headers)
    token_length = len(owner.tokens)
    if record_length != header_length:
        raise EncodeError("direct instruction provenance is out of sequence")
    try:
        for operand in operands:
            owner.append_token(operand.first, operand.second, operand.third, operand.fourth)
        owner.append_header(
            semantics.semantic_opcode_id(mnemonic), semantics.FORM_CANONICAL,
            token_length // 4, len(operands),
        )
        records.append4(word, family, fixup, symbol)
    except Exception:
        records.truncate(record_length)
        owner.headers.truncate(header_length)
        owner.tokens.truncate(token_length)
        raise


def emitted_scalar_register_line(
    mnemonic: str, destination: str, left: str,
    right: str = "", fourth: str = "", shift: int = 0,
    *, explicit_shift: bool = False,
) -> str:
    if _DIRECT_INSTRUCTION_RECORDS is not None:
        word = encode_producer_registers(mnemonic, destination, left, right, fourth, shift)
        operands = [_semantic_register(destination), _semantic_register(left)]
        if right:
            operands.append(
                _semantic_condition(right) if mnemonic == "cneg" else _semantic_register(right)
            )
        if fourth:
            operands.append(
                _semantic_condition(fourth) if mnemonic in ("csel", "fcsel", "csinv")
                else _semantic_register(fourth)
            )
        if shift or explicit_shift:
            operands.append(CompilerInt4(semantics.TOKEN_SHIFT, 0, shift, 1))
        _capture_direct_instruction(word, EMITTED_INSTRUCTION_SCALAR, 0, -1, mnemonic, operands)
        return DIRECT_INSTRUCTION_PLACEHOLDER
    line = "  " + mnemonic + " " + destination + ", " + left
    if right:
        line += ", " + right
    if fourth:
        line += ", " + fourth
    if shift or explicit_shift:
        line += ", lsl #" + str(shift)
    return line


def emitted_scalar_immediate_line(
    mnemonic: str, destination: str, left: str, immediate,
    *, radix: int = 10,
) -> str:
    if _DIRECT_INSTRUCTION_RECORDS is not None:
        word = encode_producer_immediate(mnemonic, destination, left, immediate)
        operands = [_semantic_register(destination)]
        if left:
            operands.append(_semantic_register(left))
        operands.append(_semantic_immediate(immediate, radix))
        _capture_direct_instruction(
            word,
            EMITTED_INSTRUCTION_MOVE if mnemonic == "mov" else EMITTED_INSTRUCTION_SCALAR,
            0, -1, mnemonic, operands,
        )
        return DIRECT_INSTRUCTION_PLACEHOLDER
    line = "  " + mnemonic + " " + destination
    if left:
        line += ", " + left
    if radix == 16:
        return line + ", #" + hex(immediate)
    if radix != 10:
        raise EncodeError("unsupported producer immediate radix")
    return line + ", #" + str(immediate)


def emitted_atomic_instruction_line(
    mnemonic: str, first: str, base: str, second: str = "",
) -> str:
    if _DIRECT_INSTRUCTION_RECORDS is not None:
        word = encode_producer_atomic(mnemonic, first, base, second)
        operands = [_semantic_register(first)]
        if second:
            operands.append(_semantic_register(second))
        operands.extend([
            CompilerInt4(semantics.TOKEN_ADDRESS, semantics.ADDRESS_BASE, 1, 0),
            _semantic_register(base),
        ])
        _capture_direct_instruction(word, EMITTED_INSTRUCTION_SCALAR, 0, -1, mnemonic, operands)
        return DIRECT_INSTRUCTION_PLACEHOLDER
    line = "  " + mnemonic + " " + first
    if second:
        line += ", " + second
    return line + ", [" + base + "]"


def emitted_system_register_line(mnemonic: str, register: str, system: str) -> str:
    if _DIRECT_INSTRUCTION_RECORDS is not None:
        word = encode_producer_system(mnemonic, register, system)
        register_token = _semantic_register(register)
        system_token = CompilerInt4(
            semantics.TOKEN_SYSTEM, semantics.SYSTEM_NAMES.index(system), 0, 0,
        )
        operands = ([register_token, system_token] if mnemonic == "mrs"
                    else [system_token, register_token])
        _capture_direct_instruction(word, EMITTED_INSTRUCTION_SCALAR, 0, -1, mnemonic, operands)
        return DIRECT_INSTRUCTION_PLACEHOLDER
    if mnemonic == "mrs":
        return "  mrs " + register + ", " + system
    if mnemonic == "msr":
        return "  msr " + system + ", " + register
    raise EncodeError("unsupported producer system transfer")


def emitted_indirect_branch_line(mnemonic: str, register: str) -> str:
    if _DIRECT_INSTRUCTION_RECORDS is not None:
        word = encode_producer_indirect_branch(mnemonic, register)
        _capture_direct_instruction(
            word, EMITTED_INSTRUCTION_SCALAR, 0, -1,
            mnemonic, [_semantic_register(register)],
        )
        return (DIRECT_INDIRECT_CALL_INSTRUCTION_PLACEHOLDER
                if mnemonic == "blr" else DIRECT_INSTRUCTION_PLACEHOLDER)
    return "  " + mnemonic + " " + register


def emitted_pair_instruction_line(
    mnemonic: str, first: str, second: str, base: str, offset: int = 0,
    *, explicit_offset: bool = False,
) -> str:
    if _DIRECT_INSTRUCTION_RECORDS is not None:
        word = encode_producer_pair(mnemonic, first, second, base, offset)
        has_offset = offset != 0 or explicit_offset
        operands = [
            _semantic_register(first), _semantic_register(second),
            CompilerInt4(
                semantics.TOKEN_ADDRESS,
                semantics.ADDRESS_OFFSET if has_offset else semantics.ADDRESS_BASE,
                2 if has_offset else 1, 0,
            ),
            _semantic_register(base),
        ]
        if has_offset:
            operands.append(_semantic_immediate(offset))
        _capture_direct_instruction(word, EMITTED_INSTRUCTION_SCALAR, 0, -1, mnemonic, operands)
        return DIRECT_INSTRUCTION_PLACEHOLDER
    suffix = ", #" + str(offset) if offset != 0 or explicit_offset else ""
    return "  " + mnemonic + " " + first + ", " + second + ", [" + base + suffix + "]"


def emitted_fp_immediate_line(register: str, literal: str) -> str:
    encoding = direct_fp_immediate_encoding(literal)
    if encoding is None:
        raise EncodeError("unsupported producer FP immediate literal")
    if _DIRECT_INSTRUCTION_RECORDS is not None:
        word = encode_producer_fp_immediate(register, encoding)
        operands = [
            _semantic_register(register),
            CompilerInt4(
                semantics.TOKEN_FLOAT_IMMEDIATE, encoding,
                semantics.FLOAT_IMMEDIATE_LITERALS.index(literal), semantics.IMMEDIATE_HASH,
            ),
        ]
        _capture_direct_instruction(word, EMITTED_INSTRUCTION_SCALAR, 0, -1, "fmov", operands)
        return DIRECT_INSTRUCTION_PLACEHOLDER
    return "  fmov " + register + ", #" + literal


def emitted_vector_instruction_line(
    mnemonic: str, destination_index: int, source_index: int = 0,
) -> str:
    if _DIRECT_INSTRUCTION_RECORDS is not None:
        word = encode_producer_vector(mnemonic, destination_index, source_index)
        if mnemonic == "movi":
            operands = [
                CompilerInt4(semantics.TOKEN_VECTOR, destination_index, 1, -1),
                _semantic_immediate(0),
            ]
        elif mnemonic == "cnt":
            operands = [
                CompilerInt4(semantics.TOKEN_VECTOR, destination_index, 0, -1),
                CompilerInt4(semantics.TOKEN_VECTOR, source_index, 0, -1),
            ]
        elif mnemonic == "addv":
            operands = [
                _semantic_register("b" + str(destination_index)),
                CompilerInt4(semantics.TOKEN_VECTOR, source_index, 0, -1),
            ]
        elif mnemonic == "umov":
            operands = [
                _semantic_register("w" + str(destination_index)),
                CompilerInt4(semantics.TOKEN_VECTOR, source_index, 8, 0),
            ]
        else:
            raise EncodeError("unsupported producer vector instruction")
        _capture_direct_instruction(word, EMITTED_INSTRUCTION_SCALAR, 0, -1, mnemonic, operands)
        return DIRECT_INSTRUCTION_PLACEHOLDER
    if mnemonic == "movi":
        return "  movi v" + str(destination_index) + ".16b, #0"
    if mnemonic == "cnt":
        return "  cnt v" + str(destination_index) + ".8b, v" + str(source_index) + ".8b"
    if mnemonic == "addv":
        return "  addv b" + str(destination_index) + ", v" + str(source_index) + ".8b"
    if mnemonic == "umov":
        return "  umov w" + str(destination_index) + ", v" + str(source_index) + ".b[0]"
    raise EncodeError("unsupported producer vector instruction")
