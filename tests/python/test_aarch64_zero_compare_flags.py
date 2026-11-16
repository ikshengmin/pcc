"""Independent A64 flag oracle for zero-compare folding, including live flags."""
import inspect
import pytest
from tests.python import test_aarch64_typed_peepholes as o
from pcc.backend import self_backend_aarch64_typed_peepholes as typed
from pcc.backend import self_backend_aarch64_semantics as sem
from pcc.backend.self_backend_value_arena import CompilerInt4


def effects(words):
    regs = [0] * 31
    regs[19], sp = 7, 4096
    flags, result_w0 = None, None
    for word in words:
        bits = 64 if word & 0x80000000 else 32
        mask, sign = (1 << bits) - 1, 1 << (bits - 1)
        rn, rd = (word >> 5) & 31, word & 31
        if word == 0x1a9f17e0:  # CSINC W0,WZR,WZR,NE, aka CSET W0,EQ.
            assert flags is not None
            result_w0 = flags[1]
            continue
        if word & 0x7f800000 == 0x52800000:
            assert rd != 31
            regs[rd] = (((word >> 5) & 65535) << (16 * ((word >> 21) & 3))) & mask
            continue
        shape = word & 0x7fe0fc00
        if shape == 0x6b000000:
            rm = (word >> 16) & 31
            left, right = (0 if rn == 31 else regs[rn]), (0 if rm == 31 else regs[rm])
        elif shape in (0x6b206000, 0x6b204000):
            # SUBS extended, UXTX/UXTW #0: Rn31 is SP.
            assert shape == (0x6b206000 if bits == 64 else 0x6b204000)
            rm = (word >> 16) & 31
            left, right = (sp if rn == 31 else regs[rn]), (0 if rm == 31 else regs[rm])
        elif word & 0x7fc00000 == 0x71000000:
            assert not word & (1 << 22)
            left, right = (sp if rn == 31 else regs[rn]), (word >> 10) & 4095
        else:
            raise AssertionError('unsupported independent decoder ' + hex(word))
        assert rd == 31
        left, right = left & mask, right & mask
        value = (left - right) & mask
        flags = (int(bool(value & sign)), int(value == 0), int(left >= right), int(bool((left ^ right) & (left ^ value) & sign)))
    return flags, result_w0


def sequence(width, lhs, consumed=False, live=False, shift=None):
    tokens = [o.R(width+'9'), o.N(0)]
    if shift is not None:
        tokens.append(CompilerInt4(sem.TOKEN_SHIFT, 0, shift, 1))
    items = [o.I('movz', *tokens), o.I('cmp', o.R(lhs), o.R(width+'9'))]
    if live:
        items.append(o.I('mov', o.R(width+'0'), o.R(width+'9')))
    if consumed:
        items += [o.I('movz', o.R(width+'9'), o.N(1)), o.I('cset', o.R('w0'), o.C('eq'))]
    return items


def check(width, lhs, changed, *, consumed=False, live=False, shift=None, check_effect=True):
    f, s, span, lines = o._fixture(sequence(width, lhs, consumed, live, shift))
    try:
        ids = o._ids(f, span)
        before = [f.records.get4_unchecked(i).first for i in ids]
        out = typed._run(f, s, span, 5)
        after = [f.records.get4_unchecked(i).first for i in o._ids(f, out)]
        assert o._ids(f, span) == ids
        # Check the independent semantic outcome BEFORE structural/legacy assertions.
        if check_effect:
            expected_flags = (0, 1, 1, 0) if lhs.endswith('zr') else (0, 0, 1, 0)
            expected = (expected_flags, expected_flags[1] if consumed else None)
            assert effects(before) == expected, 'input NZCV oracle'
            assert effects(after) == expected, 'output NZCV oracle'
        assert len(after) == len(before) - int(changed), 'typed output length'
        if not changed:
            assert after == before
        legacy = o.legacy._fold_zero_compare_immediate(lines)
        assert (legacy != lines) == changed, 'legacy rewrite guard'
        return before, after
    finally:
        s.close(); f.close()


@pytest.mark.parametrize('width', ('x', 'w'))
@pytest.mark.parametrize('consumed', (False, True))
def test_zero_register_flags_preserved(width, consumed):
    before, after = check(width, width+'zr', False, consumed=consumed)
    expected = [0xd2800009, 0xeb0903ff] if width == 'x' else [0x52800009, 0x6b0903ff]
    assert before[:2] == after[:2] == expected


@pytest.mark.parametrize('width,lhs', (('x','x19'), ('w','w19'), ('w','wsp')))
@pytest.mark.parametrize('consumed', (False, True))
def test_safe_general_and_stack_pointer_compare_still_fold(width, lhs, consumed):
    check(width, lhs, True, consumed=consumed)


@pytest.mark.parametrize('width', ('x', 'w'))
@pytest.mark.parametrize('live', (False, True))
def test_rhs_deadness_remains_required(width, live):
    check(width, width+'19', not live, live=live, check_effect=not live)


@pytest.mark.parametrize('width', ('x', 'w'))
@pytest.mark.parametrize('shift,changed', ((0,True), (16,False)))
def test_exact_optional_zero_shift(width, shift, changed):
    check(width, width+'19', changed, shift=shift)


def mutate(function, old, new):
    source = inspect.getsource(function)
    assert source.count(old) == 1, 'unique mutation anchor'
    namespace = dict(function.__globals__)
    exec(compile(source.replace(old,new), '<zero-compare-mutant>', 'exec'), namespace)
    return namespace[function.__name__]


@pytest.mark.parametrize('width', ('x','w'))
def test_missing_typed_guard_killed_by_live_flags(monkeypatch, width):
    test_zero_register_flags_preserved(width, True)
    guard = ' and lhs not in (sem.REGISTER_XZR, sem.REGISTER_WZR)'
    monkeypatch.setattr(typed, '_run', mutate(typed._run, guard, ''))
    with pytest.raises(AssertionError, match=r'^output NZCV oracle(?:\n|$)'):
        test_zero_register_flags_preserved(width, True)


@pytest.mark.parametrize('width', ('x','w'))
def test_missing_legacy_guard_killed_independently(monkeypatch, width):
    test_zero_register_flags_preserved(width, True)
    function = o.legacy._fold_zero_compare_immediate
    source = inspect.getsource(function)
    # Exact line spelling is deliberately validated rather than mutating tests.
    guard_lines = [line for line in source.splitlines(True) if 'lhs not in' in line]
    assert len(guard_lines) == 1
    monkeypatch.setattr(o.legacy, '_fold_zero_compare_immediate', mutate(function, guard_lines[0], ''))
    with pytest.raises(AssertionError, match=r'^legacy rewrite guard(?:\n|$)'):
        test_zero_register_flags_preserved(width, True)


def test_independent_decoder_distinguishes_immediate_sp_from_shifted_zr():
    assert effects([0xd2800009,0xeb0903ff,0xd2800029,0x1a9f17e0]) == ((0,1,1,0),1)
    assert effects([0xf10003ff,0xd2800029,0x1a9f17e0]) == ((0,0,1,0),0)
