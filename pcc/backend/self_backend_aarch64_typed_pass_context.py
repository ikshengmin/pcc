"""Transient integer sequence views for producer-proven AArch64 passes.

Retained instruction provenance is exclusively in the aligned semantic arena.
This adapter does not decode machine words or parse/render instruction strings.
"""
from __future__ import annotations

from .arm64_encode import EncodeError
from .self_backend_aarch64_fragments import (
    EMISSION_RECORD_LABEL, EMISSION_RECORD_MEMORY_PAIR_BARRIER,
    EMISSION_RECORD_EMPTY, EMISSION_RECORD_DIRECTIVE,
)
from .self_backend_aarch64_semantics import (
    FORM_CANONICAL, OPCODE_NAMES, append_semantic_instruction, semantic_opcode_id,
)
from .self_backend_value_arena import CompilerInt4, CompilerIntArena


class TypedPassContext:
    def __init__(self, fragments, semantics):
        self.fragments = fragments
        self.semantics = semantics
        # Producers validate every event and instruction when appending it;
        # the per-read structure checks below run under the debug switch.
        self.verify_each = bool(getattr(semantics, "verify_each", True))
        semantics.require_complete(fragments.records, len(fragments.symbol_names))
        self.replacements = fragments.new_fragment()

    def record(self, record_id):
        if record_id < 0 or record_id >= len(self.fragments.records) // 4:
            raise EncodeError("AArch64 typed pass record ID is invalid")
        record = self.fragments.records.get4_unchecked(record_id)
        if not self.verify_each:
            return record
        if record.second == EMISSION_RECORD_LABEL:
            self.symbol(record.first)
            if record.third != 0 or record.fourth != -1:
                raise EncodeError("AArch64 typed pass label event is invalid")
        elif record.second == EMISSION_RECORD_MEMORY_PAIR_BARRIER:
            if record.first not in (0, 1) or record.third != 0 or record.fourth != -1:
                raise EncodeError("AArch64 typed pass barrier event is invalid")
        elif record.second == EMISSION_RECORD_EMPTY:
            if record.first != 0 or record.third != 0 or record.fourth != -1:
                raise EncodeError("AArch64 typed pass empty event is invalid")
        elif record.second == EMISSION_RECORD_DIRECTIVE:
            if not 1 <= record.first <= 11:
                raise EncodeError("AArch64 typed pass directive event is unknown")
            if record.fourth != -1:
                self.symbol(record.fourth)
        elif record.second < 0:
            raise EncodeError("AArch64 typed pass event family is unknown")
        return record

    def opcode(self, record_id):
        record: CompilerInt4 = self.record(record_id)
        if record.second < 0:
            return ""
        names = OPCODE_NAMES
        header: CompilerInt4 = self.semantics.require_header(record_id)
        return names[header.first]

    def operands(self, record_id):
        record: CompilerInt4 = self.record(record_id)
        if record.second < 0:
            return []
        header = self.semantics.require_header(record_id)
        tokens = []
        for index in range(header.fourth):
            token = self.semantics.tokens.get4_unchecked(header.third + index)
            tokens.append((token.first, token.second, token.third, token.fourth))
        return tokens

    def symbol(self, symbol_id):
        if symbol_id < 0 or symbol_id >= len(self.fragments.symbol_names):
            raise EncodeError("AArch64 typed pass symbol ID is invalid")
        return self.fragments.symbol_names[symbol_id]

    def label(self, record_id):
        record = self.record(record_id)
        if record.second == EMISSION_RECORD_LABEL:
            self.symbol(record.first)
            return record.first
        return None

    def is_empty(self, record_id):
        record: CompilerInt4 = self.record(record_id)
        return record.second == EMISSION_RECORD_EMPTY

    def is_directive(self, record_id):
        record: CompilerInt4 = self.record(record_id)
        return record.second in (EMISSION_RECORD_DIRECTIVE, EMISSION_RECORD_MEMORY_PAIR_BARRIER)

    def directive_kind(self, record_id):
        record = self.record(record_id)
        return record.first if record.second == EMISSION_RECORD_DIRECTIVE else 0

    def memory_pair_barrier(self, record_id):
        record = self.record(record_id)
        if record.second == EMISSION_RECORD_MEMORY_PAIR_BARRIER:
            if record.first not in (0, 1):
                raise EncodeError("AArch64 typed pass barrier is invalid")
            return record.first == 1
        return None

    def emit(self, opcode, operands):
        # The shared encoder consumes selected operands directly. Publication
        # is transactional and occurs only after encoding succeeds.
        from .self_backend_aarch64_semantic_encode import encode_semantic_instruction
        tokens = [CompilerInt4(token[0], token[1], token[2], token[3]) for token in operands]
        opcode_id = semantic_opcode_id(opcode)
        encoded = encode_semantic_instruction(opcode_id, tokens)
        return append_semantic_instruction(
            self.fragments, self.semantics, self.replacements, encoded,
            opcode_id, FORM_CANONICAL, tokens,
        )

    def validate_span(self, span):
        self.read_span(span)

    def read_span(self, span):
        # One bulk span copy; every record is still validated by record().
        ids = CompilerIntArena()
        try:
            self.fragments.copy_record_ids(span, ids)
            records = []
            index = 0
            while index < len(ids):
                record_id = ids.get_unchecked(index)
                self.record(record_id)
                records.append(record_id)
                index += 1
            return records
        finally:
            ids.close()

    def write_span(self, records):
        # The captured output becomes one run node, never a node per record.
        span = self.fragments.new_fragment()
        self.fragments.spans.append_id_list(span, records)
        return span
