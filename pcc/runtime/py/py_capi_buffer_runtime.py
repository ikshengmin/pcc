"""pcc-Python owners for the no-libpython buffer/memoryview surface.

Replaces the PyObject_CheckBuffer / PyObject_GetBuffer / PyMemoryView_*
block of the removed py_capi_shim.c. The buffer probe follows whole views
to a borrowed bytes/bytearray leaf; PyBuffer_Release lives in py_capi_misc_runtime.py.

Py_buffer layout (CPython): buf@0, obj@8, len@16, itemsize@24,
readonly@32 (i32), ndim@36 (i32), format@40, shape@48, strides@56,
suboffsets@64, internal@72.
PccBufferMeta: shape@0 (i64), strides@8 (i64).
Bytes/bytearray payloads use the shared py_bytes_data_ptr accessor.
PyMemoryViewObject: base@16, owned Py_buffer allocation@24.

The owned allocation is 96 bytes: the public 80-byte Py_buffer followed by
inline shape@80 and strides@88 words.  Py_buffer.obj is a derived borrowed
alias of the memoryview's owned base reference; the GC visits base@16 and the
refresh hook rewrites obj/buf. The opaque internal@72 word stays NULL: the
one collector never moves the payload, so no exporter record exists.

Owned surface (stable C ABI names):

  PyObject_CheckBuffer, PyObject_GetBuffer, PyMemoryView_Check,
  PyMemoryView_FromObject, PyMemoryView_FromMemory

Public object type tags come from the generated ``py_abi_constants`` module.
Private buffer flags remain owned here:
  PyBUF_WRITABLE 0x0001, PyBUF_FORMAT 0x0004, PyBUF_ND 0x0008,
  PyBUF_STRIDES 0x0018, PyBUF_WRITE 0x0200
"""

__pcc_runtime_port__ = True

from pcc.runtime.py.py_abi_constants import (
    PYBYTEARRAYOBJECT_BYTE_LEN_OFFSET,
    PY_TYPE_BYTEARRAY,
    PY_TYPE_BYTES,
    PY_TYPE_MEMORYVIEW,
)

from pcc.extern import c_abi_typed_export, c_int32, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_cas_i64,
    atomic_load_i64,
    cstr,
    define_global_i32,
    define_global_ptr_null,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    int_to_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    memcpy,
    memset,
    null,
    ptr_add,
    ptr_is_null,
    ptr_to_int,
    stack_alloc,
    store_i32,
    store_i64,
    store_i8,
    store_ptr,
)

py_type_of = extern("pcc_py_type_of", (c_ptr,), c_int64)
py_bytes_new = extern("py_bytes_new", (c_ptr, c_int64), c_ptr)
py_bytes_data_ptr = extern("py_bytes_data_ptr", (c_ptr,), c_ptr)
py_memoryview_new = extern("py_memoryview_new", (c_ptr,), c_ptr)
py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
py_raise = extern("py_raise", (c_ptr,), c_void)
# py_raise increfs; a caller that created the exception must release it.
py_raise_owned = extern("py_raise_owned", (c_ptr,), c_void)
py_exc_new = extern("py_exc_new", (c_int64, c_ptr), c_ptr)
PyMem_Malloc = extern("PyMem_Malloc", (c_int64,), c_ptr)
PyMem_Free = extern("PyMem_Free", (c_ptr,), c_void)
pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
pcc_gc_load_ptr = extern("pcc_gc_load_ptr", (c_ptr, c_ptr), c_ptr)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_gc_memoryview_initialize_owned_buffer = extern(
    "pcc_gc_memoryview_initialize_owned_buffer", (c_ptr, c_ptr), c_int64
)
pcc_gc_memoryview_refresh_owned_buffer = extern(
    "pcc_gc_memoryview_refresh_owned_buffer", (c_ptr,), c_int64
)
pcc_gc_note_frame_enter_local_checked = extern(
    "pcc_gc_note_frame_enter_local_checked", (c_ptr, c_ptr), c_int64
)
pcc_gc_note_frame_leave_local = extern("pcc_gc_note_frame_leave_local", (c_ptr,), c_void)
pcc_thread_no_park_enter = extern("pcc_thread_no_park_enter", (), c_void)
pcc_thread_no_park_exit = extern("pcc_thread_no_park_exit", (), c_void)
pcc_gc_resolve_borrowed_root_slot_unlocked = extern(
    "pcc_gc_resolve_borrowed_root_slot_unlocked", (c_ptr, c_int64), c_ptr
)

