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
    regs=[0]*31;regs[9]=77;regs[20]=33;regs[29]=8192
    sp=4096;flags=(1,0,1,1);pc=0;steps=0
    while pc<len(words):
        steps+=1;assert steps<40
        w=words[pc];rd=w&31;rn=(w>>5)&31;rm=(w>>16)&31
        mask=0xffffffffffffffff if w&0x80000000 else 0xffffffff
        if w&0x7f800000==0x52800000:
            if rd!=31:regs[rd]=(((w>>5)&65535)<<(16*((w>>21)&3)))&mask
        elif w&0x7fe0ffe0==0x2a0003e0:
            if rd!=31:regs[rd]=(0 if rm==31 else regs[rm])&mask
        elif w&0x7fc00000==0x11000000:
            assert ((w>>10)&4095)==0
            value=(sp if rn==31 else regs[rn])&mask
            if rd==31:sp=value
            else:regs[rd]=value
        elif w&0x7fe0fc00==0x6b000000:
            a=(0 if rn==31 else regs[rn])&mask;b=(0 if rm==31 else regs[rm])&mask
            v=(a-b)&mask;sign=(mask+1)//2
            flags=(int(bool(v&sign)),int(v==0),int(a>=b),int(bool((a^b)&(a^v)&sign)))
        elif w&0xffffffe0==0x1a9f17e0:
            if rd!=31:regs[rd]=flags[1]
        elif w&0xffe00c00==0x38000000:
            pass  # STURB does not alter registers/NZCV; stack slot not externally observed here.
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
            assert w&15==0,'only EQ modeled'
            d=(w>>5)&0x7ffff
            if d&(1<<18):d-=1<<19
            pc+=d if flags[1] else 1;continue
        else:raise AssertionError('unsupported independent word '+hex(w))
        pc+=1
    return {'X1':regs[1],'NZCV':flags}


def cases():
    for dead in (False,True):
        seq=[o.I('mov',o.R('w9'),o.R('w0')),o.I('cbz',o.R('w9'),o.S('L_take')),o.I('b',o.S('L_done')),'L_take:']
        if dead:seq.append(o.I('movz',o.R('w9'),o.N(7)))
        seq += [o.I('mov',o.R('x1'),o.R('x9')),'L_done:',o.I('mov',o.R('x2'),o.R('x1'))]
        yield 7,'target_overwrite' if dead else 'target_live',seq,7 if dead else 0,7 if dead else 77
    yield 7,'WSP_branch_domain',[o.I('mov',o.R('w9'),o.R('wsp')),o.I('cbz',o.R('w9'),o.S('L_take')),o.I('b',o.S('L_done')),'L_take:',o.I('mov',o.R('x1'),o.R('x20')),'L_done:',o.I('mov',o.R('x2'),o.R('x1'))],0,33
    for dead in (False,True):
        seq=['func:',o.I('cmp',o.R('w0'),o.R('w0')),o.I('cset',o.R('w9'),o.C('eq')),o.I('sturb',o.R('w9'),o.A()),o.I('b.eq',o.S('L_take')),'L_take:']
        if dead:seq.append(o.I('movz',o.R('w9'),o.N(7)))
        seq.append(o.I('mov',o.R('x1'),o.R('x9')))
        yield 14,'register_overwrite' if dead else 'register_live',seq,7 if dead else 1,7 if dead else 77


def check(mode,name,branch='cbz'):
    item=next(item for item in cases() if item[0]==mode and item[1]==name)
    seq=list(item[2])
    if mode==7:
        op,tokens=seq[1]
        seq[1]=(branch,tokens)
    f,s,span,lines=o._fixture(seq)
    try:
        ids=o._ids(f,span)
        events={rid:lines[i] for i,rid in enumerate(ids) if isinstance(seq[i],str)}
        before,_=read(f,s,span,events)
        out=p._run(f,s,span,mode)
        after,actual=read(f,s,out,events)
        legacy=getattr(o.legacy,'_fold_mov_zero_branch_source' if mode==7 else '_drop_dead_cset_branch_stores')(lines)
        raw=o.enc.assemble_text('\n'.join(legacy)).code
        legacy_words=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)]
        assert execute(after)==execute(before),'typed branch live oracle'
        assert execute(legacy_words)==execute(before),'legacy branch live oracle'
        assert actual==legacy
        assert o._ids(f,span)==ids
        if mode==7:
            assert len(after)==len(before)
            if name=='WSP_branch_domain':assert after==before
            else:assert after[0]==before[0] and after[1]&31==0
        else:
            assert after==before, 'mode14 conservatively retains all words'
            assert any(w&0xffffffe0==0x1a9f17e0 for w in after),'CSET retained'
    finally:s.close();f.close()


@pytest.mark.parametrize('name',['target_live','target_overwrite','WSP_branch_domain'])
@pytest.mark.parametrize('branch',['cbz','cbnz'])
def test_move_branch_live_registers(name,branch):check(7,name,branch)


