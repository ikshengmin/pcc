"""Opt-in shared managed envelopes over the existing GC0 object/slot contract.

The owner is an INTERNAL tuple-shaped envelope, never a language value or a
foreign payload passed to a Python tag walker. Its exact owner-slot prefix is
followed by opaque metadata and scalar bytes. GC0 frees the allocator-recorded
span; collectors which may infer/copy tuple size are deliberately rejected.
No default runtime inventory imports or links this module.

Handles are separately allocated stable root cells. A root-registry node is
private bookkeeping, not object identity. All handles and descriptors are
trusted compiler/runtime inputs, with caller-serialized lifetime and access.
Only GC0, a registered carrier, and the explicitly selected native target are
admitted; concurrent mutator use, finalizers and moving collectors are unproven.
"""

__pcc_runtime_port__ = True
# Match the existing runtime-port ABI convention (py_obj.py): annotated int
# parameters/locals are native words here. Exact exported widths remain in
# c_abi_typed_export; all layout arithmetic is checked before addition/multiply.

from pcc.extern import (
    c_abi_export,
    c_abi_typed_export,
    c_int32,
    c_int64,
    c_ptr,
    c_void,
    extern,
)
from pcc.runtime.py.py_abi_constants import (
    PYOBJECTHEADER_FLAGS_OFFSET,
    PY_FLAG_GC_TRACKED,
    PYTUPLEOBJECT_ITEMS_OFFSET,
    PYTUPLEOBJECT_LEN_OFFSET,
    PY_TYPE_TUPLE,
)
from pcc.unsafe import (
    free,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    null,
    ptr_add,
    ptr_eq,
    ptr_is_null,
    stack_alloc,
    store_i64,
    store_ptr,
)

pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
pcc_gc_backend = extern("pcc_gc_backend", (), c_int64)
pcc_gc_publish_initialized = extern("pcc_gc_publish_initialized", (c_ptr,), c_void)
pcc_gc_store_ptr = extern("pcc_gc_store_ptr", (c_ptr, c_ptr, c_ptr), c_void)
py_gc_track = extern("py_gc_track", (c_ptr,), c_void)
py_gc_collect = extern("py_gc_collect", (), c_int64)
py_incref = extern("py_incref", (c_ptr,), c_void)
py_decref = extern("py_decref", (c_ptr,), c_void)
root_register = extern("pcc_gc_scheduler_root_register_handle", (c_ptr,), c_ptr)
root_unregister = extern("pcc_gc_scheduler_root_unregister_handle", (c_ptr,), c_void)

OK = 0
OOM = 1
OVERFLOW = 2
BAD_LAYOUT = 3
UNSUPPORTED = 4
# Descriptor is immutable, process-lifetime storage, not a GC reference.
D_VERSION = 0
D_FIXED_BYTES = 8
D_ELEMENT_BYTES = 16
D_ALIGNMENT = 24
D_FIXED_REFS = 32
D_ELEMENT_REFS = 40
# Stable handle cell versus private scheduler-root registration token.
H_OBJECT = 0
H_REGISTRATION = 8
HANDLE_BYTES = 16
# Opaque tail immediately after the exact tuple owner-slot prefix.
M_DESCRIPTOR = 0
M_COUNT = 8
M_BYTES = 16
M_DATA_OFFSET = 24
METADATA_BYTES = 32
# Checked allocator request ceiling; high-bit unsigned inputs fail closed.
MAX_BYTES = 9223372036854771664
MAX_REFS = 134217728
# layout scratch: computed payload bytes, owner slots, payload offset, total.
L_BYTES = 0
L_REFS = 8
L_DATA = 16
L_TOTAL = 24
LAYOUT_BYTES = 32


