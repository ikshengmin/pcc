"""A fresh owned root is published before any potentially parking operation."""

from pathlib import Path
import os
import subprocess
import sys

import pytest

from test_foreign_address_leases import _functions


ROOT = Path(__file__).resolve().parents[2]


def publication(backend, *, initialized=1, logging=0, acquire=2, release=0):
    state = {
        "pcc_gc_config_initialized": initialized,
        "pcc_gc_backend_selected": backend,
        "pcc_diagnostics_runtime_log_fast_state": logging,
    }
    slots = {100: 1000}
    events = []

    def lease(slot):
        events.append(("acquire", slot))
        # A contended acquisition may relocate an already published root.
        slots[slot] = 2000
        return acquire

    namespace = dict(
        ptr_is_null=lambda value: int(value == 0),
        global_addr=lambda name: name,
        load_i32=lambda base, offset: state[base],
        null=lambda: 0,
        load_ptr=lambda slot, offset: slots[slot],
        pcc_gc_foreign_lease_acquire=lease,
        pcc_gc_note_slot_write_barrier=lambda *args: events.append(("barrier", *args)),
        pcc_gc_foreign_lease_release=lambda *args: events.append(("release", *args)) or release,
    )
    _functions(ROOT / "pcc/runtime/py/py_obj.py", {"pcc_gc_publish_owned_root"}, namespace)
    return namespace["pcc_gc_publish_owned_root"], slots, events


def test_gc0_published_owner_needs_no_address_transaction():
    publish, slots, events = publication(0)
    assert publish(100) == 0
    assert slots == {100: 1000}
    assert events == []
    assert publish(0) == -1


@pytest.mark.parametrize("backend", [1, 2, 3, 4])
def test_other_collectors_reload_authoritative_root_under_lease(backend):
    publish, slots, events = publication(backend)
    assert publish(100) == 0
    assert slots == {100: 2000}
    assert events == [("acquire", 100), ("barrier", 0, 100, 2000), ("release", 100, 2)]


@pytest.mark.parametrize("settings", [{"initialized": 0}, {"logging": 1}])
def test_gc0_configuration_and_diagnostics_keep_full_protocol(settings):
    publish, _, events = publication(0, **settings)
    assert publish(100) == 0
    assert [event[0] for event in events] == ["acquire", "barrier", "release"]


@pytest.mark.parametrize("status", [-1, -2, -3, -4])
def test_acquisition_failure_never_uses_an_unleased_address(status):
    publish, _, events = publication(4, acquire=status)
    assert publish(100) == status
    assert events == [("acquire", 100)]


def test_release_failure_is_reported_after_publication_barrier():
    publish, _, events = publication(3, release=-3)
    assert publish(100) == -3
    assert events[-2:] == [("barrier", 0, 100, 2000), ("release", 100, 2)]


def test_native_fresh_results_preserve_identity_exceptions_and_finalizers(tmp_path, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "publication.py"
    source.write_text('''import gc
import weakref

events = []
class Value:
    def __init__(self, value):
        self.value = value
    def __del__(self):
        events.append(self.value)

def make():
    return Value(73)

def fail():
    raise ValueError("original exception")

def invoke(callback):
    value = callback()
    gc.collect()
    return value

def main():
    value = invoke(make)
    alias = {"value": value}
    reference = weakref.ref(value)
    value = None
    gc.collect()
    assert reference() is alias["value"]
    assert alias["value"].value == 73 and events == []
    try:
        invoke(fail)
    except ValueError as error:
        assert str(error) == "original exception"
    else:
        raise AssertionError("exception was lost")
    alias.clear()
    gc.collect()
    assert reference() is None and events == [73]
    print("OWNED_ROOT_PUBLICATION_OK")
main()
''')
    reference = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=20)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, "OWNED_ROOT_PUBLICATION_OK\n", "")
    output = tmp_path / "publication.ll"
    compile_python(str(source), str(output), emit_llvm_only=True, backend="self", libpython_mode="off")
    assert "call.slot.publish.lease" in output.read_text()
    binary = tmp_path / "publication"
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (result.returncode, result.stdout, result.stderr) == (
            0, reference.stdout, "",
        ), backend
