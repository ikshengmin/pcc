"""Independent branch relocation and live-register oracle for mode 11."""
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
                assert word&0x7e000000==0x34000000
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
    r=[0]*31;r[9]=0x100000005;r[20]=33
    pc=0;steps=0;flags=(1,0,1,1)
    while pc<len(words):
        steps+=1;assert steps<32
        w=words[pc];rd=w&31
        if w&0x7f800000==0x52800000:
            r[rd]=((w>>5)&65535)<<(16*((w>>21)&3));pc+=1
        elif w&0x7fe0ffe0==0x2a0003e0:
            rm=(w>>16)&31
            if rd!=31:r[rd]=(0 if rm==31 else r[rm])&(0xffffffffffffffff if w&0x80000000 else 0xffffffff)
            pc+=1
        elif w&0xfc000000==0x14000000:
            d=w&0x3ffffff
            if d&(1<<25):d-=1<<26
            pc+=d
        elif w&0x7e000000==0x34000000:
            value=(0 if rd==31 else r[rd])&(0xffffffffffffffff if w&0x80000000 else 0xffffffff)
            taken=(value!=0) if w&0x01000000 else (value==0)
            d=(w>>5)&0x7ffff
            if d&(1<<18):d-=1<<19
            pc+=d if taken else 1
        else:raise AssertionError('unsupported independent decoder '+hex(w))
    return {'X1':r[1],'NZCV':flags}


def sequence(width='x',branch='cbz',dead=False):
    seq=[o.I('movz',o.R(width+'9'),o.N(0)),o.I(branch,o.R(width+'9'),o.S('L_target')),
         o.I('mov',o.R('x1'),o.R('x20')),'L_target:']
    if dead:seq.append(o.I('movz',o.R(width+'9'),o.N(7)))
    return seq+[o.I('mov',o.R('x1'),o.R('x9'))]


def check(width,branch,dead):
    seq=sequence(width,branch,dead)
    f,s,span,lines=o._fixture(seq)
    try:
        ids=o._ids(f,span)
        events={rid:lines[i] for i,rid in enumerate(ids) if isinstance(seq[i],str)}
        before,_=read(f,s,span,events)
        result=p._run(f,s,span,11)
        after,typed_lines=read(f,s,result,events)
        legacy=o.legacy._fold_zero_test_branch(lines)
        raw=o.enc.assemble_text('\n'.join(legacy)).code
        legacy_words=[int.from_bytes(raw[i:i+4],'little') for i in range(0,len(raw),4)]
        expected={'X1':7 if dead else 0,'NZCV':(1,0,1,1)}
        assert execute(before)==expected
        assert execute(after)==expected,'typed target live oracle'
        assert execute(legacy_words)==expected,'legacy target live oracle'
        assert typed_lines==legacy
        assert after[0]==before[0], 'zero definition retained'
        assert len(after)==len(before)-(1 if branch=='cbnz' else 0)
        if branch=='cbz':assert after[1]&0xfc000000==0x14000000
        assert o._ids(f,span)==ids
    finally:s.close();f.close()


@pytest.mark.parametrize('width',['x','w'])
@pytest.mark.parametrize('branch,dead',[('cbz',False),('cbnz',False),('cbz',True)])
def test_live_target_and_safe_controls(width,branch,dead):check(width,branch,dead)


def test_branch_symbol_all_four_fields_retained():
    f,s,span,_=o._fixture(sequence())
    try:
        ids=o._ids(f,span)
        header=s.require_header(ids[1]);offset=(header.third+1)*4
        s.tokens.set(offset+2,o.sem.SYMBOL_PAGE)
        s.tokens.set(offset+3,37)
        expected=s.tokens.get4_unchecked(header.third+1)
        out=p._run(f,s,span,11)
        actual=o._ids(f,out)
        assert actual[0]==ids[0]
        branch=s.require_header(actual[1])
        observed=s.tokens.get4_unchecked(branch.third)
        assert observed==expected
        assert f.records.get4_unchecked(actual[1]).fourth==expected.second
    finally:s.close();f.close()


