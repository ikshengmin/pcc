"""Exact-int cold charge keeps a proven scalar margin and old subclass effects."""
import ast
import inspect
import json
import random

import pytest
from pcc.backend import self_backend_indexed_codec as codec
from pcc.backend.self_backend_parse import parse_self_backend_module


def _probe(previous=False, cold_only=True):
    node = ast.parse(inspect.getsource(codec._bounded_capture_budget)).body[0]
    cold = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == 'cold')
    first = next(n for n in cold.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call))
    assert ast.unparse(first.value) == 'charge(8 if value_type is str or value_type is int or (raw_type and value_type is TypeDesc) else 32)'
    if previous:
        first.value.args = [ast.parse('8 if value_type is str else 32', mode='eval').body]
    if cold_only:
        stop = node.body.index(cold)
        node.body = node.body[:stop+1] + ast.parse('cold(module)').body
    node.body += ast.parse('return budget[0]').body
    namespace = dict(codec.__dict__)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), '<budget-probe>', 'exec'), namespace)
    return namespace['_bounded_capture_budget']


def test_exact_integer_bit_decimal_bound_and_limits():
    current, previous = _probe(), _probe(True)
    values = [0, 1, -1]
    for bits in range(1, 257):
        values.extend([2**bits-1, 2**bits, 2**bits+1, -(2**bits-1), -2**bits, -(2**bits+1)])
    for power in range(1, 100):
        values.extend([10**power-1, 10**power, -(10**power), -(10**power+1)])
    for value in values:
        size = current(value, 1 << 20)
        assert size == previous(value, 1 << 20) - 24
        assert size >= len(json.dumps(value).encode()) + 8
        assert current(value, size) == size
        with pytest.raises(ValueError, match='size limit'):
            current(value, size-1)
    assert current(False, 100) == previous(False, 100) == 33
    assert current(True, 100) == previous(True, 100) == 34


def test_nested_integer_container_bound():
    run = _probe()
    rng = random.Random(441246)
    def make(depth):
        if not depth or rng.randrange(3)==0:
            return rng.choice([0, -1, 127, 256, -(2**64), 2**256, True, None, '\U0001f600', '\ud800'])
        size = rng.randrange(7)
        kind = rng.randrange(3)
        if kind == 0:return [make(depth-1) for _ in range(size)]
        if kind == 1:return tuple(make(depth-1) for _ in range(size))
        return {i:make(depth-1) for i in range(size)}
    for value in [make(6) for _ in range(1000)] + [{i:0 for i in range(10000)}, [0]*10000]:
        actual = len(json.dumps(codec._cold_to_wire(value), sort_keys=True).encode())
        assert run(value, 1 << 25) >= actual + 8


def test_integer_subclass_callbacks_and_failure_order_unchanged():
    events=[]
    class Observed(int):
        def bit_length(self):
            events.append('bits')
            return super().bit_length()
    class Broken(int):
        def bit_length(self):
            events.append('raise')
            raise LookupError('original-bit-failure')
    for value in [Observed(0), Observed(-123), Broken(42)]:
        for limit in [1, 8, 31, 32, 33, 40, 100]:
            observed=[]
            for run in [_probe(True), _probe()]:
                events.clear()
                try:result=('ok',run(value,limit))
                except Exception as e:result=(type(e).__name__,str(e))
                observed.append((result,list(events)))
            assert observed[0]==observed[1]


def test_real_wire_parity_and_unchanged_two_mib_gate(tmp_path):
    module=parse_self_backend_module('target triple = "x86_64-unknown-linux-gnu"\ndefine i64 @run(i64 %x) {\nentry:\n ret i64 %x\n}\n')
    function=module.functions[0]
    codec.get_indexed_function_kernel(function)
    function.indexed_kernel.cold_instruction_data=(tuple(range(60000)),)
    limit=2*1024*1024
    with pytest.raises(ValueError,match='size limit'):_probe(True,False)(module,limit)
    assert _probe(False,False)(module,limit)<=limit
    path=tmp_path/'bounded.pidx';codec.encode_indexed_module_file(str(path),module,max_bytes=limit)
    payload=path.read_bytes();assert len(payload)<=limit
    codec.encode_indexed_module_file(str(path),module)
    assert path.read_bytes()==payload
    huge=codec.ParsedModule('x'*limit,(),())
    target=tmp_path/'oversized.pidx'
    with pytest.raises(ValueError,match='size limit'):codec.encode_indexed_module_file(str(target),huge,max_bytes=limit)
    assert not target.exists()


def test_arbitrary_int_json_policy_and_failure_before_open(tmp_path):
    import sys
    digit_limit=sys.get_int_max_str_digits()
    value=10**(max(digit_limit,4300)+1)
    module=parse_self_backend_module('target triple = "x86_64-unknown-linux-gnu"\ndefine i64 @run(i64 %x) {\nentry:\n ret i64 %x\n}\n')
    function=module.functions[0];codec.get_indexed_function_kernel(function)
    function.indexed_kernel.cold_instruction_data=(value,)
    path=tmp_path/'large-int.pidx'
    assert _probe()(value,2*1024*1024)>0
    if digit_limit:
        with pytest.raises(ValueError,match='integer string conversion'):
            codec.encode_indexed_module_file(str(path),module,max_bytes=2*1024*1024)
        assert not path.exists()
    else:
        codec.encode_indexed_module_file(str(path),module,max_bytes=2*1024*1024)
        assert path.stat().st_size<2*1024*1024
