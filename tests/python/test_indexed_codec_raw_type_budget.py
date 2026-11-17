"""Raw type context accounting; cold tags and fallback effects stay intact."""
import ast
import inspect
import json
import random
import pytest
from pcc.backend import self_backend_indexed_codec as codec
from pcc.backend.self_backend_ir import TypeDesc
from pcc.backend.self_backend_parse import parse_self_backend_module


def probe():
    node=ast.parse(inspect.getsource(codec._bounded_capture_budget)).body[0]
    cold=next(n for n in node.body if isinstance(n,ast.FunctionDef) and n.name=='cold')
    node.body=node.body[:node.body.index(cold)+1]+ast.parse('cold(module, True)\nreturn budget[0]').body
    ns=dict(codec.__dict__)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'<raw-probe>','exec'),ns)
    return ns[node.name]


def test_recursive_raw_type_bound_and_cold_tag_unchanged():
    run=probe();rng=random.Random(315)
    def make(depth):
        if not depth:return TypeDesc('i\"\\\ud800',width=rng.choice([0,-1,64,2**100]))
        return TypeDesc('struct',count=2**100,name='\U0001f600',pointee=make(depth-1),elem=make(depth-1),fields=(make(depth-1),))
    for depth in range(6):
        t=make(depth);wire=json.dumps(codec._type_to_wire(t),sort_keys=True).encode()
        bound=run(t,1<<24)
        assert bound>=len(wire)+8
        assert run(t,bound)==bound
        with pytest.raises(ValueError,match='size limit'):run(t,bound-1)
        assert codec._cold_to_wire((t,))=={'q':[{'t':codec._type_to_wire(t)}]}
    assert run(None,100)==64  # unchanged fallback, not a TypeDesc


def test_subclass_access_and_errors_retained():
    events=[]
    class Observed(TypeDesc):
        def __getattribute__(self,name):
            if name in ('kind','width','count','pointee','elem','fields','name'):events.append(name)
            return super().__getattribute__(name)
    t=Observed('int',width=32);run=probe()
    events.clear();bound=run(t,1000);seen=list(events)
    expected=['kind','width','pointee','count','elem','name','fields','fields']
    assert seen==expected
    assert bound==32+len(json.dumps(codec._type_to_wire(t)).encode())
    events.clear()
    with pytest.raises(ValueError,match='size limit'):run(t,31)
    assert events==[]


def test_complete_wire_parity_all_raw_positions(tmp_path):
    module=parse_self_backend_module('target triple = "x86_64-unknown-linux-gnu"\n@x = global i64 0\ndefine i64 @run(i64 %x) {\nentry:\n ret i64 %x\n}\n')
    for fn in module.functions:codec.get_indexed_function_kernel(fn)
    kernel=module.functions[0].indexed_kernel
    kernel.types=tuple(TypeDesc('int',width=32) for _ in range(10000))
    p=tmp_path/'raw.pidx';codec.encode_indexed_module_file(str(p),module)
    original=p.read_bytes()
    codec.encode_indexed_module_file(str(p),module,max_bytes=2*1024*1024)
    assert p.read_bytes()==original
    assert len(original)<2*1024*1024
    with pytest.raises(ValueError,match='size limit'):codec.encode_indexed_module_file(str(tmp_path/'fail.pidx'),module,max_bytes=100)
    assert not (tmp_path/'fail.pidx').exists()
