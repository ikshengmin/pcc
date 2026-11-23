"""GC backend selector and telemetry for pcc-Python runtime archives.

Names are algorithmic, not project-branded:

0. refcount-cycle
1. incremental-tricolor
2. concurrent-mark-sweep
3. generational-minor-major
4. colored-relocating
"""

__pcc_runtime_port__ = True

from pcc.extern import c_abi_export, c_int32, c_int64, c_ptr, c_void, extern
from pcc.runtime.py.py_abi_constants import PY_TYPE_ELLIPSIS, PY_TYPE_NOTIMPLEMENTED
from pcc.runtime.py.py_abi_constants import PY_TYPE_BOOL, PY_TYPE_BYTEARRAY, PY_TYPE_BYTES, PY_TYPE_COMPLEX, PY_TYPE_CPY_HANDLE, PY_TYPE_FLOAT, PY_TYPE_INT, PY_TYPE_NONE, PY_TYPE_STR
from pcc.unsafe import (
    define_global_ptr_null,
    define_thread_local_ptr_null,
    atomic_cas_i64,
    atomic_load_i32,
    atomic_load_i64,
    atomic_rmw_i32,
    atomic_rmw_i64,
    atomic_store_i32,
    atomic_store_i64,
    cstr,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_i8,
    load_ptr,
    memset,
    memmove,
    free,
    malloc,
    null,
    ptr_add,
    ptr_diff,
    ptr_to_int,
    int_to_ptr,
    ptr_eq,
    ptr_is_null,
    stack_alloc,
    store_ptr,
    store_i64,
    store_i8,
    store_i32,
)

# Slot-role / visit-mode values, inlined as literals at every use site
# (module-level int constants get zeroed in stripped library .o builds):
#   _PY_OBJ_SLOT_OWNED = 1
#   _PY_OBJ_SLOT_BORROWED_TRACED = 2
#   _PY_OBJ_SLOT_BORROWED_UPDATE_ONLY = 3
#   _PY_OBJ_VISIT_TRACE = 1
#   _PY_OBJ_VISIT_PROMOTE = 2
#   _PY_OBJ_VISIT_UPDATE = 3
#   _PY_OBJ_VISIT_SUBTRACT = 4
#   _PY_OBJ_VISIT_CLEAR = 5
#   _PY_OBJ_VISIT_RELOCATE_COUNT = 6
#   _PY_OBJ_VISIT_RELOCATE_FROM = 7
#   _PY_OBJ_VISIT_RELOCATE_TO = 8

# Shared ABI entry points used by backend-level finalization.
pcc_gc_tracking_enter_flag_mode = extern("pcc_gc_tracking_enter_flag_mode", (), c_void)

pcc_gc_metric_shard = extern("pcc_gc_metric_shard", (), c_int64)


pcc_gc_alloc = extern("pcc_gc_alloc", (c_int64, c_int32, c_int32), c_ptr)
pcc_gc_object_index_find = extern("pcc_gc_object_index_find", (c_ptr,), c_ptr)

pcc_gc_object_index_remove = extern("pcc_gc_object_index_remove", (c_ptr,), c_ptr)
pcc_gc_managed_pointer_index_contains = extern(
    "pcc_gc_managed_pointer_index_contains", (c_ptr,), c_int64
)
pcc_gc_managed_pointer_index_seen = extern(
    "pcc_gc_managed_pointer_index_seen", (c_ptr,), c_int64
)
pcc_gc_object_index_seen = extern(
    "pcc_gc_object_index_seen", (c_ptr,), c_int64
)
pcc_gc_index_reclaim_retired = extern(
    "pcc_gc_index_reclaim_retired", (), c_int64
)
pcc_gc_granule_is_object_start = extern(
    "pcc_gc_granule_is_object_start", (c_ptr,), c_int64
)
pcc_gc_granule_object_publish = extern(
    "pcc_gc_granule_object_publish", (c_ptr,), c_int64
)
pcc_gc_granule_object_retire = extern(
    "pcc_gc_granule_object_retire", (c_ptr,), c_int64
)
pcc_allocator_object_retire_live = extern(
    "pcc_allocator_object_retire_live", (c_ptr,), c_int64
)
pcc_gc_managed_pointer_index_insert = extern(
    "pcc_gc_managed_pointer_index_insert", (c_ptr,), c_int64
)
pcc_gc_managed_pointer_index_remove = extern(
    "pcc_gc_managed_pointer_index_remove", (c_ptr,), c_int64
)
pcc_capi_is_type_object_value = extern(
    "pcc_capi_is_type_object_value", (c_ptr,), c_int64
)

pcc_gc_forwarding_index_find = extern("pcc_gc_forwarding_index_find", (c_ptr,), c_ptr)
pcc_gc_forwarding_index_insert = extern(
    "pcc_gc_forwarding_index_insert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_forwarding_index_remove = extern(
    "pcc_gc_forwarding_index_remove",
    (c_ptr,),
    c_ptr,
)
pcc_gc_forwarding_index_clear = extern("pcc_gc_forwarding_index_clear", (), c_void)
pcc_gc_forwarding_target_index_insert = extern(
    "pcc_gc_forwarding_target_index_insert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_forwarding_target_index_upsert = extern(
    "pcc_gc_forwarding_target_index_upsert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_forwarding_target_index_remove = extern(
    "pcc_gc_forwarding_target_index_remove",
    (c_ptr,),
    c_ptr,
)
pcc_gc_forwarding_target_index_clear = extern(
    "pcc_gc_forwarding_target_index_clear", (), c_void
)

