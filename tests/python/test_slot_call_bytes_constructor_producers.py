"""Bytes-family constructor owners publish before operand cleanup."""
from __future__ import annotations

from pathlib import Path
import textwrap

import pytest

from tests.python.owned_regression_support import (
    assert_owned_program,
    assert_reference_program,
    explicit_owned_runtime,
)
from tests.python.test_foreign_address_leases import _functions
from tests.python.test_shared_call_binding import _emit
from tests.python.test_slot_call_subscript_producers import _assert_immediate_publication


@pytest.mark.parametrize('expression,runtime', (
    ('bytes()', 'py_bytes_new'), ('bytearray()', 'py_bytearray_from_obj'),
    ('bytes(item)', 'py_bytes_from_obj'), ('bytearray(item)', 'py_bytearray_from_obj'),
    ('memoryview(item)', 'py_memoryview_new'), ('bytes(3)', 'py_bytes_from_obj'),
    ("bytes('text', 'utf-8')", 'py_str_utf8_encode'),
    ("bytearray('text', 'latin-1')", 'py_str_latin1_encode'),
))
@pytest.mark.parametrize('site', ('return', 'argument', 'default', 'attribute', 'later-error'))
def test_buffer_constructor_immediate_publication(expression, runtime, site):
    prefix = 'def take(*, value, later=None):\n    return value\ndef fail():\n    raise ValueError("later")\n'
    if site == 'return':
        body = '    return ' + expression + '\n'
    elif site == 'default':
        body = '    def target(value=' + expression + '):\n        return value\n    return target()\n'
    elif site == 'attribute':
        body = '    class Holder:\n        value = ' + expression + '\n    return Holder\n'
    else:
        body = '    return take(value=' + expression + (', later=fail()' if site == 'later-error' else '') + ')\n'
    text = _emit(prefix + 'def probe(item):\n' + body)
    _assert_immediate_publication(text, runtime)
    if expression.startswith('bytearray(') and "'text'" in expression:
        _assert_immediate_publication(text, 'py_bytearray_from_obj')


def test_bytes_alias_runtime_retains_separate_result_owner():
    class Value:
        refs = 2  # Original owner plus the caller's independent source slot.
    value = Value()
    def retain(pointer):
        assert pointer is value
        pointer.refs += 1
    namespace = {
        'ptr_is_null': lambda pointer: pointer is None,
        '_type_of': lambda pointer: 6,
        'PY_TYPE_BYTES': 6,
        'py_incref': retain,
    }
    _functions(Path(__file__).resolve().parents[2] / 'pcc/runtime/py/py_obj_stubs.py',
               {'py_bytes_from_obj'}, namespace)
    result = namespace['py_bytes_from_obj'](value)
    assert result is value and result.refs == 3
    value.refs -= 1  # Caller releases source after publishing the result.
    value.refs -= 1  # Original owner may also be disposed independently.
    assert result.refs == 1
    result.refs -= 1
    assert result.refs == 0


PROGRAM = textwrap.dedent('''\
    import gc
    events = []
    def source():
        events.append('source')
        return bytearray([65, 66])
    def later():
        gc.collect()
        events.append('later')
        return None
    def take(*, value, other=None):
        gc.collect()
        return value
    def returned(value):
        return bytes(value)
    def main():
        original = bytes([65, 66])
        alias = take(value=bytes(original), other=later())
        assert alias is original
        del original
        gc.collect()
        assert alias == b'AB'
        copied = take(value=bytearray(alias))
        assert bytes(copied) == alias and copied is not alias
        viewed = take(value=memoryview(source()), other=later())
        gc.collect()
        assert bytes(viewed) == b'AB'
        assert events == ['later', 'source', 'later']
        def default(value=bytes(copied)):
            gc.collect()
            return value
        class Holder:
            value = bytearray('text', 'utf-8')
        gc.collect()
        assert default() == b'AB'
        assert bytes(Holder.value) == b'text'
        assert returned(copied) == b'AB'
        assert bytes() == b'' and bytearray() == bytearray(b'')
        assert bytes(3) == b'\\x00\\x00\\x00'
        assert bytes('text', 'utf-8') == b'text'
        try:
            take(value=bytes(-1), other=later())
        except ValueError:
            pass
        else:
            raise AssertionError('negative byte count accepted')
        assert events == ['later', 'source', 'later']
        print('BUFFER_OWNERSHIP_OK')
    main()
''')


def test_buffer_native_program_reference(tmp_path):
    assert_reference_program(PROGRAM, 'BUFFER_OWNERSHIP_OK\n', tmp_path)


@pytest.mark.integration
@pytest.mark.parametrize('python_program_compiler', ('pcc0', 'pcc1'), indirect=True)
def test_buffer_native_five_gc(python_program_compiler, request,
                               explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params['python_program_compiler']
    assert_owned_program(PROGRAM, 'BUFFER_OWNERSHIP_OK\n', tmp_path,
                         python_program_compiler, mode, explicit_owned_runtime, capfd)
