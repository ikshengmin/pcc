"""Scope-owned AArch64 fragments with explicit integer record identities."""

from __future__ import annotations

from .arm64_encode import (
    EMITTED_INSTRUCTION_CALL,
    EMITTED_INSTRUCTION_MOVE,
    EMITTED_INSTRUCTION_SCALAR,
    EMITTED_INSTRUCTION_UNSCALED,
    EncodeError,
    STRUCTURED_FIXUP_BRANCH19,
    STRUCTURED_FIXUP_BRANCH26,
    STRUCTURED_FIXUP_CALL,
    encode_emitted_addsub_immediate_parts,
    encode_emitted_addsub_register_parts,
    encode_emitted_branch_base_parts,
    encode_emitted_load_store_parts,
    encode_emitted_move_register_parts,
    encode_emitted_movewide_parts,
    encode_emitted_nop_parts,
    intern_emitted_symbol,
    validate_emitted_label_name,
)
from .self_backend_aarch64_semantics import (
    AArch64InstructionSemantics,
    FORM_CANONICAL,
    TOKEN_REGISTER,
    TOKEN_SYMBOL,
    TOKEN_ADDRESS,
    TOKEN_SHIFT,
    ADDRESS_BASE,
    ADDRESS_OFFSET,
    SHIFT_NAMES,
    SYMBOL_PLAIN,
    append_semantic_instruction,
    semantic_opcode_id,
    semantic_register_id,
    semantic_immediate_token,
)
from .self_backend_value_arena import (
    CompilerInt2,
    CompilerInt4,
    CompilerIntArena,
    CompilerRecordSpanArena,
)


EMISSION_RECORD_LABEL = -1
# A zero-width source-semantics event, retained through every sequence pass.
# first is 1 for begin and 0 for end; third/fourth remain 0/-1.
EMISSION_RECORD_MEMORY_PAIR_BARRIER = -2
EMISSION_RECORD_EMPTY = -3
EMISSION_RECORD_DIRECTIVE = -4
# Directive record=(kind, DIRECTIVE, scalar argument, symbol ID or -1).
# Metadata uses scalar argument as an index into the emission owner's retained
# metadata-only adapter payloads. It may never contain instruction text.
EMISSION_DIRECTIVE_P2ALIGN = 1
EMISSION_DIRECTIVE_ALIGN = 2
EMISSION_DIRECTIVE_GLOBAL = 3
EMISSION_DIRECTIVE_PRIVATE_EXTERN = 4
EMISSION_DIRECTIVE_WEAK_DEFINITION = 5
EMISSION_DIRECTIVE_ALT_ENTRY = 6
EMISSION_DIRECTIVE_CFI = 7
EMISSION_DIRECTIVE_LOC = 8
EMISSION_DIRECTIVE_TEXT_SECTION = 9
EMISSION_DIRECTIVE_METADATA = 10
EMISSION_DIRECTIVE_SUBSECTIONS = 11


