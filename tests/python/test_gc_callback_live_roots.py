"""Positive collection-boundary controls. No assumptions about where local
variables are physically stored or when an unreferenced object is reclaimed.
"""
import os
from pathlib import Path

import pytest

from tests.python.owned_regression_support import (
    _record_execution,
    assert_reference_program,
    explicit_owned_runtime,
)


# CPython uses a normal list for the explicit owner; the PCC program exercises
# a real off-stack, individually queried scheduler registration. The rest of
# the program, including all liveness checks, is shared.
REFERENCE_ROOT = '''holder = []
def open_root():
    return not holder
def put_root(value):
    holder.append(value)
    return True
def root_present():
    return len(holder) == 1
def running_world():
    return True
def close_root():
    holder.clear()
    return not holder
'''
NATIVE_ROOT = '''from pcc.extern import c_int64, c_obj, extern
open_root = extern("test_gc_callback_root_open", (), c_int64)
put_root = extern("test_gc_callback_root_put", (c_obj,), c_int64)
root_present = extern("test_gc_callback_root_present", (), c_int64)
running_world = extern("test_gc_callback_root_world_running", (), c_int64)
close_root = extern("test_gc_callback_root_close", (), c_int64)
'''

BODY = '''
import gc
import sys
import weakref

class Item:
    def __init__(self, label):
        self.label = label

views = []
events = []
busy = [False]
reenter = sys.argv[1] == "pcc-reentry"

def set_explicit_owner():
    item = Item(101)
    assert put_root(item)
    return weakref.ref(item)

def on_phase(phase, info):
    # gc callbacks are unraisable boundaries. The *main* function verifies
    # both success markers, so a swallowed assertion cannot pass the test.
    if busy[0]:
        events.append("recursive-hook")
        return
    busy[0] = True
    own = Item(401)
    view = weakref.ref(own)
    if reenter:
        gc.collect()
    assert view() is own and own.label == 401
    assert root_present() and views[0]().label == 101
    assert views[1]().label == 211 and views[2]().label == 307
    events.append(phase)
    busy[0] = False

def inner():
    item = Item(307)
    views.append(weakref.ref(item))
    gc.collect()
    assert views[2]() is item and item.label == 307

def outer():
    item = Item(211)
    views.append(weakref.ref(item))
    inner()
    assert views[1]() is item and item.label == 211

def main():
    gc.disable()
    assert open_root()
    try:
        views.append(set_explicit_owner())
        gc.callbacks.append(on_phase)
        try:
            outer()
        finally:
            gc.callbacks.remove(on_phase)
        # No callback may outlive its registration. The off-stack owner still
        # holds its value; the test never requires the two locals to be dead.
        gc.collect()
        assert events == ["start", "stop"], events
        assert root_present() and views[0]().label == 101
    finally:
        assert close_root()
    assert not root_present()
    print("gc-callback-live-roots", sys.argv[1])

main()
'''


def test_public_callback_live_roots_reference(tmp_path):
    # Recursive gc.collect() during a collection is not specified by Python.
    # Compare only the non-recursive public case with CPython.
    assert_reference_program(
        REFERENCE_ROOT + BODY, "gc-callback-live-roots ordinary\n", tmp_path,
        arguments=("ordinary",),
    )


def _compile_native_with_owner(tmp_path, runtime, body):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.python.test_owned_external_tls import _c_ir

    target = host_target_triple()
    helper = Path(__file__).with_name("fixtures") / "gc_callback_live_root.c"
    obj = tmp_path / "registered.o"
    obj.write_bytes(emit_owned_object(_c_ir(helper.read_text(), target), target))
    source = tmp_path / "callback_scopes.py"
    binary = tmp_path / "callback_scopes"
    source.write_text(NATIVE_ROOT + body)
    compile_python(
        str(source), str(binary), backend="self",
        runtime_archive=str(runtime), object_inputs=(str(obj),),
    )
    return binary


@pytest.mark.integration
def test_native_callback_live_roots(tmp_path, explicit_owned_runtime):
    binary = _compile_native_with_owner(tmp_path, explicit_owned_runtime, BODY)
    for mode in ("ordinary", "pcc-reentry"):
        for backend in range(5):
            env = dict(os.environ, PCC_GC_BACKEND=str(backend))
            result = _record_execution(
                tmp_path, f"{mode}-gc{backend}", [str(binary), mode], env,
            )
            assert (result["returncode"], result["stdout"], result["stderr"]) == (
                0, f"gc-callback-live-roots {mode}\n", "",
            ), (mode, backend, result)


FINALIZER_BODY = '''
import gc
import sys
import weakref

class Item:
    def __init__(self, label):
        self.label = label

views = []
events = []
collecting = [False]
reenter = sys.argv[1] == "pcc-reentry"

class Trigger:
    def __init__(self):
        self.cycle = self

    def __del__(self):
        assert collecting[0] and running_world()
        item = Item(551)
        view = weakref.ref(item)
        assert put_root(item)
        views.append(view)
        del item
        if reenter:
            assert gc.collect() == 0
        assert root_present() and view().label == 551
        events.append("finalized")

def make_trigger():
    trigger = Trigger()

def main():
    gc.disable()
    assert open_root()
    try:
        make_trigger()
        collecting[0] = True
        gc.collect()
        collecting[0] = False
        # This check cannot be swallowed as an unraisable finalizer exception.
        assert events == ["finalized"], events
        assert len(views) == 1 and root_present() and views[0]().label == 551
        gc.collect()
        assert events == ["finalized"] and views[0]().label == 551
    finally:
        assert close_root()
    print("gc-finalizer-acquired-root", sys.argv[1])

main()
'''


def test_finalizer_acquired_root_reference(tmp_path):
    # The recursive collect behavior is a PCC guard, not a CPython contract.
    assert_reference_program(
        REFERENCE_ROOT + FINALIZER_BODY, "gc-finalizer-acquired-root ordinary\n",
        tmp_path, arguments=("ordinary",),
    )


@pytest.mark.integration
def test_native_gc3_finalizer_acquired_root(tmp_path, explicit_owned_runtime):
    binary = _compile_native_with_owner(tmp_path, explicit_owned_runtime, FINALIZER_BODY)
    env = dict(os.environ, PCC_GC_BACKEND="3")
    for mode in ("ordinary", "pcc-reentry"):
        result = _record_execution(tmp_path, mode, [str(binary), mode], env)
        assert (result["returncode"], result["stdout"], result["stderr"]) == (
            0, f"gc-finalizer-acquired-root {mode}\n", "",
        ), (mode, result)