define_global_ptr_null("pcc_capi_buffer_lease_head")

# Private temporary borrowed roots, never part of either public record.
_GET_ORIGINAL = 0
_GET_LEAF = 8
_GET_ROOT_BYTES = 16
_GET_UNSUPPORTED = -1
_GET_READONLY = -2
_GET_NO_ROOTS = -3
define_global_i32("pcc_capi_buffer_get_borrowed_map", -2)

# The public Py_buffer and its inline shape/stride stay exactly 96 bytes.
# Its opaque internal word stays NULL (no exporter record).
_OWNED_BUFFER_INTERNAL = 72
_OWNED_BUFFER_BYTES = 96


def _type_error(message) -> None:
    py_raise_owned(py_exc_new(3, message))  # PY_EXC_TYPEERROR


def _runtime_error(message) -> None:
    py_raise_owned(py_exc_new(7, message))  # PY_EXC_RUNTIMEERROR


def _value_error(message) -> None:
    py_raise_owned(py_exc_new(2, message))  # PY_EXC_VALUEERROR


def _buffer_data(obj, buf_ptr, len_ptr, ro_ptr) -> c_ptr:
    # Return the borrowed leaf that owns these bytes, or NULL on failure.
    # This is the existing data walk, not a root or an address lease.
    if ptr_is_null(obj) or is_tagged_int(obj):
        return null()
    tag: int = load_i32(obj, 8)
    if tag == PY_TYPE_BYTES or tag == PY_TYPE_BYTEARRAY:
        # GetBuffer's validation-only walk needs the terminal, not an address
        # or a length from before its record allocation.
        if not ptr_is_null(buf_ptr):
            store_ptr(buf_ptr, 0, py_bytes_data_ptr(obj))
            store_i64(len_ptr, 0, load_i64(obj, 16))
            store_i32(ro_ptr, 0, 1 if tag == PY_TYPE_BYTES else 0)
        return obj
    if tag == PY_TYPE_MEMORYVIEW:  # PY_TYPE_MEMORYVIEW
        base = pcc_gc_load_ptr(
            obj, ptr_add(obj, 16)
        )  # view->base
        return _buffer_data(base, buf_ptr, len_ptr, ro_ptr)
    return null()


def _bytearray_from_memory(data, length: int) -> c_ptr:
    if length < 0:
        length = 0
    # Preserve this C factory's allocation size; 48 is not the payload offset.
    total: int = 48 + length + 1
    obj = pcc_gc_alloc(total, PY_TYPE_BYTEARRAY, 0)  # PY_TYPE_BYTEARRAY
    if ptr_is_null(obj):
        return null()
    store_i64(obj, PYBYTEARRAYOBJECT_BYTE_LEN_OFFSET, length)
    payload = py_bytes_data_ptr(obj)
    if length > 0 and not ptr_is_null(data):
        memcpy(payload, data, length)
    store_i8(payload, length, 0)
    return obj


@c_abi_typed_export("PyObject_CheckBuffer", "i32", ("ptr",))
def PyObject_CheckBuffer(obj) -> int:
    buf_ptr = stack_alloc(8)
    len_ptr = stack_alloc(8)
    ro_ptr = stack_alloc(4)
    store_ptr(buf_ptr, 0, null())
    store_i64(len_ptr, 0, 0)
    store_i32(ro_ptr, 0, 1)
    if not ptr_is_null(_buffer_data(obj, buf_ptr, len_ptr, ro_ptr)):
        return 1
    return 0


def _buffer_get_open_roots(obj, flags: int, roots) -> int:
    # The C caller keeps its input alive. A whole view owns its immediate
    # base; no release/rebinding happens to an active view's chain here.
    # Caller has already entered no-park. Validate and initialize both
    # borrowed cells before that guard's first exit. Invalid native inputs
    # are never registered, and flag errors precede fallible registration.
    pcc_py_gc_minor_graph_lock()
    leaf = _buffer_data(obj, null(), null(), null())
    status: int = 0
    if ptr_is_null(leaf):
        status = _GET_UNSUPPORTED
    elif (flags & 0x0001) != 0 and load_i32(leaf, 8) == PY_TYPE_BYTES:
        status = _GET_READONLY
    else:
        store_ptr(roots, _GET_ORIGINAL, obj)
        store_ptr(roots, _GET_LEAF, leaf)
    pcc_py_gc_minor_graph_unlock()
    # The outer no-park still protects the initialized pair. Frame arena
    # allocation, when needed, stays outside the graph transaction.
    if status == 0:
        if pcc_gc_note_frame_enter_local_checked(
            global_addr("pcc_capi_buffer_get_borrowed_map"), roots
        ) != 1:
            store_ptr(roots, _GET_ORIGINAL, null())
            store_ptr(roots, _GET_LEAF, null())
            status = _GET_NO_ROOTS
        else:
            status = 1
    return status


