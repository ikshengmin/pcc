"""Linux x64 worker sampler: exact target/leaf/ownership contracts."""
import importlib.util
from pathlib import Path

import pytest
from pcc.frontends.python.codegen.errors import L1CodegenError
from pcc.frontends.python.codegen.runtime_call_effects import (
    NO_SAFEPOINT_RUNTIME_FUNCTIONS, verify_no_safepoint_runtime_function,
)
from pcc.frontends.python.owned_runtime_build import runtime_modules
from pcc.ir.compat import ir

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / 'pcc/runtime'
SOURCE = RUNTIME / 'py/freestanding_linux_worker_sampler.py'
NAME = 'freestanding_linux_worker_sampler'
HANDLER = 'pcc_worker_cpu_sampler_handler'
MASK = 1 << 26
TOKEN = 5260100

@pytest.mark.parametrize('target,expected',[
    ('x86_64-unknown-linux-gnu',True), ('amd64-linux-gnu',True),
    ('aarch64-unknown-linux-gnu',False), ('x86_64-pc-windows-msvc',False),
    ('arm64-apple-darwin',False), ('x86_64-apple-darwin',False),
])
def test_sampler_inventory_only_linux_x64(target,expected):
    assert runtime_modules(str(RUNTIME),target).count(NAME) == int(expected)


def test_sampler_leaf_guard_does_not_admit_call_or_cycle():
    assert HANDLER in NO_SAFEPOINT_RUNTIME_FUNCTIONS
    for cyclic in (False,True):
        mod=ir.Module(name='sampler_leaf_contract')
        signature=ir.FunctionType(ir.VoidType(),[])
        func=ir.Function(mod,signature,name=HANDLER)
        entry=func.append_basic_block('entry'); b=ir.IRBuilder(entry)
        if cyclic:
            loop=func.append_basic_block('loop'); b.branch(loop); b.position_at_end(loop); b.branch(loop)
        else:
            external=ir.Function(mod,signature,name='not_signal_safe'); b.call(external,[]); b.ret_void()
        with pytest.raises(L1CodegenError,match='call|cycle'):
            verify_no_safepoint_runtime_function(func)


class Kernel:
    """Deterministic signal/timer ownership model, not a syscall execution."""
    def __init__(self):
        self.cells={}; self.pointers={}; self.calls=[]; self.mask=0; self.pending=0
        self.action=bytearray(32); self.itimer=bytearray(32); self.timer=None
        self.tid=101; self.tasks=1; self.fail=None; self.pending_after_block=False
        self.read_dir=False

    def addr(self,name):
        return self.cells.setdefault(name,bytearray(8))

    def syscall(self,n,a=0,b=0,c=0,d=0,e=0,f=0):
        self.calls.append(n)
        if self.fail==(n,len([x for x in self.calls if x==n])):return -5
        if n==39:return 101
        if n==186:return self.tid
        if n==257:self.read_dir=False;return 7
        if n==217:
            if self.read_dir:return 0
            self.read_dir=True
            for i in range(self.tasks):
                b[i*24+16]=24;b[i*24+19]=49+i
            return 24*self.tasks
        if n==3:return 0
        if n==13:
            if c is not None:c[:32]=self.action
            if b is not None:self.action[:]=b[:32]
            return 0
        if n==36:b[:32]=self.itimer;return 0
        if n==14:
            if c is not None:put(c,0,self.mask,8)
            if b is not None:
                value=get(b,0,8)
                if a==0:
                    self.mask|=value
                    if self.pending_after_block:self.pending|=MASK
                elif a==1:self.mask&=~value
                else:self.mask=value
            return 0
        if n==127:put(a,0,self.pending,8);return 0
        if n==222:
            assert self.timer is None and a==3 and get(b,8,4)==27 and get(b,12,4)==4 and get(b,16,4)==self.tid
            self.timer=23;put(c,0,23,4);return 0
        if n==223:assert self.timer==a;return 0
        if n==226:assert self.timer==a;self.timer=None;return 0
        if n==128:return -11
        raise AssertionError(('unmodelled syscall',n))

    def image(self):
        return bytes(self.action),self.mask,self.pending,bytes(self.itimer),self.timer


def get(buf,off,size):return int.from_bytes(buf[off:off+size],'little',signed=True)
def put(buf,off,val,size):buf[off:off+size]=(int(val)&((1<<(size*8))-1)).to_bytes(size,'little')


