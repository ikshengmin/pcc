"""Discard final owned results at their rooted publication boundary."""
import pytest

from tests.python.test_explicit_machine_arithmetic_boundaries import _run_native_all_gc
from tests.python.test_slot_call_operand_roots import _calls, _emit, _probe_function


@pytest.mark.parametrize("source, operation", [
    ("def probe(target, item):\n    target.append(item)\n", "py_list_append"),
    ("def probe(fn):\n    fn()\n", "py_obj_call_slots"),
    ("def probe(value: str):\n    value.replace('a', 'b')\n", "py_str_replace"),
    ("def probe(target):\n    target.pop()\n", "py_list_pop"),
    ("def probe(target, value):\n    target.extend(value)\n", "py_list_extend"),
])
def test_discarded_result_retires_root_without_raw_handoff(source, operation):
    body = _probe_function(_emit(source))
    assert _calls(body, operation)
    assert _calls(body, "pcc_gc_store_root")
    assert not _calls(body, "pcc_gc_take_pinned_slot")


def test_a_real_consumer_still_receives_and_takes_the_result():
    body = _probe_function(_emit("def probe(target, item):\n    return slot_operand_probe(target.append(item))\n"))
    assert _calls(body, "py_list_append")
    assert _calls(body, "py_obj_call_slots")
    assert _calls(body, "pcc_gc_take_pinned_slot")
    assert "probe.operand" in body


def test_nested_argument_result_is_not_discarded_with_outer_statement():
    body = _probe_function(_emit("def probe(target, inner):\n    target.append(inner.append(17))\n"))
    assert len(_calls(body, "py_list_append")) >= 2
    # The inner native None and arbitrary generic result both publish an
    # argument owner before the outer append reads it.
    assert "list.append.item.operand" in body
    assert len(_calls(body, "py_obj_call_slots")) >= 2


PROGRAM = r'''import gc
import weakref
events = []
references = []
resurrected = []
selected = ValueError("selected error")
class Result:
    def __init__(self, name):
        self.name = name
        references.append(weakref.ref(self))
    def __del__(self):
        events.append("del:" + self.name)
        if self.name == "resurrect":
            resurrected.append(self)
        gc.collect()
class Factory:
    def append(self, value):
        events.append("call:" + value)
        gc.collect()
        return Result(value)
class Reject:
    @property
    def append(self):
        events.append("lookup")
        raise selected
def argument():
    events.append("argument")
    return "unused"
def discard(receiver, name):
    receiver.append(name)
    events.append("after:" + name)
def consume(receiver, name):
    return receiver.append(name)
def invoke(fn):
    fn()
    events.append("after:invoke")
def make_result():
    return Result("function")
def outer(target, inner):
    target.append(inner.append("nested"))
    events.append("after:outer")
def invalid(receiver):
    receiver.append(argument())
class OtherMethods:
    def pop(self):
        return Result("pop")
    def count(self, value):
        return Result("count")
    def index(self, value):
        return Result("index")
    def extend(self, value):
        return Result("extend")
def other_methods(receiver):
    receiver.pop()
    receiver.count(1)
    receiver.index(1)
    receiver.extend([])
def scalar_and_text(value: str):
    object()
    type(value)
    str(value)
    repr(value)
    format(value, "")
    iter([1, 2])
    slice(1, 2, 1)
    staticmethod(make_result)
    classmethod(make_result)
    property(make_result)
    bytes.fromhex("00ff")
    float.fromhex("0x1.0p+2")
    str.maketrans("a", "b")
    value.replace("a", "b")
    value.strip(" ")
    value.ljust(20, " ")
    value.join(["a", "b"])
    b"hello".decode("utf8")
    sorted([3, 1, 2])
    ordinary = [1, 2, 3]
    ordinary.count(1)
    ordinary.index(2)
    ordinary.pop()
    ordinary.extend([4])
    assert ordinary == [1, 2, 4]
    try:
        b"hello".decode("does-not-exist")
    except LookupError:
        pass
    else:
        raise AssertionError("discard skipped decoder validation")
def parked(target):
    target.append((yield 31))
    events.append("parked:after")
def main():
    global selected
    receiver = Factory()
    discard(receiver, "statement")
    assert events == ["call:statement", "del:statement", "after:statement"]
    assert references[-1]() is None
    events.clear()
    saved = consume(receiver, "used")
    assert events == ["call:used"] and references[-1]() is saved
    del saved
    gc.collect()
    assert events == ["call:used", "del:used"] and references[-1]() is None
    events.clear()
    invoke(make_result)
    assert events == ["del:function", "after:invoke"] and references[-1]() is None
    events.clear()
    values = []
    outer(values, receiver)
    assert events == ["call:nested", "after:outer"]
    assert values[0] is references[-1]() and values[0].name == "nested"
    values.clear()
    gc.collect()
    assert events == ["call:nested", "after:outer", "del:nested"]
    events.clear()
    discard(receiver, "resurrect")
    assert events == ["call:resurrect", "del:resurrect", "after:resurrect"]
    assert references[-1]() is resurrected[0]
    resurrected.clear()
    gc.collect()
    assert references[-1]() is None and events.count("del:resurrect") == 1
    events.clear()
    try:
        invalid(Reject())
    except ValueError as error:
        assert error is selected
    else:
        raise AssertionError("descriptor error lost")
    assert events == ["lookup"]
    selected = None
    events.clear()
    other_methods(OtherMethods())
    assert events == ["del:pop", "del:count", "del:index", "del:extend"]
    scalar_and_text(" data ")
    events.clear()
    iterator = parked(values)
    assert next(iterator) == 31
    try:
        iterator.send(1 << 100)
    except StopIteration:
        pass
    assert values == [1 << 100] and events == ["parked:after"]
    print("DISCARDED_RESULTS_OK")
main()
'''


@pytest.mark.integration
def test_discarded_result_lifetimes_native_all_collectors(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
):
    _run_native_all_gc(tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
                       PROGRAM, "DISCARDED_RESULTS_OK")