def test_new_branch_failure_precedes_retaining_zero(monkeypatch):
    from pcc.backend.self_backend_value_arena import CompilerIntArena
    f,s,span,_=o._fixture(sequence())
    ids=o._ids(f,span)
    contexts,events,closed=[],[],[]
    init,append,close=p._Pass.__init__,CompilerIntArena.append,CompilerIntArena.close
    error=RuntimeError('new branch primary failure')
    def initialize(self,*args):init(self,*args);contexts.append(self)
    def fail(self,*args):
        assert not self.output._values
        events.append('producer');raise error
    def output(self,value):
        if contexts and self is contexts[0].output:
            events.append('output');raise AssertionError('output before failing producer')
        return append(self,value)
    def shutdown(self):
        if contexts and (self is contexts[0].output or self is contexts[0].ids):closed.append(self)
        return close(self)
    monkeypatch.setattr(p._Pass,'__init__',initialize)
    monkeypatch.setattr(p._Pass,'new_branch',fail)
    monkeypatch.setattr(CompilerIntArena,'append',output)
    monkeypatch.setattr(CompilerIntArena,'close',shutdown)
    try:
        with pytest.raises(RuntimeError) as caught:p._run(f,s,span,11)
        assert caught.value is error and events==['producer']
        assert closed==[contexts[0].output,contexts[0].ids]
        assert all(a._closed for a in closed)
        assert o._ids(f,span)==ids
    finally:s.close();f.close()


def mutate(function,old,new):
    text=inspect.getsource(function)
    assert text.count(old)==1,'unique mutation anchor'
    namespace=dict(function.__globals__)
    exec(compile(text.replace(old,new),'<zero-branch-mutant>','exec'),namespace)
    return namespace[function.__name__]


@pytest.mark.parametrize('side',['typed','legacy'])
@pytest.mark.parametrize('width',['w','x'])
def test_missing_retention_killed_by_independent_control_flow(monkeypatch,side,width):
    check(width,'cbz',False)
    if side=='typed':
        old='                    if branch[0] == "cbz":\n                        p.output.append(a)\n'
        monkeypatch.setattr(p,'_run',mutate(p._run,old,''))
    else:
        old='                    if opcode == "cbz":\n                        out.append(lines[index])\n'
        monkeypatch.setattr(o.legacy,'_fold_zero_test_branch',mutate(o.legacy._fold_zero_test_branch,old,'                    if opcode == "cbz":\n'))
    with pytest.raises(AssertionError,match=r'^'+side+r' target live oracle(?:\n|$)'):
        check(width,'cbz',False)


@pytest.mark.parametrize('fail_at',[1,2])
def test_output_failure_after_successful_branch_producer(monkeypatch,fail_at):
    from pcc.backend.self_backend_value_arena import CompilerIntArena
    f,s,span,_=o._fixture(sequence())
    ids=o._ids(f,span)
    prefixes=[list(a._values) for a in (f.records,s.headers,s.tokens)]
    contexts,events,closed,produced=[],[],[],[]
    init,branch=p._Pass.__init__,p._Pass.new_branch
    append,close=CompilerIntArena.append,CompilerIntArena.close
    error=RuntimeError('output append failure '+str(fail_at))
    append_count=0
    def initialize(self,*args):init(self,*args);contexts.append(self)
    def producer(self,*args):
        assert not self.output._values
        events.append('producer_start')
        rid=branch(self,*args)
        produced.append(rid)
        events.append('producer_complete')
        return rid
    def output(self,value):
        nonlocal append_count
        if contexts and self is contexts[0].output:
            append_count+=1
            assert produced, 'producer must finish before output'
            assert value==(ids[0] if append_count==1 else produced[0])
            events.append('append_'+str(append_count))
            if append_count==fail_at:raise error
        return append(self,value)
    def shutdown(self):
        if contexts and (self is contexts[0].output or self is contexts[0].ids):closed.append(self)
        return close(self)
    monkeypatch.setattr(p._Pass,'__init__',initialize)
    monkeypatch.setattr(p._Pass,'new_branch',producer)
    monkeypatch.setattr(CompilerIntArena,'append',output)
    monkeypatch.setattr(CompilerIntArena,'close',shutdown)
    try:
        with pytest.raises(RuntimeError) as caught:p._run(f,s,span,11)
        assert caught.value is error
        assert events==['producer_start','producer_complete']+['append_'+str(i) for i in range(1,fail_at+1)]
        assert closed==[contexts[0].output,contexts[0].ids]
        assert all(a._closed and not a._values for a in closed)
        assert len(produced)==1
        # A successfully staged instruction survives a later output failure.
        assert len(f.records)==len(prefixes[0])+4
        assert produced[0]==len(prefixes[0])//4
        assert s.require_header(produced[0]).first==o.sem.semantic_opcode_id('b')
        assert o._ids(f,contexts[0].result)==produced
        assert o._ids(f,span)==ids
        assert all(a._values[:len(old)]==old for a,old in zip((f.records,s.headers,s.tokens),prefixes))
        s.require_complete(f.records,len(f.symbol_names))
    finally:s.close();f.close()
