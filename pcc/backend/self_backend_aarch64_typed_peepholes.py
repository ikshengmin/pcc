"""Exact producer-provenance counterparts of the first fourteen ARM peepholes.

The input is the complete ordered module event stream. Words are never decoded
for matcher eligibility. Selected register-field edits have per-form provenance
proofs; all other new instructions use the canonical typed encoders.
"""
from __future__ import annotations

from . import self_backend_aarch64_semantics as sem
from .arm64_encode import (
    EMITTED_INSTRUCTION_MOVE, EMITTED_INSTRUCTION_SCALAR,
    STRUCTURED_FIXUP_BRANCH19, STRUCTURED_FIXUP_BRANCH26,
    encode_emitted_move_register_parts, encode_emitted_compare_register_parts,
    encode_emitted_addsub_register_parts, EncodeError,
    encode_emitted_compare_immediate_parts, encode_emitted_branch_base_parts,
)
from .arm64_encode_producer import encode_producer_registers, encode_producer_immediate
from .self_backend_aarch64_fragments import (
    AArch64EmissionFragments, EMISSION_RECORD_LABEL,
    EMISSION_RECORD_MEMORY_PAIR_BARRIER, EMISSION_RECORD_EMPTY,
    EMISSION_RECORD_DIRECTIVE,
)
from .self_backend_value_arena import CompilerInt2, CompilerInt4, CompilerIntArena

_RENAMEABLE = frozenset((
    "add", "adds", "sub", "subs", "adrp", "orr", "orn", "and", "ands",
    "eor", "eon", "bic", "mul", "madd", "msub", "smulh", "umulh", "sdiv",
    "udiv", "lsl", "lsr", "asr", "ror", "lslv", "lsrv", "asrv", "neg",
    "negs", "mvn", "sxtw", "sxth", "sxtb", "uxtb", "uxth", "ubfx", "sbfx",
    "ubfiz", "sbfiz", "csel", "csinc", "csinv", "csneg", "cset", "csetm",
    "cinc", "ldr", "ldur", "ldrb", "ldurb", "ldrh", "ldurh", "ldrsw",
    "ldursw", "ldrsb", "ldursb", "ldrsh", "ldursh", "mov", "movz", "movn",
    "fmov", "fcvtzs", "fcvtzu", "clz",
))
_NO_DEFINE = frozenset((
    "cmp", "cmn", "tst", "fcmp", "ret", "br", "blr", "tbz", "tbnz",
    "stur", "str", "stlr", "sturb", "strb", "stlrb", "sturh", "strh",
    "stlrh", "stp", "b", "bl", "cbz", "cbnz",
))
_INVERSE = {"eq":"ne", "ne":"eq", "lt":"ge", "le":"gt", "gt":"le",
            "ge":"lt", "lo":"hs", "ls":"hi", "hi":"ls", "hs":"lo",
            "mi":"pl", "pl":"mi", "vs":"vc", "vc":"vs"}


def _reg(value: int) -> CompilerInt4:
    return CompilerInt4(sem.TOKEN_REGISTER, value, 0, 0)


def _spelling(value: int) -> str:
    return sem.semantic_register_spelling(value)


def _class(value: int) -> str:
    return _spelling(value)[0] if value >= 0 else ""


def _scratch(value: int) -> bool:
    return 9 <= value <= 15 or 41 <= value <= 47


def _number(value: int) -> int:
    if value in (sem.REGISTER_XZR, sem.REGISTER_WZR, sem.REGISTER_SP, sem.REGISTER_WSP):
        return 31
    if value == sem.REGISTER_FP:
        return 29
    if value == sem.REGISTER_LR:
        return 30
    return value & 31


def _alias(value: int) -> int:
    if 0 <= value <= 30:
        return value + 32
    if 32 <= value <= 62:
        return value - 32
    return value


def _forward_alias(value: int) -> str:
    spelling = _spelling(value)
    if spelling[0] in ("w", "x"):
        return "gpr:" + spelling[1:]
    if spelling[0] in ("s", "d"):
        return "fp:" + spelling[1:]
    return spelling


def _same(a: CompilerInt4, b: CompilerInt4) -> bool:
    return (a.first == b.first and a.second == b.second
            and a.third == b.third and a.fourth == b.fourth)


