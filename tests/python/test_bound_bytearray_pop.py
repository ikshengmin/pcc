"""bytearray.pop through an ordinary bound attribute, not a receiver-type guess."""

import operator

import pytest

from tests.python.owned_regression_support import (
    assert_owned_program, assert_reference_program, explicit_owned_runtime,
)
from test_bound_builtin_method_owners import BoundMethodMemory, _call
from test_percent_format_owners import ABI


def test_public_bytearray_pop_registration_and_body():
    # Execute the actual runtime registry, arity and bound call body. Only its
    # two leaf protocols (checked integer conversion and bytearray mutation)
    # use host operations here; the native comparator exercises both kernels.
    for args, expected in (((), 68), ((1,), 66), ((-2,), 67), ((None,), TypeError),
                           ((10**100,), OverflowError)):
        model = BoundMethodMemory(relocate=False)
        ns = model.env
        ns.update(cstr=lambda text: text, strcmp=lambda a, b: (a > b) - (a < b),
                  store_i32=model.store)
        operation = ns["_bound_method_operation"](ABI["PY_TYPE_BYTEARRAY"], "pop")
        assert operation != 0, "bytearray.pop missing from builtin attribute lookup"
        assert ns["_bound_method_operation"](ABI["PY_TYPE_BYTES"], "pop") == 0
        assert ns["_bound_method_name"](operation) == "pop"
        assert ns["_bound_method_arity"](operation) % 100 == 1
        v = bytearray(b"ABCD")
        receiver = model.new(ABI["PY_TYPE_BYTEARRAY"], v)
        converted = []
        def index_slots(src, dst):
            value = model.object(model.load(src))["data"]
            converted.append(value)
            try:
                value = operator.index(value)
            except TypeError:
                model.pending = "index-type-error"
                return -1
            model.store(dst, 0, model.new(ABI["PY_TYPE_INT"], value))
            return 0
        def narrow(value, flag):
            result = model.object(value)["data"]
            model.store(flag, 0, int(not -(1 << 63) <= result < (1 << 63)))
            return result
        def pop(value, index):
            data = model.object(value)["data"]
            result = data.pop() if index is None else data.pop(model.object(index)["data"])
            return model.new(ABI["PY_TYPE_INT"], result)
        ns.update(py_obj_index_slots=index_slots, py_int_to_i64=narrow, py_bytearray_pop=pop)
        operands = tuple(model.new(ABI["PY_TYPE_INSTANCE"], a) for a in args)
        result, _ = _call(model, receiver, operation, *operands)
        assert converted == list(args)
        if expected in (TypeError, OverflowError):
            assert result is None and v == b"ABCD"
            assert model.pending == ("index-type-error" if expected is TypeError else "error-15")
        else:
            assert model.pending is None and model.object(result)["data"] == expected
            baseline = bytearray(b"ABCD")
            baseline.pop(*args)
            assert v == baseline


PROGRAM = """import gc

class Box:
    def __init__(self, target):
        self.target = target

    def last(self):
        return self.target.pop()

    def at(self, index):
        return self.target.pop(index)

    def method(self):
        return self.target.pop

class Index:
    def __init__(self, events):
        self.events = events

    def __index__(self):
        self.events.append(1)
        return -2

class Reject:
    def __init__(self, reason):
        self.reason = reason

    def __index__(self):
        raise self.reason

def main():
    direct = bytearray(b'ABCD')
    assert direct.pop() == 68 and direct.pop(0) == 65
    original = bytearray(b'ABCD')
    box = Box(original)
    assert box.last() == 68 and box.at(0) == 65 and bytes(original) == bytes(direct)
    method = box.method()
    gc.collect()
    calls = []
    assert method(Index(calls)) == 66 and calls == [1]
    assert bytes(original) == b'C'
    reason = ValueError('index callback')
    try:
        method(Reject(reason))
    except ValueError as error:
        assert error is reason
    else:
        raise AssertionError('index callback exception lost')
    for invalid in (None, '0'):
        try:
            method(invalid)
        except TypeError:
            pass
        else:
            raise AssertionError('invalid index accepted')
    try:
        method(10**100)
    except OverflowError:
        pass
    else:
        raise AssertionError('oversized index accepted')
    try:
        method(1, 2)
    except TypeError:
        pass
    else:
        raise AssertionError('two indices accepted')
    try:
        method(index=0)
    except TypeError:
        pass
    else:
        raise AssertionError('keyword accepted')
    assert bytes(original) == b'C'
    try:
        method(3)
    except IndexError:
        pass
    else:
        raise AssertionError('out-of-bounds index accepted')
    assert method(False) == 67
    try:
        box.last()
    except IndexError:
        pass
    else:
        raise AssertionError('empty bytearray accepted')
    print(bytes(direct), bytes(original), calls)
main()
"""
EXPECTED = "b'BC' b'' [1]\n"


def test_bound_bytearray_pop_public_reference(tmp_path):
    assert_reference_program(PROGRAM, EXPECTED, tmp_path)


@pytest.mark.integration
def test_bound_bytearray_pop_public_native(python_program_compiler, request,
                                          explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(PROGRAM, EXPECTED, tmp_path, python_program_compiler, mode,
                         explicit_owned_runtime, capfd)
