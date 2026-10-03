"""Execute the existing floor ABI, including its raw bigint scratch lifetime.

Managed method selection is modeled with stable external owners. These tests
do not qualify the separate legacy method-table replacement/lookup boundary.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from tests.python.test_boolean_float_conversion import (
    NumericMemory,
    functions,
)


RUNTIME = Path(__file__).parents[2] / 'pcc/runtime/py'


class FloorMemory(NumericMemory):
    def __init__(self):
        super().__init__()
        self.events = []
        self.live_scratch = set()
        self.results = []
        self.environment.update(
            floor_c=lambda value: float(math.floor(value)),
            _both_tagged=lambda a, b: type(a) is int and type(b) is int,
            py_int_from_i64=self.result,
            py_bigint_from_any=lambda value: self.allocate(self.integer(value)),
            malloc=lambda size: self.allocate(None),
            free=self.free,
            load_ptr=lambda slot, offset: slot['value'],
            py_bigint_divmod=self.divmod,
            _wrap_bigint=self.wrap,
            py_float_from_f64=self.result,
            _instance_class=lambda value: type(value) if self.user(value) else None,
            py_class_lookup=lambda cls, name: getattr(cls, name, None),
            _lookup_dunder=lambda value, name: getattr(type(value), name, None),
            py_obj_issubclass=lambda cls, base: int(issubclass(cls, base)),
            _call_binary=self.callback,
            py_decref=lambda value: self.events.append(('drop', value)),
            global_load_ptr=lambda name: {
                'py_True': self.true, 'py_False': self.false,
                'py_NotImplemented': NotImplemented,
            }[name],
        )
        functions(RUNTIME / 'py_int_ops.py', {'py_int_floordiv'}, self.environment)
        functions(RUNTIME / 'py_protocol_runtime.py', {'py_user_binop_dispatch', 'py_obj_floordiv'}, self.environment)
        self.environment['_type_of'] = self.type_of

    def user(self, value):
        return value is not None and type(value) not in (int, float, dict, str)

    def type_of(self, value):
        if self.user(value):
            return self.abi['PY_TYPE_INSTANCE']
        if type(value) is str:
            return -1
        return self.abi['PY_TYPE_INT'] if type(value) is int else value['tag']

    def allocate(self, value):
        raw = {'value': value}
        self.live_scratch.add(id(raw))
        self.events.append(('allocate', value))
        return raw

    def free(self, raw):
        if raw is not None:
            assert id(raw) in self.live_scratch
            self.live_scratch.remove(id(raw))
            self.events.append(('free', raw['value']))

    def divmod(self, a, b, q, r):
        if b['value'] == 0:
            return -1
        quotient, remainder = divmod(a['value'], b['value'])
        q['value'] = self.allocate(quotient)
        r['value'] = self.allocate(remainder)
        return 0

    def result(self, value):
        assert not self.live_scratch, 'managed result created before scratch disposal'
        self.events.append(('new', value))
        self.results.append(value)
        return value

    def wrap(self, raw):
        value = raw['value']
        self.free(raw)
        return self.result(value)

    def callback(self, method, left, right):
        self.events.append(('callback', method.__name__))
        try:
            return method(left, right)
        except Exception as error:
            self.pending = error
            return None

    def floor(self, left, right):
        def box(value):
            if type(value) is bool:
                return self.true if value else self.false
            if type(value) is float:
                return self.float(value)
            if type(value) is int and abs(value) >= (1 << 62):
                return {'tag': self.abi['PY_TYPE_INT'], 'size': 48, 'number': value}
            return value
        return self.environment['py_obj_floordiv'](box(left), box(right))


@pytest.mark.parametrize('left,right', [
    (7, 2), (-7, 2), (7, -2), (-7, -2),
    (True, 2), (False, True), (7, True),
    ((1 << 200) + 5, 3), (-(1 << 200), 3),
    (7.0, 2), (7, 2.0), (-7.0, 2), (True, 2.0),
])
def test_floor_numeric_routes_return_new_after_raw_scratch_cleanup(left, right):
    memory = FloorMemory()
    result = memory.floor(left, right)
    assert result == left // right
    assert type(result) is type(left // right)
    assert memory.pending is None
    assert memory.events[-1] == ('new', result)
    assert not memory.live_scratch


@pytest.mark.parametrize('zero', (0, False, 0.0))
def test_floor_zero_keeps_the_existing_integer_null_and_float_tls_abis(zero):
    memory = FloorMemory()
    assert memory.floor(7, zero) is None
    assert memory.pending == ((9, 'float floor division by zero') if type(zero) is float else None)
    assert not memory.live_scratch


@pytest.mark.parametrize('alias', ('left', 'right', 'new'))
def test_floor_custom_forward_and_reflected_return_contract(alias):
    events = []
    marker = object()
    class Left:
        def __floordiv__(self, other):
            events.append('left')
            return NotImplemented
    class Right:
        def __rfloordiv__(self, other):
            events.append('right')
            return {'left': other, 'right': self, 'new': marker}[alias]
    left, right = Left(), Right()
    memory = FloorMemory()
    assert memory.floor(left, right) is {'left': left, 'right': right, 'new': marker}[alias]
    assert events == ['left', 'right']
    assert memory.events == [('callback', '__floordiv__'), ('drop', NotImplemented), ('callback', '__rfloordiv__')]


def test_floor_reflected_subclass_priority_and_same_type_skip():
    class Left:
        def __floordiv__(self, other):
            return NotImplemented
        def __rfloordiv__(self, other):
            return 'base'
    class Right(Left):
        def __rfloordiv__(self, other):
            return 'derived'
    memory = FloorMemory()
    assert memory.floor(Left(), Right()) == 'derived'
    assert memory.events == [('callback', '__rfloordiv__')]
    memory = FloorMemory()
    assert memory.floor(Left(), Left()) is None
    assert memory.pending == (3, 'unsupported operand type(s) for //')
    assert memory.events == [('callback', '__floordiv__'), ('drop', NotImplemented)]


def test_floor_callback_error_stops_reflected_dispatch():
    class Left:
        def __floordiv__(self, other):
            raise KeyError('operator')
    class Right:
        def __rfloordiv__(self, other):
            pytest.fail('callback error must stop dispatch')
    memory = FloorMemory()
    assert memory.floor(Left(), Right()) is None
    assert isinstance(memory.pending, KeyError)
    assert memory.pending.args == ('operator',)


def test_floor_unsupported_operands_keep_type_error():
    memory = FloorMemory()
    assert memory.floor('text', 2) is None
    assert memory.pending == (3, 'unsupported operand type(s) for //')
