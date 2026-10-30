"""A stable parameter caches exact-list membership, never method lookup."""
import os
import re
import subprocess
import sys

from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.pipeline import compile_python
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module


PROGRAM = '''events = []

def argument(value):
    events.append(("argument", value))
    return value

def record(value):
    events.append(("append", value))
    return value

class Receiver:
    @property
    def append(self):
        events.append("lookup")
        return record

def exercise(target, count):
    index = 0
    while index < count:
        target.append(argument(index))
        index += 1
    return target

def pair(target):
    return (target.append(argument(7)), target.append(argument(8)))

def rebound(target, other):
    target.append(10)
    target = other
    target.append(20)

def recursive(target, other, count):
    target.append(count)
    if count:
        return recursive(other, target, count - 1)
    return None

def main():
    values = []
    print(exercise(values, 3) is values, values)
    print(events)
    events.clear()
    receiver = Receiver()
    print(exercise(receiver, 2) is receiver)
    print(events)
    events.clear()
    print(pair([]))
    events.clear()
    print(pair(receiver))
    print(events)
    events.clear()
    values = []
    rebound(values, receiver)
    print(values, events)
    events.clear()
    values = []
    recursive(values, receiver, 3)
    print(values, events)
main()
'''


def test_guard_storage_is_one_scalar_pair_per_parameter_activation(monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    typed = infer_module(parse_and_lift(PROGRAM, "list_guards.py", "list_guards"))
    generator = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
    generator._strict_no_libpython = True
    generator._prefer_native_callable_values = True
    text = str(generator.generate(typed))
    for function in ("exercise", "pair", "recursive"):
        entries = [entry for entry in generator._exact_list_parameter_guards.values()
                   if entry[1].name == "user_list_guards_" + function]
        assert len(entries) == 1, function
        entry = entries[0]
        assert entry[2].type.pointee.width == 1 and entry[3].type.pointee.width == 1
        assert entry[4].type.pointee.width == 64
        body = re.search(r"^define [^\n]*@user_list_guards_" + function + r"\(.*?^}",
                         text, re.M | re.S).group(0)
        assert "list.parameter.guard.query" in body
        assert "@pcc_object_type_epoch acquire" in body
        assert "@py_obj_getattr(" in body  # Each non-list call still loads the live method.


def test_stable_list_guards_preserve_dynamic_semantics_five_gc(tmp_path, pcc_runtime_archive):
    source = tmp_path / "list_guards.py"
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True,
                               text=True, timeout=15)
    assert reference.returncode == 0, reference.stderr
    assert "[3, 1] ['lookup', ('append', 2), 'lookup', ('append', 0)]" in reference.stdout
    output = tmp_path / "list_guards"
    compile_python(str(source), str(output), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for gc in range(5):
        result = subprocess.run([str(output)], capture_output=True, text=True, timeout=20,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc)))
        assert (result.returncode, result.stdout, result.stderr) == (
            0, reference.stdout, "",
        ), gc
