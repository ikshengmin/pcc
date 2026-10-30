"""Experimental one-shot reuse of the canonical measured AArch64 assembler.

No production module imports this package. See README.md for the private
ownership preconditions, memory HOLD and limits of host-only evidence.
"""
from __future__ import annotations

from pcc.backend import arm64_asm_driver as _driver
from pcc.backend.arm64_encode import EncodeError


class OwnedMeasuredAssembly:
    """One fresh indexed-worker assembly; never a mutable transport cache.

    The caller owns the decoded module, prefix, record arena, and symbol list.
    After measure(), only emitter-owned non-text metadata may be appended.
    Public/reusable transports must not enter this one-shot private protocol.
    """

    def __init__(self) -> None:
        self.builder: _driver.AArch64ModuleBuilder | None = None
        self.prefix_count: int = 0
        self.closed: bool = False

    def measure(self, lines, encoded_line_records, symbol_names):
        if self.closed or self.builder is not None:
            raise EncodeError("owned assembly measurement is not fresh")
        builder = _driver.AArch64ModuleBuilder(symbol_names)
        self.builder = builder
        try:
            _driver._append_assembly_lines(builder, lines, encoded_line_records)
            builder.finalize_text_layout()
            self.prefix_count = len(lines)
            return dict(builder.text_label_offsets()), builder.text_size()
        except Exception:
            self.close()
            raise

    def release_prefix(self, lines, encoded_line_records) -> None:
        """Transfer private already-parsed inputs; no public transport uses it."""
        if self.closed or self.builder is None or len(lines) != self.prefix_count:
            raise EncodeError("owned assembly prefix release is invalid")
        # Both containers were created inside this fresh PCO emitter. The
        # builder owns the parsed words/data; no later metadata reader needs
        # these input containers. Its symbol-name table remains live.
        lines.clear()
        if encoded_line_records is not None:
            encoded_line_records.close()
        self.prefix_count = 0

    def assemble(self, transport):
        if self.closed:
            raise EncodeError("owned assembly is closed")
        try:
            builder = self.builder
            if builder is None:
                # Empty modules and the existing unoptimized native-finalized
                # route did not call measure(); retain their ordinary path.
                return transport.assemble_sections()
            if transport.native_finalized:
                raise EncodeError("owned measured assembly is already native")
            if len(transport.line_chunks) < self.prefix_count:
                raise EncodeError("owned measured assembly prefix was truncated")
            index = self.prefix_count
            while index < len(transport.line_chunks):
                builder.append_chunk(transport.line_chunks[index])
                index += 1
            return builder.finish(transport.structured_sections)
        finally:
            self.close()

    def close(self) -> None:
        builder = self.builder
        self.builder = None
        self.closed = True
        if builder is not None:
            builder.close()