class _Pass:
    def __init__(self, fragments, semantics, span):
        self.fragments = fragments
        self.semantics = semantics
        self.ids = CompilerIntArena()
        try:
            self.output = CompilerIntArena()
        except Exception:
            self.ids.close()
            raise
        try:
            fragments.start_cursor(span)
            record_id = fragments.next_record_id()
            while record_id >= 0:
                if record_id >= len(fragments.records) // 4:
                    raise EncodeError("AArch64 fragment record ID is invalid")
                encoded = fragments.records.get4_unchecked(record_id)
                if encoded.second >= 0:
                    semantics.require_header(record_id)
                self.ids.append(record_id)
                record_id = fragments.next_record_id()
            self.result = fragments.new_fragment()
        except Exception:
            self.output.close()
            self.ids.close()
            raise

    def close(self):
        self.output.close()
        self.ids.close()

    def rid(self, index):
        return self.ids.get_unchecked(index) if 0 <= index < len(self.ids) else -1

    def code(self, rid):
        return self.fragments.records.get4_unchecked(rid)

    def op(self, rid):
        if rid < 0 or self.code(rid).second < 0:
            return ""
        return sem.OPCODE_NAMES[self.semantics.require_header(rid).first]

    def tokens(self, rid):
        if not self.op(rid):
            return []
        header = self.semantics.require_header(rid)
        return [self.semantics.operand(rid, i) for i in range(header.fourth)]

    def register(self, rid, index):
        tokens = self.tokens(rid)
        if index >= len(tokens) or tokens[index].first != sem.TOKEN_REGISTER:
            return -1
        return tokens[index].second

    def stack(self, rid, opcode):
        t = self.tokens(rid)
        if self.op(rid) != opcode or len(t) != 4:
            return None
        if (t[0].first != sem.TOKEN_REGISTER or t[1].first != sem.TOKEN_ADDRESS
            or t[1].second != sem.ADDRESS_OFFSET or t[1].third != 2
            or t[2].first != sem.TOKEN_REGISTER or t[2].second != 29
            or t[3].first != sem.TOKEN_IMMEDIATE):
            return None
        # Preserve the literal '#-' prefix, including negative zero and radix.
        if not (t[3].fourth & sem.IMMEDIATE_HASH) or not (
            t[3].second < 0 or t[3].fourth & sem.IMMEDIATE_NEGATIVE_ZERO
        ):
            return None
        return t[0].second, t[3]

    def move(self, rid):
        if self.op(rid) != "mov" or len(self.tokens(rid)) != 2:
            return None
        dest, src = self.register(rid, 0), self.register(rid, 1)
        if dest < 0 or src < 0 or _class(dest) not in ("w", "x"):
            return None
        return (dest, src) if dest != src and _class(dest) == _class(src) else None

    def compare(self, rid):
        if self.op(rid) != "cmp" or len(self.tokens(rid)) != 2:
            return None
        lhs, rhs = self.register(rid, 0), self.register(rid, 1)
        if lhs >= 0 and rhs >= 0 and _class(lhs) in ("w", "x") and _class(lhs) == _class(rhs):
            return lhs, rhs
        return None

    def zero(self, rid):
        t = self.tokens(rid)
        if self.op(rid) != "movz" or len(t) not in (2, 3):
            return -1
        r = self.register(rid, 0)
        if r < 0 or _class(r) not in ("w", "x"):
            return -1
        if not _same(t[1], CompilerInt4(sem.TOKEN_IMMEDIATE, 0, 10, sem.IMMEDIATE_HASH)):
            return -1
        if len(t) == 3 and not _same(t[2], CompilerInt4(sem.TOKEN_SHIFT, 0, 0, 1)):
            return -1
        return r

    def cset(self, rid):
        t = self.tokens(rid)
        r = self.register(rid, 0)
        if self.op(rid) == "cset" and len(t) == 2 and r >= 0 and _class(r) == "w" and t[1].first == sem.TOKEN_CONDITION:
            return r, sem.CONDITION_NAMES[t[1].second]
        return None

    def branch(self, rid):
        t = self.tokens(rid)
        r = self.register(rid, 0)
        if self.op(rid) in ("cbz", "cbnz") and len(t) == 2 and r >= 0 and _class(r) in ("w", "x") and t[1].first == sem.TOKEN_SYMBOL:
            return self.op(rid), r, t[1]
        return None

    def token_uses(self, token, reg):
        if token.first == sem.TOKEN_REGISTER:
            return token.second == reg
        if token.first == sem.TOKEN_SYMBOL:
            return sem.semantic_symbol_uses_register(self.fragments.symbol_names[token.second], reg)
        return False

    def effect(self, rid, reg):
        op = self.op(rid)
        if not op:
            return 0
        t = self.tokens(rid)
        aliases = (reg, _alias(reg))
        lse = op.startswith(("ldadd", "ldclr", "ldeor", "ldset", "ldsmax", "ldsmin", "ldumax", "ldumin", "swp"))
        cas = op.startswith("cas")
        defines = False
        source = t
        if lse:
            defines = self.register(rid, 1) in aliases
            source = t[:1] + t[2:]
        elif cas:
            defines = self.register(rid, 0) in aliases
        elif op not in _NO_DEFINE and not op.startswith("b."):
            defines = self.register(rid, 0) in aliases
            if defines and op not in ("movk", "bfi", "bfxil"):
                source = t[1:]
        for token in source:
            if any(self.token_uses(token, alias) for alias in aliases):
                return 1
        return 2 if defines else 0

    def function_label(self, rid):
        if rid < 0 or self.code(rid).second != EMISSION_RECORD_LABEL:
            return False
        name = self.fragments.symbol_names[self.code(rid).first]
        return bool(name) and not name.startswith((" ", ".", "L_"))

    def boundary(self, rid):
        code = self.code(rid)
        if code.second in (EMISSION_RECORD_DIRECTIVE, EMISSION_RECORD_MEMORY_PAIR_BARRIER):
            return True
        if code.second == EMISSION_RECORD_LABEL:
            name = self.fragments.symbol_names[code.first]
            return name.startswith(("L_", ".")) or self.function_label(rid)
        return False

    def dead(self, start, reg, calls=False):
        for i in range(start, len(self.ids)):
            rid = self.rid(i)
            op = self.op(rid)
            if self.boundary(rid):
                return False
            if op == "bl" or op.startswith("ret"):
                return calls
            if op in ("b", "cbz", "cbnz", "tbz", "tbnz", "blr", "br") or op.startswith("b."):
                return False
            effect = self.effect(rid, reg)
            if effect:
                return effect == 2
        return True

    def false_path(self, start):
        while start < len(self.ids) and self.code(self.rid(start)).second == EMISSION_RECORD_EMPTY:
            start += 1
        return start == len(self.ids) or self.op(self.rid(start)) == "b" or self.op(self.rid(start)).startswith("ret")

    def new(self, op, tokens, word, family=EMITTED_INSTRUCTION_SCALAR, fixup=0, symbol=-1):
        return sem.append_semantic_instruction(
            self.fragments, self.semantics, self.result,
            CompilerInt4(word, family, fixup, symbol), sem.semantic_opcode_id(op),
            sem.FORM_CANONICAL, tokens,
        )

    def changed_register(self, rid, token_index, value, bit_offset):
        # Source provenance proves the selected field: scalar Rd/Rt, Rn, or
        # CBZ/CBNZ Rt. No opcode or source-form eligibility comes from bits.
        t = self.tokens(rid)
        t[token_index] = _reg(value)
        code = self.code(rid)
        word = (code.first & ~(31 << bit_offset)) | (_number(value) << bit_offset)
        if bit_offset == 5 and value == sem.REGISTER_WSP and t[2].first == sem.TOKEN_REGISTER:
            # Moving WSP into Rn selects the extended-register ADD/SUB form;
            # merely replacing Rn bits would instead read WZR.
            if len(t) != 3:
                raise EncodeError("typed arithmetic WSP with shift needs explicit encoding")
            word = encode_emitted_addsub_register_parts(
                self.op(rid), _spelling(t[0].second), _spelling(value),
                _spelling(t[2].second),
            )
        return self.new(self.op(rid), t, word, code.second, code.third, code.fourth)

    def new_move(self, dest, src, byte=False):
        if byte:
            if _class(dest) != "w" or _class(src) != "w":
                return -2
            t = [_reg(dest), _reg(src), CompilerInt4(sem.TOKEN_IMMEDIATE, 255, 16, sem.IMMEDIATE_HASH)]
            return self.new("and", t, encode_producer_immediate("and", _spelling(dest), _spelling(src), 255))
        if dest == src:
            return -1
        if _class(dest) != _class(src):
            return -2
        if _class(dest) in ("w", "x"):
            return self.new("mov", [_reg(dest), _reg(src)], encode_emitted_move_register_parts(_spelling(dest), _spelling(src)), EMITTED_INSTRUCTION_MOVE)
        if _class(dest) in ("s", "d"):
            return self.new("fmov", [_reg(dest), _reg(src)], encode_producer_registers("fmov", _spelling(dest), _spelling(src)))
        return -2

    def new_branch(self, op, symbol, reg=-1):
        t = [symbol] if reg < 0 else [_reg(reg), symbol]
        kind = STRUCTURED_FIXUP_BRANCH26 if op == "b" else STRUCTURED_FIXUP_BRANCH19
        word = encode_emitted_branch_base_parts(op, "" if reg < 0 else _spelling(reg))
        return self.new(op, t, word, fixup=kind, symbol=symbol.second)

    def finish(self):
        # New records were transactionally staged on result; the returned root
        # is rebuilt from selected IDs so staging cannot duplicate instructions.
        result = self.fragments.new_fragment()
        for i in range(len(self.output)):
            self.fragments.spans.append(result, self.output.get_unchecked(i))
        return result