@c_abi_export("pcc_rt_managed_internal_layout")
def _layout(descriptor, count: int, output) -> int:
    if ptr_is_null(descriptor):
        return BAD_LAYOUT
    if count < 0:
        return OVERFLOW
    if load_i64(descriptor, D_VERSION) != 1:
        return BAD_LAYOUT
    alignment: int = load_i64(descriptor, D_ALIGNMENT)
    if alignment <= 0 or alignment > 16 or (alignment & (alignment - 1)) != 0:
        return BAD_LAYOUT
    fixed: int = load_i64(descriptor, D_FIXED_BYTES)
    stride: int = load_i64(descriptor, D_ELEMENT_BYTES)
    refs: int = load_i64(descriptor, D_FIXED_REFS)
    ref_stride: int = load_i64(descriptor, D_ELEMENT_REFS)
    if fixed < 0 or stride < 0 or refs < 0 or ref_stride < 0:
        return OVERFLOW
    if fixed > MAX_BYTES or refs > MAX_REFS:
        return OVERFLOW
    if stride != 0 and count > (MAX_BYTES - fixed) // stride:
        return OVERFLOW
    if ref_stride != 0 and count > (MAX_REFS - refs) // ref_stride:
        return OVERFLOW
    size: int = fixed + count * stride
    refs = refs + count * ref_stride
    offset: int = PYTUPLEOBJECT_ITEMS_OFFSET + refs * 8 + METADATA_BYTES
    offset = (offset + alignment - 1) & (0 - alignment)
    if size > MAX_BYTES - offset:
        return OVERFLOW
    store_i64(output, L_BYTES, size)
    store_i64(output, L_REFS, refs)
    store_i64(output, L_DATA, offset)
    store_i64(output, L_TOTAL, offset + size)
    return OK


@c_abi_export("pcc_rt_managed_internal_handle")
def _new_handle() -> c_ptr:
    handle = malloc(HANDLE_BYTES)
    if ptr_is_null(handle):
        return null()
    store_ptr(handle, H_OBJECT, null())
    token = root_register(ptr_add(handle, H_OBJECT))
    if ptr_is_null(token):
        free(handle)
        return null()
    store_ptr(handle, H_REGISTRATION, token)
    return handle


@c_abi_export("pcc_rt_managed_internal_metadata")
def _metadata(obj) -> c_ptr:
    return ptr_add(obj, PYTUPLEOBJECT_ITEMS_OFFSET + load_i64(obj, PYTUPLEOBJECT_LEN_OFFSET) * 8)


@c_abi_typed_export("pcc_rt_managed_alloc", "i32", ("ptr", "i64", "ptr"))
def pcc_rt_managed_alloc(descriptor, count: int, output) -> int:
    if ptr_is_null(output):
        return BAD_LAYOUT
    if pcc_gc_backend() != 0:
        return UNSUPPORTED
    layout = stack_alloc(LAYOUT_BYTES)
    status: int = _layout(descriptor, count, layout)
    if status != OK:
        return status
    handle = _new_handle()
    if ptr_is_null(handle):
        return OOM
    obj = pcc_gc_alloc(load_i64(layout, L_TOTAL), PY_TYPE_TUPLE, 0)
    if ptr_is_null(obj):
        root_unregister(load_ptr(handle, H_REGISTRATION))
        free(handle)
        return OOM
    # The allocator zeroed the complete extent. Publish this NEW owner into
    # its already registered root before tracking or any subsequent safepoint.
    store_ptr(handle, H_OBJECT, obj)
    store_i64(obj, PYTUPLEOBJECT_LEN_OFFSET, load_i64(layout, L_REFS))
    metadata = _metadata(obj)
    store_ptr(metadata, M_DESCRIPTOR, descriptor)
    store_i64(metadata, M_COUNT, count)
    store_i64(metadata, M_BYTES, load_i64(layout, L_BYTES))
    store_i64(metadata, M_DATA_OFFSET, load_i64(layout, L_DATA))
    # Even an initially empty reference vector may later form cycles.
    if load_i64(layout, L_REFS) != 0:
        py_gc_track(obj)
        if (load_i32(obj, PYOBJECTHEADER_FLAGS_OFFSET) & PY_FLAG_GC_TRACKED) == 0:
            pcc_rt_managed_drop(handle)
            return OOM
    pcc_gc_publish_initialized(obj)
    store_ptr(output, 0, handle)
    return OK


