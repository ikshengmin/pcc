"""Common PASS-0/PASS-1/PASS-2 sweep kernel for tracing GC backends."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    abi_constant,
    global_addr,
    global_load_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    ptr_is_null,
    stack_alloc,
    store_i32,
    store_i64,
)


__pcc_freestanding__ = True


pcc_gc_object_is_address_pinned = extern("pcc_gc_object_is_address_pinned", (c_ptr,), c_int64)


pcc_py_gc_minor_graph_lock = extern(
    "pcc_py_gc_minor_graph_lock", (), c_void
)
pcc_py_gc_minor_graph_unlock = extern(
    "pcc_py_gc_minor_graph_unlock", (), c_void
)
pcc_gc_object_node_is_active = extern(
    "pcc_gc_object_node_is_active", (c_ptr,), c_int64
)
pcc_gc_seed_roots = extern("pcc_gc_seed_roots", (), c_void)
pcc_gc_drain_all_gray_unlocked = extern(
    "pcc_gc_drain_all_gray_unlocked", (), c_int64
)
pcc_gc_tracing_clear_unreachable = extern(
    "pcc_gc_tracing_clear_unreachable", (c_ptr,), c_void
)
pcc_gc_note_object_freeing = extern(
    "pcc_gc_note_object_freeing", (c_ptr,), c_void
)
pcc_gc_free_watch_begin = extern(
    "pcc_gc_free_watch_begin", (c_ptr, c_ptr), c_void
)
pcc_gc_free_watch_end = extern("pcc_gc_free_watch_end", (c_ptr,), c_int64)
# pcc_gc_free_watch_begin's record (layout owned by py_gc_backend.py).
GC_FREE_WATCH_BYTES = 24
pcc_gc_forwarding_index_find = extern(
    "pcc_gc_forwarding_index_find", (c_ptr,), c_ptr
)
pcc_refcount_forget = extern("pcc_refcount_forget", (c_ptr,), c_void)
py_gc_untrack = extern("py_gc_untrack", (c_ptr,), c_void)
py_user_del_dispatch = extern("py_user_del_dispatch", (c_ptr,), c_void)
pcc_capi_is_cext_type_tag = extern(
    "pcc_capi_is_cext_type_tag", (c_int64,), c_int64
)
pcc_capi_dealloc_cext_object = extern(
    "pcc_capi_dealloc_cext_object", (c_ptr, c_int64), c_int64
)

py_dealloc_int = extern("py_dealloc_int", (c_ptr,), c_void)
py_dealloc_float = extern("py_dealloc_float", (c_ptr,), c_void)
py_dealloc_str = extern("py_dealloc_str", (c_ptr,), c_void)
py_dealloc_list = extern("py_dealloc_list", (c_ptr,), c_void)
py_dealloc_tuple = extern("py_dealloc_tuple", (c_ptr,), c_void)
py_dealloc_dict = extern("py_dealloc_dict", (c_ptr,), c_void)
py_dealloc_set = extern("py_dealloc_set", (c_ptr,), c_void)
py_dealloc_func = extern("py_dealloc_func", (c_ptr,), c_void)
py_class_dealloc = extern("py_class_dealloc", (c_ptr,), c_void)
py_instance_dealloc = extern("py_instance_dealloc", (c_ptr,), c_void)
py_descriptor_dealloc = extern("py_descriptor_dealloc", (c_ptr,), c_void)
py_dealloc_exc = extern("py_dealloc_exc", (c_ptr,), c_void)
py_dealloc_file = extern("py_dealloc_file", (c_ptr,), c_void)
py_dealloc_iter = extern("py_dealloc_iter", (c_ptr,), c_void)
py_dealloc_gen = extern("py_dealloc_gen", (c_ptr,), c_void)
py_dealloc_coroutine = extern("py_dealloc_coroutine", (c_ptr,), c_void)
py_dealloc_continuation = extern("py_dealloc_continuation", (c_ptr,), c_void)
py_dealloc_task = extern("py_dealloc_task", (c_ptr,), c_void)
py_dealloc_memoryview = extern("py_dealloc_memoryview", (c_ptr,), c_void)
py_dealloc_weakref = extern("py_dealloc_weakref", (c_ptr,), c_void)
py_dealloc_thread_lock = extern("py_dealloc_thread_lock", (c_ptr,), c_void)
py_dealloc_thread_rlock = extern("py_dealloc_thread_rlock", (c_ptr,), c_void)
py_dealloc_thread_event = extern("py_dealloc_thread_event", (c_ptr,), c_void)
py_dealloc_thread_condition = extern(
    "py_dealloc_thread_condition", (c_ptr,), c_void
)
py_dealloc_thread_semaphore = extern(
    "py_dealloc_thread_semaphore", (c_ptr,), c_void
)
py_dealloc_thread_thread = extern("py_dealloc_thread_thread", (c_ptr,), c_void)
py_dealloc_virtual_thread = extern(
    "py_dealloc_virtual_thread", (c_ptr,), c_void
)
py_dealloc_vthread_channel = extern(
    "py_dealloc_vthread_channel", (c_ptr,), c_void
)
py_dealloc_generic = extern("py_dealloc_generic", (c_ptr,), c_void)
pcc_thread_world_stop_epoch_held = extern(
    "pcc_thread_world_stop_epoch_held", (), c_int64
)


@c_abi_export("pcc_gc_tracing_has_sweep_candidate")
def pcc_gc_tracing_has_sweep_candidate() -> i64:
    # Only a finished mark sets SWEEP_CANDIDATE (1024), and it commits after
    # flagging (pcc_gc_tracing_finish_commits). A walk that finds none stays
    # true until the next commit; schedulers ask once per step, so walking
    # every tracked object per question made a GC3 collection quadratic.
    pcc_py_gc_minor_graph_lock()
    commits: i64 = load_i64(global_addr("pcc_gc_tracing_finish_commits"), 0)
    if load_i64(global_addr("pcc_gc_tracing_no_candidate_commit"), 0) == commits:
        pcc_py_gc_minor_graph_unlock()
        return 0
    node = global_load_ptr("pcc_gc_object_head")
    while ptr_is_null(node) == 0:
        if pcc_gc_object_node_is_active(node) == 0:
            node = load_ptr(node, 16)
            continue
        obj = load_ptr(node, 0)
        flags: i64 = load_i32(obj, 12)
        if (flags & 1024) != 0:
            pcc_py_gc_minor_graph_unlock()
            return 1
        node = load_ptr(node, 16)
    # A finish that committed during the walk may have flagged nodes behind it.
    if load_i64(global_addr("pcc_gc_tracing_finish_commits"), 0) == commits:
        store_i64(global_addr("pcc_gc_tracing_no_candidate_commit"), 0, commits)
    pcc_py_gc_minor_graph_unlock()
    return 0


@c_abi_export("pcc_gc_tracing_finalize_unreachable")
def pcc_gc_tracing_finalize_unreachable(obj) -> None:
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return
    if pcc_gc_object_is_address_pinned(obj) != 0:
        return
    backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    flags: i64 = load_i32(obj, 12)
    delay_zpage_freeing_note: i64 = 0
    if backend == 4 and (flags & 65536) != 0:
        delay_zpage_freeing_note: i64 = 1

    # Publish logical death before a type-specific deallocator can safepoint.
    store_i32(obj, 12, flags | 524288)
    if delay_zpage_freeing_note == 0:
        pcc_gc_note_object_freeing(obj)
    pcc_refcount_forget(obj)
    py_gc_untrack(obj)
    store_i64(obj, 0, 0)
    watch = stack_alloc(GC_FREE_WATCH_BYTES)
    if delay_zpage_freeing_note != 0:
        pcc_gc_free_watch_begin(watch, obj)

    tag: i64 = load_i32(obj, 8)
    if tag == abi_constant("object.type.int"):
        py_dealloc_int(obj)
    elif tag == abi_constant("object.type.float"):
        py_dealloc_float(obj)
    elif tag == abi_constant("object.type.str"):
        py_dealloc_str(obj)
    elif tag == abi_constant("object.type.list"):
        py_dealloc_list(obj)
    elif tag == abi_constant("object.type.tuple"):
        py_dealloc_tuple(obj)
    elif tag == abi_constant("object.type.dict"):
        py_dealloc_dict(obj)
    elif tag == abi_constant("object.type.set"):
        py_dealloc_set(obj)
    elif tag == abi_constant("object.type.func"):
        py_dealloc_func(obj)
    elif tag == abi_constant("object.type.class"):
        py_class_dealloc(obj)
    elif (
        tag == abi_constant("object.type.instance")
        or tag == abi_constant("object.type.valuebox")
    ):
        py_instance_dealloc(obj)
    elif tag == abi_constant("object.type.exc"):
        py_dealloc_exc(obj)
    elif tag == abi_constant("object.type.file"):
        py_dealloc_file(obj)
    elif tag == abi_constant("object.type.iter"):
        py_dealloc_iter(obj)
    elif tag == abi_constant("object.type.gen"):
        py_dealloc_gen(obj)
    elif tag == abi_constant("object.type.coroutine"):
        py_dealloc_coroutine(obj)
    elif tag == abi_constant("object.type.continuation"):
        py_dealloc_continuation(obj)
    elif tag == abi_constant("object.type.memoryview"):
        py_dealloc_memoryview(obj)
    elif tag == abi_constant("object.type.weakref"):
        py_dealloc_weakref(obj)
    elif (
        tag == abi_constant("object.type.property")
        or tag == abi_constant("object.type.classmethod")
        or tag == abi_constant("object.type.staticmethod")
    ):
        py_descriptor_dealloc(obj)
    elif tag == abi_constant("object.type.thread_lock"):
        py_dealloc_thread_lock(obj)
    elif tag == abi_constant("object.type.thread_rlock"):
        py_dealloc_thread_rlock(obj)
    elif tag == abi_constant("object.type.thread_event"):
        py_dealloc_thread_event(obj)
    elif tag == abi_constant("object.type.thread_condition"):
        py_dealloc_thread_condition(obj)
    elif tag == abi_constant("object.type.thread_semaphore"):
        py_dealloc_thread_semaphore(obj)
    elif tag == abi_constant("object.type.thread"):
        py_dealloc_thread_thread(obj)
    elif tag == abi_constant("object.type.task"):
        py_dealloc_task(obj)
    elif tag == abi_constant("object.type.virtual_thread"):
        py_dealloc_virtual_thread(obj)
    elif tag == abi_constant("object.type.vthread_channel"):
        py_dealloc_vthread_channel(obj)
    else:
        if pcc_capi_dealloc_cext_object(obj, tag) == 0:
            if tag >= abi_constant("object.type.user_class_start"):
                py_instance_dealloc(obj)
            else:
                py_dealloc_generic(obj)
    if delay_zpage_freeing_note != 0:
        # The deallocator's free normally noted obj, and that note may have
        # handed its zpage bytes to another thread: note only when it did not.
        if pcc_gc_free_watch_end(watch) == 0:
            pcc_gc_note_object_freeing(obj)


@c_abi_export("pcc_gc_tracing_recheck_reachability_after_finalizers")
def pcc_gc_tracing_recheck_reachability_after_finalizers() -> None:
    # Refresh the candidate verdict from current roots.  This is also used
    # before PASS-0; keep the exported name for the existing runtime ABI.
    # After PASS-0, PEP 442 requires the same refresh to rescue resurrection
    # before any referent is cleared.
    pcc_gc_seed_roots()
    pcc_gc_drain_all_gray_unlocked()
    backend: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    node = global_load_ptr("pcc_gc_object_head")
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if pcc_gc_object_node_is_active(node) != 0:
            obj = load_ptr(node, 0)
            flags: i64 = load_i32(obj, 12)
            # A GC4 forwarding source is a physical shell, not another
            # finalizable object. Its payload and forwarding reference belong
            # to remap retirement. Test the edge, not the candidate flag: a
            # selected object that has not moved still needs ordinary sweeping.
            if (flags & 1024) != 0 and (
                (flags & 8) == 0
                or (
                    backend == 4
                    and ptr_is_null(pcc_gc_forwarding_index_find(obj)) == 0
                )
            ):
                store_i32(obj, 12, flags & ~1024)
        node = nxt


@c_abi_export("pcc_gc_tracing_sweep_unreachable")
def pcc_gc_tracing_sweep_unreachable(budget: i64) -> i64:
    if budget <= 0:
        return 0

    # A completed concurrent/incremental mark may leave a pending candidate
    # that is reachable at this sweep boundary.  Revalidate before invoking
    # user finalizers: rescuing it only after __del__ is already too late.
    # A mark finished in this same stopped world saw no mutator since, so
    # its verdicts stand. Keep the separate post-finalizer refresh for PEP
    # 442 resurrection.
    held: i64 = pcc_thread_world_stop_epoch_held()
    if held < 0 or held != load_i64(global_addr("pcc_gc_tracing_finish_stop_epoch"), 0):
        pcc_gc_tracing_recheck_reachability_after_finalizers()
    finalizer_runs: i64 = load_i64(global_addr("pcc_gc_user_finalizer_runs"), 0)

    # PASS 0: finalizers see intact fields.  C-extension tags are excluded
    # because their lifecycle is owned by the C-API bridge.
    node = global_load_ptr("pcc_gc_object_head")
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if pcc_gc_object_node_is_active(node) != 0:
            obj = load_ptr(node, 0)
            flags: i64 = load_i32(obj, 12)
            # Shell retirement owns this physical object, even when the
            # same-world shortcut omitted the reachability refresh.
            if (
                (flags & 1024) != 0
                and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 4
                and ptr_is_null(pcc_gc_forwarding_index_find(obj)) == 0
            ):
                flags = flags & ~1024
                store_i32(obj, 12, flags)
            if (flags & 1024) != 0 and (flags & 16384) == 0 and pcc_gc_object_is_address_pinned(obj) == 0:
                tag: i64 = load_i32(obj, 8)
                if pcc_capi_is_cext_type_tag(tag) == 0:
                    py_user_del_dispatch(obj)
        node = nxt

    # Only user code -- a finalizer -- can resurrect a candidate or change
    # the graph during this stopped-world sweep.
    if load_i64(global_addr("pcc_gc_user_finalizer_runs"), 0) != finalizer_runs:
        pcc_gc_tracing_recheck_reachability_after_finalizers()

    # PASS 1: clear at most budget candidates without freeing any sibling.
    cleared: i64 = 0
    node = global_load_ptr("pcc_gc_object_head")
    while ptr_is_null(node) == 0 and cleared < budget:
        nxt = load_ptr(node, 16)
        if pcc_gc_object_node_is_active(node) == 0:
            node = nxt
            continue
        obj = load_ptr(node, 0)
        flags = load_i32(obj, 12)
        # Re-evaluate eligibility after callback-capable finalizers.
        if (
            (flags & 1024) != 0
            and load_i32(global_addr("pcc_gc_backend_selected"), 0) == 4
            and ptr_is_null(pcc_gc_forwarding_index_find(obj)) == 0
        ):
            flags = flags & ~1024
            store_i32(obj, 12, flags)
        if (flags & 1024) != 0 and (flags & 16384) == 0 and pcc_gc_object_is_address_pinned(obj) == 0:
            pcc_gc_tracing_clear_unreachable(obj)
            cleared = cleared + 1
        node = nxt

    # PASS 2: finalize the same number of still-candidate objects.
    reclaimed: i64 = 0
    node = global_load_ptr("pcc_gc_object_head")
    while ptr_is_null(node) == 0:
        nxt = load_ptr(node, 16)
        if pcc_gc_object_node_is_active(node) == 0:
            node = nxt
            continue
        obj = load_ptr(node, 0)
        flags = load_i32(obj, 12)
        if (flags & 1024) != 0:
            # Slot/weakref cleanup may call user code; check again before
            # generic destruction, without charging a shell to the budget.
            if (
                load_i32(global_addr("pcc_gc_backend_selected"), 0) == 4
                and ptr_is_null(pcc_gc_forwarding_index_find(obj)) == 0
            ):
                store_i32(obj, 12, flags & ~1024)
            elif (flags & 16384) != 0 or pcc_gc_object_is_address_pinned(obj) != 0:
                store_i32(obj, 12, flags & ~1024)
            elif load_i32(obj, 8) == abi_constant("object.type.class"):
                # Its instances and subclasses own counted references and
                # read it while they are freed: it dies by reference count
                # when the last of them releases it.
                store_i32(obj, 12, flags & ~1024)
            elif reclaimed < cleared:
                pcc_gc_tracing_finalize_unreachable(obj)
                reclaimed = reclaimed + 1
        node = nxt
    return reclaimed