class AArch64EmissionFragments:
    """Native records plus persistent sequence roots for one emission scope.

    Instruction records use (word, family, relocation kind, symbol ID), just
    like the direct encoder. EMISSION_RECORD_LABEL uses its first field to
    index the owned symbol side table. EMISSION_RECORD_MEMORY_PAIR_BARRIER
    uses its first field for begin/end (1/0); it has no symbol or code bytes.
    Other negative families are unsupported. No record contains a pointer.
    This first producer slice encodes pointer reloads; unsupported operand
    families still fail through the canonical encoder before publication.
    """

    def __init__(self, *, capture_semantics: bool = False) -> None:
        names: list[str] = []
        symbol_ids: dict[str, int] = {}
        records = CompilerIntArena()
        try:
            spans = CompilerRecordSpanArena()
            try:
                cursor = CompilerIntArena()
            except Exception:
                spans.close()
                raise
        except Exception:
            records.close()
            raise
        self.records: CompilerIntArena = records
        self.spans: CompilerRecordSpanArena = spans
        self.cursor: CompilerIntArena = cursor
        self.symbol_names: list[str] = names
        self.symbol_ids: dict[str, int] = symbol_ids
        self.closed: bool = False
        self.semantics: AArch64InstructionSemantics | None = None
        if capture_semantics:
            try:
                self.semantics = AArch64InstructionSemantics()
            except Exception:
                self.close()
                raise

    def _require_open(self) -> None:
        if self.closed:
            raise RuntimeError("AArch64 emission fragments are closed")

    def new_fragment(self) -> CompilerInt2:
        self._require_open()
        return self.spans.new_span()

    def extend_fragment(self, destination: CompilerInt2, source: CompilerInt2) -> None:
        self._require_open()
        self.spans.extend(destination, source)

    def _append_record(self, fragment: CompilerInt2, record: CompilerInt4) -> None:
        self._require_open()
        self.spans.length(fragment)
        old_records = len(self.records)
        record_id = old_records // 4
        added_header = False
        semantics = self.semantics
        if semantics is not None:
            if len(semantics.headers) == old_records:
                semantics.append_unproven()
                added_header = True
            elif len(semantics.headers) != old_records + 4:
                raise EncodeError("AArch64 provenance record IDs do not match encoded records")
        try:
            self.records.append4(record.first, record.second, record.third, record.fourth)
            self.spans.append(fragment, record_id)
        except Exception:
            self.records.truncate(old_records)
            if semantics is not None and added_header:
                semantics.headers.truncate(old_records)
            raise

    def _append_typed(
        self, fragment: CompilerInt2, word: int, family: int, mnemonic: str,
        operands: list[CompilerInt4], kind: int = 0, symbol_id: int = -1,
    ) -> None:
        if self.semantics is None:
            self._append_record(fragment, CompilerInt4(word, family, kind, symbol_id))
        else:
            append_semantic_instruction(
                self, self.semantics, fragment, CompilerInt4(word, family, kind, symbol_id),
                semantic_opcode_id(mnemonic), FORM_CANONICAL, operands,
            )

    def append_word(self, fragment: CompilerInt2, word: int, family: int) -> None:
        if word < 0 or word > 0xFFFFFFFF:
            raise EncodeError("emission fragment word is outside uint32")
        if family not in (
            EMITTED_INSTRUCTION_MOVE,
            EMITTED_INSTRUCTION_SCALAR,
            EMITTED_INSTRUCTION_UNSCALED,
        ):
            raise EncodeError("emission fragment instruction family is unsupported")
        self._append_record(fragment, CompilerInt4(word, family, 0, -1))

    def append_label(self, fragment: CompilerInt2, name: str) -> None:
        self._require_open()
        validate_emitted_label_name(name)
        symbol_id = intern_emitted_symbol(name, self.symbol_ids, self.symbol_names)
        self._append_record(fragment, CompilerInt4(symbol_id, EMISSION_RECORD_LABEL, 0, -1))

    def append_memory_pair_barrier(self, fragment: CompilerInt2, begin: bool) -> None:
        """Record a boundary, not an instruction and not a hardware fence.

        Nesting is validated in replay order, not construction order: separate
        fragments may contribute opposite ends of the same protected region.
        Do not omit these events when assembling a sequence for optimization.
        """
        self._require_open()
        self.spans.length(fragment)
        old_records = len(self.records)
        try:
            self._append_record(
                fragment,
                CompilerInt4(1 if begin else 0, EMISSION_RECORD_MEMORY_PAIR_BARRIER, 0, -1),
            )
        except Exception:
            self.records.truncate(old_records)
            raise

    def _append_fixup(
        self, fragment: CompilerInt2, word: int, family: int, kind: int, target: str,
        mnemonic: str, register: str = "",
    ) -> None:
        # Validate the destination before publishing a symbol. Invalid/stale
        # roots must not change the shared interning table or record arena.
        self.spans.length(fragment)
        old_symbols = len(self.symbol_names)
        old_records = len(self.records)
        target_was_interned = target in self.symbol_ids
        try:
            symbol_id = intern_emitted_symbol(target, self.symbol_ids, self.symbol_names)
            if self.semantics is None:
                self._append_record(fragment, CompilerInt4(word, family, kind, symbol_id))
            else:
                operands = []
                if register:
                    operands.append(CompilerInt4(TOKEN_REGISTER, semantic_register_id(register), 0, 0))
                operands.append(CompilerInt4(TOKEN_SYMBOL, symbol_id, SYMBOL_PLAIN, 0))
                self._append_typed(fragment, word, family, mnemonic, operands, kind, symbol_id)
        except Exception:
            self.records.truncate(old_records)
            if not target_was_interned:
                # Interning inserts the dictionary entry before growing the
                # name list. Either allocation can fail independently.
                if len(self.symbol_names) > old_symbols:
                    self.symbol_names.pop()
                if target in self.symbol_ids:
                    del self.symbol_ids[target]
            raise

    def append_branch(
        self, fragment: CompilerInt2, mnemonic: str, target: str,
        register: str = "",
    ) -> None:
        """Keep branch targets symbolic until the final native text layout."""
        self._require_open()
        validate_emitted_label_name(target)
        word = encode_emitted_branch_base_parts(mnemonic, register)
        kind = STRUCTURED_FIXUP_BRANCH19
        if mnemonic == "b":
            kind = STRUCTURED_FIXUP_BRANCH26
        self._append_fixup(fragment, word, EMITTED_INSTRUCTION_SCALAR, kind, target, mnemonic, register)

    def append_call(self, fragment: CompilerInt2, target: str) -> None:
        self._require_open()
        validate_emitted_label_name(target)
        self._append_fixup(
            fragment, 0x94000000, EMITTED_INSTRUCTION_CALL, STRUCTURED_FIXUP_CALL, target, "bl",
        )

    def drop_fallthrough_branches(self, fragment: CompilerInt2) -> CompilerInt2:
        """Drop only B to the next matching L_ label, skipping EMPTY events.

        Every skipped empty event is retained in order. Directives, barriers,
        aliases and instructions block adjacency. The input must contain the
        complete module event sequence, never an instruction-only projection.
        """
        self._require_open()
        self.start_cursor(fragment)
        result = self.new_fragment()
        empty_ids = CompilerIntArena()
        try:
            pending_id = self.next_record_id()
            while pending_id >= 0:
                if pending_id >= len(self.records) // 4:
                    raise EncodeError("emission fragment record ID is invalid")
                pending: CompilerInt4 = self.records.get4_unchecked(pending_id)
                next_id = self.next_record_id()
                empty_ids.clear()
                candidate = (
                    pending.first == 0x14000000
                    and pending.second == EMITTED_INSTRUCTION_SCALAR
                    and pending.third == STRUCTURED_FIXUP_BRANCH26
                )
                drop = False
                if candidate:
                    if pending.fourth < 0 or pending.fourth >= len(self.symbol_names):
                        raise EncodeError("emission fragment branch symbol ID is invalid")
                    while next_id >= 0:
                        if next_id >= len(self.records) // 4:
                            raise EncodeError("emission fragment record ID is invalid")
                        following: CompilerInt4 = self.records.get4_unchecked(next_id)
                        if (
                            following.second != EMISSION_RECORD_EMPTY
                            or following.first != 0 or following.third != 0 or following.fourth != -1
                        ):
                            break
                        empty_ids.append(next_id)
                        next_id = self.next_record_id()
                    if next_id >= 0:
                        following: CompilerInt4 = self.records.get4_unchecked(next_id)
                        drop = (
                            following.second == EMISSION_RECORD_LABEL
                            and following.first == pending.fourth
                            and following.third == 0 and following.fourth == -1
                            and self.symbol_names[pending.fourth].startswith("L_")
                        )
                if not drop:
                    self.spans.append(result, pending_id)
                index = 0
                while index < len(empty_ids):
                    self.spans.append(result, empty_ids.get_unchecked(index))
                    index += 1
                pending_id = next_id
            return result
        finally:
            empty_ids.close()

    def append_nop(self, fragment: CompilerInt2) -> None:
        if self.semantics is None:
            self.append_word(fragment, encode_emitted_nop_parts(), EMITTED_INSTRUCTION_SCALAR)
            return
        self._append_typed(fragment, encode_emitted_nop_parts(), EMITTED_INSTRUCTION_SCALAR, "nop", [])

    def append_memory(
        self, fragment: CompilerInt2, mnemonic: str, register: str,
        base: str, offset: int = 0,
    ) -> None:
        word = encode_emitted_load_store_parts(mnemonic, register, base, offset)
        family = EMITTED_INSTRUCTION_SCALAR
        if mnemonic in ("ldur", "stur", "ldurb", "sturb", "ldurh", "sturh"):
            family = EMITTED_INSTRUCTION_UNSCALED
        if self.semantics is None:
            self.append_word(fragment, word, family)
            return
        operands = [
            CompilerInt4(TOKEN_REGISTER, semantic_register_id(register), 0, 0),
            CompilerInt4(TOKEN_ADDRESS, ADDRESS_BASE if offset == 0 else ADDRESS_OFFSET, 1 if offset == 0 else 2, 0),
            CompilerInt4(TOKEN_REGISTER, semantic_register_id(base), 0, 0),
        ]
        if offset != 0:
            operands.append(semantic_immediate_token(offset))
        self._append_typed(fragment, word, family, mnemonic, operands)

    def append_move(self, fragment: CompilerInt2, destination: str, source: str) -> None:
        word = encode_emitted_move_register_parts(destination, source)
        if self.semantics is None:
            self.append_word(fragment, word, EMITTED_INSTRUCTION_MOVE)
            return
        self._append_typed(fragment, word, EMITTED_INSTRUCTION_MOVE, "mov", [
            CompilerInt4(TOKEN_REGISTER, semantic_register_id(destination), 0, 0),
            CompilerInt4(TOKEN_REGISTER, semantic_register_id(source), 0, 0),
        ])

    def append_movewide(
        self, fragment: CompilerInt2, mnemonic: str, destination: str,
        immediate: int, shift: int = 0,
    ) -> None:
        word = encode_emitted_movewide_parts(mnemonic, destination, immediate, shift)
        if self.semantics is None:
            self.append_word(fragment, word, EMITTED_INSTRUCTION_MOVE)
            return
        operands = [
            CompilerInt4(TOKEN_REGISTER, semantic_register_id(destination), 0, 0),
            semantic_immediate_token(immediate),
        ]
        if shift:
            operands.append(CompilerInt4(TOKEN_SHIFT, SHIFT_NAMES.index("lsl"), shift, 1))
        self._append_typed(fragment, word, EMITTED_INSTRUCTION_MOVE, mnemonic, operands)

    def append_addsub_immediate(
        self, fragment: CompilerInt2, mnemonic: str, destination: str,
        left: str, immediate: int,
    ) -> None:
        word = encode_emitted_addsub_immediate_parts(mnemonic, destination, left, immediate)
        if self.semantics is None:
            self.append_word(fragment, word, EMITTED_INSTRUCTION_SCALAR)
            return
        self._append_typed(fragment, word, EMITTED_INSTRUCTION_SCALAR, mnemonic, [
            CompilerInt4(TOKEN_REGISTER, semantic_register_id(destination), 0, 0),
            CompilerInt4(TOKEN_REGISTER, semantic_register_id(left), 0, 0),
            semantic_immediate_token(immediate),
        ])

    def append_addsub_register(
        self, fragment: CompilerInt2, mnemonic: str, destination: str,
        left: str, right: str,
    ) -> None:
        word = encode_emitted_addsub_register_parts(mnemonic, destination, left, right)
        if self.semantics is None:
            self.append_word(fragment, word, EMITTED_INSTRUCTION_SCALAR)
            return
        self._append_typed(fragment, word, EMITTED_INSTRUCTION_SCALAR, mnemonic, [
            CompilerInt4(TOKEN_REGISTER, semantic_register_id(destination), 0, 0),
            CompilerInt4(TOKEN_REGISTER, semantic_register_id(left), 0, 0),
            CompilerInt4(TOKEN_REGISTER, semantic_register_id(right), 0, 0),
        ])

    def start_cursor(self, fragment: CompilerInt2) -> None:
        self._require_open()
        self.spans.start_cursor(fragment, self.cursor)

    def next_record_id(self) -> int:
        self._require_open()
        return self.spans.next_record(self.cursor)

    def reset(self) -> None:
        self._require_open()
        self.spans.reset()
        self.records.clear()
        self.cursor.clear()
        if self.semantics is not None:
            self.semantics.reset()
        self.symbol_names.clear()
        self.symbol_ids.clear()

    def close(self) -> None:
        if self.closed:
            return
        if self.semantics is not None:
            self.semantics.close()
        self.cursor.close()
        self.spans.close()
        self.records.close()
        self.symbol_names.clear()
        self.symbol_ids.clear()
        self.closed = True
