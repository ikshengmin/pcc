"""Exact static producers bypass temporary address carriers; heap values do not."""
import os
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

from pcc.frontends.python.py_ast import Call, DynType, FloatLit, Name, SourceSpan, StrType
from pcc.frontends.python.codegen.static_object_operands import emit_nonmoving_literal_operand
from pcc.frontends.python.pipeline import compile_python
from tests.python.test_slot_call_operand_roots import _emit as emit_operand, _probe_function
from tests.python.test_shared_call_binding import _emit


def test_only_proven_publication_omits_address_lease():
    for expression in ("None", "'static'", "1267650600228229401496703205376"):
        body = _probe_function(emit_operand("def probe():\n    return slot_operand_probe(" + expression + ")\n"))
        assert "call.slot.publish.lease" not in body
        assert "@pcc_gc_note_slot_write_barrier(" in body
    heap = _probe_function(emit_operand("def probe():\n    return slot_operand_probe(b'heap')\n"))
    assert "call.slot.publish.lease" in heap
    dynamic = _probe_function(emit_operand("def probe(value: str):\n    return slot_operand_probe(value)\n"))
    assert "@pcc_gc_root_copy_borrowed_lease(" in dynamic


def test_runtime_annotation_and_names_are_not_static_storage_proof():
    span = SourceSpan("proof.py", 1, 0, 1, 1)
    host = SimpleNamespace(_strict_no_libpython=True)
    expressions = [
        Name(span, StrType("str"), "value"),
        Call(span, StrType("str"), Name(span, DynType("dyn"), "produce"), (), ()),
        FloatLit(span, DynType("dyn"), 1.5),
    ]
    assert all(emit_nonmoving_literal_operand(host, expr) is None for expr in expressions)


def test_literal_append_has_no_input_root_but_dynamic_fallback_remains():
    text = _emit("def append_constant(target):\n    target.append('value')\n")
    body = re.search(r"^define [^\n]*@user_binding_append_constant\(.*?^}", text, re.M | re.S).group(0)
    assert "list.append.item.operand" not in body
    assert "@py_list_append(" in body and "@py_obj_call_slots(" in body
    assert "@py_obj_getattr(" in body


PROGRAM = '''import gc

events = []
class Box:
    def __del__(self):
        events.append("released")

def append_values(values, dynamic):
    values.append("static")
    values.append(1267650600228229401496703205376)
    values.append(None)
    values.append(True)
    values.append(17)
    values.append(b"bytes")
    values.append(1.25)
    values.append(dynamic)
    values.append(Box())

class Receiver:
    def append(self, value):
        print("custom", value)
        return value

def append_constant(target):
    return target.append("static")

def main():
    values = []
    index = 0
    while index < 20:
        append_values(values, "heap" + str(index))
        index += 1
        if index % 4 == 0:
            gc.collect()
    assert len(values) == 180
    assert values[0] == "static" and values[1] == 2 ** 100
    assert values[2] is None and values[3] is True and values[4] == 17
    assert values[5] == b"bytes" and values[6] == 1.25
    assert values[7] == "heap0" and values[-2] == "heap19"
    assert events == []
    print(append_constant(Receiver()))
    values.clear()
    gc.collect()
    print(len(events))
main()
'''


def test_static_and_heap_operands_preserve_lifetimes_native_five_gc(tmp_path, pcc_runtime_archive):
    source = tmp_path / "static_inputs.py"
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=15)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, "custom static\nstatic\n20\n", "")
    output = tmp_path / "static_inputs"
    compile_python(str(source), str(output), backend="self", libpython_mode="off", ir_scaffold_mode="on",
                   runtime_archive=str(pcc_runtime_archive))
    for gc in range(5):
        result = subprocess.run([str(output)], capture_output=True, text=True, timeout=20,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc)))
        assert (result.returncode, result.stdout, result.stderr) == (0, reference.stdout, ""), gc