def _run(fragments, semantics, span, mode):
    p = _Pass(fragments, semantics, span)
    try:
        changed = False
        index = 0
        function_end = 0
        in_function = False
        loaded = []
        while index < len(p.ids):
            a, b, c, d = (p.rid(index + n) for n in range(4))
            replacement = -2
            consumed = 0
            if mode in (1, 2):
                last = b if mode == 1 else c
                if last >= 0:
                    middle = None
                    if mode == 2:
                        middle = p.stack(b, "ldur") or p.stack(b, "ldurb")
                    for store_op, load_op in (("stur", "ldur"), ("sturb", "ldurb")):
                        store, load = p.stack(a, store_op), p.stack(last, load_op)
                        if store is None or load is None or not _same(store[1], load[1]):
                            continue
                        if mode == 2 and (middle is None or _same(middle[1], store[1]) or _forward_alias(middle[0]) == _forward_alias(store[0])):
                            continue
                        replacement = p.new_move(load[0], store[0], load_op == "ldurb")
                        if replacement != -2:
                            p.output.append(a)
                            if mode == 2:
                                p.output.append(b)
                            consumed = mode + 1
                            break
            elif mode in (3, 4) and b >= 0:
                zero = p.zero(a)
                move = p.move(a)
                old = zero if mode == 3 else (move[0] if move else -1)
                if old >= 0 and _scratch(old) and p.op(b) in ("stur", "str", "sturb", "strb") and p.register(b, 0) == old and p.dead(index + 2, old):
                    new = (sem.REGISTER_WZR if _class(old) == "w" else sem.REGISTER_XZR) if mode == 3 else move[1]
                    replacement = p.changed_register(b, 0, new, 0)
                    consumed = 2
            elif mode in (5, 6) and b >= 0:
                compare = p.compare(b)
                zero = p.zero(a)
                move = p.move(a)
                if compare is not None:
                    lhs, rhs = compare
                    if mode == 5 and zero >= 0 and rhs == zero and lhs != zero and p.dead(index + 2, zero):
                        replacement = p.new("cmp", [_reg(lhs), CompilerInt4(sem.TOKEN_IMMEDIATE, 0, 10, sem.IMMEDIATE_HASH)], encode_emitted_compare_immediate_parts(_spelling(lhs), 0))
                        consumed = 2
                    if mode == 6 and move and _scratch(move[0]) and move[0] in (lhs, rhs) and p.dead(index + 2, move[0]):
                        lhs = move[1] if lhs == move[0] else lhs
                        rhs = move[1] if rhs == move[0] else rhs
                        replacement = p.new("cmp", [_reg(lhs), _reg(rhs)], encode_emitted_compare_register_parts(_spelling(lhs), _spelling(rhs)))
                        consumed = 2
            elif mode == 7 and b >= 0:
                move, branch = p.move(a), p.branch(b)
                if move and branch and move[0] == branch[1] and _class(move[0]) == "w" and _class(move[1]) == "w" and _scratch(move[0]) and p.false_path(index + 2):
                    replacement = p.new_branch(branch[0], branch[2], move[1])
                    consumed = 2
            elif mode == 8 and b >= 0:
                move = p.move(a)
                t = p.tokens(b)
                dest, lhs = p.register(b, 0), p.register(b, 1)
                if (move and p.op(b) in ("add", "sub") and len(t) >= 3
                    and dest >= 0 and lhs >= 0 and _class(dest) in ("w", "x")
                    and _class(dest) == _class(lhs) and dest == lhs == move[0]
                    and _scratch(dest) and not any(p.token_uses(token, dest) for token in t[2:])):
                    replacement = p.changed_register(b, 1, move[1], 5)
                    consumed = 2
            elif mode == 9:
                move = p.move(a)
                if move and len(p.output) and _scratch(move[1]) and _spelling(move[0])[1:] not in ("sp", "zr") and p.dead(index + 1, move[1], True):
                    previous = p.output.get_unchecked(len(p.output) - 1)
                    t = p.tokens(previous)
                    if (p.op(previous) in _RENAMEABLE and len(t) >= 2
                        and p.register(previous, 0) == move[1]
                        and not any(token.first == sem.TOKEN_ADDRESS and token.second in (sem.ADDRESS_PREINDEX, sem.ADDRESS_POSTINDEX) for token in t[1:])):
                        p.output.truncate(len(p.output) - 1)
                        if p.op(previous) == "mov" and len(t) == 2 and p.register(previous, 1) == move[0]:
                            replacement = -1
                        else:
                            replacement = p.changed_register(previous, 0, move[0], 0)
                        consumed = 1
            elif mode == 10 and b >= 0:
                first, second = p.move(a), p.move(b)
                if first and second and first[0] == second[1] and _scratch(first[0]) and p.dead(index + 2, first[0], True):
                    replacement = p.new_move(second[0], first[1])
                    if replacement != -2:
                        consumed = 2
            elif mode == 11 and b >= 0:
                zero, branch = p.zero(a), p.branch(b)
                if zero >= 0 and branch and branch[1] == zero:
                    replacement = p.new_branch("b", branch[2]) if branch[0] == "cbz" else a
                    consumed = 2
            elif mode == 12 and d >= 0:
                cset, store, branch = p.cset(a), p.stack(b, "sturb"), p.branch(d)
                t = p.tokens(c)
                dst, src = p.register(c, 0), p.register(c, 1)
                if (cset and store and branch and p.op(c) == "and" and len(t) == 3
                    and dst >= 0 and src >= 0 and _class(dst) == _class(src) == "w"
                    and _same(t[2], CompilerInt4(sem.TOKEN_IMMEDIATE, 255, 16, sem.IMMEDIATE_HASH))
                    and store[0] == src == cset[0] and branch[1] == dst):
                    p.output.append(a)
                    p.output.append(b)
                    replacement = p.new_branch(branch[0], branch[2], cset[0])
                    consumed = 4
            elif mode == 13 and d >= 0 and p.op(a) in ("cmp", "fcmp"):
                cset, store, branch = p.cset(b), p.stack(c, "sturb"), p.branch(d)
                if cset and store and branch:
                    cond = cset[1] if branch[0] == "cbnz" else _INVERSE.get(cset[1], "")
                    if cond and store[0] == branch[1] == cset[0]:
                        p.output.append(a)
                        p.output.append(b)
                        p.output.append(c)
                        replacement = p.new_branch("b." + cond, branch[2])
                        consumed = 4
            elif mode == 14:
                # The original pass preserves the prefix before the first
                # function and partitions at function labels, not sections.
                if p.function_label(a):
                    in_function = True
                    function_end = index + 1
                    while function_end < len(p.ids) and not p.function_label(p.rid(function_end)):
                        function_end += 1
                    loaded = []
                    for j in range(index, function_end):
                        load = p.stack(p.rid(j), "ldurb")
                        if load:
                            loaded.append(load[1])
                if in_function and index + 3 < function_end and p.op(a) in ("cmp", "fcmp"):
                    cset, store = p.cset(b), p.stack(c, "sturb")
                    if cset and store and p.op(d).startswith("b.") and store[0] == cset[0] and not any(_same(store[1], offset) for offset in loaded):
                        p.output.append(a)
                        replacement = d
                        consumed = 4
            if consumed:
                changed = True
                if replacement >= 0:
                    p.output.append(replacement)
                index += consumed
            else:
                p.output.append(a)
                index += 1
        if not changed and fragments.spans.length(span) == len(p.ids):
            # No rewrite selected: keep a distinct mutable root, but snapshot
            # the immutable input tree instead of copying every record ID.
            # A corrupted root count still takes the old rebuilding path.
            result = fragments.new_fragment()
            fragments.extend_fragment(result, span)
            return result
        return p.finish()
    finally:
        p.close()


