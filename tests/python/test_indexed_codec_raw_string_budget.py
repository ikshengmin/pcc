"""Raw name/text arrays charge JSON separators without cold tagging reserve."""
import ast
import inspect
import json
import random

import pytest
from pcc.backend import self_backend_indexed_codec as codec
from pcc.backend.self_backend_parse import parse_self_backend_module


def _probe(raw_only=False, old=False):
    node=ast.parse(inspect.getsource(codec._bounded_capture_budget)).body[0]
    if old:
        class Restore(ast.NodeTransformer):
            def visit_Call(self, call):
                if isinstance(call.func, ast.Name) and call.func.id=='raw_strings':
                    call.func.id='cold'
                return self.generic_visit(call)
        node=Restore().visit(node)
    if raw_only:
        end=next(i for i,n in enumerate(node.body) if isinstance(n,ast.FunctionDef) and n.name=='raw_strings')
        node.body=node.body[:end+1]+ast.parse(('cold' if old else 'raw_strings')+'(module)').body
    node.body+=ast.parse('return budget[0]').body
    ns=dict(codec.__dict__)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])), '<probe>', 'exec'),ns)
    return ns[node.name]


def test_raw_strings_exact_json_bound_and_size_limits():
    run=_probe(True)
    rng=random.Random(441811)
    strings=['', 'ascii', '\\"', '\0\n\t', '\ud800', '\U0010ffff']
    strings += [''.join(chr(rng.randrange(0x110000)) for _ in range(25)) for _ in range(50)]
    for values in (strings,tuple(strings),[],(),['']*10000):
        actual=len(json.dumps(list(values),sort_keys=True).encode())
        bound=run(values,1<<22)
        assert bound>=actual
        assert bound==_probe(True,True)(values,1<<22)-6*len(values)
        assert run(values,bound)==bound
        with pytest.raises(ValueError,match='size limit'):run(values,bound-1)


def test_raw_strings_subclass_effects_and_errors_preserved():
    events=[]
    class Container(tuple):
        def __iter__(self):
            events.append('iter')
            return super().__iter__()
    class String(str):
        def __len__(self):
            events.append('len')
            return super().__len__()
        def isascii(self):
            events.append('ascii')
            return super().isascii()
        def isprintable(self):
            events.append('printable')
            return super().isprintable()
        def count(self, value):
            events.append(('count',value))
            return super().count(value)
    class Broken(str):
        def __len__(self):raise LookupError('original string error')
    for values in (Container(('abc',)), (String('a'),),[String('b')],(Broken('x'),)):
        for limit in (1,31,32,33,64,100,1000):
            outcomes=[]
            for run in (_probe(True,True),_probe(True)):
                events.clear()
                try:outcome=('ok',run(values,limit))
                except Exception as exc:outcome=(type(exc),str(exc))
                outcomes.append((outcome,list(events)))
            assert outcomes[0]==outcomes[1]


def test_raw_string_fields_wire_identity_and_original_budget(tmp_path):
    module=parse_self_backend_module('target triple = "x86_64-unknown-linux-gnu"\ndefine i64 @run(i64 %x) {\nentry:\n ret i64 %x\n}\n')
    f=module.functions[0];codec.get_indexed_function_kernel(f)
    f.indexed_kernel.call_texts=tuple('callee'+str(i) for i in range(120000))
    limit=2*1024*1024
    with pytest.raises(ValueError,match='size limit'):_probe(old=True)(module,limit)
    assert _probe()(module,limit)<=limit
    bounded=tmp_path/'bounded.pidx';ordinary=tmp_path/'ordinary.pidx'
    codec.encode_indexed_module_file(str(bounded),module,max_bytes=limit)
    codec.encode_indexed_module_file(str(ordinary),module)
    assert bounded.read_bytes()==ordinary.read_bytes()
    assert bounded.stat().st_size<=limit
    f.indexed_kernel.call_texts=('x'*limit,)
    with pytest.raises(ValueError,match='size limit'):codec.encode_indexed_module_file(str(tmp_path/'reject.pidx'),module,max_bytes=limit)
    assert not (tmp_path/'reject.pidx').exists()
