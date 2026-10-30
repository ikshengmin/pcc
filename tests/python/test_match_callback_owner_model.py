"""Callback owner/lease model, not a native/collector semantics substitute."""
import ast
from pathlib import Path
import pytest
from tests.python.test_match_constructor_owner_model import MatchModel
SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_re_match_runtime.py"

class CallbackModel(MatchModel):
    def __init__(self, case, fail=None):
        super().__init__()
        for i in (2,3):self.ns['_re_drop'](self.slots,self.tokens,i)
        self.case=case
        text=self.new('str',b'ab',record=False)
        spans=self.build_tuple([self.new('int',v,record=False) for v in (0,2,0,1,-1,-1)])
        names=self.build_tuple([self.new('str',v,record=False) for v in (b'first',b'second')])
        if case in ('default_alias','dict_default_alias'):
            self.incref(text);args=self.build_tuple([text]);kind=4 if case=='default_alias' else 5
        elif case=='index_alias':
            index=self.new('int',1,record=False);args=self.build_tuple([index]);kind=0
        elif case=='index_new':
            index=self.new('index',1,record=False);args=self.build_tuple([index]);kind=3
        elif case=='name':
            args=self.build_tuple([self.new('str',b'first',record=False)]);kind=0
        else:
            args=self.build_tuple([self.new('int',1,record=False),self.new('int',2,record=False)]);kind=0
        self.kind=kind
        self.nargs=len(self.objects[args]['value'])
        for i,obj in ((3,args),(4,text),(5,spans),(6,names)):
            self.memory[self.slots+i*8]=obj;self.memory[self.tokens+i*8]=1;self.objects[obj]['leases']+=1
        self.events=[];self.fail=fail
        self.ns.update({'memset':lambda p,v,n:[self.memory.__setitem__(p+i,v) for i in range(n)],
            'load_i32':lambda p,n:self.memory[p+n],
            'py_tuple_get':self.get_item,'py_str_check':self.is_string,
            'py_obj_has_index':self.has_index,'py_obj_index_slots':self.index_slots,
            'py_int_to_i64':self.as_int,'py_str_utf8':self.utf8_value,
            'py_str_byte_len':self.strlen,'py_str_byte_slice_i64':self.slice,
            'py_dict_new':lambda:self.new('dict',{}),'py_dict_set':self.dict_set,
            'pcc_gc_root_copy_lease':self.copy_owned,
            'py_exc_new':lambda tag,msg:self.new('error',(tag,msg)), 'py_raise':self.raise_error})
        selected={'_re_span_value','_re_same_name','_re_select_group','_re_group_item','_re_callback_result','_re_error'}
        nodes=[]
        for n in ast.parse(SOURCE.read_text()).body:
            if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id.startswith('_CB_') for t in n.targets):nodes.append(n)
            elif isinstance(n,ast.FunctionDef) and n.name in selected:n.decorator_list=[];nodes.append(n)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),self.ns)

    def build_tuple(self,children):
        obj=self.new('tuple',children,record=False);self.objects[obj]['children']=children[:];return obj
    def get_item(self,obj,index):
        self.require(obj)
        if self.operation('tuple_get'):
            self.set_error('tuple_get');return 0
        item=self.objects[obj]['value'][index];self.incref(item);return item
    def is_string(self,obj):self.require(obj);return int(self.objects[obj]['kind']=='str')
    def has_index(self,obj):self.require(obj);return int(self.objects[obj]['kind'] in ('int','index'))
    def index_slots(self,source,dest):
        assert self.memory[dest]==0
        obj=self.memory[source];self.require(obj)
        if self.operation('index_call'):
            self.set_error('index');return -1
        if self.objects[obj]['kind']=='int':self.incref(obj);self.memory[dest]=obj
        else:
            self.memory[dest]=self.new('int',self.objects[obj]['value'])
            if not self.memory[dest]:return -1
        return 0
    def as_int(self,obj,overflow):self.require(obj);self.memory[overflow]=0;return self.objects[obj]['value']
    def utf8_value(self,obj):
        self.require(obj);data=self.objects[obj]['value'];p=self.allocate_raw(len(data))
        for i,v in enumerate(data):self.memory[p+i]=v
        return p
    def strlen(self,obj):self.require(obj);return len(self.objects[obj]['value'])
    def slice(self,obj,lo,hi):self.require(obj);return self.new('str',self.objects[obj]['value'][lo:hi])
    def copy_owned(self,dest,source):
        assert self.memory[dest]==0
        obj=self.memory[source];self.require(obj)
        if self.operation('default_copy'):return -1
        self.memory[dest]=obj;self.incref(obj)
        if obj==1:return 0
        self.objects[obj]['leases']+=1;return 1
    def dict_set(self,obj,key,value):
        for item in (obj,key,value):self.require(item)
        if self.operation('dict_set'):self.set_error('dict_set');return
        self.objects[obj]['value'][key]=value
        for item in (key,value):self.objects[obj]['children'].append(item);self.incref(item)
    def raise_error(self,error):
        self.require(error);self.incref(error);self.tls=error
        if not self.original_error:self.original_error=error
    def callback(self):self.ns['_re_callback_result'](self.slots,self.tokens,self.kind,self.nargs,2)

CASES=['default_alias','dict_default_alias','index_alias','index_new','name','multi']
@pytest.mark.parametrize('case',CASES)
def test_callback_success_alias_graph_and_no_leases(case):
    m=CallbackModel(case);m.callback();assert not m.tls
    m.cleanup_error=True;m.finish()
    result=m.memory[m.slots]
    assert result and m.pin=={result} and not m.tls
    assert all(o['leases']==0 for o in m.objects.values())
    assert set(m.objects)==m.reachable(result)|{1,2}

@pytest.mark.parametrize('case',CASES)
def test_callback_all_faults_preserve_original_tls_and_drop_owners(case):
    baseline=CallbackModel(case);baseline.callback()
    for point in range(1,len(baseline.events)+1):
        m=CallbackModel(case,point);m.callback()
        assert m.tls,(case,point,baseline.events[point-1])
        original=m.tls;m.fail=None;m.cleanup_error=True;m.finish()
        assert m.tls==original and m.memory[m.slots]==0
        assert set(m.objects)=={1,2,original},(case,point,m.objects)
        assert all(o['leases']==0 for o in m.objects.values())