def _buffer_get_close_roots(roots, registered: int) -> None:
    store_ptr(roots, _GET_ORIGINAL, null())
    store_ptr(roots, _GET_LEAF, null())
    if registered == 1:
        pcc_gc_note_frame_leave_local(roots)


@c_abi_typed_export("PyObject_GetBuffer", "i32", ("ptr", "ptr", "i32"))
def PyObject_GetBuffer(obj, view, flags: int) -> int:
    if ptr_is_null(view):
        _runtime_error(cstr("NULL Py_buffer"))
        return -1
    pcc_thread_no_park_enter()
    memset(view, 0, 80)
    roots = stack_alloc(_GET_ROOT_BYTES)
    memset(roots, 0, _GET_ROOT_BYTES)
    registered: int = _buffer_get_open_roots(obj, flags, roots)
    pcc_thread_no_park_exit()
    if registered == _GET_UNSUPPORTED:
        _type_error(cstr("object does not support buffer protocol"))
        return -1
    if registered == _GET_READONLY:
        _type_error(cstr("object is not writable"))
        return -1
    if registered == _GET_NO_ROOTS:
        _runtime_error(cstr("out of memory creating buffer view"))
        return -1
    meta = PyMem_Malloc(40)
    if ptr_is_null(meta):
        _buffer_get_close_roots(roots, registered)
        _runtime_error(cstr("out of memory creating buffer view"))
        return -1
    pcc_py_gc_minor_graph_lock()
    obj = pcc_gc_resolve_borrowed_root_slot_unlocked(roots, _GET_ORIGINAL)
    lease_owner = pcc_gc_resolve_borrowed_root_slot_unlocked(roots, _GET_LEAF)
    # Sample once, at registration, never across the metadata allocation.
    buf = py_bytes_data_ptr(lease_owner)
    length: int = load_i64(lease_owner, 16)
    readonly: int = 1 if load_i32(lease_owner, 8) == PY_TYPE_BYTES else 0
    store_i64(meta, 0, length)  # shape
    store_i64(meta, 8, 1)  # strides
    store_ptr(meta, 16, lease_owner)
    store_ptr(meta, 24, obj)
    store_ptr(meta, 32, global_load_ptr("pcc_capi_buffer_lease_head"))
    global_store_ptr("pcc_capi_buffer_lease_head", meta)
    store_ptr(view, 0, buf)  # buf
    store_ptr(view, 8, obj)  # obj
    store_i64(view, 16, length)  # len
    store_i64(view, 24, 1)  # itemsize
    store_i32(view, 32, readonly)  # readonly
    if (flags & 0x0008) != 0:  # PyBUF_ND
        store_i32(view, 36, 1)  # ndim
    else:
        store_i32(view, 36, 0)
    if (flags & 0x0004) != 0:  # PyBUF_FORMAT
        store_ptr(view, 40, cstr("B"))
    else:
        store_ptr(view, 40, null())
    if (flags & 0x0008) != 0:
        store_ptr(view, 48, meta)  # shape
        if (flags & 0x0018) != 0:  # PyBUF_STRIDES
            store_ptr(view, 56, ptr_add(meta, 8))  # strides
        else:
            store_ptr(view, 56, null())
    else:
        store_ptr(view, 48, null())
        store_ptr(view, 56, null())
    store_ptr(view, 64, null())  # suboffsets
    store_ptr(view, 72, meta)  # internal
    pcc_py_gc_minor_graph_unlock()
    py_incref(obj)  # The installed record pins the original across this call.
    _buffer_get_close_roots(roots, registered)
    return 0


@c_abi_typed_export("PyMemoryView_Check", "i32", ("ptr",))
def PyMemoryView_Check(obj) -> int:
    if ptr_is_null(obj) or is_tagged_int(obj):
        return 0
    if load_i32(obj, 8) == PY_TYPE_MEMORYVIEW:  # PY_TYPE_MEMORYVIEW
        return 1
    return 0


