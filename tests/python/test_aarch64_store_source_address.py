"""Store-source safety: explicit A64 words and independent restricted decoder.

Writeback-overlap cases assert preservation only, not a deterministic ISA effect.
"""
import pytest
from tests.python import test_aarch64_typed_peepholes as o
from pcc.backend import self_backend_aarch64_typed_peepholes as p
from pcc.backend import self_backend_aarch64_semantics as s
from pcc.backend.self_backend_value_arena import CompilerInt4


def fixture(mode, old='x9', source='x19', base='x20', address='base', index='x10'):
    first = o.I('movz', o.R(old), o.N(0)) if mode == 3 else o.I('mov', o.R(old), o.R(source))
    f, sem, span, lines = o._fixture([first])
    rt = int(old[1:]); rn = 31 if base == 'sp' else int(base[1:])
    width = 0x40000000 if old.startswith('x') else 0
    if address == 'base':
        tokens = [CompilerInt4(s.TOKEN_ADDRESS, s.ADDRESS_BASE, 1, 0), o.R(base)]
        word = 0xb9000000 | width | (rn << 5) | rt
        addr = '[' + base + ']'
    elif address == 'index':
        rm = int(index[1:])
        tokens = [CompilerInt4(s.TOKEN_ADDRESS, s.ADDRESS_REGISTER, 2, 0), o.R(base), o.R(index)]
        word = 0xb8206800 | width | (rm << 16) | (rn << 5) | rt
        addr = '[' + base + ', ' + index + ']'
    else:
        kind = s.ADDRESS_PREINDEX if address == 'pre' else s.ADDRESS_POSTINDEX
        tokens = [CompilerInt4(s.TOKEN_ADDRESS, kind, 2, 0), o.R(base), o.N(8)]
        word = 0xb8000000 | width | (8 << 12) | (0xc00 if address == 'pre' else 0x400) | (rn << 5) | rt
        addr = '[' + base + ', #8]!' if address == 'pre' else '[' + base + '], #8'
    s.append_semantic_instruction(f, sem, span, CompilerInt4(word, o.enc.EMITTED_INSTRUCTION_SCALAR, 0, -1), s.semantic_opcode_id('str'), s.FORM_CANONICAL, [o.R(old)] + tokens)
    lines.append('  str ' + old + ', ' + addr)
    return f, sem, span, lines


def run_case(mode, changed, **kwargs):
    f, sem, span, lines = fixture(mode, **kwargs)
    try:
        ids = o._ids(f, span)
        before = [f.records.get4_unchecked(i).first for i in ids]
        result = p._run(f, sem, span, mode)
        after = [f.records.get4_unchecked(i).first for i in o._ids(f, result)]
        assert o._ids(f, span) == ids
        assert len(after) == (1 if changed else 2), 'store rewrite output length'
        if not changed:
            assert after == before
        legacy = getattr(o.legacy, '_fold_zero_store_source' if mode == 3 else '_fold_mov_store_source')(lines)
        assert (legacy != lines) == changed
        return before, after
    finally:
        sem.close(); f.close()


@pytest.mark.parametrize('mode', [3, 4])
@pytest.mark.parametrize('old', ['x9', 'w9'])
@pytest.mark.parametrize('address', ['base', 'index', 'pre', 'post'])
def test_address_definition_and_other_width_alias_not_removed(mode, old, address):
    # Pre/post input overlap is constrained-unpredictable: preserve, don't emulate.
    run_case(mode, False, old=old, source='x19' if old == 'x9' else 'w19',
             base='x20' if address == 'index' else 'x9', address=address, index='x9')


@pytest.mark.parametrize('old,source', [('x9', 'sp'), ('w9', 'wsp')])
def test_stack_pointer_is_not_zero_register(old, source):
    run_case(4, False, old=old, source=source)