pcc_gc_identity_index_find = extern("pcc_gc_identity_index_find", (c_ptr,), c_ptr)
pcc_gc_identity_index_insert = extern(
    "pcc_gc_identity_index_insert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_identity_index_remove = extern("pcc_gc_identity_index_remove", (c_ptr,), c_ptr)
pcc_gc_identity_index_clear = extern("pcc_gc_identity_index_clear", (), c_void)
_forwarding_head = extern("pcc_gc_forwarding_list_head", (), c_ptr)
_forwarding_find = extern("pcc_gc_forwarding_find", (c_ptr,), c_ptr)
_forwarding_target_exists = extern(
    "pcc_gc_forwarding_target_exists", (c_ptr,), c_int64
)
_forwarding_target_unlink = extern(
    "pcc_gc_forwarding_target_unlink", (c_ptr,), c_void
)
_forwarding_unlink_main = extern(
    "pcc_gc_forwarding_unlink_main", (c_ptr,), c_void
)
_forwarding_clear_all = extern("pcc_gc_forwarding_clear_all", (), c_void)
_identity_head = extern("pcc_gc_identity_list_head", (), c_ptr)
_identity_ensure = extern("pcc_gc_identity_ensure", (c_ptr,), c_ptr)
_identity_remove = extern("pcc_gc_identity_remove", (c_ptr,), c_void)
_identity_head_acquire = extern("pcc_gc_identity_list_head", (), c_ptr)
_identity_clear_all = extern("pcc_gc_identity_clear_all", (), c_void)
_install_forwarding_unlocked = extern(
    "pcc_gc_install_forwarding_unlocked", (c_ptr, c_ptr), c_int64
)
_trace_cursor = extern("pcc_gc_trace_cursor_load", (), c_ptr)
_backend3_young_head = extern("pcc_gc_backend3_young_list_head", (), c_ptr)
_object_node_size = extern("pcc_gc_object_node_size", (c_ptr,), c_int64)
_set_object_node_next = extern(
    "pcc_gc_object_node_set_next", (c_ptr, c_ptr), c_void
)
_object_node_freeing = extern("pcc_gc_object_node_freeing", (c_ptr,), c_int64)
_set_object_node_freeing = extern(
    "pcc_gc_object_node_set_freeing", (c_ptr, c_int64), c_void
)
_object_node_prev = extern("pcc_gc_object_node_prev", (c_ptr,), c_ptr)
_set_object_node_zpage = extern(
    "pcc_gc_object_node_set_zpage", (c_ptr, c_ptr), c_void
)
_object_node_young_next = extern(
    "pcc_gc_object_node_young_next", (c_ptr,), c_ptr
)
_object_node_young_prev = extern(
    "pcc_gc_object_node_young_prev", (c_ptr,), c_ptr
)
# pcc_allocator_object_cell_locate's answer (freestanding_allocator).
_CELL_LOCATE_SPAN = 0
_CELL_LOCATE_CELL = 8
_CELL_LOCATE_BYTES = 16
_object_node_release = extern("pcc_gc_object_node_release", (c_ptr,), c_void)
_unlink_object_node = extern("pcc_gc_object_node_unlink", (c_ptr,), c_void)
_backend3_young_unlink = extern(
    "pcc_gc_backend3_young_unlink", (c_ptr,), c_void
)
_live_bytes_subtract = extern("pcc_gc_live_bytes_subtract", (c_int64,), c_void)
_live_bytes_flush = extern("pcc_gc_live_bytes_flush", (), c_void)
_relocate_copy_supported_tag = extern(
    "pcc_gc_generational_oldify_supported_tag", (c_int64,), c_int64
)
_mark_forwarded_source_inactive = extern(
    "pcc_gc_generational_mark_forwarded_source_inactive", (c_ptr,), c_void
)
_generational_oldify_copy = extern(
    "pcc_gc_generational_oldify_copy", (c_ptr,), c_ptr
)
_colored_relocate_copy_supported_tag = extern(
    "pcc_gc_backend4_relocate_copy_supported_tag", (c_int64,), c_int64
)
_remap_referents = extern(
    "pcc_gc_backend4_remap_referents", (c_ptr,), c_void
)
_relocate_copy_payload = extern(
    "pcc_gc_relocate_copy_payload",
    (c_ptr, c_ptr, c_int64, c_int64),
    c_int64,
)
pcc_gc_relocate_copy = extern(
    "pcc_gc_relocate_copy", (c_ptr, c_int64), c_ptr
)
_backend4_select_relocation_pages = extern(
    "pcc_gc_backend4_select_relocation_pages", (c_int64,), c_int64
)
pcc_gc_select_relocation_set = extern(
    "pcc_gc_select_relocation_set", (c_int64,), c_int64
)
pcc_gc_backend4_evacuation_drain = extern(
    "pcc_gc_backend4_evacuation_drain", (c_int64,), c_int64
)
pcc_gc_backend4_evacuation_page_drain = extern(
    "pcc_gc_backend4_evacuation_page_drain", (c_int64,), c_int64
)
pcc_gc_backend4_try_zpage_alloc = extern(
    "pcc_gc_backend4_try_zpage_alloc", (c_int64, c_int64), c_ptr
)
_backend4_zpage_track_alloc = extern(
    "pcc_gc_backend4_zpage_track_alloc", (c_ptr, c_int64), c_ptr
)
_backend4_zpage_track_alloc_selected = extern(
    "pcc_gc_backend4_zpage_track_alloc_selected",
    (c_ptr, c_int64, c_ptr, c_ptr, c_int64, c_ptr, c_int64),
    c_ptr,
)
_backend4_active_page = extern(
    "pcc_gc_backend4_zpage_active_page", (c_int64, c_int64), c_ptr
)
_backend4_set_active_page = extern(
    "pcc_gc_backend4_zpage_set_active_page", (c_ptr,), c_void
)
_backend4_zpage_find_reusable_page_for_gen = extern(
    "pcc_gc_backend4_zpage_find_reusable_page_for_gen",
    (c_int64, c_int64),
    c_ptr,
)
_backend4_zpage_find_reusable_page = extern(
    "pcc_gc_backend4_zpage_find_reusable_page", (c_ptr, c_int64), c_ptr
)
_backend4_zpage_pop_free_page = extern(
    "pcc_gc_backend4_zpage_pop_free_page", (c_int64,), c_ptr
)
_backend4_zpage_reset = extern(
    "pcc_gc_backend4_zpage_reset", (c_ptr, c_ptr, c_int64), c_void
)
_backend4_zpage_node_alloc = extern(
    "pcc_gc_backend4_zpage_node_alloc", (), c_ptr
)
_backend4_zpage_node_plan_requires_prepare = extern(
    "pcc_gc_backend4_zpage_node_plan_requires_prepare", (), c_int64
)
_backend4_zpage_node_release = extern(
    "pcc_gc_backend4_zpage_node_release", (c_ptr,), c_void
)
_backend4_zpage_link_node = extern(
    "pcc_gc_backend4_zpage_link_node", (c_ptr,), c_void
)
_backend4_zpage_find_page_for_addr = extern(
    "pcc_gc_backend4_zpage_find_page_for_addr", (c_ptr, c_int64), c_ptr
)
_backend4_free_page_count_for_class = extern(
    "pcc_gc_backend4_free_page_count_for_class", (c_int64,), c_int64
)
_backend4_free_page_limit_for_class = extern(
    "pcc_gc_backend4_free_page_limit_for_class", (c_int64,), c_int64
)
_backend4_zpage_clear_reusable_state = extern(
    "pcc_gc_backend4_zpage_clear_reusable_state", (c_ptr,), c_void
)
_backend4_zpage_cache = extern(
    "pcc_gc_backend4_zpage_cache", (c_ptr,), c_void
)
_backend4_zpage_destroy = extern(
    "pcc_gc_backend4_zpage_destroy", (c_ptr,), c_void
)
_backend4_zpage_recycle = extern(
    "pcc_gc_backend4_zpage_recycle", (c_ptr,), c_void
)
_backend4_zpage_page_head = extern(
    "pcc_gc_backend4_zpage_page_head", (c_ptr,), c_ptr
)
_backend4_zpage_set_page_head = extern(
    "pcc_gc_backend4_zpage_set_page_head", (c_ptr, c_ptr), c_void
)
_backend4_zpage_unlink_node = extern(
    "pcc_gc_backend4_zpage_unlink_node", (c_ptr,), c_void
)
_backend4_zpage_find = extern(
    "pcc_gc_backend4_zpage_find", (c_ptr,), c_ptr
)
_backend4_zpage_unlink_page = extern(
    "pcc_gc_backend4_zpage_unlink_page", (c_ptr,), c_void
)
_backend4_zpage_find_owner_for_page = extern(
    "pcc_gc_backend4_zpage_find_owner_for_page", (c_ptr,), c_ptr
)
_backend4_zpage_remove_payload_spans = extern(
    "pcc_gc_backend4_zpage_remove_payload_spans", (c_ptr,), c_void
)
_backend4_zpage_remove_payload_span_base = extern(
    "pcc_gc_backend4_zpage_remove_payload_span_base",
    (c_ptr, c_ptr),
    c_int64,
)
_forwarding_remove = extern("pcc_gc_forwarding_remove", (c_ptr,), c_void)
_backend4_park_page = extern(
    "pcc_gc_backend4_park_page", (c_ptr,), c_void
)
_backend4_drain_parked_pages = extern(
    "pcc_gc_backend4_drain_parked_pages", (), c_void
)
_backend4_finish_retained_page_releases = extern(
    "pcc_gc_backend4_finish_retained_page_releases", (c_ptr,), c_void
)
_backend4_note_forwarding_removed_on_page = extern(
    "pcc_gc_backend4_note_forwarding_removed_on_page", (c_ptr,), c_void
)
_backend4_zpage_note_forwarding_removed = extern(
    "pcc_gc_backend4_zpage_note_forwarding_removed", (c_ptr,), c_void
)
pcc_gc_step = extern("pcc_gc_step", (c_int64,), c_int64)
pcc_gc_note_slot_write_barrier = extern(
    "pcc_gc_note_slot_write_barrier", (c_ptr, c_ptr, c_ptr), c_void
)
pcc_gc_note_write_barrier = extern(
    "pcc_gc_note_write_barrier", (c_ptr, c_ptr), c_void
)
_promote_young_if_known = extern(
    "pcc_gc_generational_promote_young_if_known", (c_ptr,), c_void
)
_promote_young_slot_mode = extern(
    "pcc_gc_generational_promote_owned_slot_mode",
    (c_ptr, c_int64, c_int64),
    c_void,
)
_promote_young_borrowed_slot_mode = extern(
    "pcc_gc_generational_promote_borrowed_slot_mode",
    (c_ptr, c_int64, c_int64),
    c_void,
)
_trace_referents_for_promotion_mode = extern(
    "pcc_gc_trace_referents_for_promotion_mode", (c_ptr, c_int64), c_void
)
_trace_referents_for_promotion = extern(
    "pcc_gc_trace_referents_for_promotion", (c_ptr,), c_void
)
pcc_gc_frame_index_insert = extern(
    "pcc_gc_frame_index_insert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_frame_index_find = extern("pcc_gc_frame_index_find", (c_ptr,), c_ptr)
pcc_gc_frame_index_replace = extern(
    "pcc_gc_frame_index_replace",
    (c_ptr, c_ptr),
    c_ptr,
)
pcc_gc_frame_index_remove = extern("pcc_gc_frame_index_remove", (c_ptr,), c_ptr)
pcc_gc_frame_node_tls_pool_drain = extern(
    "pcc_gc_frame_node_tls_pool_drain",
    (),
    c_void,
)
pcc_gc_scheduler_root_tls_cache_drain = extern(
    "pcc_gc_scheduler_root_tls_cache_drain",
    (),
    c_void,
)
pcc_gc_zpage_owner_index_find = extern(
    "pcc_gc_zpage_owner_index_find",
    (c_ptr,),
    c_ptr,
)
pcc_gc_zpage_owner_index_insert = extern(
    "pcc_gc_zpage_owner_index_insert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_zpage_owner_index_upsert = extern(
    "pcc_gc_zpage_owner_index_upsert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_zpage_owner_index_plan_capacity = extern(
    "pcc_gc_zpage_owner_index_plan_capacity", (c_int64,), c_int64
)
pcc_gc_zpage_owner_index_plan_commit = extern(
    "pcc_gc_zpage_owner_index_plan_commit",
    (c_ptr, c_int64, c_int64),
    c_int64,
)
pcc_gc_zpage_owner_index_remove = extern(
    "pcc_gc_zpage_owner_index_remove",
    (c_ptr,),
    c_ptr,
)
pcc_gc_zpage_page_index_find = extern(
    "pcc_gc_zpage_page_index_find",
    (c_ptr,),
    c_ptr,
)
pcc_gc_zpage_page_index_insert = extern(
    "pcc_gc_zpage_page_index_insert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_zpage_page_index_upsert = extern(
    "pcc_gc_zpage_page_index_upsert",
    (c_ptr, c_ptr),
    c_int64,
)
pcc_gc_zpage_page_index_remove = extern(
    "pcc_gc_zpage_page_index_remove",
    (c_ptr,),
    c_ptr,
)
pcc_gc_store_root_plan_init = extern(
    "pcc_gc_store_root_plan_init", (c_ptr, c_int64), c_void
)
pcc_gc_store_root_plan_commit_locked = extern(
    "pcc_gc_store_root_plan_commit_locked",
    (c_ptr, c_ptr, c_ptr),
    c_int64,
)
pcc_gc_store_root_plan_finish = extern(
    "pcc_gc_store_root_plan_finish", (c_ptr,), c_void
)
py_tls_exc_get = extern("py_tls_exc_get", (), c_ptr)
py_tls_exc_set = extern("py_tls_exc_set", (c_ptr,), c_void)
pcc_mutex_lock = extern("pcc_mutex_lock", (c_ptr,), c_int64)
pcc_mutex_unlock = extern("pcc_mutex_unlock", (c_ptr,), c_int64)
pcc_capi_is_cext_type_tag = extern("pcc_capi_is_cext_type_tag", (c_int64,), c_int64)
pcc_thread_enable_conservative_roots = extern("pcc_thread_enable_conservative_roots", (), c_void)
pcc_thread_live_count = extern("pcc_thread_live_count", (), c_int64)
pcc_threads_enabled = extern("pcc_threads_enabled", (), c_int64)
pcc_thread_no_park_depth = extern("pcc_thread_no_park_depth", (), c_int64)
pcc_thread_owns_stopped_world = extern(
    "pcc_thread_owns_stopped_world", (), c_int64
)
pcc_thread_world_lock_if_sole = extern(
    "pcc_thread_world_lock_if_sole", (), c_int64
)
pcc_thread_world_unlock = extern("pcc_thread_world_unlock", (), c_void)
pcc_py_gc_minor_graph_lock_depth = extern(
    "pcc_py_gc_minor_graph_lock_depth", (), c_int64
)
pcc_stop_the_world = extern("pcc_stop_the_world", (), c_int64)
pcc_resume_world = extern("pcc_resume_world", (), c_int64)
pcc_runtime_now_us = extern("pcc_platform_monotonic_us", (), c_int64)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_lock_for = extern(
    "pcc_py_gc_minor_graph_lock_for", (c_int64,), c_void
)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)
pcc_gc_scheduler_root_link_locked = extern(
    "pcc_gc_scheduler_root_link_locked", (c_ptr,), c_void
)
pcc_gc_scheduler_root_unlink_locked = extern(
    "pcc_gc_scheduler_root_unlink_locked", (c_ptr,), c_int64
)
pcc_gc_cycle_requested_store_release = extern(
    "pcc_gc_cycle_requested_store_release", (c_int64,), c_void
)
_py_visit_registered_root_slots = extern(
    "pcc_gc_visit_registered_root_slots",
    (c_int64, c_int64),
    c_int64,
)
_gray_mapped_roots = extern(
    "pcc_gc_gray_mapped_roots", (c_ptr, c_ptr, c_int64), c_int64
)
_rewrite_mapped_roots = extern(
    "pcc_gc_rewrite_mapped_roots", (c_ptr, c_ptr), c_int64
)
_mode_leases_freeze = extern("pcc_gc_mode_leases_freeze", (), c_int64)
_mode_leases_thaw = extern("pcc_gc_mode_leases_thaw", (), c_void)
_static_literal_record = extern("pcc_gc_static_literal_record", (c_ptr,), c_void)
_gc_object_is_known_no_lock = extern(
    "pcc_gc_object_is_known_no_lock", (c_ptr,), c_int64
)
_mark_root_gray_if_known = extern(
    "pcc_gc_mark_root_gray_if_known", (c_ptr,), c_void
)
_resolve_root_slot_unlocked = extern(
    "pcc_gc_resolve_root_slot_unlocked", (c_ptr, c_int64), c_ptr
)
_trace_referents = extern("pcc_gc_trace_referents", (c_ptr,), c_void)
_seed_roots = extern("pcc_gc_seed_roots", (), c_void)
_drain_all_gray_unlocked = extern(
    "pcc_gc_drain_all_gray_unlocked", (), c_int64
)
_begin_mark_cycle = extern("pcc_gc_begin_mark_cycle", (), c_void)
_finish_tracing_cycle = extern(
    "pcc_gc_finish_tracing_cycle", (c_int64, c_int64), c_int64
)
_tracing_cycle_epoch_advance_unlocked = extern(
    "pcc_gc_tracing_cycle_epoch_advance_unlocked", (), c_int64
)
_clear_unreachable = extern("pcc_gc_tracing_clear_unreachable", (c_ptr,), c_void)
_has_sweep_candidate = extern(
    "pcc_gc_tracing_has_sweep_candidate", (), c_int64
)
_sweep_unreachable = extern(
    "pcc_gc_tracing_sweep_unreachable", (c_int64,), c_int64
)
_init_config = extern("pcc_gc_config_ensure", (), c_int64)
# The object this thread noted freeing last: a skip of the repeated note
# is only valid for this thread's own immediately preceding free.
define_thread_local_ptr_null("pcc_gc_last_noted_free")
# This thread's innermost free watch (see pcc_gc_free_watch_begin).
define_thread_local_ptr_null("pcc_gc_free_watch")
# Free watch record, in the caller's frame (GC_FREE_WATCH_BYTES there).
_FREE_WATCH_PREVIOUS = 0
_FREE_WATCH_OBJECT = 8
_FREE_WATCH_NOTED = 16
_record_pause = extern(
    "pcc_gc_tracing_record_pause", (c_int64, c_int64), c_void
)
_stop_cms_worker = extern("pcc_gc_cms_stop_worker", (), c_int64)
_is_cms_worker = extern("pcc_gc_is_cms_worker", (), c_int64)
_step_tracing = extern("pcc_gc_tracing_step_cycle", (c_int64,), c_int64)
_step_incremental_concurrent = extern(
    "pcc_gc_incremental_concurrent_step", (c_int64,), c_int64
)





def _counter_global(metric: int):
    # The allocation count (0) and pin balance (4) are sharded; see
    # pcc_gc_metric_shards.
    if metric == 1:
        return global_addr("pcc_gc_metric_store")
    if metric == 2:
        return global_addr("pcc_gc_metric_load")
    if metric == 3:
        return global_addr("pcc_gc_metric_safepoint")
    if metric == 5:
        return global_addr("pcc_gc_metric_step")
    return global_addr("pcc_gc_metric_step")


def _counter_inc(metric: int, delta: int) -> None:
    if metric == 1:
        atomic_rmw_i64("add", global_addr("pcc_gc_metric_shards"),
                       pcc_gc_metric_shard() + 16, delta, "relaxed")
        return
    slot = _counter_global(metric)
    if metric < 0 or metric >= 5:
        store_i64(slot, 0, load_i64(slot, 0) + delta)
        return
    v: int = load_i32(slot, 0)
    store_i32(slot, 0, v + delta)


def _object_graph_lock() -> None:
    pcc_py_gc_minor_graph_lock()


def _object_graph_unlock() -> None:
    pcc_py_gc_minor_graph_unlock()


def _gc_tracks_objects() -> int:
    return pcc_gc_backend() != 0


pcc_gc_tracked_count_flush = extern("pcc_gc_tracked_count_flush", (), c_void)


# A remembered entry: owner @0, slot @8, next @16 and prev @24 in the set's
# list, then next @32 and prev @40 in its owner's chain, whose head is the
# owner's value in the remembered member index. While that index is exact
# (pcc_gc_remembered_member_exact), an owner's chain reaches all of its
# entries, so removing, counting or retargeting an owner's entries walks
# only them instead of the whole set; otherwise those walk the set list.
# Chains are read and written only while the index is exact: an entry
# linked or unlinked while it is not leaves the index tainted, so no chain
# is trusted again before the set is cleared.


# ---- Backend-4 remembered pages and ZPage cards ------------------------------
#
# Remembered slots are grouped two ways.  A *remembered page* groups them by
# their real 4 KiB address page: node = page key @0, slot count @8, next @16,
# one byte per 8-byte slot @24..535 (536 bytes).  A *ZPage card* groups them by
# owner position: a slot's card is its owner span offset (the owner's ZPage
# offset plus the slot's offset inside the owner, or the slot's registered
# payload-span offset) / 512, mod 64.  Each ZPage keeps 64 i32 card refcounts
# at page+120, and page+48 counts the cards whose refcount is non-zero.  Both
# are maintained on every remembered-set add, remove and retarget.


def _is_known_object(o) -> int:
    return _gc_object_is_known_no_lock(o)


def _pointer_is_managed_no_lock(o) -> int:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return 0
    if ptr_eq(o, global_load_ptr("py_None")) != 0:
        return 1
    if ptr_eq(o, global_load_ptr("py_NotImplemented")) != 0:
        return 1
    if ptr_eq(o, global_load_ptr("py_Ellipsis")) != 0:
        return 1
    if ptr_eq(o, global_load_ptr("py_True")) != 0:
        return 1
    if ptr_eq(o, global_load_ptr("py_False")) != 0:
        return 1
    # A granule hit is an exact positive only for a fully initialized LIVE
    # object-family slot.  Unknown, reserved, free, raw, large, foreign and
    # moving-arena/zpage addresses all continue through the exact/index/type/
    # forwarding chain below.
    if pcc_gc_granule_is_object_start(o) == 1:
        return 1
    # Ordering matters and is not arbitrary.  This is a disjunction of
    # side-effect-free lookups, so any order returns the same answer, but the
    # costs differ by an order of magnitude: the managed-pointer index is one
    # hash probe and is the case that actually hits, while
    # `pcc_capi_is_type_object_value` walks a linear list of every registered
    # builtin type object and almost always fails.  With the scan first, every
    # GC barrier in the program paid that walk before reaching the answer, and
    # it was the second hottest leaf in a self-hosted pcc1 compile.
    if pcc_gc_managed_pointer_index_contains(o) != 0:
        return 1
    # These indexes compare pointer values only.  Unknown raw C pointers are
    # never dereferenced while provenance is being decided.
    if ptr_is_null(pcc_gc_object_index_find(o)) == 0:
        return 1
    if pcc_capi_is_type_object_value(o) != 0:
        return 1
    if ptr_is_null(_forwarding_find(o)) == 0:
        return 1
    if _forwarding_target_exists(o) != 0:
        return 1
    return 0


@c_abi_export("pcc_gc_pointer_is_managed")
def pcc_gc_pointer_is_managed(o) -> int:
    # Answer the value-only cases before taking the object-graph lock.
    # `_pointer_is_managed_no_lock` returns 0 for exactly these inputs as its
    # first act, and both tests are pure bit checks on the value itself — they
    # read no shared state, so hoisting them cannot change the answer.
    #
    # This is the hot path: `_ptr_is_class`/`_ptr_is_instance` run this query
    # on every attribute access and method dispatch, and a tagged small int is
    # a very common argument there.  Those calls used to pay a full lock /
    # unlock pair plus the managed-pointer hash probe
    # (`pcc_gc_managed_pointer_find_slot` was the #1 leaf, 705 of ~10000
    # samples, when pcc1 compiles a real module) to learn something decidable
    # from the pointer bits alone.
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return 0
    # Hoisting the four immortal-singleton compares ahead of the granule probe
    # was measured and DENIED on 2026-09-06: on the cli_bootstrap ASM worker it
    # added 3.2% instructions (four extra compares on every probe) and saved
    # less, so singleton refcount traffic is too rare to pay for it here.
    # The allocator publishes LIVE with release ordering only after the object
    # header is complete.  Readers may therefore accept this exact positive
    # without the graph lock.
    if pcc_gc_granule_is_object_start(o) == 1:
        return 1
    # Objects outside the allocator's slabs -- every GC4 zpage object -- are
    # registered in the GC1-4 object index once tracked and in the
    # managed-pointer set before that. Both answer an exact positive without
    # the lock (pcc_gc_index_py_seen). Every other result takes the lock and
    # executes the complete historical provenance chain.
    if pcc_gc_object_index_seen(o) != 0:
        return 1
    if pcc_gc_managed_pointer_index_seen(o) != 0:
        return 1
    # The immortal singletons are in none of those indexes, so every probe
    # of None, True or False ended in the graph lock: a barrier asks about
    # each stored value, and four mutators storing them queued on the lock
    # for it. The locked chain answers 1 for them by the same five address
    # compares; make them here, after the probes a heap object answers (the
    # 2026-09-06 measurement kept them off that path), instead of under it.
    if (
        ptr_eq(o, global_load_ptr("py_None")) != 0
        or ptr_eq(o, global_load_ptr("py_True")) != 0
        or ptr_eq(o, global_load_ptr("py_False")) != 0
        or ptr_eq(o, global_load_ptr("py_NotImplemented")) != 0
        or ptr_eq(o, global_load_ptr("py_Ellipsis")) != 0
    ):
        return 1
    _object_graph_lock()
    managed: int = _pointer_is_managed_no_lock(o)
    _object_graph_unlock()
    return managed


@c_abi_export("pcc_gc_reclaim_retired_index_tables")
def pcc_gc_reclaim_retired_index_tables() -> int:
    """Free the index tables that rehashes retired, when no thread can hold one.

    That is when this thread is the only registered one (it keeps the world
    lock, which registration takes, across the free) or holds a stopped
    world, where every other thread is parked or exited and so outside the
    lock-free probes. Collections call it after they stop the world, and
    pcc_gc_alloc after a rehash in a single-threaded process. Returns the
    number freed, or -1 when another thread may still be probing.
    """
    if pcc_thread_world_lock_if_sole() != 0:
        freed: int = pcc_gc_index_reclaim_retired()
        pcc_thread_world_unlock()
        return freed
    if pcc_thread_owns_stopped_world() == 0:
        return -1
    return pcc_gc_index_reclaim_retired()


@c_abi_export("pcc_gc_reclaim_retired_index_tables_if_alone")
def pcc_gc_reclaim_retired_index_tables_if_alone() -> None:
    """After a registration or tracking: free retired tables in a one-thread process.

    The thread count read here is only the hint that keeps a multi-threaded
    process from taking the world lock on every allocation; the reclaim
    decides under that lock.
    """
    if ptr_is_null(global_load_ptr("pcc_py_gc_index_retired_tables")) != 0:
        return
    if load_i64(global_addr("pcc_live_thread_count_py"), 0) > 1:
        return
    pcc_gc_reclaim_retired_index_tables()


def _nonmoving_count_free_backend(backend: int) -> int:
    """1 when no object of the collector being fixed moves while a mutator
    runs, and none is counted.

    The four tracing collectors share one mutator protocol: an object keeps
    its address for as long as any running thread can name it, a load is a
    plain load, and a store shades its value during a mark. GC3 is the
    generational member (its reference is OCaml's minor heap) and promotes
    a survivor where it is. GC4 is the relocating member: it moves objects
    only inside a stopped world, rewrites every heap and root reference to
    them before the world resumes (pcc_gc_relocation_settle_stopped), and
    leaves in place whatever a thread's stack or registers name.
    """
    return 0


@c_abi_export("pcc_gc_fix_backend")
def pcc_gc_fix_backend() -> None:
    """Fix the collector at the first managed allocation.

    pcc_gc_alloc runs this, and so does registering a raw allocation (a big
    int, a C-API object, a builtin class or standard stream: malloc'd and
    flagged PY_FLAG_GC_MALLOC_ALLOC). The configuration is read first, so
    PCC_GC_BACKEND applies before the first managed object exists; from then
    on pcc_gc_set_backend refuses every other collector. Data-segment images
    (compiled literals, the ASCII character table) register without fixing
    it: no collector moves or frees them, and a C-ABI image's constructor
    registers its literals before main, ahead of a C program's own choice.
    """
    if atomic_load_i32(global_addr("pcc_gc_backend_frozen"), 0, "acquire") != 0:
        return
    _init_config()
    # Serialize the first allocation's freeze with the backend setter's
    # commit. A frozen allocator must never race a collector switch.
    _object_graph_lock()
    if atomic_load_i32(global_addr("pcc_gc_backend_frozen"), 0, "acquire") != 0:
        _object_graph_unlock()
        return
    # GC0 lists and instance fields lock themselves (py_list, and
    # pcc_gc_store_field_ptr with its readers). So do the nonmoving
    # count-free tracers: their marker scans lists and instances under the
    # same container lock (pcc_gc_trace_referents) and those stores shade
    # without the graph lock (pcc_gc_note_slot_write_barrier), so carriers
    # touching different containers no longer serialize on the graph lock.
    # A relocating collector keeps the graph lock for its copies and remaps,
    # and the provenance probe's checked lease path takes it inside a list
    # transaction.
    container_backend: int = load_i32(global_addr("pcc_gc_backend_selected"), 0)
    nonmoving: int = _nonmoving_count_free_backend(container_backend)
    if (
        load_i32(global_addr('pcc_gc_refcount_provenance_probe'), 0) == 0
    ):
        store_i32(global_addr("pcc_gc_list_container_mode"), 0, 1)
    # The nonmoving count-free tracers find a native entry's raw inputs by
    # their conservative stopped-world stack scans; no activation frame or
    # root binding is needed for them.
    if nonmoving != 0:
        store_i32(global_addr("pcc_gc_nonmoving_count_free"), 0, 1)
        # Their allocations register in batches (freestanding_gc_alloc_stage).
    # GC0 builds cycle candidates from TRACKED flags. Count-free GC1/2 keep
    # their independent ObjectNode graph for tracing and need tracked nodes
    # only for cold snapshots. Freeze both representations with this policy;
    # GC3/4 retain their existing tracking/relocation behavior.
    pcc_gc_tracking_enter_flag_mode()
    atomic_store_i32(global_addr("pcc_gc_backend_frozen"), 0, 1, "release")
    _object_graph_unlock()


@c_abi_export("pcc_gc_pointer_register")
def pcc_gc_pointer_register(o) -> int:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return -1
    # 262144 = PY_FLAG_GC_MALLOC_ALLOC: a raw managed allocation.
    if (
        atomic_load_i32(global_addr("pcc_gc_backend_frozen"), 0, "acquire") == 0
        and (load_i32(o, 12) & 262144) != 0
    ):
        pcc_gc_fix_backend()
    granule_result: int = pcc_gc_granule_object_publish(o)
    if granule_result < 0:
        return -1
    if granule_result > 0:
        return 0
    pcc_py_gc_minor_graph_lock_for(2)
    result: int = pcc_gc_managed_pointer_index_insert(o)
    _object_graph_unlock()
    # The insert may have grown the set (see pcc_gc_reclaim_retired_index_tables).
    pcc_gc_reclaim_retired_index_tables_if_alone()
    return result


@c_abi_export("pcc_gc_pointer_register_static_literal")
def pcc_gc_pointer_register_static_literal(o) -> int:
    """Register a compiler-created process-lifetime scalar image object."""
    result: int = pcc_gc_pointer_register(o)
    if result >= 0:
        _static_literal_record(o)
    return result


@c_abi_export("pcc_gc_pointer_unregister")
def pcc_gc_pointer_unregister(o) -> int:
    if ptr_is_null(o) != 0 or is_tagged_int(o) != 0:
        return 0
    granule_was_live: int = pcc_gc_granule_is_object_start(o)
    granule_result: int = pcc_gc_granule_object_retire(o)
    if granule_result < 0:
        return -1
    if granule_result > 0:
        # Constructor/error cleanup may retire a RESERVED/FREE object-family
        # slot after note_object_freeing conservatively inserted an exact key.
        # Do not let that key survive when the address returns to the freelist.
        if granule_was_live != 1:
            _object_graph_lock()
            pcc_gc_managed_pointer_index_remove(o)
            _object_graph_unlock()
        return 0
    _object_graph_lock()
    result: int = pcc_gc_managed_pointer_index_remove(o)
    _object_graph_unlock()
    return result


@c_abi_export("pcc_gc_object_is_known")
def pcc_gc_object_is_known(o) -> int:
    _object_graph_lock()
    known: int = _is_known_object(o)
    _object_graph_unlock()
    return known


@c_abi_export("pcc_gc_backend")
def pcc_gc_backend() -> int:
    return _init_config()


    # A span indexes the slots of an owner's payload for the store buffer.
    # Stores under the nonmoving protocol fill no store buffer, and a new
    # owner has no page node until its registration batch runs: the lock
    # was taken for every list, dict and frame to record nothing.


@c_abi_export("pcc_gc_backend4_zpage_unregister_owner_payload_span")
def pcc_gc_backend4_zpage_unregister_owner_payload_span(owner, base) -> int:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return -1
    if ptr_is_null(base) != 0:
        return -1
    _init_config()
    _object_graph_lock()
    removed: int = 0
    node = _backend4_zpage_find(owner)
    if ptr_is_null(node) == 0:
        removed = _backend4_zpage_remove_payload_span_base(node, base)
    _object_graph_unlock()
    return removed


@c_abi_export("pcc_gc_backend4_zpage_retarget_owner_payload_span")
def pcc_gc_backend4_zpage_retarget_owner_payload_span(
    owner, old_base, new_base, size_bytes: int
) -> int:
    if ptr_is_null(owner) != 0 or is_tagged_int(owner) != 0:
        return -1
    if ptr_is_null(old_base) != 0 or ptr_is_null(new_base) != 0:
        return -1
    if size_bytes <= 0:
        return -1
    _init_config()
    _object_graph_lock()
    offset: int = _backend4_zpage_retarget_owner_payload_span_locked(
        owner, old_base, new_base, size_bytes
    )
    _object_graph_unlock()
    return offset


def _backend4_zpage_retarget_owner_payload_span_locked(
    owner, old_base, new_base, size_bytes: int
) -> int:
    node = _backend4_zpage_find(owner)
    if ptr_is_null(node) != 0:
        return -1
    page = load_ptr(node, 8)
    if ptr_is_null(page) != 0:
        return -1
    span = load_ptr(node, 64)
    while ptr_is_null(span) == 0:
        if ptr_eq(load_ptr(span, 8), old_base) != 0:
            if ptr_eq(load_ptr(span, 32), page) == 0:
                return -1
            offset: int = load_i64(span, 24)
            if offset < 0:
                store_ptr(span, 8, new_base)
                store_i64(span, 16, size_bytes)
                return 0
            capacity: int = load_i64(page, 16)
            old_size: int = load_i64(span, 16)
            allocated: int = load_i64(page, 64)
            if size_bytes > capacity - offset or (
                size_bytes > old_size and allocated != offset + old_size
            ):
                overflow_old_size: int = old_size
                used: int = load_i64(page, 8)
                if used >= overflow_old_size:
                    store_i64(page, 8, used - overflow_old_size)
                else:
                    store_i64(page, 8, 0)
                store_ptr(span, 8, new_base)
                store_i64(span, 16, size_bytes)
                store_i64(span, 24, -1)
                return 0
            used: int = load_i64(page, 8)
            if size_bytes >= old_size:
                store_i64(page, 8, used + size_bytes - old_size)
            else:
                delta: int = old_size - size_bytes
                if used >= delta:
                    store_i64(page, 8, used - delta)
                else:
                    store_i64(page, 8, 0)
            store_ptr(span, 8, new_base)
            store_i64(span, 16, size_bytes)
            end: int = offset + size_bytes
            allocated: int = load_i64(page, 64)
            if allocated < end:
                store_i64(page, 64, end)
            return offset
        span = load_ptr(span, 40)
    return -1


    # Under the nonmoving protocol no store buffer entry or payload span
    # names the old array: there is nothing to move to the new one.


def _gc0_terminal_free_has_no_metadata(o) -> int:
    # Only already-terminal, structurally proven object-family allocations.
    # GC0 discards the moving/tracing object index before publishing its
    # atomic-refcount mode; the reserved transition sentinel excludes entry
    # during that teardown. Other collectors/origins and diagnostic modes
    # retain the full graph transaction.
    if load_i32(global_addr("pcc_gc_refcount_atomic"), 0) == 0:
        return 0
    if atomic_load_i64(global_addr("pcc_gc_foreign_lease_active"), 0, "acquire") < 0:
        return 0
    if ptr_is_null(_identity_head_acquire()) == 0:
        return 0
    if pcc_gc_granule_is_object_start(o) != 1:
        return 0
    if atomic_load_i64(o, 0, "acquire") != 0:
        return 0
    return 1 if (atomic_load_i32(o, 12, "acquire") & 524288) != 0 else 0


    # Target-death preflight established this stop and rejected every pinned
    # reverse source. Keep the inactive node until the existing free owner
    # consumes its MinorBlock/live or OBJECT/malloc allocation origin.


@c_abi_export("pcc_gc_free_object_memory")
def pcc_gc_free_object_memory(o) -> None:
    if ptr_is_null(o) != 0:
        return
    if is_tagged_int(o) != 0:
        return
    if _gc0_terminal_free_has_no_metadata(o) != 0:
        # The predicate just proved what pcc_gc_note_object_freeing and
        # pcc_gc_pointer_unregister would establish again for this object:
        # GC0's atomic counts, no lease, no identity list (nothing can add
        # a dying object to one) and a LIVE exact object cell. Their
        # answers for it are the free-watch note and one lifecycle CAS, so
        # take those directly instead of repeating the provenance probe
        # and the cell's radix validation.
        # Without an armed free watch ever (pcc_gc_free_watch_used), the
        # note's evidence below records nothing for this lifetime.
        if (load_i32(global_addr("pcc_gc_free_watch_used"), 0) != 0
                and ptr_eq(o, global_load_ptr("pcc_gc_last_noted_free")) == 0):
            watch = global_load_ptr("pcc_gc_free_watch")
            if ptr_is_null(watch) == 0 and ptr_eq(load_ptr(watch, _FREE_WATCH_OBJECT), o) != 0:
                store_i64(watch, _FREE_WATCH_NOTED, 1)
            global_store_ptr("pcc_gc_last_noted_free", o)
        if pcc_allocator_object_retire_live(o) == 0:
            # Not LIVE any more: the ordinary retirement decides.
            if pcc_gc_pointer_unregister(o) < 0:
                return
        free(o)
        return
    if pcc_gc_pointer_is_managed(o) == 0:
        return
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = _init_config()
    _object_graph_lock()
    # As in pcc_gc_note_object_freeing: only a target (8192) can be kept.
    lease_node = pcc_gc_object_index_find(o)
    if ptr_is_null(lease_node) == 0 and load_i64(lease_node, 80) != 0:
        _object_graph_unlock()
        return
    flags: int = 0
    _object_graph_unlock()
    flags = load_i32(o, 12)
    # Direct constructor cleanup and nested frees still need their note.
    # Do not carry the earlier ownership result across this unlock or
    # the note: public unregister retains its independent ownership guard.
    if ptr_eq(o, global_load_ptr("pcc_gc_last_noted_free")) == 0:
        pcc_gc_note_object_freeing(o)
    # Failed object-family retirement quarantines instead of freeing.
    if pcc_gc_pointer_unregister(o) < 0:
        return
    if (flags & 65536) != 0:
        return
    free(o)


@c_abi_export("pcc_gc_free_watch_begin")
def pcc_gc_free_watch_begin(record, o) -> None:
    """Watch for a freeing note of ``o`` until pcc_gc_free_watch_end.

    A backend-4 zpage object's freeing note runs after its deallocator, and
    the note releases its zpage bytes: from then on another thread may
    allocate at that address. The callers used to ask afterwards whether the
    address was still managed, which in a threaded program could be another
    thread's new object and noted it freeing. The watch tells them instead
    whether this death noted ``o``; while it has not, the bytes are still
    ``o``'s. Records nest (deaths inside a deallocator) through PREVIOUS.
    """
    # Set once and never cleared: from now on GC0's terminal frees keep
    # their free-watch evidence (pcc_gc_note_object_freeing).
    if load_i32(global_addr("pcc_gc_free_watch_used"), 0) == 0:
        atomic_store_i32(global_addr("pcc_gc_free_watch_used"), 0, 1, "release")
    store_ptr(record, _FREE_WATCH_PREVIOUS, global_load_ptr("pcc_gc_free_watch"))
    store_ptr(record, _FREE_WATCH_OBJECT, o)
    store_i64(record, _FREE_WATCH_NOTED, 0)
    global_store_ptr("pcc_gc_free_watch", record)


@c_abi_export("pcc_gc_free_watch_end")
def pcc_gc_free_watch_end(record) -> int:
    """Close ``record``; nonzero when a freeing note of its object ran."""
    previous = load_ptr(record, _FREE_WATCH_PREVIOUS)
    global_store_ptr("pcc_gc_free_watch", previous)
    noted: int = load_i64(record, _FREE_WATCH_NOTED)
    # A note marks only the innermost record. A watch directly inside a
    # watch of the same object (the collector's finalize around
    # py_instance_dealloc's own) hands its outcome out.
    if noted != 0 and ptr_is_null(previous) == 0:
        if ptr_eq(load_ptr(previous, _FREE_WATCH_OBJECT), load_ptr(record, _FREE_WATCH_OBJECT)) != 0:
            store_i64(previous, _FREE_WATCH_NOTED, 1)
    return noted


@c_abi_export("pcc_gc_note_object_freeing")
def pcc_gc_note_object_freeing(o) -> None:
    backend: int = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        backend = _init_config()
    if ptr_is_null(o) != 0:
        return
    # Only a forwarding target can be kept by an inactive source. Every
    # target install sets 8192 and GC3 never clears it, so an object without
    # it is no target: skip the extra graph hold and target-index probe.
    # GC1/2 can check pending scalar ownership in the metadata transaction
    # already required below. Keep this node under the same graph hold, and
    # reject a negative owner before publishing free-watch success.
    lease_node = null()
    if (load_i32(global_addr('pcc_gc_free_watch_used'), 0) == 0 and _gc0_terminal_free_has_no_metadata(o) != 0):
        # No free watch was ever armed (only backend 4's delayed zpage notes
        # arm one), and this lifetime has no metadata to retire: its note
        # records nothing, and its free reads no last-note evidence either
        # (pcc_gc_free_object_memory). Two thread-local accesses per free.
        return
    watch = global_load_ptr("pcc_gc_free_watch")
    if ptr_is_null(watch) == 0 and ptr_eq(load_ptr(watch, _FREE_WATCH_OBJECT), o) != 0:
        store_i64(watch, _FREE_WATCH_NOTED, 1)
    if _gc0_terminal_free_has_no_metadata(o) != 0:
        # Keep free-watch/last-note evidence even though this lifetime has no
        # identity or traced-object metadata to remove under the graph lock.
        global_store_ptr("pcc_gc_last_noted_free", o)
        return
    # The remap-finish struct is written only by the forwarding backends
    # (3/4): every writer below sits under that backend check, so on 0/1/2 it
    # is all-null at every exit and the six-way retirement fan-out is pure
    # per-free overhead.  Gate the fan-out, not the struct init.
    moving: int = 0
    finish = stack_alloc(48)
    store_ptr(finish, 0, null())
    store_ptr(finish, 8, null())
    store_ptr(finish, 16, null())
    store_ptr(finish, 24, null())
    store_ptr(finish, 32, null())
    store_ptr(finish, 40, null())
    _object_graph_lock()
    lease_node = pcc_gc_object_index_find(o)
    if ptr_is_null(lease_node) == 0 and load_i64(lease_node, 80) != 0:
        _object_graph_unlock()
        return
    if pcc_gc_granule_is_object_start(o) != 1:
        if pcc_gc_managed_pointer_index_insert(o) < 0:
            _object_graph_unlock()
            return
    _identity_remove(o)
    if _gc_tracks_objects() == 0:
        _object_graph_unlock()
        global_store_ptr("pcc_gc_last_noted_free", o)
        return
    # GC1/2's provenance and identity work does not mutate the object index.
    # Moving backends may detach forwarding metadata and need a fresh lookup.
    node = lease_node
    node = pcc_gc_object_index_find(o)
    if ptr_is_null(node) == 0:
        if _object_node_freeing(node) == 0:
            # Retire this mark-cycle work before publishing freeing, including
            # the minor-block branch that leaves the node linked. Clearing the
            # bit and the first-transition guard make repeated notifications
            # idempotent. Only this still-active node owns the gray work;
            # already retired forwarding-source nodes do not retire it twice.
            _live_bytes_subtract(_object_node_size(node))
        _set_object_node_freeing(node, 1)
        pcc_gc_object_index_remove(o)
        _unlink_object_node(node)
        _object_node_release(node)
        _object_graph_unlock()
        global_store_ptr("pcc_gc_last_noted_free", o)
        return
    last = global_load_ptr("pcc_gc_last_alloc")
    if ptr_eq(last, o) != 0:
        global_store_ptr("pcc_gc_last_alloc", null())
    _object_graph_unlock()
    global_store_ptr("pcc_gc_last_noted_free", o)


@c_abi_export("pcc_gc_note_store")
def pcc_gc_note_store() -> None:
    # Distinct container/root stores do not share the graph lock. Keep their
    # count alongside the existing per-carrier allocation/pin counters.
    atomic_rmw_i64("add", global_addr("pcc_gc_metric_shards"),
                   pcc_gc_metric_shard() + 16, 1, "relaxed")


@c_abi_export("pcc_gc_note_safepoint")
def pcc_gc_note_safepoint() -> None:
    _counter_inc(3, 1)


def _scheduler_root_node_alloc(slot):
    if ptr_is_null(slot) != 0:
        return null()
    # A scheduler-root node has a fourth word, the per-thread cache link of
    # freestanding_gc_root_registry.
    node = malloc(32)
    if ptr_is_null(node) != 0:
        return null()
    memset(node, 0, 32)
    store_ptr(node, 0, slot)
    return node


def _scheduler_root_node_free(node) -> None:
    if ptr_is_null(node) == 0:
        free(node)


def _scheduler_queue_entry_alloc(queue):
    entry = null()
    if ptr_is_null(queue) == 0:
        mutex = load_ptr(queue, 0)
        if ptr_is_null(mutex) == 0 and pcc_mutex_lock(mutex) == 0:
            entry = load_ptr(queue, 32)
            if ptr_is_null(entry) == 0:
                store_ptr(queue, 32, load_ptr(entry, 8))
                count: int = load_i64(queue, 40)
                if count > 0:
                    store_i64(queue, 40, count - 1)
            pcc_mutex_unlock(mutex)
    if ptr_is_null(entry) != 0:
        entry = malloc(24)
    if ptr_is_null(entry) == 0:
        memset(entry, 0, 24)
    return entry


def _scheduler_queue_entry_recycle(queue, entry) -> None:
    if ptr_is_null(entry) != 0:
        return
    memset(entry, 0, 24)
    if ptr_is_null(queue) != 0:
        free(entry)
        return
    mutex = load_ptr(queue, 0)
    if ptr_is_null(mutex) != 0 or pcc_mutex_lock(mutex) != 0:
        free(entry)
        return
    count: int = load_i64(queue, 40)
    # 4096 == C #define PCC_GC_SCHEDULER_QUEUE_ENTRY_POOL_LIMIT; inlined because a
    # module-level const emits a `.modvar.` global that is zeroed in the stripped
    # runtime-library .o build (see test_runtime_substrate_spike).
    if count >= 4096:
        pcc_mutex_unlock(mutex)
        free(entry)
        return
    store_ptr(entry, 8, load_ptr(queue, 32))
    store_ptr(queue, 32, entry)
    store_i64(queue, 40, count + 1)
    pcc_mutex_unlock(mutex)


def _scheduler_queue_entry_release(queue, entry) -> None:
    if ptr_is_null(entry) != 0:
        return
    clear_plan = stack_alloc(128)
    pcc_gc_store_root_plan_init(clear_plan, pcc_gc_backend())
    root_node = load_ptr(entry, 16)
    _object_graph_lock()
    pcc_gc_store_root_plan_commit_locked(clear_plan, entry, null())
    pcc_gc_scheduler_root_unlink_locked(root_node)
    store_ptr(entry, 16, null())
    _object_graph_unlock()
    if ptr_is_null(root_node) == 0:
        pcc_gc_cycle_requested_store_release(1)
        _scheduler_root_node_free(root_node)
    _scheduler_queue_entry_recycle(queue, entry)
    pcc_gc_store_root_plan_finish(clear_plan)


@c_abi_export("pcc_gc_scheduler_queue_push")
def pcc_gc_scheduler_queue_push(queue, value) -> int:
    if ptr_is_null(queue):
        return -1
    entry = _scheduler_queue_entry_alloc(queue)
    if ptr_is_null(entry):
        return -1
    root_node = _scheduler_root_node_alloc(entry)
    if ptr_is_null(root_node) != 0:
        _scheduler_queue_entry_recycle(queue, entry)
        return -1
    store_plan = stack_alloc(128)
    pcc_gc_store_root_plan_init(store_plan, pcc_gc_backend())
    _object_graph_lock()
    published: int = pcc_gc_store_root_plan_commit_locked(
        store_plan, entry, value
    )
    if published != 0:
        store_ptr(entry, 16, root_node)
        pcc_gc_scheduler_root_link_locked(root_node)
    _object_graph_unlock()
    if published != 0:
        pcc_gc_cycle_requested_store_release(1)
    if published == 0:
        _scheduler_root_node_free(root_node)
        _scheduler_queue_entry_recycle(queue, entry)
    pcc_gc_store_root_plan_finish(store_plan)
    if published == 0:
        return -1
    mutex = load_ptr(queue, 0)
    if pcc_mutex_lock(mutex) != 0:
        _scheduler_queue_entry_release(queue, entry)
        return -1
    tail = load_ptr(queue, 16)
    if ptr_is_null(tail):
        store_ptr(queue, 8, entry)
        store_ptr(queue, 16, entry)
    else:
        store_ptr(tail, 8, entry)
        store_ptr(queue, 16, entry)
    store_i64(queue, 24, load_i64(queue, 24) + 1)
    return pcc_mutex_unlock(mutex)


@c_abi_export("pcc_gc_scheduler_queue_pop_into")
def pcc_gc_scheduler_queue_pop_into(queue, out_slot) -> int:
    if ptr_is_null(queue):
        return -1
    mutex = load_ptr(queue, 0)
    if pcc_mutex_lock(mutex) != 0:
        return -1
    entry = load_ptr(queue, 8)
    if ptr_is_null(entry):
        pcc_mutex_unlock(mutex)
        return 0
    nxt = load_ptr(entry, 8)
    store_ptr(queue, 8, nxt)
    if ptr_is_null(nxt):
        store_ptr(queue, 16, null())
    store_i64(queue, 24, load_i64(queue, 24) - 1)
    pcc_mutex_unlock(mutex)
    backend: int = pcc_gc_backend()
    out_plan = stack_alloc(128)
    if ptr_is_null(out_slot) == 0:
        pcc_gc_store_root_plan_init(out_plan, backend)
    clear_plan = stack_alloc(128)
    pcc_gc_store_root_plan_init(clear_plan, backend)
    root_node = load_ptr(entry, 16)
    _object_graph_lock()
    value = load_ptr(entry, 0)
    if ptr_is_null(out_slot) == 0:
        pcc_gc_store_root_plan_commit_locked(
            out_plan, out_slot, value
        )
    pcc_gc_store_root_plan_commit_locked(clear_plan, entry, null())
    pcc_gc_scheduler_root_unlink_locked(root_node)
    store_ptr(entry, 16, null())
    _object_graph_unlock()
    if ptr_is_null(root_node) == 0:
        pcc_gc_cycle_requested_store_release(1)
        _scheduler_root_node_free(root_node)
    _scheduler_queue_entry_recycle(queue, entry)
    if ptr_is_null(out_slot) == 0:
        pcc_gc_store_root_plan_finish(out_plan)
    pcc_gc_store_root_plan_finish(clear_plan)
    return 1


pcc_gc_gray_worklist_unregister = extern(
    "pcc_gc_gray_worklist_unregister", (), c_void
)
pcc_gc_alloc_stage_thread_exit = extern(
    "pcc_gc_alloc_stage_thread_exit", (), c_void
)


@c_abi_export("pcc_gc_thread_unregister_buffers")
def pcc_gc_thread_unregister_buffers() -> None:
    pcc_gc_frame_node_tls_pool_drain()
    # After the exception root is retired: its node may be among them.
    pcc_gc_scheduler_root_tls_cache_drain()
    pcc_gc_gray_worklist_unregister()
    pcc_gc_alloc_stage_thread_exit()
    pcc_gc_tracked_count_flush()
    # Publish the batch of allocation debt the thread had not yet published.
    _live_bytes_flush()
    # Called on thread exit. The removed C runtime flushed and freed a
    # PER-THREAD backend-4 medium store-buffer state here. This runtime keeps the
    # backend-4 store buffer as GLOBAL state (see _store_buffer_medium_head;
    # there is no per-thread TLS buffer), so there is nothing per-thread to
    # flush or free on thread exit. This mirror MUST exist: pcc_threads.c is
    # always compiled into every archive variant and references this symbol,
    # but for PCC_RUNTIME_HIGH=py the archive uses py_gc_backend.py (not the
    # .c), so without this stub libpy_runtime_pcc_py.a fails to link with
    # `Undefined symbols: _pcc_gc_thread_unregister_buffers`, breaking every
    # pcc1 / high=py test. The freestanding frame registry does own a bounded
    # per-thread cache, and it is drained above before this backend-4 no-op.
    return
