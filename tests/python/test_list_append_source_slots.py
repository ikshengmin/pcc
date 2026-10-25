"""Caller roots survive threaded growth and keep append argument ownership."""
import os
import re
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python
from tests.python.test_shared_call_binding import _emit


def test_exact_list_append_passes_root_addresses_and_retains_dynamic_fallback():
    text = _emit("def append_item(target, item):\n    target.append(item)\n")
    body = re.search(r"^define [^\n]*@user_binding_append_item\(.*?^}", text, re.M | re.S).group(0)
    assert re.search(r"@py_list_append_from_slots\(ptr %target[^,]*, ptr %item[^,]*, i64 3\)", body)
    assert "@py_obj_call_slots(" in body and "@py_obj_getattr(" in body


@pytest.mark.parametrize("borrowed", (False, True))
def test_source_slots_reload_after_wait_and_growth_without_consuming_them(borrowed):
    from tests.python.test_rooted_list_append import AppendRoots

    model = AppendRoots(action="grow")
    model.ns["pcc_gc_load_borrowed_ptr"] = model.heal
    result = model.ns["_append_rooted_slots"](100, 200, 0, 0, 0, 2 if borrowed else 1)
    assert result == 0 and model.depth == 0
    assert model.memory[100] == 1100 and model.memory[200] == 5200
    assert model.memory[2032] == 6000 and model.memory[2040] == 5200
    assert model.memory[1116] == 6


PROGRAM = '''import gc
import threading

events = []
class Box:
    def __init__(self, value):
        self.value = value
    def __del__(self):
        events.append(self.value)

def worker(index, values):
    iteration = 0
    while iteration < 100:
        values.append(Box(index * 1000 + iteration))
        if iteration % 20 == 0:
            gc.collect()
        iteration += 1

def borrowed(values, item):
    values.append(item)
    values.append("literal")
    values.append(None)
    values.append(2 ** 130)

def main():
    values = []
    first = threading.Thread(target=worker, args=(0, values))
    second = threading.Thread(target=worker, args=(1, values))
    first.start()
    second.start()
    first.join()
    second.join()
    gc.collect()
    assert len(values) == 200 and events == []
    total = 0
    for value in values:
        total += value.value
    value = None
    assert total == 109900
    held = Box(9999)
    borrowed(values, held)
    gc.collect()
    assert values[-4] is held and values[-3] == "literal" and values[-2] is None
    assert values[-1] == 2 ** 130
    values.clear()
    gc.collect()
    assert len(events) == 200 and held.value == 9999
    held = None
    gc.collect()
    assert len(events) == 201
    print("SOURCE_SLOT_APPEND_OK")
main()
'''


def test_threaded_growth_and_argument_lifetimes_native_five_gc(tmp_path, pcc_runtime_archive):
    source = tmp_path / "source_slot_append.py"
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=20)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, "SOURCE_SLOT_APPEND_OK\n", "")
    output = tmp_path / "source_slot_append"
    compile_python(str(source), str(output), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for gc in range(5):
        result = subprocess.run([str(output)], capture_output=True, text=True, timeout=40,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc),
                                         PCC_GC_REFCOUNT_PROVENANCE_PROBE="2"))
        (tmp_path / (str(gc) + ".stdout")).write_text(result.stdout)
        (tmp_path / (str(gc) + ".stderr")).write_text(result.stderr)
        assert (result.returncode, result.stdout, result.stderr) == (0, reference.stdout, ""), gc
