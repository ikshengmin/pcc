"""A compiler-private list cursor cannot escape or change public iter()."""
from __future__ import annotations

import re
import textwrap

import pytest

from tests.python.test_shared_call_binding import _emit, _function
from tests.python.owned_regression_support import (
    assert_owned_program, assert_reference_program, explicit_owned_runtime,
)


def test_dynamic_ordinary_for_uses_private_cursor():
    body = _function(_emit('def probe(source):\n    result = []\n    for item in source:\n        result.append(item)\n    return result\n'))
    assert '@py_obj_private_for_begin(' in body
    assert '@py_obj_private_for_next(' in body
    assert 'for.obj.private.cursor' in body
    assert not re.search(r'call [^\n]*@py_obj_iter\(', body)


def test_public_iter_and_next_unchanged():
    body = _function(_emit('def probe(source):\n    iterator = iter(source)\n    return next(iterator)\n'))
    assert '@py_obj_iter(' in body or '@py_obj_iter' in body
    assert '@py_obj_private_for_begin(' not in body
    assert '@py_obj_private_for_next(' not in body


def test_generator_for_keeps_persistent_iterator():
    text = _emit('def probe(source):\n    for item in source:\n        yield item\n')
    assert re.search(r'call [^\n]*@py_obj_iter\(', text)
    assert not re.search(r'call [^\n]*@py_obj_private_for_(begin|next)\(', text)


PROGRAM = textwrap.dedent('''\
    import gc
    events = []
    class Override(list):
        def __iter__(self):
            events.append('override')
            return iter([9, 8])
    class Broken:
        def __iter__(self):
            raise ValueError('iter failed')
    class Item:
        def __init__(self, n):
            self.n = n
        def __del__(self):
            events.append(self.n)
    def collect(source):
        out = []
        for item in source:
            out.append(item)
        return out
    def mutate(source):
        out = []
        for item in source:
            out.append(item)
            if item == 1:
                source.append(3)
            elif item == 2:
                source.append(4)
            elif item == 3:
                source.pop()
        return out
    def control(source):
        out = []
        for item in source:
            if item == 1:
                continue
            if item == 3:
                break
            out.append(item)
        return out
    def rebound(source):
        out = []
        for item in source:
            source = []
            gc.collect()
            out.append(item)
        return out
    def first(source):
        for item in source:
            return item
        return None
    def fails(source):
        try:
            for item in source:
                raise KeyError('body')
        except KeyError:
            return 'caught'
    def gen(source):
        for item in source:
            yield item
    def main():
        assert collect([1, 2, 3]) == [1, 2, 3]
        assert collect([]) == []
        assert mutate([1, 2]) == [1, 2, 3]
        assert control([1, 2, 3, 4]) == [2]
        assert rebound([4, 5]) == [4, 5]
        assert first([7, 8]) == 7 and first([]) is None
        assert fails([1]) == 'caught'
        assert collect((6, 5)) == [6, 5]
        assert collect(Override([1])) == [9, 8]
        assert events == ['override']
        try:
            collect(Broken())
        except ValueError as error:
            assert str(error) == 'iter failed'
        else:
            raise AssertionError('missing iter exception')
        it = iter([3, 4])
        assert next(it) == 3 and next(it) == 4
        try:
            next(it)
        except StopIteration:
            pass
        else:
            raise AssertionError('missing stop')
        assert collect(gen([1, 2])) == [1, 2]
        assert first([Item(11), Item(12)]).n == 11
        gc.collect()
        assert 11 in events and 12 in events
        print('PRIVATE_FOR_LIST_CURSOR_OK')
    main()
''')


def test_private_for_reference(tmp_path):
    assert_reference_program(PROGRAM, 'PRIVATE_FOR_LIST_CURSOR_OK\n', tmp_path)


@pytest.mark.integration
@pytest.mark.parametrize('python_program_compiler', ('pcc0', 'pcc1'), indirect=True)
def test_private_for_native(python_program_compiler, request,
                            explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params['python_program_compiler']
    assert_owned_program(PROGRAM, 'PRIVATE_FOR_LIST_CURSOR_OK\n', tmp_path,
                         python_program_compiler, mode, explicit_owned_runtime, capfd)


def test_private_cursor_retype_fallback_keeps_exact_sequence_position():
    """Model the rare type-change handoff without corrupting a builtin C layout."""
    import ast
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / 'pcc/runtime/py/py_iter.py'
    tree = ast.parse(path.read_text())
    fn = next(node for node in tree.body
              if isinstance(node, ast.FunctionDef) and node.name == 'py_obj_private_for_next')
    fn.decorator_list = []
    code = compile(ast.Module(body=[fn], type_ignores=[]), str(path), 'exec')
    owner, cursor, sequence, iterator, item = 100, 200, 300, 400, 500
    memory = {(owner, 0): sequence, (cursor, 0): 2}
    trace = []
    def create(value):
        assert value == sequence and memory[(owner, 0)] == sequence
        trace.append(('retain', value))
        return iterator
    def transfer(slot, value):
        assert slot == owner and value == iterator
        assert memory[(iterator, 24)] == 2
        trace.append(('transfer', value))
        memory[(slot, 0)] = value
    def next_value(value):
        assert value == iterator and memory[(cursor, 0)] == -1
        trace.append(('next', value))
        return item
    ns = {
        'i64': int, 'load_i64': lambda addr, offset: memory[(addr, offset)],
        'store_i64': lambda addr, offset, val: memory.__setitem__((addr, offset), val),
        'pcc_gc_load_ptr': lambda _base, slot: memory[(slot, 0)],
        'py_obj_iter_next': next_value, 'ptr_is_null': lambda value: value is None,
        'null': lambda: None, '_type_of': lambda _value: 7, 'PY_TYPE_LIST': 5,
        'global_addr': lambda name: name, 'load_i32': lambda _addr, _off: 1,
        '_iter_new': create, 'pcc_gc_store_root_take': transfer,
        '_require_result': lambda *_args: pytest.fail('unexpected failure'),
        'cstr': lambda value: value,
    }
    exec(code, ns)
    assert ns['py_obj_private_for_next'](owner, cursor) == item
    assert trace == [('retain', sequence), ('transfer', iterator), ('next', iterator)]
    assert ns['py_obj_private_for_next'](owner, cursor) == item
    assert trace[-1] == ('next', iterator)
