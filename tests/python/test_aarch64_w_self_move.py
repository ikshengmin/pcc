"""W writes zero-extend X: independent MOV/ADD-zero/unscaled-memory model."""
import inspect
import textwrap
import pytest
from tests.python import test_aarch64_typed_peepholes as o
from pcc.backend import self_backend_aarch64_typed_peepholes as p


def execute(words):
    r=[0]*31
    r[0]=r[9]=0x100000001
    r[29]=8192
    sp=0x100003000
    memory={8176:7}
    flags=(1,0,1,1)
    for w in words:
        rt,rn,rm=w&31,(w>>5)&31,(w>>16)&31
        if w&0x7fe0ffe0==0x2a0003e0:
            assert rt!=31
            r[rt]=(0 if rm==31 else r[rm]) & (0xffffffffffffffff if w&0x80000000 else 0xffffffff)
        elif w&0x7fc00000==0x11000000:
            assert ((w>>10)&4095)==0
            value=(sp if rn==31 else r[rn]) & (0xffffffffffffffff if w&0x80000000 else 0xffffffff)
            if rt==31:sp=value
            else:r[rt]=value
        elif w&0xffe00c00 in (0xb8000000,0xb8400000,0xf8000000,0xf8400000):
            offset=(w>>12)&511
            if offset&256:offset-=512
            addr=(sp if rn==31 else r[rn])+offset
            mask=0xffffffffffffffff if w&0x40000000 else 0xffffffff
            if w&0x00400000:
                if rt!=31:r[rt]=memory[addr]&mask
            else:memory[addr]=(0 if rt==31 else r[rt])&mask
        else:raise AssertionError('unsupported independent decoder '+hex(w))
    return r,sp,flags,memory


NAMES={1:'_forward_adjacent_stack_store_load',2:'_forward_one_intervening_stack_store_load',
       9:'_fold_scratch_def_into_move',10:'_fold_mov_mov_chain'}


def sequence(mode,width,case='roundtrip'):
    if mode in (1,2):
        result=[o.I('stur',o.R(width+'9'),o.A())]
        if mode==2:result.append(o.I('ldur',o.R('x11'),o.A(-16)))
        result += [o.I('ldur',o.R(width+'9'),o.A()),o.I('mov',o.R('x1'),o.R('x9'))]
        return result
    source='wsp' if case=='wsp' else width+'0'
    dest=source if case in ('roundtrip','wsp','live_alias') else width+'2'
    return [o.I('mov',o.R(width+'9'),o.R(source)),o.I('mov',o.R(dest),o.R(width+'9')),
            o.I('mov',o.R('x1'),o.R('sp' if case=='wsp' else 'x9' if case=='live_alias' else 'x2' if case=='different' else 'x0'))]


def check(mode,width,owned=False,case='roundtrip'):
    f,s,span,lines=o._fixture(sequence(mode,width,case))
    try:
        ids=o._ids(f,span)
        before=[f.records.get4_unchecked(i).first for i in ids]
        out=p._run_owned_stack(f,s,span,mode) if owned else p._run(f,s,span,mode)
        after=[f.records.get4_unchecked(i).first for i in o._ids(f,out)]
        legacy=getattr(o.legacy,NAMES[mode])(lines)
        legacy_words=[int.from_bytes(o.enc.assemble_text(line).code,'little') for line in legacy]
        a,b,c=execute(before),execute(after),execute(legacy_words)
        # Only architecturally live outputs, memory and NZCV: the scratch may die.
        assert (b[0][1],b[1],b[2],b[3])==(a[0][1],a[1],a[2],a[3]), 'typed W zero extension oracle'
        assert (c[0][1],c[1],c[2],c[3])==(a[0][1],a[1],a[2],a[3]), 'legacy W zero extension oracle'
        assert a[2]==b[2]==c[2]==(1,0,1,1)
        assert o._ids(f,span)==ids
        if case=='live_alias':assert after==before
        elif case=='wsp':
            assert a[0][1]==0x3000
            assert len(after)==2 and after[0]==0x110003ff
        elif case=='different':assert len(after)==2
        elif mode in (9,10):
            assert a[0][1]==(1 if width=='w' else 0x100000001)
            assert len(after)==(2 if width=='w' else 1)
            if width=='w':assert after[0]==0x2a0003e0
        else:
            assert a[0][1]==(1 if width=='w' else 0x100000001)
            assert len(after)==len(before)-(0 if width=='w' else 1)
            if width=='w':assert 0x2a0903e9 in after
        return before,after
    finally:s.close();f.close()


@pytest.mark.parametrize('mode',[9,10])
@pytest.mark.parametrize('width',['w','x'])
def test_roundtrip_live_x_read(mode,width):check(mode,width)


@pytest.mark.parametrize('mode',[1,2])
@pytest.mark.parametrize('width',['w','x'])
@pytest.mark.parametrize('owned',[False,True])
def test_same_stack_load_live_x_read(mode,width,owned,monkeypatch):
    if owned:
        from pcc.backend import self_backend_aarch64_owned_stack as kernel
        original=kernel.decide
        calls=[]
        def decide(*args):calls.append(args);return original(*args)
        monkeypatch.setattr(kernel,'decide',decide)
    check(mode,width,owned)
    if owned:assert calls