def forward_adjacent_stack_store_load(
    fragments: AArch64EmissionFragments, semantics: sem.AArch64InstructionSemantics,
    span: CompilerInt2,
) -> CompilerInt2:
    return _run(fragments, semantics, span, 1)


def forward_one_intervening_stack_store_load(
    fragments: AArch64EmissionFragments, semantics: sem.AArch64InstructionSemantics,
    span: CompilerInt2,
) -> CompilerInt2:
    return _run(fragments, semantics, span, 2)


def fold_zero_store_source(
    fragments: AArch64EmissionFragments, semantics: sem.AArch64InstructionSemantics,
    span: CompilerInt2,
) -> CompilerInt2:
    return _run(fragments, semantics, span, 3)


def fold_mov_store_source(
    fragments: AArch64EmissionFragments, semantics: sem.AArch64InstructionSemantics,
    span: CompilerInt2,
) -> CompilerInt2:
    return _run(fragments, semantics, span, 4)


def fold_zero_compare_immediate(
    fragments: AArch64EmissionFragments, semantics: sem.AArch64InstructionSemantics,
    span: CompilerInt2,
) -> CompilerInt2:
    return _run(fragments, semantics, span, 5)


def fold_mov_compare_source(
    fragments: AArch64EmissionFragments, semantics: sem.AArch64InstructionSemantics,
    span: CompilerInt2,
) -> CompilerInt2:
    return _run(fragments, semantics, span, 6)