@pytest.mark.parametrize('name',['register_live','register_overwrite'])
def test_cset_register_retained_when_only_store_dies(name):check(14,name)


def test_mode7_symbol_full_payload_preserved():
    seq=next(item[2] for item in cases() if item[:2]==(7,'target_live'))
    f,s,span,_=o._fixture(seq)
    try:
        ids=o._ids(f,span)
        h=s.require_header(ids[1]);at=h.third+1
        s.tokens.set(at*4+2,o.sem.SYMBOL_PAGE);s.tokens.set(at*4+3,37)
        expected=s.tokens.get4_unchecked(at)
        out=p._run(f,s,span,7);result=o._ids(f,out)
        assert result[0]==ids[0]
        h=s.require_header(result[1])
        assert s.tokens.get4_unchecked(h.third+1)==expected
    finally:s.close();f.close()


@pytest.mark.parametrize('failure',['producer','append_original','append_branch'])
def test_mode7_producer_and_append_failure_order(monkeypatch,failure):
    from pcc.backend.self_backend_value_arena import CompilerIntArena
    seq=next(item[2] for item in cases() if item[:2]==(7,'target_live'))
    f,s,span,_=o._fixture(seq)
    ids=o._ids(f,span);contexts=[];events=[];closed=[];produced=[]
    init,branch=p._Pass.__init__,p._Pass.new_branch
    append,close=CompilerIntArena.append,CompilerIntArena.close
    error=RuntimeError('branch-live '+failure)
    def initialize(self,*args):init(self,*args);contexts.append(self)
    def producer(self,*args):
        assert not self.output._values
        events.append('producer')
        if failure=='producer':raise error
        result=branch(self,*args);produced.append(result);return result
    def output(self,value):
        if contexts and self is contexts[0].output:
            label='append_original' if value==ids[0] else 'append_branch'
            events.append(label)
            assert produced
            if label==failure:raise error
        return append(self,value)
    def shutdown(self):
        if contexts and (self is contexts[0].output or self is contexts[0].ids):closed.append(self)
        return close(self)
    monkeypatch.setattr(p._Pass,'__init__',initialize);monkeypatch.setattr(p._Pass,'new_branch',producer)
    monkeypatch.setattr(CompilerIntArena,'append',output);monkeypatch.setattr(CompilerIntArena,'close',shutdown)
    try:
        with pytest.raises(RuntimeError) as caught:p._run(f,s,span,7)
        assert caught.value is error
        assert events==(['producer'] if failure=='producer' else ['producer','append_original'] if failure=='append_original' else ['producer','append_original','append_branch'])
        assert closed==[contexts[0].output,contexts[0].ids]
        assert all(a._closed for a in closed)
        assert o._ids(f,span)==ids
        s.require_complete(f.records,len(f.symbol_names))
    finally:s.close();f.close()


@pytest.mark.parametrize('kind,mode,name,old,new',[
    ('wsp',7,'WSP_branch_domain',' and move[1] != sem.REGISTER_WSP',''),
    ('mov',7,'target_live','                    # The taken target may observe the scratch definition.\n                    p.output.append(a)\n',''),
    ('cset',14,'register_live','                        p.output.append(b)\n                        p.output.append(c)\n                        replacement = d', '                        p.output.append(c)\n                        replacement = d'),
])
def test_guard_and_retention_mutants_killed(monkeypatch,kind,mode,name,old,new):
    check(mode,name)
    source=inspect.getsource(p._run);assert source.count(old)==1,'unique mutation anchor'
    namespace=dict(p._run.__globals__)
    exec(compile(source.replace(old,new),'<branch-live-mutant>','exec'),namespace)
    monkeypatch.setattr(p,'_run',namespace['_run'])
    if kind == 'wsp':
        # The valid source above must compile. Removing this optimizer guard
        # now reaches the shared encoder's defensive rejection, rather than
        # emitting the previously incorrect SP-as-ZR branch.
        with pytest.raises(o.enc.EncodeError) as caught:
            check(mode,name)
        assert type(caught.value) is o.enc.EncodeError
        assert str(caught.value) == 'bad emitted zero-branch register'
        frames = []
        encoder_frames = []
        tb = caught.value.__traceback__
        while tb is not None:
            frames.append(tb.tb_frame.f_code)
            if tb.tb_frame.f_code is o.enc.encode_emitted_branch_base_parts.__code__:
                encoder_frames.append(tb.tb_frame.f_locals)
            tb = tb.tb_next
        assert namespace['_run'].__code__ in frames
        assert len(encoder_frames) == 1
        assert encoder_frames[0]['register'] == 'wsp'
        assert encoder_frames[0]['mnemonic'] in ('cbz', 'cbnz')
    else:
        with pytest.raises(AssertionError,match=r'^typed branch live oracle(?:\n|$)'):
            check(mode,name)


