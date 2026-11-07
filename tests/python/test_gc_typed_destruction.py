"""The tracing sweeper must use the complete existing typed destructor.

Contract: docs/project-intent.md preserves native/foreign resource ownership;
count-free collection must release the same payload as terminal GC0 decref.
These tests execute the production dispatch and native-handle destructor bodies
with raw memory/allocator primitives, not a replacement destructor table.
"""
import ast
from pathlib import Path

import pytest

from pcc.runtime.py import py_abi_constants as abi


ROOT = Path(__file__).resolve().parents[2] / "pcc/runtime/py"


def _load(namespace, filename, names):
    tree = ast.parse((ROOT / filename).read_text())
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in functions} == set(names)
    for node in functions:
        node.decorator_list = []
    exec(compile(ast.Module(functions, type_ignores=[]), str(ROOT / filename), "exec"), namespace)


@pytest.mark.parametrize("backend", (1, 2, 3, 4))
@pytest.mark.parametrize("foreign", (0, 0x9000))
def test_tracing_releases_foreign_handle_payload_once(backend, foreign):
    obj = 0x1000
    memory = {obj: 1, obj + 8: abi.PY_TYPE_CPY_HANDLE,
              obj + 12: 8 | 1024, obj + 16: foreign}
    globals_ = {"pcc_gc_backend_selected": backend,
                "pcc_gc_backend4_no_refcounts": 1,
                "py_cpy_handle_release_fn": 0xA000}
    events = []

    def load(base, offset=0):
        return globals_.get(base, 0) if isinstance(base, str) else memory.get(base + offset, 0)

    def store(base, offset, value):
        memory[base + offset] = value

    def release(function, value):
        assert function == 0xA000
        events.append(("release", value))

    def free(value):
        assert value == obj and memory[obj + 16] == 0
        assert memory[obj + 12] & 524288
        events.append(("free", value))

    namespace = {name: getattr(abi, name) for name in dir(abi) if name.startswith("PY")}
    namespace.update(
        i64=int, ptr_is_null=lambda value: int(value == 0),
        is_tagged_int=lambda value: value & 1, null=lambda: 0,
        load_i32=load, load_i64=load, load_ptr=load,
        store_i32=store, store_i64=store, store_ptr=store,
        global_addr=lambda name: name, global_load_ptr=globals_.__getitem__,
        pcc_gc_object_is_address_pinned=lambda value: 0,
        pcc_capi_is_cext_type_tag=lambda tag: int(tag >= abi.PY_TYPE_CEXT_TAG_BASE),
        pcc_gc_note_object_freeing=lambda value: events.append(("note", value)),
        pcc_refcount_forget=lambda value: None, py_gc_untrack=lambda value: None,
        stack_alloc=lambda size: 0x2000, GC_FREE_WATCH_BYTES=24,
        pcc_gc_free_watch_begin=lambda *args: pytest.fail("non-zpage handle"),
        pcc_gc_free_watch_end=lambda *args: pytest.fail("non-zpage handle"),
        pcc_gc_free_object_memory=free, call_void_ptr1=release,
        _cpy_handle_tripwire=lambda *args: pytest.fail("invalid handle ownership"),
        cstr=lambda value: value,
    )
    _load(namespace, "py_obj_dealloc.py", {"_dealloc_dispatch", "py_dealloc_cpy_handle"})
    namespace["pcc_gc_dealloc_typed"] = namespace["_dealloc_dispatch"]
    _load(namespace, "freestanding_gc_tracing_sweep_collector.py",
          {"pcc_gc_tracing_finalize_unreachable", "pcc_gc_tracing_finalize_unpinned"})
    namespace["pcc_gc_tracing_finalize_unreachable"](obj)
    expected = [("note", obj)]
    if foreign:
        expected.append(("release", foreign))
    expected.append(("free", obj))
    assert events == expected
    assert memory[obj] == 0 and memory[obj + 16] == 0