@pytest.mark.parametrize('old,source', [('x9', 'x19'), ('w9', 'w19')])
@pytest.mark.parametrize('address', ['pre', 'post'])
def test_no_new_writeback_rt_rn_overlap(old, source, address):
    run_case(4, False, old=old, source=source, base='x19', address=address)


@pytest.mark.parametrize('mode,old,source,base,address', [
    (3, 'x9', 'x19', 'sp', 'base'),
    (3, 'w9', 'w19', 'x20', 'base'),
    (4, 'x9', 'x19', 'x19', 'base'),  # No writeback: source/base overlap is safe.
    (4, 'w9', 'w19', 'sp', 'base'),
    (4, 'x9', 'xzr', 'x20', 'base'),
    (4, 'w9', 'wzr', 'x20', 'base'),
    (4, 'x9', 'x19', 'x20', 'pre'),
    (4, 'w9', 'w19', 'x20', 'post'),
])
def test_safe_rewrites_remain_enabled(mode, old, source, base, address):
    run_case(mode, True, old=old, source=source, base=base, address=address)


def decode(words):
    """Only MOVZ, MOV(ORR), MOV-from-SP(ADD #0), STR unsigned.

    No production encoder/semantics helpers are used to determine effects.
    """
    regs = [0] * 31
    regs[9], regs[19], regs[20] = 4096, 16384, 8192
    sp, writes = 12288, []
    for word in words:
        wide = bool(word & 0x80000000)
        mask = (1 << (64 if wide else 32)) - 1
        rd, rn = word & 31, (word >> 5) & 31
        if word & 0x7fe00000 == 0x52800000:
            assert rd != 31
            regs[rd] = ((word >> 5) & 65535) & mask
        elif word & 0x7fe0ffe0 == 0x2a0003e0:
            rm = (word >> 16) & 31
            regs[rd] = (0 if rm == 31 else regs[rm]) & mask
        elif word & 0x7fc00000 == 0x11000000:
            assert rn == 31 and ((word >> 10) & 4095) == 0
            regs[rd] = sp & mask
        elif word & 0xbfc00000 == 0xb9000000:
            size = 8 if word & 0x40000000 else 4
            addr = (sp if rn == 31 else regs[rn]) + ((word >> 10) & 4095) * size
            value = 0 if rd == 31 else regs[rd] & ((1 << (size * 8)) - 1)
            writes.append((addr, value))
        else:
            raise AssertionError('unsupported independent decoder word ' + hex(word))
    return writes


@pytest.mark.parametrize('mode,old,source,base,changed,expected', [
    (3, 'x9', 'x19', 'x9', False, [(0, 0)]),
    (3, 'w9', 'w19', 'x9', False, [(0, 0)]),
    (4, 'x9', 'x19', 'x9', False, [(16384, 16384)]),
    (4, 'w9', 'wsp', 'x20', False, [(8192, 12288)]),
    (4, 'x9', 'x19', 'x19', True, [(16384, 16384)]),
    (3, 'x9', 'x19', 'x20', True, [(8192, 0)]),
])
def test_independent_machine_word_effects(mode, old, source, base, changed, expected):
    before, after = run_case(mode, changed, old=old, source=source, base=base)
    assert decode(before) == expected
    assert decode(after) == expected


@pytest.mark.parametrize('guard,kwargs', [
    ('store_address_uses', dict(mode=3, old='x9', base='x9')),
    ('store_replacement_safe', dict(mode=4, old='w9', source='wsp')),
    ('store_replacement_safe', dict(mode=4, old='x9', source='x19', base='x19', address='post')),
])
def test_missing_guard_mutants_are_killed(monkeypatch, guard, kwargs):
    # Positive control, then require an output assertion (not fixture exception).
    run_case(changed=False, **kwargs)
    monkeypatch.setattr(p._Pass, guard, lambda *args: guard != 'store_address_uses')
    with pytest.raises(AssertionError, match="store rewrite output length"):
        run_case(changed=False, **kwargs)
