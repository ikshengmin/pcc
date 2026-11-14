"""Python reference 6.3.4: operands survive later evaluation and invocation.

The deferred-call contract also keeps the returned continuation alive after
temporary argument roots leave, before that continuation starts to park.
"""

import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.xdist_group(name="vthread_loaded_methods")


@pytest.mark.parametrize("target", ["callback", "receiver.method"])
def test_deferred_call_retires_synchronous_operand_roots_before_driving(monkeypatch, target):
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from tests.python.test_slot_call_operand_roots import _emit

    original = L1CodeGen._new_slot_call_root
    observed = []

    def record(self, label, synchronous_lexical=False):
        slot = original(self, label, synchronous_lexical)
        if label.startswith("vthread.call"):
            observed.append((label, self._slot_call_root_record(slot)[2]))
        return slot

    monkeypatch.setattr(L1CodeGen, "_new_slot_call_root", record)
    text = _emit(
        "import pcc.virtual_thread as vt\n"
        "def worker(callback, receiver, value):\n"
        "    vt.yield_now()\n"
        "    return vt.call(" + target + ", value)\n"
    )
    assert observed and all(lexical for _name, lexical in observed), observed
    assert "@py_obj_call_" in text
    assert "vthread.call.child.move" in text
    assert "@py_gen_is_continuation(" in text


PROGRAM = '''import gc
import pcc.virtual_thread as vt
selected = ValueError("selected")
class Box:
    def __init__(self):
        self.value = 10**30 + 7
def overwrite(box):
    box.value = 10**30 + 29
    gc.collect()
    return box.value
def overwrite_parked(box):
    vt.yield_now()
    return overwrite(box)
def check(first, second):
    gc.collect()
    assert first == 10**30 + 7 and second == 10**30 + 29
    return first + second
def check_parked(first, second):
    vt.yield_now()
    return check(first, second)
def fail(value):
    gc.collect()
    assert value == 10**30 + 7
    raise selected
def worker():
    vt.yield_now()
    box = Box()
    print(vt.call(check, box.value, overwrite(box)))
    box.value = 10**30 + 7
    print(vt.call(check_parked, box.value, overwrite(box)))
    box.value = 10**30 + 7
    print(vt.call(check, box.value, vt.call(overwrite_parked, box)))
    box.value = 10**30 + 7
    try:
        vt.call(fail, box.value)
    except ValueError as error:
        assert error is selected
        print("selected error")
    else:
        raise AssertionError("callback exception lost")
    vt.yield_now()
    gc.collect()
    print("DEFERRED_ROOTS_OK")
def main():
    task = vt.spawn(worker)
    vt.run(1, 256)
    error = vt.exception(task)
    if error is not None:
        raise error
    assert vt.outcome(task) == vt.OUTCOME_RETURNED
main()
'''


@pytest.mark.integration
def test_deferred_operands_match_cpython_with_heap_rebinding_and_parks(tmp_path):
    reference = PROGRAM.split("def main():", 1)[0].replace("vt.yield_now()", "None")
    expected = subprocess.run([sys.executable, "-c", reference + "\nworker()\n"],
                              check=True, capture_output=True, text=True, timeout=10).stdout
    source = tmp_path / "deferred_roots.py"
    source.write_text(PROGRAM + "\nfrom pcc.extern import c_int64, extern\n"
                      "backend = extern('pcc_gc_backend', (), c_int64)\n"
                      "print('backend', backend())\n")
    binary = source.with_suffix("")
    build = subprocess.run([sys.executable, "-m", "pcc", "--backend", "self",
                            str(source), "-o", str(binary)], capture_output=True,
                           text=True, timeout=60)
    assert build.returncode == 0, build.stdout + build.stderr
    for backend in range(5):
        run = subprocess.run([str(binary)], capture_output=True, text=True,
                             timeout=5, env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert (run.returncode, run.stdout, run.stderr) == (
            0, expected + f"backend {backend}\n", "",
        ), (backend, run.stdout, run.stderr)
