"""Ordinary numeric annotations use actual object protocols at owning sinks."""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from tests.python.test_boolean_float_conversion import (
    functions,
)
from tests.python.test_floor_division_runtime_contract import (
    FloorMemory,
    RUNTIME,
)
from tests.python.test_shared_call_binding import (
    _emit as emit_binding,
)
from tests.python.test_slot_call_division_producers import (
    assert_division_publication,
)
from tests.python.test_slot_call_floor_division_producers import (
    assert_floor_division_publication,
)
from tests.python.test_slot_call_modulo_producers import (
    assert_modulo_publication,
)
from tests.python.test_slot_call_operand_roots import (
    _emit as emit_operand,
)


SOURCE = Path(__file__).parents[2] / 'pcc/runtime/py/py_obj_ops_mod.py'
CHECKS = {'%': assert_modulo_publication, '/': assert_division_publication, '//': assert_floor_division_publication}


@pytest.mark.parametrize('operator', ('%', '/', '//'))
@pytest.mark.parametrize('annotation', ('int', 'float', 'bool'))
@pytest.mark.parametrize('shape,count', [('left OP right', 1), ('left OP (right OP left)', 2)])
def test_ordinary_numeric_operand_projection_uses_runtime_kind(operator, annotation, shape, count):
    source = 'def probe(left: ' + annotation + ', right: ' + annotation + '):\n    return slot_operand_probe(' + shape.replace('OP', operator) + ')\n'
    CHECKS[operator](emit_operand(source), count)


@pytest.mark.parametrize('operator', ('%', '/', '//'))
@pytest.mark.parametrize('site', ('argument', 'default', 'return', 'later-error'))
def test_ordinary_numeric_consumer_owner_and_error_paths(operator, site):
    prefix = 'def take(*, value, later=None):\n    return value\ndef fail():\n    raise ValueError("later")\n'
    expression = 'left ' + operator + ' right'
    if site == 'default':
        body = '    def target(value=' + expression + '):\n        return value\n    return target()\n'
    elif site == 'return':
        body = '    return ' + expression + '\n'
    elif site == 'later-error':
        body = '    return take(value=' + expression + ', later=fail())\n'
    else:
        body = '    return take(value=' + expression + ')\n'
    CHECKS[operator](emit_binding(prefix + 'def probe(left: int, right: int) -> object:\n' + body))


def test_original_alignment_modulo_shape_keeps_full_integer_value():
    text = emit_binding('def padding(size: int):\n    chunks = []\n    chunks.append(b"\\0" * ((-size) % 8))\n    return chunks\n')
    assert_modulo_publication(text)


def test_original_arm64_shift_count_division_shape_has_an_owner():
    text = emit_binding('def encode(shift: int, imm16: int, rd: int):\n    return ((shift // 16) << 21) | (imm16 << 5) | rd\n')
    assert_floor_division_publication(text)


class ModuloMemory(FloorMemory):
    def __init__(self):
        super().__init__()
        def fmod(a, b):
            try:
                return math.fmod(a, b)
            except ValueError:
                return math.nan
        self.environment.update(
            fmod_c=fmod,
            f64_signbit=lambda value: int(math.copysign(1.0, value) < 0),
            pcc_numeric_float_from_bits=lambda bits: -0.0 if bits == -(1 << 63) else pytest.fail('unexpected bits'),
            py_err_occurred=lambda: int(self.pending is not None),
        )
        functions(RUNTIME / 'py_int_ops.py', {'py_int_mod'}, self.environment)
        functions(SOURCE, {'py_obj_mod'}, self.environment)

    def modulo(self, left, right):
        def box(value):
            if type(value) is bool:
                return self.true if value else self.false
            if type(value) is float:
                return self.float(value)
            if type(value) is int and abs(value) >= (1 << 62):
                return {'tag': self.abi['PY_TYPE_INT'], 'size': 48, 'number': value}
            return value
        return self.environment['py_obj_mod'](box(left), box(right))


@pytest.mark.parametrize('left,right', [
    (7, 3), (-7, 3), (7, -3), (True, 3), (False, True),
    ((1 << 210) + 9, 7), (-(1 << 210), 7),
    (7.0, 3), (-7.0, 3), (7.0, -3), (-7.0, -3),
    (True, 2.5), (2.5, True), (False, -2.5),
    (0.0, -2.0), (-0.0, 2.0), (6.0, -3.0), (-6.0, 3.0),
    (1.0, 0.1), (math.inf, 3.0), (-math.inf, 3.0),
    (1.0, math.inf), (-1.0, math.inf), (math.nan, 2.0), (2.0, math.nan),
])
def test_actual_modulo_body_matches_integer_and_float_kind_sign_and_zero(left, right):
    memory = ModuloMemory()
    result = memory.modulo(left, right)
    expected = left % right
    assert type(result) is type(expected)
    if type(expected) is float and math.isnan(expected):
        assert math.isnan(result)
    else:
        assert result == expected
        if type(expected) is float and expected == 0.0:
            assert math.copysign(1.0, result) == math.copysign(1.0, expected)
    assert memory.pending is None and not memory.live_scratch
    assert memory.events[-1][0] == 'new'


@pytest.mark.parametrize('zero', (0, False, 0.0, -0.0))
def test_float_modulo_zero_sets_the_actual_runtime_exception(zero):
    memory = ModuloMemory()
    assert memory.modulo(1.0, zero) is None
    assert memory.pending == (9, 'float modulo')
    assert not memory.results


@pytest.mark.parametrize('pending', (None, 'existing-exception'))
def test_float_result_allocation_failure_preserves_pending_error(pending):
    memory = ModuloMemory()
    def fail(value):
        memory.pending = pending
        return None
    memory.environment['py_float_from_f64'] = fail
    assert memory.modulo(7.0, 3.0) is None
    assert memory.pending == (pending if pending is not None else (19, 'float modulo result allocation failed'))


def test_modulo_reflection_and_callback_failure_use_the_existing_protocol():
    marker = object()
    class Left:
        def __mod__(self, other):
            return NotImplemented
    class Right:
        def __rmod__(self, other):
            return marker
    memory = ModuloMemory()
    assert memory.modulo(Left(), Right()) is marker
    assert [event for event in memory.events if event[0] == 'callback'] == [('callback', '__mod__'), ('callback', '__rmod__')]
    error = KeyError('operator')
    class Raising:
        def __mod__(self, other):
            raise error
    memory = ModuloMemory()
    assert memory.modulo(Raising(), Right()) is None
    assert memory.pending is error
