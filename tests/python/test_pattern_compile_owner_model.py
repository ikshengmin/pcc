"""Actual Pattern constructor owner model with bounded normalizer oracle."""
import ast
from pathlib import Path
from tests.python.test_match_constructor_owner_model import MatchModel
SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_re_match_runtime.py"

class PatternModel(MatchModel):
    def __init__(self,fail=None):
        super().__init__()
        for i in (2,3):self.ns['_re_drop'](self.slots,self.tokens,i)
        self.objects[3]={'kind':'pattern_class','refs':1,'leases':0,'children':[],'immortal':True}
        self.pattern=self.new('str',b'(?i) a b',record=False)
        self.memory[self.slots+16]=self.pattern;self.memory[self.tokens+16]=1;self.objects[self.pattern]['leases']=1
        self.events=[];self.fail=fail;self.normalized=0
        self.ns.update({'py_str_check':lambda o:self.is_str(o), 'pcc_re_normalize_compile_pattern_into':self.normalize,
            'py_tuple_get':self.get_item,'py_index_i64_checked_slots':self.index,
            'py_str_utf8':self.utf8,'py_str_byte_len':self.strlen,
            'strlen':lambda p:self.raw_length(p),'pcc_re_engine_supported_flags':self.supported,
            'pcc_re_pattern_class_copy_lease':self.pattern_class,
            'pcc_re_pattern_legacy_address':lambda:777,'pcc_re_pattern_fullmatch_address':lambda:778,
            'py_func_new_named':self.function,'py_exc_new':lambda tag,msg:self.new('error',(tag,msg)),
            'py_raise':self.raise_error})
        selected={'_re_compile_pattern','_re_pattern_add_method','_re_error'}
        nodes=[]
        for n in ast.parse(SOURCE.read_text()).body:
            if isinstance(n,ast.FunctionDef) and n.name in selected:n.decorator_list=[];nodes.append(n)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),self.ns)
    def is_str(self,obj):self.require(obj);return int(self.objects[obj]['kind']=='str')
    def normalize(self,obj,flags,slot,verbose_slot,verbose_token_slot,projection_slot,projection_token_slot):
        self.require(obj);assert obj==self.pattern and flags==16
        if self.operation('normalize'):self.set_error('normalize');return 0
        raw=self.new('str',b'ab',record=False);flag=self.new('int',18,record=False)
        result=self.new('tuple',[raw,flag],record=False);self.objects[result]['children']=[raw,flag]
        self.normalized=raw;self.memory[slot]=result;return self.acquire(slot)
    def get_item(self,obj,index):
        self.require(obj)
        if self.operation('tuple_get'):self.set_error('tuple_get');return 0
        child=self.objects[obj]['value'][index];self.incref(child);return child
    def index(self,slot):
        obj=self.memory[slot];self.require(obj)
        if self.operation('flags_index'):self.set_error('flags_index');return 0
        return self.objects[obj]['value']
    def utf8(self,obj):
        self.require(obj);data=self.objects[obj]['value'];p=self.allocate_raw(len(data)+1)
        for i,v in enumerate(data):self.memory[p+i]=v
        self.memory[p+len(data)]=0;return p
    def strlen(self,obj):self.require(obj);return len(self.objects[obj]['value'])
    def raw_length(self,p):
        n=0
        while self.memory[p+n]:n+=1
        return n
    def supported(self,p,flags):
        assert flags==18
        return 0 if self.operation('supported') else 1
    def pattern_class(self,slot):
        assert self.memory[slot]==0
        if self.operation('pattern_class'):return -1
        self.memory[slot]=3;self.objects[3]['leases']+=1;return 1
    def function(self,entry,captures,name):
        assert entry in (777,778);self.require(captures)
        obj=self.new('function',name)
        if obj:
            self.objects[obj]['children'].append(captures);self.incref(captures);self.objects[obj]['entry']=entry
        return obj
    def raise_error(self,error):
        self.require(error);self.incref(error);self.tls=error
        if not self.original_error:self.original_error=error
    def compile(self):self.ns['_re_compile_pattern'](self.slots,self.tokens,16)


def test_pattern_constructor_all_seven_methods_share_normalized_capture_and_class():
    m=PatternModel();m.compile();assert not m.tls
    result=m.memory[m.slots];attrs=m.objects[result]['value']
    names=['match','search','findall','sub','split','finditer','fullmatch']
    assert set(attrs)==set(names)|{'pattern'} and attrs['pattern']==m.pattern
    assert 3 in m.objects[result]['children'] and 2 not in m.objects[result]['children']
    for kind,name in enumerate(names):
        fn=m.objects[attrs[name]];captures=m.objects[fn['children'][0]]['value']
        assert captures[0]==m.normalized
        assert m.objects[captures[1]]['value']==kind
        assert m.objects[captures[2]]['value']==18
        assert fn['entry']==(778 if kind==6 else 777)
    m.cleanup_error=True;m.finish()
    assert not m.tls and m.pin=={result}
    assert all(o['leases']==0 for o in m.objects.values())
    assert set(m.objects)==m.reachable(result)|{1,2,3}


def test_every_pattern_constructor_fault_cleans_owners_and_preserves_original_tls():
    baseline=PatternModel();baseline.compile()
    assert {'normalize','tuple_get','flags_index','supported','pattern_class','alloc:instance','alloc:tuple','alloc:int','alloc:function','lease','tuple_set','setattr'}<=set(baseline.events)
    for point in range(1,len(baseline.events)+1):
        m=PatternModel(point);m.compile();assert m.tls,(point,baseline.events[point-1])
        original=m.tls;m.fail=None;m.cleanup_error=True;m.finish()
        assert m.memory[m.slots]==0 and m.tls==original
        assert set(m.objects)=={1,2,3,original},(point,m.objects)
        assert all(o['leases']==0 for o in m.objects.values())
