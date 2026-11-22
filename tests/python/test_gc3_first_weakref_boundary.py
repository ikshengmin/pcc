"""Current weakref publication seam and its public identity control.

No test installs nursery promotion. Python weakref semantics don't make
the referent a strong owner: see the weakref.ref library contract.
"""
from pathlib import Path

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.owned_regression_support import (
    assert_owned_program,
    assert_reference_program,
    explicit_owned_runtime,
)
from tests.python.runtime_body_model import load_functions


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_weakref.py"


@pytest.mark.parametrize("outcome", ("unsupported", "oom", "main", "late"))
def test_weakref_target_becomes_visible_only_after_admission_and_allocation(outcome):
    referent, weak = 0x1000, 0x2000
    events = []
    words = {(referent, abi.PYOBJECTHEADER_TYPE_TAG_OFFSET):
             abi.PY_TYPE_LIST if outcome == "unsupported" else abi.PY_TYPE_USER}

    def store(ptr, offset, val):
        words[ptr, offset] = val
        if (ptr, offset) == (weak, 16):
            events.append(("weak_target", val))

    def relocate(value):
        events.append(("resolve", value))
        return value

    def alloc(size, tag, flags):
        events.append(("alloc", tag))
        return 0 if outcome == "oom" else weak

    def link(path):
        assert words[weak, 16] == referent
        events.append(("link", path))

    bindings = {
        name: getattr(abi, name) for name in dir(abi) if name.startswith("PY_TYPE_")
    }
    bindings.update(
        PYOBJECTHEADER_TYPE_TAG_OFFSET=abi.PYOBJECTHEADER_TYPE_TAG_OFFSET,
        ptr_is_null=lambda value: value == 0, is_tagged_int=lambda value: value & 1,
        null=lambda: 0, ptr_eq=lambda a, b: a == b,
        load_i32=lambda ptr, offset: int(outcome == "late")
        if ptr == "pcc_weakref_late_windows" else words[ptr, offset],
        store_ptr=store, store_i64=store, store_i32=store,
        _WEAKREF_BYTES=56, _WEAKREF_HASH=48,
        _weakref_target_is_base_object=lambda _: 0, _py_none=lambda: 0x3000,
        _weakref_refuse=lambda _: events.append(("refuse",)),
        pcc_gc_note_relocation_read=relocate, pcc_gc_alloc=alloc,
        pcc_gc_backend=lambda: 3, global_addr=lambda key: key,
        _link_main=lambda _: link("main"), _link_late=lambda _: link("late"),
        pcc_diagnostics_runtime_log_event_code=lambda *_: None,
        pcc_gc_publish_initialized=lambda _: events.append(("publish",)),
    )
    load_functions(RUNTIME, {"_weakref_tag_refused", "py_weakref_new"},
                   bindings, local_helpers=False)
    out = bindings["py_weakref_new"](referent, 0)
    if outcome == "unsupported":
        assert out == 0 and events == [("refuse",)]
        return
    prelude = [("resolve", referent), ("alloc", abi.PY_TYPE_WEAKREF)]
    if outcome == "oom":
        assert out == 0 and events == prelude
        return
    assert out == weak
    assert events == prelude + [("weak_target", referent), ("link", outcome), ("publish",)]


PROGRAM = """
import gc
import weakref

class Item:
    def __init__(self, value):
        self.value = value

def try_weakref(value):
    try:
        weakref.ref(value)
    except TypeError:
        return True
    return False

def main():
    gc.disable()
    target = Item(19)
    untouched = Item(23)
    views = []
    for index in range(2):
        views.append(weakref.ref(target))
        gc.collect()
        assert views[index]() is target and views[index]().value == 19
        assert untouched.value == 23
    assert try_weakref([])
    print("GC_FIRST_WEAKREF_IDENTITY_OK")

main()
"""
EXPECTED = "GC_FIRST_WEAKREF_IDENTITY_OK\n"


def test_first_weakref_identity_cpython(tmp_path):
    assert_reference_program(PROGRAM, EXPECTED, tmp_path)


@pytest.mark.integration
def test_first_weakref_identity_native(python_program_compiler, request, tmp_path,
                                      explicit_owned_runtime, capfd):
    compiler = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(PROGRAM, EXPECTED, tmp_path, python_program_compiler,
                         compiler, explicit_owned_runtime, capfd)
