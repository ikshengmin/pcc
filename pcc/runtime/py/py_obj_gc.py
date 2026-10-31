"""Phase 4c.7: pcc-Python port of py_obj_gc.c.

This mirrors the C runtime's default CPython-style backend: immediate
refcounting plus a side-table cycle collector for tracked containers.

PyGcNode layout (40 bytes):
    offset  0  obj       PyObject*
    offset  8  gc_refs   i64
    offset 16  reachable i32
    offset 24  prev      PyGcNode*
    offset 32  next      PyGcNode*
"""

__pcc_runtime_port__ = True

from pcc.extern import extern, c_abi_export, c_ptr, c_int64, c_void
from pcc.runtime.py.py_abi_constants import (
    PYOBJECTHEADER_FLAGS_OFFSET,
    PYOBJECTHEADER_REFCOUNT_OFFSET,
    PYOBJECTHEADER_TYPE_TAG_OFFSET,
    PY_FLAG_GC_DEALLOCATING,
    PY_FLAG_IMMORTAL,
    PY_TYPE_LIST,
)
from pcc.unsafe import (
    atomic_load_i32,
    global_addr,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_eq,
    ptr_is_null,
)


py_decref = extern("py_decref", (c_ptr,), c_void)
pcc_gc_load_ptr_extern = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_gc_visit_object_slots = extern(
    "pcc_gc_visit_object_slots", (c_ptr, c_ptr, c_ptr), c_int64
)
py_list_new = extern("py_list_new", (c_int64,), c_ptr)
py_list_copy = extern("py_list_copy", (c_ptr,), c_ptr)
py_list_append = extern("py_list_append", (c_ptr, c_ptr), c_void)
py_list_get = extern("py_list_get", (c_ptr, c_int64), c_ptr)
py_list_len = extern("py_list_len", (c_ptr,), c_int64)
py_err_occurred = extern("py_err_occurred", (), c_int64)
pcc_stop_the_world = extern("pcc_stop_the_world", (), c_int64)
pcc_resume_world = extern("pcc_resume_world", (), c_void)
pcc_thread_owns_stopped_world = extern("pcc_thread_owns_stopped_world", (), c_int64)
pcc_thread_safepoint = extern("pcc_thread_safepoint", (), c_void)
pcc_gc_default_table_lock = extern("pcc_gc_default_table_lock", (), c_void)
pcc_gc_default_table_unlock = extern("pcc_gc_default_table_unlock", (), c_void)
pcc_gc_tracked_materialize = extern("pcc_gc_tracked_materialize", (), c_int64)
pcc_gc_tracked_dematerialize = extern("pcc_gc_tracked_dematerialize", (), c_void)


def _tracked_snapshot_enter() -> int:
    owns_world: int = 0
    if pcc_thread_owns_stopped_world() == 0:
        stopped: int = pcc_stop_the_world()
        while stopped != 0:
            pcc_thread_safepoint()
            stopped = pcc_stop_the_world()
        owns_world = 1
    pcc_gc_default_table_lock()
    # Flag-only tracking (GC0) builds the tracked list for the snapshot.
    pcc_gc_tracked_materialize()
    return owns_world


def _tracked_snapshot_leave(owns_world: int) -> None:
    pcc_gc_tracked_dematerialize()
    pcc_gc_default_table_unlock()
    if owns_world != 0:
        pcc_resume_world()


def _append_referent(out, child) -> None:
    if ptr_is_null(child):
        return
    py_list_append(out, child)


def _py_obj_gc_visit_append_slot(slot, role: int, ctx) -> None:
    if role != 3:
        child = pcc_gc_load_ptr_extern(null(), slot)
        _append_referent(ctx, child)


def _append_referents_to(o, out) -> None:
    pcc_gc_visit_object_slots(o, _py_obj_gc_visit_append_slot, out)


@c_abi_export("py_gc_get_referents")
def py_gc_get_referents(o):
    # A list's referents are its items in order. Another thread may change a
    # shared list, so they come from one transaction on it (py_list_copy)
    # rather than from visiting its slots unlocked.
    if (ptr_is_null(o) == 0 and is_tagged_int(o) == 0
            and load_i32(o, PYOBJECTHEADER_TYPE_TAG_OFFSET) == PY_TYPE_LIST):
        return py_list_copy(o)
    out = py_list_new(0)
    if ptr_is_null(out) != 0:
        return null()
    _append_referents_to(o, out)
    if py_err_occurred() != 0:
        py_decref(out)
        return null()
    return out


def _list_has_identical_item(lst, target) -> int:
    length: int = load_i64(lst, 16)
    items = load_ptr(lst, 32)
    i: int = 0
    while i < length:
        if ptr_eq(load_ptr(items, i * 8), target) != 0:
            return 1
        i = i + 1
    return 0


@c_abi_export("py_gc_get_objects")
def py_gc_get_objects():
    owns_world = _tracked_snapshot_enter()
    try:
        tracked = atomic_load_i32(global_addr("py_gc_tracked_count"), 0, "acquire")
        n = global_load_ptr("py_gc_head")
        out = py_list_new(tracked)
        if ptr_is_null(out) != 0:
            return null()
        while ptr_is_null(n) == 0:
            obj = load_ptr(n, 0)
            if ptr_is_null(obj) == 0:
                # A stopped releasing thread may have already dropped the
                # last owner or started clearing fields. Match collector
                # traversal: never resurrect or inspect that partial object.
                # Active finalizers carry a temporary positive owner and
                # clear DEALLOCATING; FINALIZED alone does not exclude them.
                flags: int = load_i32(obj, PYOBJECTHEADER_FLAGS_OFFSET)
                if (flags & PY_FLAG_GC_DEALLOCATING) == 0 and (
                    (flags & PY_FLAG_IMMORTAL) != 0
                    or load_i64(obj, PYOBJECTHEADER_REFCOUNT_OFFSET) > 0
                ):
                    py_list_append(out, obj)
                    if py_err_occurred() != 0:
                        py_decref(out)
                        return null()
            n = load_ptr(n, 32)
        return out
    finally:
        _tracked_snapshot_leave(owns_world)


@c_abi_export("py_gc_get_referrers")
def py_gc_get_referrers(target):
    # The snapshot owns its objects before callbacks run. A C tp_traverse
    # callback may re-enter GC and untrack a node, so do not carry a naked
    # tracked-list cursor through that callback.
    objects = py_gc_get_objects()
    if ptr_is_null(objects) != 0:
        return null()
    out = null()
    complete: int = 0
    try:
        out = py_list_new(0)
        if ptr_is_null(out) != 0:
            return null()
        count: int = py_list_len(objects)
        i: int = 0
        while i < count:
            obj = py_list_get(objects, i)
            if ptr_is_null(obj) != 0:
                return null()
            refs = null()
            try:
                refs = py_gc_get_referents(obj)
                if ptr_is_null(refs) != 0:
                    return null()
                if _list_has_identical_item(refs, target) != 0:
                    py_list_append(out, obj)
                    if py_err_occurred() != 0:
                        return null()
            finally:
                py_decref(refs)
                py_decref(obj)
            i = i + 1
        complete = 1
        return out
    finally:
        py_decref(objects)
        if complete == 0:
            py_decref(out)
