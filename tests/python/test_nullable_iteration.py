"""Normal iterator exhaustion does not require an exception allocation."""
from pathlib import Path

import pytest

from tests.python.test_explicit_machine_arithmetic_boundaries import _run_native_all_gc
from tests.python.test_foreign_address_leases import _functions
from tests.python.test_shared_call_binding import _emit, _function
from pcc.runtime.py import py_abi_constants as abi


@pytest.mark.parametrize('statement', (
    'return next(items, fallback)',
    'for item in items:\n        pass\n    return fallback',
    'first, second = items\n    return first',
    'return [item for item in items]',
))
def test_consuming_exhaustion_uses_nullable_next(statement):
    body = _function(_emit('def probe(items, fallback):\n    ' + statement + '\n'))
    assert '@py_obj_iter_next(' in body
    assert '@py_exc_matches(' in body
    assert 'strict.nolib.stub' not in body


def test_explicit_next_keeps_the_raising_entry():
    body = _function(_emit('def probe(items):\n    return next(items)\n'))
    assert '@py_obj_next(' in body
    assert '@py_obj_iter_next(' not in body


@pytest.mark.parametrize('tag', (abi.PY_TYPE_LIST, abi.PY_TYPE_TUPLE, abi.PY_TYPE_STR,
                                abi.PY_TYPE_BYTES, abi.PY_TYPE_BYTEARRAY, abi.PY_TYPE_MEMORYVIEW))
def test_native_sequence_exhaustion_allocates_only_for_explicit_next(tag):
    iterator, sequence = 1000, 2000
    memory = {(iterator, 24): 2, (iterator, 16): sequence}
    pending = [None]
    allocations, finished = [], []
    def prepare(slot, value, backend):
        memory[slot, 0] = value
        return None
    def new_exception(kind, message):
        assert finished
        allocations.append(kind)
        return ('exception', kind)
    ns = {name: value for name, value in vars(abi).items() if name.startswith('PY_TYPE_')}
    ns.update(
        null=lambda: None, ptr_is_null=lambda value: value is None,
        is_tagged_int=lambda value: False,
        load_i32=lambda base, offset: abi.PY_TYPE_ITER,
        load_i64=lambda base, offset: memory[base, offset],
        ptr_add=lambda base, offset: (base, offset),
        pcc_gc_load_ptr=lambda owner, slot: memory[slot],
        _type_of=lambda value: abi.PY_TYPE_ITER if value == iterator else tag,
        pcc_capi_is_cext_type_tag=lambda value: 0,
        pcc_gc_backend=lambda: 0, stack_alloc=lambda size: 3000,
        _iter_prepare_moving_root=prepare,
        _iter_moving_root_failed=lambda *args: 0,
        _iter_reload_moving_root=lambda slot, handle: memory[slot, 0],
        _iter_finish_moving_root=lambda handle: finished.append(True),
        py_list_len=lambda value: 2, py_tuple_len=lambda value: 2,
        py_str_byte_len=lambda value: 2, py_bytes_len=lambda value: 2,
        py_exc_new=new_exception, py_raise_owned=lambda value: pending.__setitem__(0, value),
        py_clear_exception=lambda: pending.__setitem__(0, None),
        _TUPLE_ITER_START=-4,
    )
    _functions(Path(__file__).resolve().parents[2] / 'pcc/runtime/py/py_iter.py',
               {'py_obj_next', 'py_obj_iter_next', '_obj_next_body', '_iter_exhausted'}, ns)
    assert ns['py_obj_iter_next'](iterator) is None
    assert pending[0] is None and allocations == []
    assert ns['py_obj_next'](iterator) is None
    assert pending[0] == ('exception', 8) and allocations == [8]


