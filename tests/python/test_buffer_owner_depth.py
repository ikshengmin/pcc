"""Buffer depth contract (docs/project-intent.md: ordinary buffer semantics).

Production-body models use raw-address, allocation, retain, boolean-pin and
non-parking graph-lock doubles. They do not model moving GC, roots or safepoints.
The separate owned native fixture constructs real views, with automatic GC
explicitly disabled; neither lane proves GetBuffer relocation/parking safety.
"""

from pathlib import Path

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.runtime_body_model import load_functions


RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"
DEPTHS = (0, 15, 16, 17, 32, 64)


class BufferModel:
    def __init__(self):
        self.memory = {}
        self.next_address = 4096
        self.allocations = set()
        self.pins = set()
        self.retains = {}
        self.edges = []
        self.errors = []
        self.globals = {"pcc_capi_buffer_lease_head": 0}
        self.locked = False
        self.fail_malloc = False
        self.ns = dict(
            c_ptr=int, null=lambda: 0, ptr_is_null=lambda p: p == 0,
            ptr_eq=lambda a, b: a == b, ptr_add=lambda p, n: p + n,
            is_tagged_int=lambda p: bool(p & 1), cstr=lambda s: s,
            PY_TYPE_BYTES=abi.PY_TYPE_BYTES,
            PY_TYPE_BYTEARRAY=abi.PY_TYPE_BYTEARRAY,
            PY_TYPE_MEMORYVIEW=abi.PY_TYPE_MEMORYVIEW,
            load_ptr=self.load, load_i32=self.load, load_i64=self.load,
            store_ptr=self.store, store_i32=self.store, store_i64=self.store,
            store_i8=self.store, memset=self.zero, stack_alloc=self.raw,
            PyMem_Malloc=self.malloc, PyMem_Free=self.free, free_c=self.free,
            global_load_ptr=self.globals.__getitem__,
            global_store_ptr=self.globals.__setitem__,
            pcc_gc_load_ptr=self.edge,
            pcc_gc_pin=lambda p: self.pin(p, True),
            pcc_gc_unpin=lambda p: self.pin(p, False),
            pcc_py_gc_minor_graph_lock=lambda: self.lock(True),
            pcc_py_gc_minor_graph_unlock=lambda: self.lock(False),
            py_incref=lambda p: self.retain(p, 1),
            py_decref=lambda p: self.retain(p, -1),
            py_memoryview_new=self.new_view,
            _type_error=lambda s: self.errors.append(("type", s)),
            _runtime_error=lambda s: self.errors.append(("runtime", s)),
        )
        load_functions(RUNTIME / "py_capi_buffer_runtime.py", {
            "_buffer_data", "PyObject_CheckBuffer", "PyObject_GetBuffer",
            "PyMemoryView_FromObject",
        }, self.ns)
        load_functions(RUNTIME / "py_capi_misc_runtime.py", {"PyBuffer_Release"}, self.ns)

    def raw(self, size):
        address = self.next_address
        self.next_address += (size + 15) // 16 * 16
        return address

    def load(self, pointer, offset=0):
        return self.memory.get(pointer + offset, 0)

    def store(self, pointer, offset, value):
        self.memory[pointer + offset] = value

    def zero(self, pointer, value, size):
        for offset in range(size):
            self.store(pointer, offset, value)

    def malloc(self, size):
        if self.fail_malloc:
            return 0
        pointer = self.raw(size)
        self.allocations.add(pointer)
        return pointer

    def free(self, pointer):
        self.allocations.remove(pointer)

    def lock(self, locked):
        assert self.locked != locked
        self.locked = locked

    def pin(self, pointer, pin):
        assert self.locked
        if pin:
            assert pointer not in self.pins
            self.pins.add(pointer)
        else:
            self.pins.remove(pointer)

    def retain(self, pointer, delta):
        self.retains[pointer] = self.retains.get(pointer, 0) + delta

    def edge(self, owner, slot):
        assert slot == owner + 16
        self.edges.append(owner)
        return self.load(slot)

    def new_view(self, source):
        # Constructor double only: the native lane verifies real construction.
        pointer = self.raw(32)
        self.store(pointer, 8, abi.PY_TYPE_MEMORYVIEW)
        self.store(pointer, 16, source)
        return pointer

    def chain(self, depth, writable):
        leaf = self.raw(64)
        self.store(leaf, 8, abi.PY_TYPE_BYTEARRAY if writable else abi.PY_TYPE_BYTES)
        self.store(leaf, 16, 7)
        for index, byte in enumerate(b"lease\x00x"):
            self.store(leaf, 24 + index, byte)
        outer = leaf
        for _ in range(depth):
            outer = self.ns["PyMemoryView_FromObject"](outer)
        self.edges.clear()
        return leaf, outer


