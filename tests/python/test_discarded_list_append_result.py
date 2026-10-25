"""Discard only exact builtin None; preserve arbitrary method-result cleanup."""
import os
import re
import subprocess
import sys

from pcc.frontends.python.pipeline import compile_python
from tests.python.test_shared_call_binding import _emit


def test_builtin_discard_does_not_publish_none_into_result_root():
    text = _emit("def statement(target):\n    target.append('item')\ndef expression(target):\n    return target.append('item')\n")
    body = re.search(r"^define [^\n]*@user_binding_statement\(.*?^}", text, re.M | re.S).group(0)
    expression = re.search(r"^define [^\n]*@user_binding_expression\(.*?^}", text, re.M | re.S).group(0)
    assert "@py_list_append(" in body and "@py_obj_call_slots(" in body
    publication = r"store ptr %none[^\n]*, ptr %list\.append\.result[^\n]*"
    assert re.search(publication, body) is None
    assert re.search(publication, expression) is not None
    assert "list.append.result" in body  # The dynamic method still has an owner.


PROGRAM = '''import gc
import weakref

events = []
survivors = []
class Result:
    def __del__(self):
        events.append("result released")
        survivors.append(self)

class Receiver:
    def append(self, value):
        events.append(("called", value))
        return Result()

def statement(target, value):
    target.append(value)
    gc.collect()
    events.append("after")

def expression(target):
    return target.append("returned")

def throwing(target, error):
    try:
        target.append(fail(error))
    except ValueError as caught:
        print("same error", caught is error)

def fail(error):
    raise error

def rebound(target):
    target.append("first")
    target = Receiver()
    target.append("second")

def main():
    values = []
    statement(values, "item")
    print(values, events)
    events.clear()
    receiver = Receiver()
    statement(receiver, "item")
    print(events, len(survivors))
    events.clear()
    result = expression(receiver)
    reference = weakref.ref(result)
    gc.collect()
    print(reference() is result, events)
    del result
    gc.collect()
    print(events, reference() is survivors[-1])
    events.clear()
    throwing(values, ValueError("marker"))
    print(values, events)
    rebound(values)
    gc.collect()
    print(values, events)
main()
'''


def test_discard_preserves_results_exceptions_and_resurrection_five_gc(tmp_path, pcc_runtime_archive):
    source = tmp_path / "discard_append.py"
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=15)
    assert reference.returncode == 0, reference.stderr
    assert "[('called', 'item'), 'result released', 'after'] 1" in reference.stdout
    output = tmp_path / "discard_append"
    compile_python(str(source), str(output), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for gc in range(5):
        result = subprocess.run([str(output)], capture_output=True, text=True, timeout=20,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc)))
        assert (result.returncode, result.stdout, result.stderr) == (0, reference.stdout, ""), gc