def fold_mov_zero_branch_source(
    fragments: AArch64EmissionFragments, semantics: sem.AArch64InstructionSemantics,
    span: CompilerInt2,
) -> CompilerInt2:
    return _run(fragments, semantics, span, 7)


def fold_mov_arith_self_update(
    fragments: AArch64EmissionFragments, semantics: sem.AArch64InstructionSemantics,
    span: CompilerInt2,
) -> CompilerInt2:
    return _run(fragments, semantics, span, 8)


def fold_scratch_def_into_move(
    fragments: AArch64EmissionFragments, semantics: sem.AArch64InstructionSemantics,
    span: CompilerInt2,
) -> CompilerInt2:
    return _run(fragments, semantics, span, 9)


def fold_mov_mov_chain(
    fragments: AArch64EmissionFragments, semantics: sem.AArch64InstructionSemantics,
    span: CompilerInt2,
) -> CompilerInt2:
    return _run(fragments, semantics, span, 10)


def fold_zero_test_branch(
    fragments: AArch64EmissionFragments, semantics: sem.AArch64InstructionSemantics,
    span: CompilerInt2,
) -> CompilerInt2:
    return _run(fragments, semantics, span, 11)


def fold_forwarded_cset_branch(
    fragments: AArch64EmissionFragments, semantics: sem.AArch64InstructionSemantics,
    span: CompilerInt2,
) -> CompilerInt2:
    return _run(fragments, semantics, span, 12)


