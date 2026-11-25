"""Host-only bounded-capture contracts; included in the default pytest gate."""
import ast
import copy
import inspect
import json
import random
from pathlib import Path

import pytest
from pcc.backend import self_backend_indexed_codec as codec


def _probe(*, old=False, cold_only=False):
    """Observe the production walk; old=True reconstructs the old reserve."""
    node = ast.parse(inspect.getsource(codec._bounded_capture_budget)).body[0]
    if old:
        cold = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == 'cold')
        first = next(n for n in cold.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call))
        assert isinstance(first.value.func, ast.Name) and first.value.func.id == 'charge'
        first.value.args = [ast.Constant(32)]
    if cold_only:
        end = next(i for i,n in enumerate(node.body) if isinstance(n, ast.FunctionDef) and n.name == 'cold')
        node.body = node.body[:end+1] + ast.parse('cold(module)').body
    node.body += ast.parse('return budget[0]').body
    tree = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    namespace = dict(codec.__dict__)
    exec(compile(tree, '<budget-observation>', 'exec'), namespace)
    return namespace['_bounded_capture_budget']


def test_cold_string_container_bound_and_thresholds():
    probe = _probe(cold_only=True)
    rng = random.Random(44097019829)
    strings = ['', '"\\', '\b\f\n\r\t', '\x00\x1f\x7f', '\u0080\uffff', '\ud800\udfff', '\U00010000\U0010ffff']
    strings += [chr(i) for i in range(128)]
    strings += [''.join(chr(rng.randrange(0x110000)) for _ in range(20)) for _ in range(50)]
    leaves = strings + [None, True, False, 0, -1, 2**256, -2**256, 1.0, float('inf'), float('nan'), codec.TypeDesc('int', width=64)]
    def gen(depth):
        if not depth or rng.randrange(3)==0:
            return rng.choice(leaves)
        count = rng.randrange(5)
        kind = rng.randrange(3)
        if kind == 0:
            return [gen(depth-1) for _ in range(count)]
        if kind == 1:
            return tuple(gen(depth-1) for _ in range(count))
        return {rng.choice(strings):gen(depth-1) for _ in range(count)}
    values = leaves + [gen(5) for _ in range(1000)]
    values += [['']*10000, {str(i):'' for i in range(10000)}]
    for value in values:
        bound = probe(value, 1 << 30)
        actual = len(json.dumps(codec._cold_to_wire(value), sort_keys=True).encode())
        assert bound >= actual + 8
        assert probe(value, bound) == bound
        with pytest.raises(ValueError, match='size limit'):
            probe(value, bound-1)


def test_exact_string_reserve_and_subclass_old_order():
    candidate, old = _probe(cold_only=True), _probe(old=True, cold_only=True)
    assert candidate('abc', 100) == 13
    events = []
    class Observable(str):
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
            events.append(('count', value))
            return super().count(value)
    value = Observable('abc')
    for limit in (31, 32, 36, 37, 100):
        results = []
        for run in (candidate, old):
            events.clear()
            try:
                result = run(value, limit)
            except ValueError as error:
                result = (type(error), str(error))
            results.append((result, events.copy()))
        assert results[0] == results[1]


def test_subclass_exception_order_is_preserved():
    class Explosive(str):
        def __len__(self):
            raise RuntimeError('observed len')
    for run in (_probe(cold_only=True), _probe(old=True, cold_only=True)):
        with pytest.raises(ValueError, match='size limit'):
            run(Explosive('x'), 31)
        with pytest.raises(RuntimeError, match='observed len'):
            run(Explosive('x'), 32)
    for value in (object(), set(), b'bytes'):
        with pytest.raises(ValueError, match='unsupported diagnostic cold payload'):
            _probe(cold_only=True)(value, 1000)


def test_unchanged_two_mib_gate_recovers_real_wire_headroom(tmp_path):
    limit = 2 * 1024 * 1024
    globals_ = tuple(codec.GlobalDef('g', codec.TypeDesc('int', width=8), '0', False, False) for _ in range(6000))
    module = codec.ParsedModule('', globals_, ())
    assert _probe(old=True)(module, 1 << 30) > limit
    assert _probe()(module, limit) <= limit
    bounded, ordinary = tmp_path/'bounded.pidx', tmp_path/'ordinary.pidx'
    codec.encode_indexed_module_file(str(bounded), module, max_bytes=limit)
    codec.encode_indexed_module_file(str(ordinary), module)
    assert bounded.read_bytes() == ordinary.read_bytes()
    assert bounded.stat().st_size <= limit


def test_oversize_rejected_before_materialization(tmp_path, monkeypatch):
    limit = 2 * 1024 * 1024
    module = codec.ParsedModule('x'*limit, (), ())
    def unexpected(*args, **kwargs):
        pytest.fail('preflight rejection must precede JSON materialization')
    monkeypatch.setattr(codec.json, 'dumps', unexpected)
    path = tmp_path/'oversize.pidx'
    diagnostic = {}
    with pytest.raises(ValueError, match='size limit'):
        codec.encode_indexed_module_file(str(path), module, max_bytes=limit, diagnostic=diagnostic)
    assert diagnostic['stage']=='codec-preflight'
    assert not path.exists()


def test_mutating_container_still_rejected_by_actual_wire_gate(tmp_path):
    # Existing API accepts container subclasses. Preflight is not a snapshot:
    # mutation after traversal is still guarded by the actual wire-size check.
    class Growing(list):
        calls = 0
        def __iter__(self):
            self.calls += 1
            return iter([''] if self.calls == 1 else ['x'*(2*1024*1024)])
    attrs = Growing()
    g = codec.GlobalDef('g', codec.TypeDesc('int', width=8), '0', False, False, trailing_attributes=attrs)
    module = codec.ParsedModule('', (g,), ())
    diagnostic = {}
    path = tmp_path/'mutating.pidx'
    with pytest.raises(ValueError, match='size limit'):
        codec.encode_indexed_module_file(str(path), module, max_bytes=2*1024*1024, diagnostic=diagnostic)
    assert diagnostic['stage']=='codec-wire-size'
    assert not path.exists()


def test_preflight_failure_poison_is_retained_without_completion(tmp_path, monkeypatch):
    from pcc.backend import indexed_input_receipts as receipts
    token = 'a'*32
    monkeypatch.setenv('PCC_DEBUG_SELF_IR_DUMP_DIR', str(tmp_path))
    monkeypatch.setenv('PCC_DEBUG_SELF_IR_INVOCATION', token)
    context = {'directory':str(tmp_path), 'invocation':token, 'index':0,
               'module':'oversized', 'attempt':'b'*32}
    # Compact capture preflights the decoded work bound; a string at the 2 MiB
    # on-disk limit now passes it and fails later, at codec-wire-size.
    module = codec.ParsedModule('x'*(codec._COMPACT_DECODED_LIMIT + 1), (), ())
    assert receipts.begin_capture(module, context, ()) is None
    marker = tmp_path/'indexed-capture-failed'
    initial = marker.read_bytes()
    failure = json.loads(initial)
    assert failure['stage']=='codec-preflight'
    assert failure['reason']=='conservative_limit'
    assert not list(tmp_path.glob('*.pidx'))
    assert not (tmp_path/'link.json').exists()
    assert receipts.begin_capture(module, context, ()) is None
    assert marker.read_bytes()==initial