@pytest.mark.parametrize("depth", DEPTHS)
@pytest.mark.parametrize("writable", (False, True))
@pytest.mark.parametrize("reverse", (False, True))
def test_production_buffer_probe_selects_data_and_owner_once(depth, writable, reverse):
    model = BufferModel()
    leaf, outer = model.chain(depth, writable)
    assert model.ns["PyObject_CheckBuffer"](outer) == 1
    assert len(model.edges) == depth
    views = [model.raw(80), model.raw(80)]
    for view in views:
        model.edges.clear()
        assert model.ns["PyObject_GetBuffer"](outer, view, 0x1C | int(writable)) == 0
        # Exactly one base read per layer, not a second owner walk.
        assert len(model.edges) == depth
        assert len(set(model.edges)) == depth
        assert model.load(view, 0) == leaf + 24
        assert model.load(view, 8) == outer
        assert model.load(view, 16) == 7
        assert model.load(view, 24) == 1
        assert model.load(view, 32) == int(not writable)
        assert model.load(view, 36) == 1
        assert model.load(view, 40) == "B"
        assert model.load(model.load(view, 48)) == 7
        assert model.load(model.load(view, 56)) == 1
        assert model.load(view, 64) == 0
        assert model.load(model.load(view, 72), 16) == leaf
    assert model.pins == {leaf, outer}
    assert model.retains[outer] == 2
    first, last = reversed(views) if reverse else views
    model.ns["PyBuffer_Release"](first)
    assert model.pins == {leaf, outer}
    assert len(model.allocations) == 1
    model.ns["PyBuffer_Release"](last)
    assert not model.pins and not model.allocations
    assert model.retains[outer] == 0
    assert model.globals["pcc_capi_buffer_lease_head"] == 0
    for view in views:
        assert all(model.load(view, offset) == 0 for offset in range(80))
        model.ns["PyBuffer_Release"](view)  # Idempotent release.
    assert not model.errors and not model.locked


@pytest.mark.parametrize("kind", ("null", "tagged", "unsupported", "readonly", "malloc"))
def test_production_buffer_failure_preserves_empty_export(kind):
    model = BufferModel()
    _, outer = model.chain(17, False)
    if kind == "null":
        outer = 0
    elif kind == "tagged":
        outer = 3
    elif kind == "unsupported":
        outer = model.raw(32)  # Unrecognized tag, not a valid exporter.
    model.fail_malloc = kind == "malloc"
    view = model.raw(80)
    model.zero(view, 99, 80)
    assert model.ns["PyObject_GetBuffer"](outer, view, int(kind == "readonly")) == -1
    assert all(model.load(view, offset) == 0 for offset in range(80))
    assert not model.pins and not model.allocations and not model.retains
    assert not model.locked
    expected = ("runtime", "out of memory creating buffer view") if kind == "malloc" else (
        "type", "object is not writable" if kind == "readonly" else "object does not support buffer protocol"
    )
    assert model.errors == [expected]
    if kind in ("null", "tagged", "unsupported"):
        assert model.ns["PyObject_CheckBuffer"](outer) == 0
        assert model.ns["PyMemoryView_FromObject"](outer) == 0
        assert model.errors[-1] == expected


def test_buffer_data_borrowed_pointer_in_final_runtime_ir(tmp_path):
    # Compile the whole real module, then apply the runtime archive's final
    # optimization sequence. Never substitute a copied/extracted tiny helper.
    import re

    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.owned_runtime_build import runtime_ir_passes
    from pcc.ir.optimization.driver import optimize_ir

    output = tmp_path / "buffer.ll"
    compile_python(
        str(RUNTIME / "py_capi_buffer_runtime.py"), str(output),
        backend="self", emit_llvm_only=True, python_library=True,
    )
    text = optimize_ir(output.read_text(), runtime_ir_passes(str(RUNTIME.parent)))
    symbol = "user_py_capi_buffer_runtime__buffer_data"
    match = re.search(
        r"^define[^\n]*\bptr\s+@" + re.escape(symbol) + r"\([^\n]*\n(.*?)^}",
        text, re.M | re.S,
    )
    assert match is not None, "buffer-data helper must return raw ptr"
    body = match.group(1)
    returns = [line.strip() for line in body.splitlines() if line.strip().startswith("ret ")]
    assert returns and all(line.startswith("ret ptr ") for line in returns)
    # Inspect only this helper's own calls. The real read barrier may perform
    # legitimate healing/ownership work in its separate implementation.
    calls = re.findall(r"\b(?:call|invoke)\b[^\n]*@([\w.$]+)\(", body)
    assert not any(re.search(
        r"(?:incref|decref|root_copy|root_move|store_root|foreign_lease|note_frame)", name
    ) for name in calls), calls
    assert "_buffer_lease_owner" not in text


def test_production_buffer_null_output_preserves_error_without_export():
    model = BufferModel()
    _, outer = model.chain(17, False)
    assert model.ns["PyObject_GetBuffer"](outer, 0, 0) == -1
    assert model.errors == [("runtime", "NULL Py_buffer")]
    assert not model.edges
    assert not model.pins and not model.allocations and not model.retains
    assert model.globals["pcc_capi_buffer_lease_head"] == 0
    assert not model.locked