@c_abi_typed_export("PyMemoryView_FromObject", "ptr", ("ptr",))
def PyMemoryView_FromObject(obj) -> c_ptr:
    buf_ptr = stack_alloc(8)
    len_ptr = stack_alloc(8)
    ro_ptr = stack_alloc(4)
    store_ptr(buf_ptr, 0, null())
    store_i64(len_ptr, 0, 0)
    store_i32(ro_ptr, 0, 1)
    if ptr_is_null(_buffer_data(obj, buf_ptr, len_ptr, ro_ptr)):
        _type_error(cstr("object does not support buffer protocol"))
        return null()
    return py_memoryview_new(obj)


@c_abi_typed_export("PyMemoryView_FromMemory", "ptr", ("ptr", "i64", "i32"))
def PyMemoryView_FromMemory(mem, size: int, flags: int) -> c_ptr:
    if size < 0:
        _value_error(cstr("negative memoryview size"))
        return null()
    if ptr_is_null(mem) and size > 0:
        _value_error(cstr("NULL memoryview buffer"))
        return null()
    if (flags & 0x0200) != 0:  # PyBUF_WRITE
        base = _bytearray_from_memory(mem, size)
    else:
        base = py_bytes_new(mem, size)
    if ptr_is_null(base):
        _runtime_error(cstr("out of memory creating memoryview"))
        return null()
    view = py_memoryview_new(base)
    py_decref(base)
    return view


# --- pcc_PyMemoryView_GET_BASE ---------------------------------------


@c_abi_typed_export("pcc_memoryview_free_owned_buffer", "void", ("ptr",))
def pcc_memoryview_free_owned_buffer(buffer_view) -> None:
    # Only the memoryview-owned cache uses this record. PyObject_GetBuffer
    # has a different internal layout and must still use PyBuffer_Release.
    if ptr_is_null(buffer_view):
        return
    store_ptr(buffer_view, _OWNED_BUFFER_INTERNAL, null())
    PyMem_Free(buffer_view)


def _memoryview_owned_buffer(obj) -> c_ptr:
    bits: int = atomic_load_i64(
        obj, 24, "acquire"
    )
    if bits != 0:
        pcc_gc_memoryview_refresh_owned_buffer(obj)
        return int_to_ptr(bits)

    base = pcc_gc_load_ptr(
        obj, ptr_add(obj, 16)
    )
    if ptr_is_null(base):
        _runtime_error(cstr("memoryview has no base object"))
        return null()
    candidate = PyMem_Malloc(96)
    if ptr_is_null(candidate):
        _runtime_error(cstr("out of memory creating memoryview buffer"))
        return null()
    # Validation can fail before the initializer writes any public field.
    store_ptr(candidate, _OWNED_BUFFER_INTERNAL, null())
    if pcc_gc_memoryview_initialize_owned_buffer(obj, candidate) == 0:
        pcc_memoryview_free_owned_buffer(candidate)
        _type_error(cstr("memoryview base does not support buffer protocol"))
        return null()

    candidate_bits: int = ptr_to_int(candidate)
    installed: int = atomic_cas_i64(
        obj,
        24,
        0,
        candidate_bits,
        "acq_rel",
        "acquire",
    )
    if installed != 0:
        pcc_memoryview_free_owned_buffer(candidate)
        pcc_gc_memoryview_refresh_owned_buffer(obj)
        return int_to_ptr(installed)
    return candidate


@c_abi_typed_export("pcc_PyMemoryView_GET_BASE", "ptr", ("ptr",))
def pcc_PyMemoryView_GET_BASE(obj) -> c_ptr:
    if PyMemoryView_Check(obj) == 0:
        _type_error(cstr("expected memoryview"))
        return null()
    pcc_gc_memoryview_refresh_owned_buffer(obj)
    return pcc_gc_load_ptr(obj, ptr_add(obj, 16))  # view->base


# --- pcc_PyMemoryView_GET_BUFFER -------------------------------------


@c_abi_typed_export("pcc_PyMemoryView_GET_BUFFER", "ptr", ("ptr",))
def pcc_PyMemoryView_GET_BUFFER(obj) -> c_ptr:
    if PyMemoryView_Check(obj) == 0:
        _type_error(cstr("expected memoryview"))
        return null()
    return _memoryview_owned_buffer(obj)