@pytest.fixture
def model(monkeypatch):
    import pcc.unsafe as unsafe
    import pcc.extern as abi
    k=Kernel()
    for name,fn in {
        'global_addr':lambda n:1235 if n=='pcc_linux_rt_sigreturn' else k.addr(n),
        'define_global_i64':lambda n,v:put(k.addr(n),0,v,8),
        'define_global_ptr_null':lambda n:k.pointers.update({n:None}),
        'global_load_ptr':lambda n:k.pointers[n],
        'global_store_ptr':lambda n,v:k.pointers.update({n:v}),
        'function_addr':lambda n:1234,
        'syscall6':k.syscall,
        'stack_alloc':lambda n:bytearray(n),
        'null':lambda:None,
        'ptr_is_null':lambda p:p is None or p==0,
        'ptr_to_int':lambda p:0 if p is None else int(p),
        'load_i8':lambda p,o:get(p,o,1),
        'load_i32':lambda p,o:get(p,o,4),
        'load_i64':lambda p,o:get(p,o,8),
        'atomic_load_i64':lambda p,o,_:get(p,o,8),
        'store_i32':lambda p,o,v:put(p,o,v,4),
        'store_i64':lambda p,o,v:put(p,o,v,8),
        'atomic_store_i64':lambda p,o,v,_:put(p,o,v,8),
        'store_ptr':lambda p,o,v:put(p,o,v,8),
        'memset':lambda p,v,n:p.__setitem__(slice(0,n),bytes([v])*n),
        'cstr':lambda s:s.encode()+b'\0',
    }.items():monkeypatch.setattr(unsafe,name,fn)
    monkeypatch.setattr(abi,'c_abi_export',lambda name:lambda fn:fn)
    monkeypatch.setattr(abi,'extern',lambda *args:lambda:1)
    spec=importlib.util.spec_from_file_location('worker_sampler_model',SOURCE)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert k.calls==[]  # OFF/import never installs a signal/timer.
    return module,k


def test_sampler_model_conflicts_do_not_acquire(model):
    m,k=model;buf=bytearray(64)
    for condition,undo,expected in (
        (lambda:put(k.action,0,1,8),lambda:put(k.action,0,0,8),-5),
        (lambda:put(k.itimer,0,60,8),lambda:put(k.itimer,0,0,8),-7),
        (lambda:setattr(k,'mask',MASK),lambda:setattr(k,'mask',0),-9),
        (lambda:setattr(k,'tasks',2),lambda:setattr(k,'tasks',1),-3),
    ):
        condition();before=k.image();assert m.start(buf,8,1000000)==expected;assert k.image()==before;undo()
    assert m.start(buf,0,1000000)==-1
    k.pending_after_block=True
    assert m.start(buf,8,1000000)==-11
    assert k.mask==0 and k.pending==MASK and k.timer is None and not any(k.action)


def test_sampler_model_owner_bound_and_foreign(model):
    m,k=model;before=k.image();buf=bytearray(16)
    assert m.start(buf,2,1000000)==0
    assert m.start(buf,2,1000000)==-2
    info=bytearray(128);context=bytearray(256)
    put(info,8,-2,4);put(info,16,23,4);put(info,24,TOKEN,8)
    for pc in (123,456,789):
        put(context,168,pc,8);m.handler(27,info,context)
    assert get(buf,0,8)==123 and get(buf,8,8)==456 and m.dropped()==1
    k.tid=102;assert m.stop()==-100;k.tid=101
    assert m.stop()==2 and k.image()==before
    assert m.start(buf,2,1000000)==0
    put(info,16,24,4);m.handler(27,info,context)
    assert m.stop()==-3 and k.image()==before


@pytest.mark.parametrize('syscall,occurrence,expected',[(14,2,-12),(13,2,-13),(222,1,-14),(223,1,-15)])
def test_sampler_model_setup_failure_restores_owned_only(model,syscall,occurrence,expected):
    m,k=model;before=k.image();k.fail=(syscall,occurrence)
    assert m.start(bytearray(16),2,1000000)==expected
    assert k.image()==before
    assert k.calls.count(syscall)>=occurrence
    assert k.pointers['pcc_worker_cpu_sampler_buffer'] is None
    assert get(k.addr('pcc_worker_cpu_sampler_timer'),0,8)==-1


def test_sampler_exact_internal_exports():
    import ast
    funcs=[node for node in ast.parse(SOURCE.read_text()).body if isinstance(node,ast.FunctionDef)]
    exports=[]
    for function in funcs:
        matched=[d.args[0].value for d in function.decorator_list
                 if isinstance(d,ast.Call) and isinstance(d.func,ast.Name)
                 and d.func.id=='c_abi_export' and len(d.args)==1 and isinstance(d.args[0],ast.Constant)]
        assert len(matched)==1,function.name
        exports.append(matched[0])
    assert exports==[HANDLER,'pcc_worker_cpu_sampler_singleton','pcc_worker_cpu_sampler_restore',
                     'pcc_worker_cpu_sampler_start','pcc_worker_cpu_sampler_stop','pcc_worker_cpu_sampler_dropped']


def test_sampler_raw_globals_disjoint_from_abi_symbols():
    import ast
    tree=ast.parse(SOURCE.read_text())
    raw=set(); abi=set()
    for node in tree.body:
        if isinstance(node,ast.Expr) and isinstance(node.value,ast.Call):
            call=node.value
            if isinstance(call.func,ast.Name) and call.func.id in ('define_global_i64','define_global_ptr_null'):
                name=call.args[0].value; assert name not in raw; raw.add(name)
        if isinstance(node,ast.FunctionDef):
            for d in node.decorator_list:
                if isinstance(d,ast.Call) and isinstance(d.func,ast.Name) and d.func.id=='c_abi_export':
                    name=d.args[0].value; assert name not in abi; abi.add(name)
    assert raw.isdisjoint(abi),raw & abi
