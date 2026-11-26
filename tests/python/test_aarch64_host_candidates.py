"""Typed peepholes find their candidate windows at C speed on the CPython host.

Every window a typed peephole can rewrite starts with one of a few opcodes
(a stack store, mov, movz, cset, cmp/fcmp, ldr/ldur, or a record without an
instruction header for the label checks of mode 14), yet each pass tested
every position in Python. ``_host_positions`` finds the positions from the
header opcodes with one bytes scan and the passes bulk-copy the IDs between
them; native arenas keep the record-by-record scan. The output must equal
that scan's. Replaying two Stage1 codegen workers (five alternating pairs,
objects byte-identical): array_core 8.13 -> 6.98 s CPU, 297 4.41 -> 3.84 s.
"""
from __future__ import annotations

import pytest

from pcc.backend import self_backend_aarch64_semantics as sem
from pcc.backend import self_backend_aarch64_typed_peepholes as p
from tests.python import test_aarch64_typed_peepholes as o

I, R, N, C, S, A = o.I, o.R, o.N, o.C, o.S, o.A

ROUTES = {
    1: lambda f, s, span: p._run_owned_stack(f, s, span, 1),
    2: lambda f, s, span: p._run_owned_stack(f, s, span, 2),
    3: lambda f, s, span: p._run_owned_store(f, s, span, 3),
    4: lambda f, s, span: p._run_owned_store(f, s, span, 4),
    5: p._run_owned_zero_compare,
    6: lambda f, s, span: p._run_owned_move_matchers(f, s, span, 6),
    7: lambda f, s, span: p._run_owned_move_matchers(f, s, span, 7),
    8: p._run_owned_arith,
    9: lambda f, s, span: p._run_owned_sparse(f, s, span, 9),
    10: lambda f, s, span: p._run_owned_move_matchers(f, s, span, 10),
    11: p._run_owned_zero_branch,
    12: p._run_owned_cset,
    13: lambda f, s, span: p._run_owned_sparse(f, s, span, 13),
    14: lambda f, s, span: p._run_owned_move_matchers(f, s, span, 14),
    15: p.fold_sign_extending_load,
}

FILLER = [I("add", R("x1"), R("x2"), N(3)), "L_mid:", I("mov", R("x3"), R("x4"))]
SIGN_EXTEND = [I("ldr", R("w9"), (o.CompilerInt4(sem.TOKEN_ADDRESS, sem.ADDRESS_OFFSET, 2, 0), R("x19"), N(8))),
               I("sxtw", R("x9"), R("w9")), I("ret")]


def mixed(mode=0):
    """Every case window, apart and back to back, between unrelated records.

    MODE's own window comes last too, where its scratch register is dead.
    """
    items = ["_fn:"]
    for _name, case in o.CASES:
        items += [entry for entry in case if entry != "_fn:"] + FILLER
    for _name, case in o.CASES:
        items += [entry for entry in case if entry != "_fn:"]
    items += SIGN_EXTEND + FILLER
    if 1 <= mode <= len(o.CASES):
        items += [entry for entry in o.CASES[mode - 1][1] if entry != "_fn:"]
    elif mode == 15:
        items += SIGN_EXTEND
    return items


def signature(f, s, span):
    rows = []
    for rid in o._ids(f, span):
        code = f.records.get4_unchecked(rid)
        if code.second < 0:
            rows.append(("event", rid))
            continue
        header = s.require_header(rid)
        tokens = [s.tokens.get4_unchecked(header.third + index) for index in range(header.fourth)]
        rows.append((code.first, code.second, code.third, code.fourth, header.first,
                     tuple((t.first, t.second, t.third, t.fourth) for t in tokens)))
    return rows


def run(monkeypatch, mode, owned, host):
    f, s, span, _lines = o._fixture(mixed(mode))
    if owned:
        f.semantics = s
    found = []
    original = p._host_positions
    def observed(pass_, opcodes):
        result = original(pass_, opcodes) if host else None
        found.append(result is not None)
        return result
    try:
        with monkeypatch.context() as patch:
            patch.setattr(p, "_host_positions", observed)
            result = ROUTES[mode](f, s, span)
        return signature(f, s, result), signature(f, s, span), found
    finally:
        s.close()
        f.close()


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("mode", sorted(ROUTES))
def test_host_candidates_match_the_record_by_record_scan(monkeypatch, mode, owned):
    fast, fast_input, fast_found = run(monkeypatch, mode, owned, True)
    slow, slow_input, slow_found = run(monkeypatch, mode, owned, False)
    assert fast_found and all(fast_found), "host candidate search not taken"
    assert slow_found and not any(slow_found)
    assert fast == slow
    assert fast_input == slow_input
    if mode != 14:
        # The mixed sequence holds a rewritable window for every pass.
        assert fast != fast_input, "the pass rewrote nothing"


@pytest.mark.parametrize("owned", [False, True])
def test_host_positions_equal_a_scan_of_the_header_opcodes(owned):
    f, s, span, _lines = o._fixture(mixed())
    if owned:
        f.semantics = s
    try:
        pass_ = p._Pass(f, s, span)
        try:
            ids = o._ids(f, span)
            opcodes = [s.headers.get4_unchecked(rid).first for rid in ids]
            for wanted in [(sem.semantic_opcode_id("mov"),), (0,),
                           (sem.semantic_opcode_id("cmp"), sem.semantic_opcode_id("fcmp")),
                           (sem.semantic_opcode_id("stur"), sem.semantic_opcode_id("sturb"))]:
                expected = [index for index, opcode in enumerate(opcodes) if opcode in wanted]
                assert p._host_positions(pass_, wanted) == expected
                assert expected
            # The per-record cache grows with the records and stays exact.
            assert p._host_positions(pass_, (sem.semantic_opcode_id("mov"),)) == [
                index for index, opcode in enumerate(opcodes) if opcode == sem.semantic_opcode_id("mov")]
            if owned:
                assert len(f.decoded_opcode_codes) == len(f.records) // 4
            # No candidate search at or above code 254, nor on native arenas.
            assert p._host_positions(pass_, (254,)) is None
            f.records._address = 1
            try:
                assert p._host_positions(pass_, (sem.semantic_opcode_id("mov"),)) is None
            finally:
                f.records._address = 0
        finally:
            pass_.close()
    finally:
        s.close()
        f.close()


def test_reset_drops_the_opcode_cache():
    f, s, span, _lines = o._fixture(mixed())
    f.semantics = s
    try:
        pass_ = p._Pass(f, s, span)
        try:
            p._host_positions(pass_, (sem.semantic_opcode_id("mov"),))
        finally:
            pass_.close()
        assert len(f.decoded_opcode_codes) > 0
        f.reset()
        assert len(f.decoded_opcode_codes) == 0
    finally:
        s.close()
        f.close()
