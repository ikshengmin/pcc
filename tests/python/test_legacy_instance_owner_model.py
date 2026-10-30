"""Narrow shared-class creation model; not legacy entry safety certification."""
import ast
from pathlib import Path
from tests.python.test_match_constructor_owner_model import MatchModel

ROOT=Path(__file__).resolve().parents[2] / 'pcc/runtime/py'
SOURCE=ROOT/'py_re_match_runtime.py'

def run(model):
    # Constructor model starts with leased inputs; no-argument bridge has none.
    for index in (2,3):
        model.ns['_re_drop'](model.slots,model.tokens,index)
    node=next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='pcc_re_match_instance_into')
    node.decorator_list=[]
    model.ns['memset']=lambda p,v,n:[model.memory.__setitem__(p+i,v) for i in range(n)]
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(SOURCE),'exec'),model.ns)
    return model.ns[node.name](model.slots)

def test_shared_class_is_leased_through_instance_allocation_and_released_before_return():
    model=MatchModel()
    assert run(model)==0
    result=model.memory[model.slots]
    assert model.objects[result]['kind']=='instance'
    assert model.objects[result]['children']==[2]
    assert model.pin=={result} and model.tls==0
    assert set(model.objects)=={1,2,result}
    assert all(o['leases']==0 for o in model.objects.values())

def test_all_instance_factory_fault_points_preserve_error_and_drop_owners():
    baseline=MatchModel();run(baseline)
    assert baseline.events==['class_copy','alloc:instance','lease']
    for point in range(1,4):
        model=MatchModel(fail=point,cleanup_error=True)
        run(model)
        assert model.memory[model.slots]==0
        assert model.tls!=0
        assert set(model.objects)=={1,2,model.tls}
        assert all(o['leases']==0 for o in model.objects.values())

def test_legacy_singleton_removed_without_removing_pattern_instance_dependency():
    source=(ROOT/'py_re_engine_runtime.py').read_text()
    assert 'def _match_class(' not in source
    assert 'define_global_ptr_null("pcc_re_match_class")' not in source
    assert 'instance = pcc_re_match_instance_guarded()' in source
    assert 'py_instance_new = extern(' in source
    assert 'define_global_ptr_null("pcc_re_pattern_class")' in source
