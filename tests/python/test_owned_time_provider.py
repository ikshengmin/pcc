"""Reference controls for the semantic pieces of the owned time provider.

Extern publication/GC is covered separately by test_structseq_slot_owners;
these tests do not substitute CPython records for a native acceptance run.
"""
from __future__ import annotations
import ast
import math
import operator
from pathlib import Path
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
NAMES = ('tm_year', 'tm_mon', 'tm_mday', 'tm_hour', 'tm_min', 'tm_sec',
         'tm_wday', 'tm_yday', 'tm_isdst', 'tm_zone', 'tm_gmtoff')


def functions(path, names, namespace):
    body = [node for node in ast.parse(path.read_text()).body
            if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


@pytest.fixture
def constructor():
    ns = functions(ROOT / 'pcc/stdlib/_structseq.py', ('new',),
                   {'_new': lambda cls, visible, hidden: (visible, hidden),
                    '_storage': lambda value: value, '_dict_keys': dict.keys, '_dict_get_default': dict.get})
    return lambda sequence, mapping: ns['new'](object, sequence, mapping, NAMES, 9, 'time.struct_time')


def outcome(fn):
    try:
        return ('value', fn())
    except Exception as error:
        return ('error', type(error), str(error))


@pytest.mark.parametrize('length', (0, 8, 9, 10, 11, 12))
@pytest.mark.parametrize('mapping', ({}, {'tm_zone': 'X'}, {'tm_gmtoff': 19800},
                                   {'tm_zone': 'X', 'tm_gmtoff': None},
                                   {'tm_year': 2000}, {'extra': 1}, None, []))
def test_structseq_constructor_shape_and_optional_fields_match_reference(constructor, length, mapping):
    def reference():
        value = time.struct_time(tuple(range(length)), mapping)
        return tuple(value), (value.tm_zone, value.tm_gmtoff)
    assert outcome(lambda: constructor(tuple(range(length)), mapping)) == outcome(reference)


def test_structseq_mapping_subclasses_do_not_override_storage_reads(constructor):
    class Mapping(dict):
        def __iter__(self):
            raise AssertionError('iter override')
        def keys(self):
            raise AssertionError('keys override')
        def get(self, *args):
            raise AssertionError('get override')
    mapping = Mapping(tm_zone='X', tm_gmtoff=3600)
    value = time.struct_time(range(9), mapping)
    assert constructor(range(9), mapping) == (tuple(value), (value.tm_zone, value.tm_gmtoff))


def test_structseq_iterator_error_order_matches_reference(constructor):
    class NonIterable:
        def __iter__(self):
            raise TypeError('body TypeError')
    class BrokenIterator:
        def __iter__(self):
            return self
        def __next__(self):
            raise RuntimeError('next body')
    for sequence in (1, NonIterable(), BrokenIterator()):
        assert outcome(lambda: constructor(sequence, [])) == outcome(lambda: time.struct_time(sequence, []))


@pytest.fixture
def timestamp():
    ns = functions(ROOT / 'pcc/stdlib/time.py', ('_timestamp_seconds',),
                   {'time': time.time, '_index_value': operator.index,
                    '_float_value': float.__float__})
    return ns['_timestamp_seconds']


@pytest.mark.parametrize('value', (False, True, 0, -1, -0.25, -1.25, 0.25, 1.25,
                                  2**53+1, float('nan'), float('inf'), -float('inf'),
                                  2**63, -2**63-1, 10**100, '1', b'1', object()))
def test_timestamp_argument_and_floor_semantics_match_reference(timestamp, value):
    def expected():
        record = time.gmtime(value)
        # Huge representable time_t values can exceed struct-tm calendar range;
        # these are a later platform error, outside this conversion stage.
        return record
    actual = outcome(lambda: timestamp(value))
    reference = outcome(expected)
    if reference[0] == 'error' and reference[1] is not OSError:
        assert actual == reference
    else:
        assert actual == ('value', math.floor(value))


def test_timestamp_uses_builtin_payloads_and_custom_numeric_protocols(timestamp):
    class Integer(int):
        def __int__(self):
            return 0
        def __index__(self):
            return 1
        def __lt__(self, value):
            raise AssertionError('int comparison override')
    class Float(float):
        def __float__(self):
            return 0.0
        def __lt__(self, value):
            raise AssertionError('float comparison override')
    class Index:
        def __index__(self):
            return -1
    class Real:
        def __float__(self):
            return -1.25
    for value, expected in ((Integer(42),42), (Float(42.25),42), (Index(),-1), (Real(),-2)):
        assert timestamp(value) == expected
        assert time.gmtime(value) == time.gmtime(expected)


@pytest.mark.parametrize('returned', (42, 2**53+1, 2**63-1))
def test_timestamp_index_protocol_precedes_float_without_losing_precision(timestamp, returned):
    class Both:
        def __index__(self):
            return returned
        def __float__(self):
            raise AssertionError('index protocol must take precedence')
    value = Both()
    assert timestamp(value) == returned
    try:
        expected = time.gmtime(returned)
    except OSError as reference:
        with pytest.raises(OSError) as actual:
            time.gmtime(value)
        assert actual.value.errno == reference.errno
    else:
        assert time.gmtime(value) == expected


def test_timestamp_float_subclass_can_supply_index_protocol(timestamp):
    class IndexedFloat(float):
        def __index__(self):
            return 18
    value = IndexedFloat(42.0)
    assert timestamp(value) == 18
    assert time.gmtime(value) == time.gmtime(18)