def test_wsp_roundtrip_still_zero_extends_sp():check(10,'w',case='wsp')


@pytest.mark.parametrize('mode',[9,10])
def test_different_destination_still_optimizes(mode):check(mode,'w',case='different')


@pytest.mark.parametrize('mode',[9,10])
def test_scratch_x_alias_live_prevents_fold(mode):check(mode,'w',case='live_alias')


def mutate(function,old,new):
    source=textwrap.dedent(inspect.getsource(function))
    assert source.count(old)==1,'unique mutation anchor'
    namespace=dict(function.__globals__)
    exec(compile(source.replace(old,new),'<w-self-mutant>','exec'),namespace)
    return namespace[function.__name__]


@pytest.mark.parametrize('mode',[1,2,10])
def test_missing_w_self_guard_killed_by_live_x(monkeypatch,mode):
    check(mode,'w',owned=mode in (1,2))
    monkeypatch.setattr(p._Pass,'new_move',mutate(p._Pass.new_move,' and _class(dest) != "w"',''))
    with pytest.raises(AssertionError,match=r'^typed W zero extension oracle(?:\n|$)'):
        check(mode,'w',owned=mode in (1,2))


def test_old_mode9_drop_killed_by_live_x(monkeypatch):
    check(9,'w')
    monkeypatch.setattr(p,'_run',mutate(p._run,'replacement = p.new_move(move[0], move[0])','replacement = -1'))
    with pytest.raises(AssertionError,match=r'^typed W zero extension oracle(?:\n|$)'):
        check(9,'w')


def test_mode10_wzr_self_roundtrip_discards_write_and_preserves_flags():
    items=[o.I('mov',o.R('w9'),o.R('wzr')),o.I('mov',o.R('wzr'),o.R('w9')),
           o.I('mov',o.R('x1'),o.R('x0'))]
    f,s,span,lines=o._fixture(items)
    def ignored_write_effect(words):
        regs=[0]*31;regs[0]=0x100000001
        nzcv=(1,1,0,1)
        for word in words:
            assert word&0x7fe0ffe0==0x2a0003e0
            rd,rm=word&31,(word>>16)&31
            value=0 if rm==31 else regs[rm]
            if rd!=31:regs[rd]=value&(0xffffffffffffffff if word&0x80000000 else 0xffffffff)
        return regs[1],nzcv
    try:
        before=[f.records.get4_unchecked(i).first for i in o._ids(f,span)]
        out=p._run(f,s,span,10)
        after=[f.records.get4_unchecked(i).first for i in o._ids(f,out)]
        assert before==[0x2a1f03e9,0x2a0903ff,0xaa0003e1]
        assert after==[0x2a1f03ff,0xaa0003e1]
        legacy=getattr(o.legacy,NAMES[10])(lines)
        legacy_words=[int.from_bytes(o.enc.assemble_text(line).code,'little') for line in legacy]
        assert ignored_write_effect(before)==ignored_write_effect(after)==ignored_write_effect(legacy_words)==(0x100000001,(1,1,0,1))
    finally:s.close();f.close()


def test_mode9_self_move_failure_after_output_truncate_rolls_back(monkeypatch):
    from pcc.backend import self_backend_aarch64_semantics as sem
    from pcc.backend.self_backend_value_arena import CompilerIntArena
    f,s,span,_=o._fixture(sequence(9,'w'))
    before=o._ids(f,span)
    prefixes=[list(a._values) for a in (f.records,s.headers,s.tokens)]
    contexts,events,closed=[],[],[]
    init,new=p._Pass.__init__,p._Pass.new
    header,close=sem.AArch64InstructionSemantics.append_header,CompilerIntArena.close
    error=RuntimeError('mode9 real self-move partial producer')
    def initialize(self,*args):init(self,*args);contexts.append(self)
    def produce(self,*args):
        assert len(self.output)==0, 'previous private output must already be truncated'
        events.append('producer_after_truncate')
        return new(self,*args)
    def fail_header(self,*args):
        header(self,*args)
        if self is s:
            assert len(s.headers)>len(prefixes[1])
            assert len(s.tokens)>len(prefixes[2])
            events.append('header_appended')
            raise error
    def shutdown(self):
        if contexts and (self is contexts[0].output or self is contexts[0].ids):closed.append(self)
        return close(self)
    monkeypatch.setattr(p._Pass,'__init__',initialize)
    monkeypatch.setattr(p._Pass,'new',produce)
    monkeypatch.setattr(sem.AArch64InstructionSemantics,'append_header',fail_header)
    monkeypatch.setattr(CompilerIntArena,'close',shutdown)
    try:
        with pytest.raises(RuntimeError) as caught:p._run(f,s,span,9)
        assert caught.value is error
        assert events==['producer_after_truncate','header_appended']
        assert closed==[contexts[0].output,contexts[0].ids]
        assert all(a._closed and not a._values for a in closed)
        assert all(a._values==old for a,old in zip((f.records,s.headers,s.tokens),prefixes))
        assert o._ids(f,span)==before
        s.require_complete(f.records,len(f.symbol_names))
    finally:s.close();f.close()