@c_abi_export("pcc_rt_managed_internal_clone_object")
def _clone_object(obj, output) -> int:
    if ptr_is_null(output):
        return BAD_LAYOUT
    if ptr_is_null(obj):
        store_ptr(output, 0, null())
        return OK
    handle = _new_handle()
    if ptr_is_null(handle):
        return OOM
    # Input is retained by the caller's live handle or owner field throughout.
    py_incref(obj)
    store_ptr(handle, H_OBJECT, obj)
    store_ptr(output, 0, handle)
    return OK


@c_abi_typed_export("pcc_rt_managed_clone", "i32", ("ptr", "ptr"))
def pcc_rt_managed_clone(handle, output) -> int:
    obj = null()
    if not ptr_is_null(handle):
        obj = load_ptr(handle, H_OBJECT)
    return _clone_object(obj, output)


@c_abi_export("pcc_rt_managed_drop")
def pcc_rt_managed_drop(handle) -> None:
    if ptr_is_null(handle):
        return
    obj = load_ptr(handle, H_OBJECT)
    store_ptr(handle, H_OBJECT, null())
    root_unregister(load_ptr(handle, H_REGISTRATION))
    free(handle)
    py_decref(obj)


@c_abi_export("pcc_rt_managed_data")
def pcc_rt_managed_data(handle) -> c_ptr:
    if ptr_is_null(handle):
        return null()
    obj = load_ptr(handle, H_OBJECT)
    return ptr_add(obj, load_i64(_metadata(obj), M_DATA_OFFSET))


@c_abi_export("pcc_rt_managed_descriptor")
def pcc_rt_managed_descriptor(handle) -> c_ptr:
    if ptr_is_null(handle):
        return null()
    return load_ptr(_metadata(load_ptr(handle, H_OBJECT)), M_DESCRIPTOR)


@c_abi_export("pcc_rt_managed_element_count")
def pcc_rt_managed_element_count(handle) -> int:
    if ptr_is_null(handle):
        return 0
    return load_i64(_metadata(load_ptr(handle, H_OBJECT)), M_COUNT)


@c_abi_export("pcc_rt_managed_payload_size")
def pcc_rt_managed_payload_size(handle) -> int:
    if ptr_is_null(handle):
        return 0
    return load_i64(_metadata(load_ptr(handle, H_OBJECT)), M_BYTES)


@c_abi_typed_export("pcc_rt_managed_identity_equal", "i32", ("ptr", "ptr"))
def pcc_rt_managed_identity_equal(left, right) -> int:
    a = null()
    b = null()
    if not ptr_is_null(left):
        a = load_ptr(left, H_OBJECT)
    if not ptr_is_null(right):
        b = load_ptr(right, H_OBJECT)
    return ptr_eq(a, b)


@c_abi_typed_export("pcc_rt_managed_ref_store", "i32", ("ptr", "i64", "ptr"))
def pcc_rt_managed_ref_store(handle, index: int, child) -> int:
    if ptr_is_null(handle):
        return BAD_LAYOUT
    obj = load_ptr(handle, H_OBJECT)
    if index < 0 or index >= load_i64(obj, PYTUPLEOBJECT_LEN_OFFSET):
        return BAD_LAYOUT
    value = null()
    if not ptr_is_null(child):
        value = load_ptr(child, H_OBJECT)
    pcc_gc_store_ptr(obj, ptr_add(obj, PYTUPLEOBJECT_ITEMS_OFFSET + index * 8), value)
    return OK


@c_abi_typed_export("pcc_rt_managed_ref_load", "i32", ("ptr", "i64", "ptr"))
def pcc_rt_managed_ref_load(handle, index: int, output) -> int:
    if ptr_is_null(handle) or ptr_is_null(output):
        return BAD_LAYOUT
    obj = load_ptr(handle, H_OBJECT)
    if index < 0 or index >= load_i64(obj, PYTUPLEOBJECT_LEN_OFFSET):
        return BAD_LAYOUT
    return _clone_object(load_ptr(obj, PYTUPLEOBJECT_ITEMS_OFFSET + index * 8), output)


@c_abi_export("pcc_rt_managed_collect")
def pcc_rt_managed_collect() -> int:
    if pcc_gc_backend() != 0:
        return -UNSUPPORTED
    return py_gc_collect()
