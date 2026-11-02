"""Opt-in GC4 prototype: tracing owns liveness, counts no longer change."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from pcc.backend.owned_object_emit import emit_owned_object
from pcc.frontends.python.pipeline import compile_python
from pcc.frontends.python.pipeline_targets import host_target_triple
from tests.python.test_owned_external_tls import _c_ir


COUNT_PROBE = '''extern void *py_list_new(long);
extern void py_incref(void *);
extern void py_decref(void *);
long tracing_count_probe(void) {
    void *object = py_list_new(0);
    py_incref(object);
    long count = *(long *)object;
    py_decref(object);
    py_decref(object);
    return count;
}
'''

VALUES = '''import gc

def main():
    keep = ["retained" + str(index) for index in range(20)]
    total = 0
    for batch in range(20):
        values = {str(index): [index, index * index, 1 << (70 + index)] for index in range(20)}
        total += values["17"][1]
        gc.collect()
        assert values["19"][2] == 1 << 89
        assert keep[batch] == "retained" + str(batch)
    print(total, keep[0], keep[-1])
main()
'''

LIFECYCLE = '''import gc
import weakref

events = []
rescued = []
class Cycle:
    def __init__(self, value, resurrect):
        self.value = value
        self.resurrect = resurrect
        self.link = self
    def __del__(self):
        events.append("final " + str(self.value))
        if self.resurrect:
            rescued.append(self)

def callback(ref):
    events.append("weak")

def create(value, resurrect):
    item = Cycle(value, resurrect)
    return weakref.ref(item, callback)

def collect():
    for index in range(4):
        gc.collect()

def main():
    first = create(1, False)
    collect()
    print(first() is None, events)
    events.clear()
    second = create(2, True)
    collect()
    print(second() is None, events, rescued[0].value)
    rescued.clear()
    collect()
    print(events)
main()
'''


def reference(tmp_path, program):
    source = tmp_path / "reference.py"
    source.write_text(program)
    result = subprocess.run([sys.executable, str(source)], capture_output=True,
                            text=True, timeout=20)
    assert result.returncode == 0 and result.stderr == "", result
    return result.stdout


@pytest.mark.integration
def test_gc4_tracing_keeps_values_while_reference_updates_are_idle(tmp_path, pcc_runtime_archive):
    expected = reference(tmp_path, VALUES)
    target = host_target_triple()
    probe = tmp_path / "count_probe.o"
    probe.write_bytes(emit_owned_object(_c_ir(COUNT_PROBE, target), target))
    source = tmp_path / "values.py"
    source.write_text('''from pcc.extern import extern, c_int64
count_probe = extern("tracing_count_probe", (), c_int64)
print(count_probe())
''' + VALUES)
    binary = tmp_path / "values"
    compile_python(str(source), str(binary), backend="self",
                   runtime_archive=str(pcc_runtime_archive), object_inputs=(str(probe),))
    for enabled, count in (("0", 2), ("1", 1)):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=45,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND="4",
                                         PCC_GC4_TRACING_ONLY=enabled))
        assert (result.returncode, result.stdout, result.stderr) == (0, str(count) + "\n" + expected, ""), enabled


@pytest.mark.integration
def test_gc4_tracing_weakrefs_finalizers_and_resurrection_match_cpython(tmp_path, pcc_runtime_archive):
    expected = reference(tmp_path, LIFECYCLE)
    source = tmp_path / "lifecycle.py"
    source.write_text(LIFECYCLE)
    binary = tmp_path / "lifecycle"
    compile_python(str(source), str(binary), backend="self", runtime_archive=str(pcc_runtime_archive))
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=45,
                            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND="4",
                                     PCC_GC4_TRACING_ONLY="1"))
    assert (result.returncode, result.stdout, result.stderr) == (0, expected, "")
