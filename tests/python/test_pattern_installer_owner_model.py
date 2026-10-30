"""Actual optional installer under checked owners/leases; no public hookup."""
import ast
from pathlib import Path
from tests.python.test_match_constructor_owner_model import MatchModel

SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_re_match_runtime.py"

class InstallModel(MatchModel):
    def __init__(self, fail=None):
        super().__init__(cleanup_error=False)
        for index in (2,3):
            self.ns['_re_drop'](self.slots,self.tokens,index)
        self.cleanup_error=True
        self.target=self.new('instance',{},record=False)
        self.pattern=self.new('pattern','(ab)',record=False)
        self.borrowed=80000
        self.memory[self.borrowed]=self.target
        self.memory[self.borrowed+8]=self.pattern
        self.events=[]
        self.fail=fail
        self.ns.update({'pcc_gc_root_copy_borrowed_lease':self.copy_input,
                        'pcc_re_pattern_fullmatch_address':lambda:777,
                        'memset':lambda p,v,n:[self.memory.__setitem__(p+i,v) for i in range(n)]})
        selected={'_re_copy_input','_re_pattern_add_method','_re_pattern_install','pcc_re_pattern_install_into'}
        nodes=[]
        for n in ast.parse(SOURCE.read_text()).body:
            if isinstance(n,ast.FunctionDef) and n.name in selected:
                n.decorator_list=[];nodes.append(n)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),self.ns)

    def copy_input(self,dest,source):
        assert self.memory[dest]==0
        if self.operation('copy_input'):
            return -1
        obj=self.memory[source]
        self.memory[dest]=obj
        self.incref(obj)
        self.objects[obj]['leases']+=1
        return 1

    def install(self):
        return self.ns['pcc_re_pattern_install_into'](self.borrowed,self.slots,18)


def test_installer_captures_pattern_kind_flags_and_returns_none():
    model=InstallModel();assert model.install()==0
    assert model.memory[model.slots]==1 and model.pin=={1} and model.tls==0
    attrs=model.objects[model.target]['value'];assert set(attrs)=={'fullmatch'}
    fn=model.objects[attrs['fullmatch']]
    captures=model.objects[fn['children'][0]]
    pattern,kind,flags=captures['value']
    assert pattern==model.pattern
    assert model.objects[kind]['value']==6 and model.objects[flags]['value']==18
    assert set(model.objects)==model.reachable(model.target)|{1,2,model.pattern}
    assert all(o['leases']==0 for o in model.objects.values())


def test_every_installer_copy_allocation_lease_tuple_set_and_setattr_failure():
    baseline=InstallModel();baseline.install()
    assert {'copy_input','alloc:tuple','alloc:int','alloc:function','lease','tuple_set','setattr'}<=set(baseline.events)
    assert baseline.events[-1]=='setattr' # Return lease prepared before mutation.
    for point in range(1,len(baseline.events)+1):
        model=InstallModel(fail=point);model.install()
        assert model.memory[model.slots]==0,(point,baseline.events[point-1])
        assert model.tls and model.tls==model.original_error
        assert model.objects[model.target]['value']=={}
        assert set(model.objects)=={1,2,model.target,model.pattern,model.tls}
        assert all(o['leases']==0 for o in model.objects.values())
        assert all(model.memory[model.slots+i*8]==0 for i in range(16))


def test_installer_has_no_getattr_or_public_compile_dispatch():
    tree=ast.parse(SOURCE.read_text())
    selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {'_re_pattern_add_method','_re_pattern_install','pcc_re_pattern_install_into'}]
    called={n.func.id for f in selected for n in ast.walk(f) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)}
    assert 'py_obj_getattr' not in called
    assert 'py_obj_setattr' in called
