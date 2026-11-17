"""Bound fixed function syntax without hiding arbitrary metadata integers."""
import ast
import inspect
import json
import sys

import pytest
from pcc.backend import self_backend_indexed_codec as codec
from pcc.backend.self_backend_parse import parse_self_backend_module


def probe(previous=False):
    node = ast.parse(inspect.getsource(codec._bounded_capture_budget)).body[0]
    loop = next(n for n in node.body if isinstance(n, ast.For)
                and ast.unparse(n.iter) == 'module.functions')
    branch = next(n for n in loop.body if isinstance(n, ast.If)
                  and 'type(function)' in ast.unparse(n.test))
    if previous:
        loop.body[loop.body.index(branch)] = ast.parse('charge(4096)').body[0]
    node.body += ast.parse('return budget[0]').body
    ns = dict(codec.__dict__)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 '<function-reserve-probe>', 'exec'), ns)
    return ns[node.name]


def module():
    value = parse_self_backend_module('target triple = "x86_64-unknown-linux-gnu"\ndefine void @run() {\nentry:\n ret void\n}\n')
    codec.get_indexed_function_kernel(value.functions[0])
    return value


def test_fixed_syntax_bound_tracks_actual_serializer():
    m = module(); f = m.functions[0]; k = f.indexed_kernel
    wire = codec._function_to_wire(f, k)
    # Replace independently-charged payloads with null (overcounting four bytes).
    for key in ('name', 'ret_type', 'args', 'block_names', 'value_names',
                'instruction_arithmetic_flags', 'cold_instruction_data',
                'call_texts', 'types', 'first_duplicate_definition_value_id',
                'instruction_use_total'):
        wire[key] = None
    wire['is_global'] = False; wire['is_vararg'] = False
    wire['definition_linkage'] = 'external_odr'
    wire['arenas'] = [[name, sys.maxsize] for name, _, _ in codec._ARENA_FIELDS]
    assert len(codec._ARENA_FIELDS) == 22
    # Includes one function's outer array separator as well.
    assert len(json.dumps(wire, sort_keys=True).encode()) + 2 <= 1536


def test_exact_metadata_bound_and_actual_wire(tmp_path):
    run = probe(); old = probe(True)
    for first, total in [(-1, 0), (-(2**63), 2**64-1), (10**3000, -(10**3000))]:
        m = module(); k = m.functions[0].indexed_kernel
        k.first_duplicate_definition_value_id = first
        k.instruction_use_total = total
        bound = run(m, 1 << 20)
        path = tmp_path / 'value.pidx'
        codec.encode_indexed_module_file(str(path), m, max_bytes=bound)
        payload = path.read_bytes()
        assert len(payload) <= bound
        codec.encode_indexed_module_file(str(path), m)
        assert path.read_bytes() == payload
        with pytest.raises(ValueError, match='size limit'):
            run(m, bound-1)
        if first == -1:
            assert old(m, 1 << 20) - bound == 2557
        else:
            assert bound >= len(str(first)) + len(str(total))


def test_subclass_no_added_metadata_access():
    events = []
    class Observed(codec.ParsedFunction):
        def __getattribute__(self, name):
            if name in ('is_global', 'is_vararg'):
                events.append(name)
            return super().__getattribute__(name)
    m = module(); m.functions[0].__class__ = Observed
    observed = []
    for run in (probe(True), probe()):
        events.clear(); value = run(m, 1 << 20)
        observed.append((value, list(events)))
    assert observed[0] == observed[1]
    assert observed[0][1] == []


def test_nonexact_metadata_retains_previous_reserve():
    class IntSubclass(int):
        def bit_length(self):
            raise AssertionError('must not add subclass callbacks')
    for attribute, value in [('instruction_use_total', IntSubclass(5)),
                              ('first_duplicate_definition_value_id', None)]:
        m = module(); setattr(m.functions[0].indexed_kernel, attribute, value)
        assert probe()(m, 1 << 20) == probe(True)(m, 1 << 20)
    m = module(); m.functions[0].is_vararg = 1
    assert probe()(m, 1 << 20) == probe(True)(m, 1 << 20)


def test_large_metadata_preflight_and_no_file(tmp_path):
    m = module(); m.functions[0].indexed_kernel.instruction_use_total = 10**3000
    bound = probe()(m, 1 << 20)
    path = tmp_path / 'too-small.pidx'
    with pytest.raises(ValueError, match='size limit'):
        codec.encode_indexed_module_file(str(path), m, max_bytes=bound-1)
    assert not path.exists()


def test_fallback_metadata_not_retained_across_cold_callbacks():
    outcomes = []
    for run in (probe(True), probe()):
        events = []
        m = module(); f = m.functions[0]; k = f.indexed_kernel
        class Metadata:
            def __del__(self):
                events.append('metadata-destroyed')
        class Name(str):
            def __len__(self):
                events.append('name-before')
                k.instruction_use_total = 0
                events.append('name-after')
                return super().__len__()
        k.instruction_use_total = Metadata()
        f.name = Name(f.name)
        result = run(m, 1 << 20)
        outcomes.append((result, list(events)))
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][1] == ['name-before', 'metadata-destroyed', 'name-after']


def test_linkage_subclass_keeps_old_reserve_without_extra_callbacks():
    events = []
    class Linkage(str):
        def __eq__(self, other):
            events.append(other)
            return other == 'external_odr'
    m = module(); m.functions[0].definition_linkage = Linkage('x'*10000)
    assert probe()(m, 1 << 20) == probe(True)(m, 1 << 20)
    assert events == []
