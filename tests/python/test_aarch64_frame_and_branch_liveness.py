import inspect
import pytest
from tests.python import test_aarch64_typed_peepholes as o
from pcc.backend import self_backend_aarch64_typed_peepholes as p


def link(records):
    labels={};pc=0
    for word,target,label in records:
        if label is not None:labels[label]=pc
        else:pc+=4
    words=[];pc=0
    for word,target,label in records:
        if label is not None:continue
        if target is not None:
            delta=(labels[target]-pc)//4
            if word&0xfc000000==0x14000000:word=(word&0xfc000000)|(delta&0x3ffffff)
            else:
                assert word&0x7e000000==0x34000000 or word&0xff000010==0x54000000
                word=(word&~0xffffe0)|((delta&0x7ffff)<<5)
        words.append(word);pc+=4
    return words


def read(f,s,span,original_events):
    records=[];lines=[]
    for rid in o._ids(f,span):
        code=f.records.get4_unchecked(rid)
        if code.second<0:
            line=original_events[rid]
            assert line.endswith(':')
            records.append((None,None,line[:-1]));lines.append(line)
        else:
            h=s.require_header(rid)
            tokens=[s.operand(rid,i) for i in range(h.fourth)]
            lines.append(o._line(o.sem.OPCODE_NAMES[h.first],tokens,f.symbol_names))
            records.append((code.first,f.symbol_names[code.fourth] if code.third else None,None))
    words=link(records)
    encoded=o.enc.assemble_text('\n'.join(lines)).code
    assert words==[int.from_bytes(encoded[i:i+4],'little') for i in range(0,len(encoded),4)]
    return words,lines


def execute(words):
    regs=[0]*31;regs[9]=111;regs[10]=77;regs[20]=33;regs[29]=8192
    memory={8176:16384,8184:99,16376:222};flags=(1,0,1,1);pc=0;steps=0
    while pc<len(words):
        steps+=1;assert steps<48
        w=words[pc];rd,rn,rm=w&31,(w>>5)&31,(w>>16)&31
        mask=0xffffffffffffffff if w&0x80000000 else 0xffffffff
        if w&0x7f800000==0x52800000:
            if rd!=31:regs[rd]=(((w>>5)&65535)<<(16*((w>>21)&3)))&mask
        elif w&0x7fe0ffe0==0x2a0003e0:
            if rd!=31:regs[rd]=(0 if rm==31 else regs[rm])&mask
        elif w&0x7fe0fc00==0x6b000000:
            a=(0 if rn==31 else regs[rn])&mask;b=(0 if rm==31 else regs[rm])&mask
            v=(a-b)&mask;sign=(mask+1)//2
            flags=(int(bool(v&sign)),int(v==0),int(a>=b),int(bool((a^b)&(a^v)&sign)))
        elif w&0xffffffe0==0x1a9f17e0:
            if rd!=31:regs[rd]=flags[1]
        elif w&0xfffffc00==0x12001c00:
            # AND Wd,Wn,#0xff: N=0,immr=0,imms=7.
            if rd!=31:regs[rd]=(0 if rn==31 else regs[rn])&255
        elif w&0xffe00c00 in (0xf8000000,0xf8400000,0xb8000000,0xb8400000,0x38000000,0x38400000):
            size=8 if w>>30==3 else 4 if w>>30==2 else 1
            offset=(w>>12)&511
            if offset&256:offset-=512
            addr=regs[rn]+offset;maskmem=(1<<(size*8))-1
            if w&0x00400000:
                if rd!=31:regs[rd]=memory.get(addr,0)&maskmem
            else:memory[addr]=(memory.get(addr,0)&~maskmem)|((0 if rd==31 else regs[rd])&maskmem)
        elif w&0xfc000000==0x14000000:
            d=w&0x3ffffff
            if d&(1<<25):d-=1<<26
            pc+=d;continue
        elif w&0x7e000000==0x34000000:
            value=(0 if rd==31 else regs[rd])&mask
            taken=bool(value) if w&0x01000000 else not value
            d=(w>>5)&0x7ffff
            if d&(1<<18):d-=1<<19
            pc+=d if taken else 1;continue
        elif w&0xff000010==0x54000000:
            assert w&15==0
            d=(w>>5)&0x7ffff
            if d&(1<<18):d-=1<<19
            pc+=d if flags[1] else 1;continue
        else:raise AssertionError('unsupported independent word '+hex(w))
        pc+=1
    return {'X1':regs[1],'NZCV':flags}



def items(mode,case):
    if mode==2:
        middle={'xbase':'x29','wbase':'w29','fpbase':'fp','safe':'x11'}[case]
        return [o.I('stur',o.R('x9'),o.A()),o.I('ldur',o.R(middle),o.A(-16)),
                o.I('ldur',o.R('x10'),o.A()),o.I('mov',o.R('x1'),o.R('x10'))]
    same=case=='same';zero=case=='zero';dest='w9' if same else 'w10'
    seq=[o.I('cmp',o.R('w0'),o.R('w20' if zero else 'w0')),o.I('cset',o.R('w9'),o.C('eq')),
         o.I('sturb',o.R('w9'),o.A()),o.I('and',o.R(dest),o.R('w9'),o.N(255,16)),
         o.I('cbz' if zero else 'cbnz',o.R(dest),o.S('L_take')),o.I('b',o.S('L_done')),'L_take:']
    if case=='fallthrough':
        seq[4]=o.I('cbz',o.R(dest),o.S('L_take'))
        seq.insert(5,o.I('mov',o.R('x1'),o.R('x10')))
    if case=='overwrite':seq.append(o.I('movz',o.R('w10'),o.N(7)))
    return seq+[o.I('mov',o.R('x1'),o.R('x9' if same else 'x10')),'L_done:',o.I('mov',o.R('x2'),o.R('x1'))]


