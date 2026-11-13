"""Exact scalar provenance pairing and noninstruction boundary cases."""
import pytest

from pcc.backend import self_backend_aarch64_typed_pair as typed
from pcc.backend.self_backend_aarch64_semantics import (
    TOKEN_REGISTER, TOKEN_ADDRESS, TOKEN_IMMEDIATE, ADDRESS_OFFSET,
    REGISTER_SP, REGISTER_XZR,
)


class PairEvents:
    def __init__(self):
        self.events = []

    def add(self, opcode, operands=(), barrier=None, text=None):
        record = len(self.events)
        self.events.append((opcode, list(operands), barrier, text))
        return record

    def emit(self, opcode, operands):
        return self.add(opcode, operands)

    def opcode(self, record):
        return self.events[record][0]

    def operands(self, record):
        return list(self.events[record][1])

    def memory_pair_barrier(self, record):
        return self.events[record][2]

    def transfer(self, opcode, reg, base, offset):
        return self.add(opcode, [(TOKEN_REGISTER, reg, 0, 0),
            (TOKEN_ADDRESS, ADDRESS_OFFSET, 2, 0), (TOKEN_REGISTER, base, 0, 0),
            (TOKEN_IMMEDIATE, offset, 10, 1)])


def test_pair_output_retains_canonical_provenance():
    ctx = PairEvents()
    records = [ctx.transfer("stur", 9, 29, -16), ctx.transfer("str", 10, 29, -8)]
    out = typed.pair_adjacent_aarch64_64bit_memory_ops_records(ctx, records)
    assert len(out) == 1
    assert ctx.opcode(out[0]) == "stp"
    assert ctx.operands(out[0]) == [(TOKEN_REGISTER, 9, 0, 0), (TOKEN_REGISTER, 10, 0, 0),
        (TOKEN_ADDRESS, ADDRESS_OFFSET, 2, 0), (TOKEN_REGISTER, 29, 0, 0),
        (TOKEN_IMMEDIATE, -16, 10, 1)]


@pytest.mark.parametrize("offset,expected", [(-520, 2), (-512, 1), (0, 1), (504, 1), (512, 2), (3, 2)])
def test_pair_signed_scaled_range(offset, expected):
    ctx = PairEvents()
    records = [ctx.transfer("ldr", 9, REGISTER_SP, offset), ctx.transfer("ldur", 10, REGISTER_SP, offset + 8)]
    assert len(typed.pair_adjacent_aarch64_64bit_memory_ops_records(ctx, records)) == expected


@pytest.mark.parametrize("first,second,base", [(9, 9, 29), (29, 9, 29), (9, 29, 29)])
def test_load_pair_rejects_overlap(first, second, base):
    ctx = PairEvents()
    records = [ctx.transfer("ldr", first, base, 0), ctx.transfer("ldr", second, base, 8)]
    assert typed.pair_adjacent_aarch64_64bit_memory_ops_records(ctx, records) == records


@pytest.mark.parametrize("reg,expected", [(REGISTER_XZR, 1), (9, 2)])
def test_descending_stores_only_reverse_zero(reg, expected):
    ctx = PairEvents()
    records = [ctx.transfer("stur", reg, 29, -8), ctx.transfer("stur", reg, 29, -16)]
    assert len(typed.pair_adjacent_aarch64_64bit_memory_ops_records(ctx, records)) == expected


@pytest.mark.parametrize("enabled", [True, False])
def test_nested_barriers_are_removed_without_pairing_protected_records(enabled):
    ctx = PairEvents()
    first = ctx.transfer("str", 9, REGISTER_SP, 0)
    second = ctx.transfer("str", 10, REGISTER_SP, 8)
    begin = ctx.add("", barrier=True)
    end = ctx.add("", barrier=False)
    assert typed.pair_adjacent_aarch64_64bit_memory_ops_records(ctx, [begin, begin, first, second, end, end], enabled=enabled) == [first, second]


@pytest.mark.parametrize("opening", ["ldxr", "ldaxr", "ldxp", "ldaxp"])
@pytest.mark.parametrize("closing", ["stxr", "stlxr", "stxp", "stlxp", "clrex"])
def test_exclusive_monitor_intervals(opening, closing):
    ctx = PairEvents()
    first = ctx.transfer("str", 9, REGISTER_SP, 0)
    second = ctx.transfer("str", 10, REGISTER_SP, 8)
    start = ctx.add(opening)
    stop = ctx.add(closing)
    out = typed.pair_adjacent_aarch64_64bit_memory_ops_records(ctx, [start, first, second, stop, first, second])
    assert out[:4] == [start, first, second, stop]
    assert len(out) == 5


@pytest.mark.parametrize("begin", [True, False])
def test_unbalanced_barrier_fails_closed(begin):
    ctx = PairEvents()
    with pytest.raises(Exception, match="barrier"):
        typed.pair_adjacent_aarch64_64bit_memory_ops_records(ctx, [ctx.add("", barrier=begin)])
