"""Mode-6 SP/ZR domain safety with independent live-flags machine semantics."""
import inspect
import pytest
from tests.python import test_aarch64_typed_peepholes as o
from pcc.backend import self_backend_aarch64_typed_peepholes as p


def effect(words):
    regs=[0]*31
    regs[20]=7
    sp, flags, w0=4096,None,None
    for word in words:
        bits=64 if word & 0x80000000 else 32
        mask,sign=(1<<bits)-1,1<<(bits-1)
        rd,rn,rm=word&31,(word>>5)&31,(word>>16)&31
        if word==0x1a9f17e0:
            assert flags is not None
            w0=flags[1];continue
        if word&0x7fe0ffe0==0x2a0003e0:
            regs[rd]=(0 if rm==31 else regs[rm])&mask;continue
        if word&0x7fc00000==0x11000000:
            assert rn==31 and ((word>>10)&4095)==0
            regs[rd]=sp&mask;continue
        if word&0x7f800000==0x52800000:
            regs[rd]=(((word>>5)&65535)<<(16*((word>>21)&3)))&mask;continue
        shape=word&0x7fe0fc00
        if shape==0x6b000000:
            left=0 if rn==31 else regs[rn]
        elif shape in (0x6b204000,0x6b206000):
            assert shape==(0x6b206000 if bits==64 else 0x6b204000)
            left=sp if rn==31 else regs[rn]
        else:
            raise AssertionError('unsupported independent machine word '+hex(word))
        assert rd==31
        left &= mask
        right=(0 if rm==31 else regs[rm])&mask
        value=(left-right)&mask
        flags=(int(bool(value&sign)),int(value==0),int(left>=right),int(bool((left^right)&(left^value)&sign)))
    return flags,w0


def sequence(source,lhs,rhs,tail='consume'):
    width='x' if source.startswith('x') or source=='sp' else 'w'
    result=[o.I('mov',o.R(width+'9'),o.R(source)),o.I('cmp',o.R(lhs),o.R(rhs))]
    if tail=='consume':
        result += [o.I('movz',o.R(width+'9'),o.N(1)),o.I('cset',o.R('w0'),o.C('eq'))]
    elif tail=='read':
        result.append(o.I('mov',o.R(width+'0'),o.R(width+'9')))
    elif tail=='alias_read':
        alias='x' if width=='w' else 'w'
        result.append(o.I('mov',o.R(alias+'0'),o.R(alias+'9')))
    elif tail=='label':
        result.append('L_keep:')
    return result


def check(source,lhs,rhs,changed,tail='consume'):
    f,s,span,lines=o._fixture(sequence(source,lhs,rhs,tail))
    try:
        ids=o._ids(f,span)
        before=[f.records.get4_unchecked(i).first for i in ids]
        out=p._run(f,s,span,6)
        after=[f.records.get4_unchecked(i).first for i in o._ids(f,out)]
        assert o._ids(f,span)==ids
        legacy=o.legacy._fold_mov_compare_source(lines)
        if tail=='consume':
            legacy_words=[int.from_bytes(o.enc.assemble_text(line).code,'little') for line in legacy]
            expected=effect(before)
            assert effect(after)==expected, 'typed live NZCV oracle'
            assert effect(legacy_words)==expected, 'legacy live NZCV oracle'
        assert len(after)==len(before)-int(changed), 'typed rewrite shape'
        assert (legacy!=lines)==changed, 'legacy rewrite shape'
        if not changed:assert after==before
        return before,after
    finally:
        s.close();f.close()


@pytest.mark.parametrize('lhs,rhs,cmpword', [('w19','w9',0x6b09027f),('wzr','w9',0x6b0903ff),('w9','w9',0x6b09013f),('wsp','w9',0x6b2943ff)])
def test_rhs_wsp_does_not_become_zero_register(lhs,rhs,cmpword):
    before,after=check('wsp',lhs,rhs,False)
    assert before==after==[0x110003e9,cmpword,0x52800029,0x1a9f17e0]
    assert effect(before)[1]==int(lhs in ('w9','wsp'))


def test_left_only_wsp_uses_legal_extended_source():
    before,after=check('wsp','w9','w19',True)
    assert after==[0x6b3343ff,0x52800029,0x1a9f17e0]
    assert effect(before)==effect(after)==((0,0,1,0),0)


@pytest.mark.parametrize('width', ['x','w'])
@pytest.mark.parametrize('case', ['source_zr','left_zr','both','left'])
def test_normal_and_zero_register_sources_still_fold(width,case):
    source=width+('zr' if case=='source_zr' else '20')
    lhs=width+('zr' if case=='left_zr' else '9' if case in ('both','left') else '20')
    rhs=width+('19' if case=='left' else '9')
    check(source,lhs,rhs,True)


@pytest.mark.parametrize('tail', ['read','alias_read','label'])
def test_original_liveness_and_alias_guards_remain(tail):
    check('w20','w19','w9',False,tail)


def test_sp_source_is_not_newly_admitted():
    check('sp','x19','x9',False)


def mutate(function,guard):
    source=inspect.getsource(function)
    assert source.count(guard)==1, 'unique mutation anchor'
    namespace=dict(function.__globals__)
    exec(compile(source.replace(guard,''),'<mov-compare-mutant>','exec'),namespace)
    return namespace[function.__name__]


@pytest.mark.parametrize('side', ['typed','legacy'])
@pytest.mark.parametrize('lhs', ['wzr','w9'])
def test_missing_guard_is_killed_by_live_machine_semantics(monkeypatch,side,lhs):
    check('wsp',lhs,'w9',False)
    if side=='typed':
        monkeypatch.setattr(p,'_run',mutate(p._run,' and not (rhs == move[0] and move[1] == sem.REGISTER_WSP)'))
    else:
        fn=o.legacy._fold_mov_compare_source
        source=inspect.getsource(fn)
        lines=[line for line in source.splitlines(True) if 'and not (rhs == dest_reg and src_reg == "wsp")' in line]
        assert len(lines)==1
        monkeypatch.setattr(o.legacy,'_fold_mov_compare_source',mutate(fn,lines[0]))
    with pytest.raises(AssertionError,match=r'^'+side+r' live NZCV oracle(?:\n|$)'):
        check('wsp',lhs,'w9',False)