def fold_cset_zero_branch(
    fragments: AArch64EmissionFragments, semantics: sem.AArch64InstructionSemantics,
    span: CompilerInt2,
) -> CompilerInt2:
    return _run(fragments, semantics, span, 13)


def drop_dead_cset_branch_stores(
    fragments: AArch64EmissionFragments, semantics: sem.AArch64InstructionSemantics,
    span: CompilerInt2,
) -> CompilerInt2:
    return _run(fragments, semantics, span, 14)


def run_aarch64_typed_peepholes(
    fragments: AArch64EmissionFragments, semantics: sem.AArch64InstructionSemantics,
    span: CompilerInt2,
) -> CompilerInt2:
    """Apply the original ordered first-fourteen module-stream passes."""
    semantics.require_complete(fragments.records, len(fragments.symbol_names))
    span = forward_adjacent_stack_store_load(fragments, semantics, span)
    span = forward_one_intervening_stack_store_load(fragments, semantics, span)
    span = fold_zero_store_source(fragments, semantics, span)
    span = fold_mov_store_source(fragments, semantics, span)
    span = fold_zero_compare_immediate(fragments, semantics, span)
    span = fold_mov_compare_source(fragments, semantics, span)
    span = fold_mov_zero_branch_source(fragments, semantics, span)
    span = fold_mov_arith_self_update(fragments, semantics, span)
    span = fold_scratch_def_into_move(fragments, semantics, span)
    span = fold_mov_mov_chain(fragments, semantics, span)
    span = fold_zero_test_branch(fragments, semantics, span)
    span = fold_forwarded_cset_branch(fragments, semantics, span)
    span = fold_cset_zero_branch(fragments, semantics, span)
    return drop_dead_cset_branch_stores(fragments, semantics, span)
