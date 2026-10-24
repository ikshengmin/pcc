"""Dynamic reverse preserves keyword evaluation and sorted callback ordering."""
import re

import pytest

from tests.python.test_explicit_machine_arithmetic_boundaries import _run_native_all_gc
from tests.python.test_shared_call_binding import _emit, _function


@pytest.mark.parametrize('keywords', ('reverse=flag, key=rank', 'key=rank, reverse=flag', 'reverse=flag'))
def test_dynamic_reverse_uses_rooted_runtime_entry(keywords):
    body = _function(_emit('def probe(values, flag, rank):\n    return sorted(values, ' + keywords + ')\n'))
    assert '@py_obj_sorted_reverse_slots(' in body
    assert '@py_cpy_' not in body and 'strict.nolib.stub' not in body
    assert 'sorted.reverse' in body


@pytest.mark.parametrize('keywords,first,second', (
    ('reverse=make_flag(), key=make_key()', 'make_flag', 'make_key'),
    ('key=make_key(), reverse=make_flag()', 'make_key', 'make_flag'),
))
def test_dynamic_reverse_keeps_keyword_expression_order(keywords, first, second):
    body = _function(_emit(
        'def source():\n    return [2, 1]\n'
        'def make_flag():\n    return True\n'
        'def make_key():\n    return None\n'
        'def probe():\n    return sorted(source(), ' + keywords + ')\n'
    ))
    def call(name):
        return re.search(r'call[^\n]*@user_binding_' + name + r'\(', body).start()
    assert call('source') < call(first) < call(second) < body.index('@py_obj_sorted_reverse_slots(')


PROGRAM = r'''import gc
import weakref

events = []
references = []
destroyed = []
iteration_error = ValueError("iteration chosen")
truth_error = ValueError("truth chosen")

class Source:
    def __init__(self, fail=False):
        self.fail = fail
        self.values = [3, 1, 2]
    def __iter__(self):
        events.append("iter")
        gc.collect()
        if self.fail:
            raise iteration_error
        return iter(self.values)

class Flag:
    def __init__(self, value, fail=False, source=None):
        self.value = value
        self.fail = fail
        self.source = source
        references.append(weakref.ref(self))
    def __bool__(self):
        events.append("truth")
        gc.collect()
        if self.source is not None:
            self.source.values.clear()
        if self.fail:
            raise truth_error
        return self.value
    def __del__(self):
        destroyed.append(1)
        gc.collect()

class LengthFlag:
    def __len__(self):
        events.append("length")
        gc.collect()
        return 1

class InvalidFlag:
    def __bool__(self):
        return 3

def source_factory():
    events.append("source-factory")
    return Source()
def flag_factory():
    events.append("flag-factory")
    return Flag(True)
def rank(value):
    events.append("key" + str(value))
    gc.collect()
    return value
def key_factory():
    events.append("key-factory")
    return rank
def invoke(source, flag):
    return sorted(source, reverse=flag, key=rank)

def main():
    events.clear()
    result = sorted(source_factory(), reverse=flag_factory(), key=key_factory())
    assert result == [3, 2, 1]
    assert events == ["source-factory", "flag-factory", "key-factory", "iter", "truth", "key3", "key1", "key2"]
    events.clear()
    result = sorted(source_factory(), key=key_factory(), reverse=flag_factory())
    assert result == [3, 2, 1]
    assert events == ["source-factory", "key-factory", "flag-factory", "iter", "truth", "key3", "key1", "key2"]
    assert references[0]() is None and references[1]() is None
    assert len(destroyed) == 2
    events.clear()
    source = Source()
    result = invoke(source, Flag(True, source=source))
    assert result == [3, 2, 1] and source.values == []
    assert events == ["iter", "truth", "key3", "key1", "key2"]
    events.clear()
    try:
        invoke(Source(True), Flag(True, fail=True))
    except ValueError as error:
        assert error is iteration_error
    else:
        raise AssertionError("iteration failure lost")
    assert events == ["iter"]
    events.clear()
    try:
        invoke(Source(), Flag(True, fail=True))
    except ValueError as error:
        assert error is truth_error
    else:
        raise AssertionError("truth failure lost")
    assert events == ["iter", "truth"]
    events.clear()
    assert invoke(Source(), LengthFlag()) == [3, 2, 1]
    assert events == ["iter", "length", "key3", "key1", "key2"]
    try:
        invoke(Source(), InvalidFlag())
    except TypeError:
        pass
    else:
        raise AssertionError("invalid bool result accepted")
    for flag in (False, 0, None, "", []):
        assert invoke([3, 1, 2], flag) == [1, 2, 3]
    for flag in (True, 2, "yes", [1]):
        assert invoke([3, 1, 2], flag) == [3, 2, 1]
    events.clear()
    assert invoke([], Flag(True)) == [] and events == ["truth"]
    print("DYNAMIC_REVERSE_OK")

main()
'''


@pytest.mark.integration
def test_dynamic_reverse_callbacks_native_all_collectors(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
):
    _run_native_all_gc(tmp_path, monkeypatch, pcc_runtime_archive,
                       python_program_compiler, PROGRAM, "DYNAMIC_REVERSE_OK")