def execute_memory(words):
    regs=[0]*31;regs[9]=111;regs[10]=77;regs[20]=33;regs[29]=8192
    memory={8176:16384,8184:99,16376:222};flags=(1,0,1,1);pc=0;steps=0
    while pc<len(words):
        steps+=1;assert steps<48
        w=words[pc];rd,rn,rm=w&31,(w>>5)&31,(w>>16)&31
        mask=0xffffffffffffffff if w&0x80000000 else 0xffffffff
        if w&0x7fe0ffe0==0x2a0003e0:
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



def memory_sequence(load='ldurb',radix=16):
    return ['func:',o.I('cmp',o.R('w0'),o.R('w0')),o.I('cset',o.R('w9'),o.C('eq')),
            o.I('sturb',o.R('w9'),o.A()),o.I('b.eq',o.S('L_take')),'L_take:',
            o.I(load,o.R('w1'),o.A(-8,radix))]


def memory_check(load,radix):
    seq=memory_sequence(load,radix)
    f,s,span,lines=o._fixture(seq)
    try:
        ids=o._ids(f,span)
        events={rid:lines[i] for i,rid in enumerate(ids) if isinstance(seq[i],str)}
        before,_=read(f,s,span,events)
        out=p._run(f,s,span,14)
        after,actual=read(f,s,out,events)
        legacy=o.legacy._drop_dead_cset_branch_stores(lines)
        raw=o.enc.assemble_text('\n'.join(legacy)).code
        legacy_words=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)]
        expected={'X1':1,'NZCV':(0,1,1,0)}
        assert execute_memory(before)==expected
        assert execute_memory(after)==expected,'typed aliased stack memory oracle'
        assert execute_memory(legacy_words)==expected,'legacy aliased stack memory oracle'
        assert after==before and actual==legacy==lines
        assert o._ids(f,span)==ids
    finally:s.close();f.close()


@pytest.mark.parametrize('load,radix',[('ldurb',16),('ldur',10),('ldurb',10)])
def test_mode14_retains_aliased_and_exact_live_stack_slot(load,radix):memory_check(load,radix)


@pytest.mark.parametrize('load,radix',[('ldurb',16),('ldur',10)])
def test_missing_store_retention_killed_by_memory_oracle(monkeypatch,load,radix):
    memory_check(load,radix)
    old='                        p.output.append(b)\n                        p.output.append(c)\n                        replacement = d'
    new='                        p.output.append(b)\n                        replacement = d'
    source=inspect.getsource(p._run)
    assert source.count(old)==1,'unique store retention anchor'
    namespace=dict(p._run.__globals__)
    exec(compile(source.replace(old,new),'<missing-retained-store>','exec'),namespace)
    monkeypatch.setattr(p,'_run',namespace['_run'])
    with pytest.raises(AssertionError,match=r'^typed aliased stack memory oracle(?:\n|$)'):
        memory_check(load,radix)


@pytest.mark.parametrize('selected',[2,3])
def test_mode14_retained_cset_store_append_failures(monkeypatch,selected):
    from pcc.backend.self_backend_value_arena import CompilerIntArena
    f,s,span,_=o._fixture(memory_sequence())
    ids=o._ids(f,span)
    prefixes=[list(a._values) for a in (f.records,s.headers,s.tokens)]
    contexts,closed,appended=[],[],[]
    init,append,close=p._Pass.__init__,CompilerIntArena.append,CompilerIntArena.close
    error=RuntimeError('retained mode14 output failure')
    def initialize(self,*args):init(self,*args);contexts.append(self)
    def output(self,value):
        if contexts and self is contexts[0].output:
            appended.append(value)
            if value==ids[selected]:raise error
        return append(self,value)
    def shutdown(self):
        if contexts and (self is contexts[0].output or self is contexts[0].ids):closed.append(self)
        return close(self)
    monkeypatch.setattr(p._Pass,'__init__',initialize)
    monkeypatch.setattr(CompilerIntArena,'append',output)
    monkeypatch.setattr(CompilerIntArena,'close',shutdown)
    try:
        with pytest.raises(RuntimeError) as caught:p._run(f,s,span,14)
        assert caught.value is error
        assert appended==ids[:selected+1]
        assert closed==[contexts[0].output,contexts[0].ids]
        assert all(a._closed and not a._values for a in closed)
        assert o._ids(f,span)==ids
        assert all(a._values==old for a,old in zip((f.records,s.headers,s.tokens),prefixes))
        s.require_complete(f.records,len(f.symbol_names))
    finally:s.close();f.close()


def test_conservative_mode14_still_validates_late_record():
    f,s,span,_=o._fixture(memory_sequence())
    try:
        ids=o._ids(f,span)
        s.headers.set(ids[-1]*4,0)
        with pytest.raises(o.enc.EncodeError,match='AArch64 instruction provenance is unproven'):
            p._run(f,s,span,14)
        assert o._ids(f,span)==ids
    finally:s.close();f.close()