def check(mode,case,owned=False):
    seq=items(mode,case)
    f,s,span,lines=o._fixture(seq)
    try:
        ids=o._ids(f,span);events={rid:lines[i] for i,rid in enumerate(ids) if isinstance(seq[i],str)}
        before,_=read(f,s,span,events)
        if owned:out=p._run_owned_stack(f,s,span,2) if mode==2 else p._run_owned_cset(f,s,span)
        else:out=p._run(f,s,span,mode)
        after,actual=read(f,s,out,events)
        name='_forward_one_intervening_stack_store_load' if mode==2 else '_fold_forwarded_cset_branch'
        legacy=getattr(o.legacy,name)(lines)
        raw=o.enc.assemble_text('\n'.join(legacy)).code
        legacy_words=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)]
        expected=222 if case in ('xbase','wbase','fpbase') else 111 if mode==2 else 0 if case=='zero' else 7 if case=='overwrite' else 1
        assert execute(before)['X1']==expected
        assert execute(after)==execute(before),'typed frame or branch live oracle'
        assert execute(legacy_words)==execute(before),'legacy frame or branch live oracle'
        assert actual==legacy
        assert o._ids(f,span)==ids
        if mode==2:
            if case in ('xbase','wbase','fpbase'):assert after==before
            else:assert after!=before
        else:
            if case=='same':assert len(after)==len(before)-1
            else:assert after==before, 'distinct AND destination retained'
    finally:s.close();f.close()


@pytest.mark.parametrize('owned',[False,True])
@pytest.mark.parametrize('case',['xbase','wbase','fpbase','safe'])
def test_frame_base_definition_preserved(case,owned):check(2,case,owned)


@pytest.mark.parametrize('owned',[False,True])
@pytest.mark.parametrize('case',['nonzero','zero','same','overwrite','fallthrough'])
def test_and_destination_live_on_taken_target(case,owned):check(12,case,owned)


@pytest.mark.parametrize('mode,owned,old,new',[
    (2,False,' or middle[0] in (29, 61, sem.REGISTER_FP)',''),
    (2,True,'            or middle[0] in (29, 61, REGISTER_FP)\n',''),
    (12,False,' and dst == src):','):'),
    (12,True,'    if tokens[ct + 1] != tokens[ct + 5]:\n        return (0, 0, 0, 0, 0, 0, 0)\n',''),
])
def test_missing_safety_guard_killed_by_independent_semantics(monkeypatch,mode,owned,old,new):
    from pcc.backend import self_backend_aarch64_owned_stack as stack
    from pcc.backend import self_backend_aarch64_owned_cset as cset
    case='xbase' if mode==2 else 'nonzero'
    check(mode,case,owned)
    module=stack if mode==2 else cset
    function=module.decide if owned else p._run
    source=inspect.getsource(function)
    assert source.count(old)==1,'unique mutation anchor'
    namespace=dict(function.__globals__)
    exec(compile(source.replace(old,new),'<frame-branch-mutant>','exec'),namespace)
    monkeypatch.setattr(module if owned else p,'decide' if owned else '_run',namespace[function.__name__])
    with pytest.raises(AssertionError,match=r'^typed frame or branch live oracle(?:\n|$)'):
        check(mode,case,owned)


@pytest.mark.parametrize('mode',[2,12])
def test_safe_owned_producer_failure_preserves_error_and_cleans_scratch(monkeypatch,mode):
    from pcc.backend.self_backend_value_arena import CompilerIntArena
    f,s,span,_=o._fixture(items(mode,'safe' if mode==2 else 'same'))
    before=o._ids(f,span)
    prefixes=[list(a._values) for a in (f.records,s.headers,s.tokens)]
    contexts,closed=[],[]
    init,close=p._Pass.__init__,CompilerIntArena.close
    error=RuntimeError('safe owned producer primary')
    def initialize(self,*args):init(self,*args);contexts.append(self)
    def failure(self,*args,**kwargs):
        assert self.output._values==([] if mode==2 else before[:3])
        raise error
    def shutdown(self):
        if contexts and (self is contexts[0].output or self is contexts[0].ids):closed.append(self)
        return close(self)
    monkeypatch.setattr(p._Pass,'__init__',initialize)
    monkeypatch.setattr(p._Pass,'new',failure)
    monkeypatch.setattr(CompilerIntArena,'close',shutdown)
    try:
        with pytest.raises(RuntimeError) as caught:
            if mode==2:p._run_owned_stack(f,s,span,2)
            else:p._run_owned_cset(f,s,span)
        assert caught.value is error
        assert closed==[contexts[0].output,contexts[0].ids]
        assert all(a._closed and not a._values for a in closed)
        assert o._ids(f,span)==before
        assert all(a._values==old for a,old in zip((f.records,s.headers,s.tokens),prefixes))
        s.require_complete(f.records,len(f.symbol_names))
    finally:s.close();f.close()