PROGRAM = r'''import gc
import weakref

events = []
references = []
selected = ValueError("selected iterator error")
stopped = StopIteration("selected stop")
sentinel = object()

class Cursor:
    def __init__(self, values, fail=False):
        self.values = values
        self.index = 0
        self.fail = fail
    def __iter__(self):
        return self
    def __next__(self):
        gc.collect()
        if self.index == len(self.values):
            if self.fail:
                raise selected
            raise stopped
        value = self.values[self.index]
        self.index += 1
        return value

class Sequence:
    def __getitem__(self, index):
        gc.collect()
        if index == 3:
            raise IndexError("done")
        return index

class Item:
    def __init__(self):
        references.append(weakref.ref(self))
    def __del__(self):
        events.append("item destroyed")
        gc.collect()

def consume(source):
    values = []
    for value in source:
        gc.collect()
        values.append(value)
    return values

def consume_default(source):
    iterator = iter(source)
    values = []
    while True:
        value = next(iterator, sentinel)
        if value is sentinel:
            break
        values.append(value)
        gc.collect()
    return values

def producer():
    yield None
    yield 17
    return 91

def released_loop():
    for value in [Item()]:
        gc.collect()
        assert references[-1]() is value

def main():
    for source in ([], [None, False, 0, "", 17], (None, 2, 3), "aé😀", b"abc", bytearray(b"abc"), memoryview(b"abc")):
        assert consume(source) == list(source)
        assert consume_default(source) == list(source)
    assert consume(Sequence()) == [0, 1, 2]
    assert consume_default(Sequence()) == [0, 1, 2]
    iterator = Cursor([None, 4])
    assert consume(iterator) == [None, 4]
    try:
        next(iterator)
    except StopIteration as error:
        assert error is stopped
    else:
        raise AssertionError("explicit next lost StopIteration")
    for default_mode in (False, True):
        try:
            if default_mode:
                consume_default(Cursor([5], True))
            else:
                consume(Cursor([5], True))
        except ValueError as error:
            assert error is selected
        else:
            raise AssertionError("iterator error swallowed")
    assert consume(producer()) == [None, 17]
    assert consume_default(producer()) == [None, 17]
    generator = producer()
    assert next(generator) is None and next(generator) == 17
    try:
        next(generator)
    except StopIteration as error:
        assert error.value == 91
    else:
        raise AssertionError("generator return lost")
    items = [1, 2, 3]
    def advance():
        gc.collect()
        return items.pop(0)
    assert consume(iter(advance, 3)) == [1, 2]
    items[:] = [1, 2, 3]
    assert consume_default(iter(advance, 3)) == [1, 2]
    assert next(iter([]), sentinel) is sentinel
    try:
        next(iter([]))
    except StopIteration:
        pass
    else:
        raise AssertionError("native iterator did not raise")
    first, second = [11, 12]
    assert first == 11 and second == 12
    try:
        first, second = iter([11])
    except ValueError:
        pass
    else:
        raise AssertionError("short unpack accepted")
    try:
        first, second = iter([11, 12, 13])
    except ValueError:
        pass
    else:
        raise AssertionError("long unpack accepted")
    released_loop()
    gc.collect()
    assert references[-1]() is None and events == ["item destroyed"]
    print("NULLABLE_ITERATION_OK")

main()
'''


@pytest.mark.integration
def test_nullable_iteration_native_all_collectors(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
):
    _run_native_all_gc(tmp_path, monkeypatch, pcc_runtime_archive,
                       python_program_compiler, PROGRAM, "NULLABLE_ITERATION_OK")


@pytest.mark.integration
def test_nullable_file_iteration_native_all_collectors(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
):
    data = tmp_path / 'iteration.txt'
    data.write_text('hello\né\n')
    program = '''def main():
    with open(PATH) as stream:
        assert [line for line in stream] == ["hello\\n", "é\\n"]
        assert next(stream, 31) == 31
        try:
            next(stream)
        except StopIteration:
            pass
        else:
            raise AssertionError("explicit file next did not raise")
    print("NULLABLE_FILE_OK")
main()
'''.replace('PATH', repr(str(data)))
    _run_native_all_gc(tmp_path, monkeypatch, pcc_runtime_archive,
                       python_program_compiler, program, "NULLABLE_FILE_OK")
