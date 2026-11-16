"""Mixed WSP/WZR cannot collapse to one MOV; independent live-SP oracle."""
import inspect
import textwrap
import pytest
from tests.python import test_aarch64_typed_peepholes as o
from pcc.backend import self_backend_aarch64_typed_peepholes as p


def effect(words):
    regs=[0]*31;regs[20]=0x100000007
    sp,flags=0x100003000,(1,0,1,1)
    for word in words:
        rd,rn,rm=word&31,(word>>5)&31,(word>>16)&31
        mask=0xffffffffffffffff if word&0x80000000 else 0xffffffff
        if word&0x7fe0ffe0==0x2a0003e0:
            value=(0 if rm==31 else regs[rm])&mask
            if rd!=31:regs[rd]=value
        elif word&0x7fc00000==0x11000000:
            assert ((word>>10)&4095)==0
            value=(sp if rn==31 else regs[rn])&mask
            if rd==31:sp=value
            else:regs[rd]=value
        else:raise AssertionError('unsupported independent decoder '+hex(word))
    return sp,regs[1],flags


def check(source,dest,changed):
    width='x' if source.startswith('x') else 'w'
    read='sp' if dest in ('wsp','wzr') else 'x'+dest[1:]
    seq=[o.I('mov',o.R(width+'9'),o.R(source)),o.I('mov',o.R(dest),o.R(width+'9')),
         o.I('mov',o.R('x1'),o.R(read))]
    f,s,span,lines=o._fixture(seq)
    try:
        ids=o._ids(f,span)
        before=[f.records.get4_unchecked(i).first for i in ids]
        out=p._run(f,s,span,10)
        after=[f.records.get4_unchecked(i).first for i in o._ids(f,out)]
        legacy=o.legacy._fold_mov_mov_chain(lines)
        legacy_words=[int.from_bytes(o.enc.assemble_text(line).code,'little') for line in legacy]
        assert effect(after)==effect(before), 'typed SP domain oracle'
        assert effect(legacy_words)==effect(before), 'legacy SP domain oracle'
        assert len(after)==3-int(changed)
        assert (legacy!=lines)==changed
        if not changed:assert after==before
        assert o._ids(f,span)==ids
        assert effect(before)[2]==(1,0,1,1)
        return before,after,effect(before)
    finally:s.close();f.close()


@pytest.mark.parametrize('source,dest,words,expected',[
    ('wzr','wsp',[0x2a1f03e9,0x1100013f,0x910003e1],0),
    ('wsp','wzr',[0x110003e9,0x2a0903ff,0x910003e1],0x100003000),
])
def test_mixed_domain_chain_retains_legal_original(source,dest,words,expected):
    before,after,state=check(source,dest,False)
    assert before==after==words
    assert state==(expected,expected,(1,0,1,1))


@pytest.mark.parametrize('source,dest',[
    ('wsp','w19'),('w20','wsp'),('wzr','w19'),('w20','wzr'),
    ('wsp','wsp'),('wzr','wzr'),('x20','x0'),('xzr','x0'),
])
def test_legal_single_domain_and_self_moves_still_fold(source,dest):
    check(source,dest,True)


def without_guard(function,condition):
    source=textwrap.dedent(inspect.getsource(function))
    lines=source.splitlines(True)
    matches=[i for i,line in enumerate(lines) if condition in line]
    assert len(matches)==1,'unique guard anchor'
    index=matches[0]
    assert lines[index+1].strip() in ('return -2','return None')
    del lines[index:index+2]
    namespace=dict(function.__globals__)
    exec(compile(''.join(lines),'<mov-domain-mutant>','exec'),namespace)
    return namespace[function.__name__]


@pytest.mark.parametrize('side',['typed','legacy'])
@pytest.mark.parametrize('source,dest',[('wzr','wsp'),('wsp','wzr')])
def test_missing_domain_guard_killed_by_independent_sp(monkeypatch,side,source,dest):
    check(source,dest,False)
    if side=='typed':
        fn=p._Pass.new_move
        monkeypatch.setattr(p._Pass,'new_move',without_guard(fn,'dest == sem.REGISTER_WSP and src == sem.REGISTER_WZR'))
    else:
        fn=o.legacy._forward_move
        monkeypatch.setattr(o.legacy,'_forward_move',without_guard(fn,'dest_reg == "wsp" and src_reg == "wzr"'))
    with pytest.raises(AssertionError,match=r'^'+side+r' SP domain oracle(?:\n|$)'):
        check(source,dest,False)


@pytest.mark.parametrize('source,dest',[('wzr','wsp'),('wsp','wzr')])
def test_rejected_domain_never_calls_producer(monkeypatch,source,dest):
    f,s,span,_=o._fixture([o.I('mov',o.R('w9'),o.R('w20'))])
    context=p._Pass(f,s,span)
    def forbidden(*args):raise AssertionError('rejected domain reached producer')
    monkeypatch.setattr(p._Pass,'new',forbidden)
    try:
        assert context.new_move(o.R(dest).second,o.R(source).second)==-2
    finally:context.close();s.close();f.close()


def test_allowed_move_producer_keeps_primary_exception(monkeypatch):
    f,s,span,_=o._fixture([o.I('mov',o.R('w9'),o.R('w20'))])
    before=o._ids(f,span)
    context=p._Pass(f,s,span)
    error=RuntimeError('allowed move producer failed')
    calls=[]
    def failure(self,*args):calls.append(args);raise error
    monkeypatch.setattr(p._Pass,'new',failure)
    try:
        with pytest.raises(RuntimeError) as caught:
            context.new_move(o.R('w19').second,o.R('wsp').second)
        assert caught.value is error
        assert len(calls)==1
        assert o._ids(f,span)==before
    finally:context.close();s.close();f.close()
