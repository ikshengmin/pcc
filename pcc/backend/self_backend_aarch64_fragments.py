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
from .self_backend_value_arena import (
    CompilerInt2,
    CompilerInt4,
    CompilerIntArena,
    CompilerRecordSpanArena,
)


EMISSION_RECORD_LABEL = -1


class AArch64EmissionFragments:
    """Native records plus persistent sequence roots for one emission scope.

    Instruction records use (word, family, relocation kind, symbol ID), just
    like the direct encoder. A negative family identifies a label whose first
    field indexes the owned symbol side table. No record contains a pointer.
    This first producer slice encodes pointer reloads; unsupported operand
    families still fail through the canonical encoder before publication.
    """

    def __init__(self) -> None:
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
        record_id = len(self.records) // 4
        self.records.append4(record.first, record.second, record.third, record.fourth)
        # If publication raises, no new record becomes visible in the span.
        # The scope still owns the unpublished storage and closes it on error.
        self.spans.append(fragment, record_id)

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

    def _append_fixup(
        self, fragment: CompilerInt2, word: int, family: int, kind: int, target: str,
    ) -> None:
        # Validate the destination before publishing a symbol. Invalid/stale
        # roots must not change the shared interning table or record arena.
        self.spans.length(fragment)
        old_symbols = len(self.symbol_names)
        old_records = len(self.records)
        target_was_interned = target in self.symbol_ids
        try:
            symbol_id = intern_emitted_symbol(target, self.symbol_ids, self.symbol_names)
            self._append_record(fragment, CompilerInt4(word, family, kind, symbol_id))
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
        self._append_fixup(fragment, word, EMITTED_INSTRUCTION_SCALAR, kind, target)

    def append_call(self, fragment: CompilerInt2, target: str) -> None:
        self._require_open()
        validate_emitted_label_name(target)
        self._append_fixup(
            fragment, 0x94000000, EMITTED_INSTRUCTION_CALL, STRUCTURED_FIXUP_CALL, target,
        )

    def drop_fallthrough_branches(self, fragment: CompilerInt2) -> CompilerInt2:
        """Return a new sequence root, retaining every label and source record.

        This is the typed counterpart of the single fallthrough-B peephole,
        not the complete optimized-native pipeline. Only a B immediately
        followed by its own L_ label is removed. Alias labels, calls and
        conditional branches are not crossed or changed. Retained record IDs
        remain stable for metadata users; layout happens after this rewrite.
        The fragment must contain the complete sequence being rewritten, not
        a projection with intervening directives or barriers omitted. This
        component does not authorize optimized production routing.
        """
        self._require_open()
        self.start_cursor(fragment)
        result = self.new_fragment()
        pending_id = self.next_record_id()
        while pending_id >= 0:
            if pending_id >= len(self.records) // 4:
                raise EncodeError("emission fragment record ID is invalid")
            pending: CompilerInt4 = self.records.get4_unchecked(pending_id)
            next_id = self.next_record_id()
            drop = False
            if (
                pending.first == 0x14000000
                and pending.second == EMITTED_INSTRUCTION_SCALAR
                and pending.third == STRUCTURED_FIXUP_BRANCH26
                and next_id >= 0
            ):
                if pending.fourth < 0 or pending.fourth >= len(self.symbol_names):
                    raise EncodeError("emission fragment branch symbol ID is invalid")
                if next_id >= len(self.records) // 4:
                    raise EncodeError("emission fragment record ID is invalid")
                following: CompilerInt4 = self.records.get4_unchecked(next_id)
                drop = (
                    following.second == EMISSION_RECORD_LABEL
                    and following.first == pending.fourth
                    and following.third == 0 and following.fourth == -1
                    and self.symbol_names[pending.fourth].startswith("L_")
                )
            if not drop:
                self.spans.append(result, pending_id)
            pending_id = next_id
        return result

    def append_nop(self, fragment: CompilerInt2) -> None:
        self.append_word(fragment, encode_emitted_nop_parts(), EMITTED_INSTRUCTION_SCALAR)

    def append_memory(
        self, fragment: CompilerInt2, mnemonic: str, register: str,
        base: str, offset: int = 0,
    ) -> None:
        word = encode_emitted_load_store_parts(mnemonic, register, base, offset)
        family = EMITTED_INSTRUCTION_SCALAR
        if mnemonic in ("ldur", "stur", "ldurb", "sturb", "ldurh", "sturh"):
            family = EMITTED_INSTRUCTION_UNSCALED
        self.append_word(fragment, word, family)

    def append_move(self, fragment: CompilerInt2, destination: str, source: str) -> None:
        word = encode_emitted_move_register_parts(destination, source)
        self.append_word(fragment, word, EMITTED_INSTRUCTION_MOVE)

    def append_movewide(
        self, fragment: CompilerInt2, mnemonic: str, destination: str,
        immediate: int, shift: int = 0,
    ) -> None:
        word = encode_emitted_movewide_parts(mnemonic, destination, immediate, shift)
        self.append_word(fragment, word, EMITTED_INSTRUCTION_MOVE)

    def append_addsub_immediate(
        self, fragment: CompilerInt2, mnemonic: str, destination: str,
        left: str, immediate: int,
    ) -> None:
        word = encode_emitted_addsub_immediate_parts(mnemonic, destination, left, immediate)
        self.append_word(fragment, word, EMITTED_INSTRUCTION_SCALAR)

    def append_addsub_register(
        self, fragment: CompilerInt2, mnemonic: str, destination: str,
        left: str, right: str,
    ) -> None:
        word = encode_emitted_addsub_register_parts(mnemonic, destination, left, right)
        self.append_word(fragment, word, EMITTED_INSTRUCTION_SCALAR)

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
        self.symbol_names.clear()
        self.symbol_ids.clear()

    def close(self) -> None:
        if self.closed:
            return
        self.cursor.close()
        self.spans.close()
        self.records.close()
        self.symbol_names.clear()
        self.symbol_ids.clear()
        self.closed = True
