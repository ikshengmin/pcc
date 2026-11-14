"""Reference 6.3.1/6.3.4: receiver ownership and binding across suspension."""

import os
import subprocess
import sys

import pytest


pytestmark = pytest.mark.xdist_group(name="vthread_loaded_methods")


@pytest.mark.parametrize("body", [
    "",
    "    receiver = other\n",
    "    def change():\n        nonlocal receiver\n        receiver = other\n",
])
def test_resume_receiver_branches_emit_verified_owned_ir(body):
    from pcc.backend.self_backend_parse import parse_self_backend_module
    from pcc.backend.self_backend_verify import verify_parsed_module
    from tests.python.test_slot_call_operand_roots import _calls, _emit

    text = _emit("import pcc.virtual_thread as vt\n"
                 "def worker(receiver, other):\n    vt.yield_now()\n" + body
                 + "    return slot_operand_probe(receiver.value)\n")
    assert _calls(text, "py_obj_getattr")
    assert _calls(text, "pcc_gc_foreign_lease_acquire")
    verify_parsed_module(parse_self_backend_module(text))


PROGRAM = '''import gc
import pcc.virtual_thread as vt
class Box:
    def __init__(self, value):
        self._value = value
    @property
    def value(self):
        gc.collect()
        return self._value
    def read(self):
        vt.yield_now()
        gc.collect()
        return self._value
def observe(receiver):
    vt.yield_now()
    first = receiver.value
    return first + vt.call(receiver.read)
def rebind(receiver, other):
    vt.yield_now()
    first = receiver.value
    receiver = other
    gc.collect()
    return first + vt.call(receiver.read)
def captured(receiver, other):
    def change():
        nonlocal receiver
        receiver = other
        gc.collect()
    vt.yield_now()
    first = receiver.value
    change()
    return first + receiver.value
def drop(receiver):
    vt.yield_now()
    value = receiver.value
    del receiver
    gc.collect()
    return value
def worker():
    print(vt.call(observe, Box(10**30 + 7)))
    print(vt.call(rebind, Box(10**30 + 7), Box(10**30 + 29)))
    print(vt.call(captured, Box(10**30 + 7), Box(10**30 + 29)))
    print(vt.call(drop, Box(10**30 + 7)))
    print("RESUME_RECEIVERS_OK")
def main():
    task = vt.spawn(worker)
    vt.run(1, 512)
    error = vt.exception(task)
    if error is not None:
        raise error
    assert vt.outcome(task) == vt.OUTCOME_RETURNED
main()
'''


@pytest.mark.integration
def test_resume_receiver_lifetimes_match_cpython_on_all_collectors(tmp_path):
    reference = PROGRAM.split("def main():", 1)[0].replace("vt.yield_now()", "None")
    expected = subprocess.run([sys.executable, "-c", reference + "\nworker()\n"],
                              check=True, capture_output=True, text=True, timeout=10).stdout
    source = tmp_path / "resume_receivers.py"
    source.write_text(PROGRAM + "\nfrom pcc.extern import c_int64, extern\n"
                      "backend = extern('pcc_gc_backend', (), c_int64)\n"
                      "print('backend', backend())\n")
    binary = source.with_suffix("")
    build = subprocess.run([sys.executable, "-m", "pcc", "--backend", "self",
                            str(source), "-o", str(binary)], capture_output=True,
                           text=True, timeout=60)
    assert build.returncode == 0, build.stdout + build.stderr
    for backend in range(5):
        run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=5,
                             env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert (run.returncode, run.stdout, run.stderr) == (
            0, expected + f"backend {backend}\n", "",
        ), (backend, run.stdout, run.stderr)
