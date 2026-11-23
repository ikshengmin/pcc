"""Raw storage for the five production GC backends.

This definition-only module keeps collector state in the strict freestanding
kernel.  Managed GC APIs and telemetry formatting live in their existing
pcc-Python modules and reference these symbols through ``pcc.unsafe``.
"""

from pcc import i64
from pcc.unsafe import (
    define_global_i32,
    define_global_i64,
    define_global_i64_array,
    define_global_ptr_null,
)

__pcc_freestanding__ = True


# A layered callback stop may skip the pin walk only if the current window
# installed no candidate pin through the shared root-owner protocol.
define_global_i32("pcc_gc_callback_candidate_pin_dirty", 0)


# Backend-0 cycle collector and public gc-module policy.
define_global_i32("py_gc_enabled", 1)
# CPython 3.15: gc.get_threshold() == (2000, 10, 10).
define_global_i32("py_gc_threshold0", 2000)
define_global_i32("py_gc_threshold1", 10)
define_global_i32("py_gc_threshold2", 10)
define_global_i32("py_gc_freeze_count", 0)
# Set by the first managed allocation (pcc_gc_fix_backend): from then on the
# selected collector never changes (pcc_gc_set_backend refuses another).
define_global_i32("pcc_gc_backend_frozen", 0)
# 1 when lists take their own container lock instead of the graph lock: GC0
# without the refcount provenance probe. Set with pcc_gc_backend_frozen,
# before the first object exists, and never changed afterwards.
define_global_i32("pcc_gc_list_container_mode", 0)
# 1 once any thread has armed a free watch (pcc_gc_free_watch_begin); never
# cleared. Only backend 4's delayed zpage notes arm one, so under GC0-GC3 it
# stays 0 and a terminal free skips the watch's thread-local evidence.
define_global_i32("pcc_gc_free_watch_used", 0)
# Fixed with the collector: 1 for the nonmoving count-free GC1/GC2. They need
# no native activation frames for list entries (py_list_native_entry), and
# the container fast paths admit them beside GC0 (pcc_gc_try_init_ptr_locked).
define_global_i32("pcc_gc_nonmoving_count_free", 0)
# 1 for header-only tracking (GC0 and count-free GC1/2). Cold stopped-world
# views build nodes from the live objects carrying the flag; the nonmoving
# tracers keep their separate ObjectNode graph. Fixed with the backend.
define_global_i32("pcc_gc_tracking_flag_mode", 0)
# 1 while pcc_gc_tracked_materialize's nodes exist (stopped world, whole
# table held); track and untrack then keep the nodes exact.
define_global_i32("pcc_gc_tracking_materialized", 0)
# Generational cycle collection of the fused collector (GC0). An object
# tracked in flag mode also gets pcc_gc_tracking_young_bit (131072 on GC0,
# 0 elsewhere: the relocating collector uses that header bit for
# forwarding). A young collection builds its view from the objects carrying
# both flags (pcc_gc_tracking_young_view set); every view's teardown clears
# the bit, which promotes the survivors.
define_global_i32("pcc_gc_tracking_young_bit", 0)
define_global_i32("pcc_gc_tracking_young_view", 0)
define_global_i32("pcc_gc_backend0_young_request", 0)
define_global_i32("pcc_gc_backend0_young_collections", 0)
define_global_i64("pcc_gc_tracked_after_full", 0)
define_global_i64("pcc_gc_young_log_chunks", 0)
define_global_i32("pcc_gc_young_log_lost", 0)
# Full collection of the fused collector in bounded stops
# (freestanding_gc_backend0_incremental.py). pcc_gc_full_phase is the cycle's
# phase (0: none). pcc_gc_full_view_active is 1 while the cycle's nodes are in
# the tracked index: untrack then removes the object's node. Those nodes carry
# node bit 64 and belong to no stopped-world view. pcc_gc_full_marking routes
# the root visitors' marks to the cycle. pcc_gc_full_slice_pending says that
# an automatic-collection request came from the cycle's pacing.
# pcc_gc_full_slice_us bounds the work of one stop.
define_global_i32("pcc_gc_full_phase", 0)
define_global_i32("pcc_gc_full_view_active", 0)
define_global_i32("pcc_gc_full_marking", 0)
define_global_i32("pcc_gc_full_slice_pending", 0)
define_global_i64("pcc_gc_full_slice_us", 200)
define_global_i64("pcc_gc_full_cycles", 0)
define_global_i64("pcc_gc_full_slices", 0)
define_global_i64("pcc_gc_full_candidates_found", 0)
define_global_ptr_null("pcc_gc_full_state")
# The cycle's collector thread (the concurrent feature of the former GC2).
# With pcc_gc_full_concurrent set, a cycle that begins in a threaded program
# starts a thread (pcc_gc_full_worker is 1 while it lives) that does the
# cycle's passes with the world running; mutators then only assist, when
# pcc_gc_full_work_done is behind pcc_gc_full_work_due, the work their
# allocation asks for. pcc_gc_full_finished tells a mutator that the
# cycle's candidates wait for a young collection. pcc_gc_full_release_head
# lists the objects whose last reference the thread ended up holding: a
# mutator releases them, because that may run a finalizer.
define_global_i32("pcc_gc_full_concurrent", 0)
define_global_i32("pcc_gc_full_worker", 0)
define_global_i32("pcc_gc_full_finished", 0)
define_global_i64("pcc_gc_full_work_done", 0)
define_global_i64("pcc_gc_full_work_due", 0)
define_global_i64("pcc_gc_full_worker_batches", 0)
define_global_i64("pcc_gc_full_release_head", 0)
define_global_ptr_null("pcc_gc_full_worker_handle")
define_global_ptr_null("py_gc_head")
define_global_ptr_null("pcc_gc_deferred_node_free_head")
# Recycled 40-byte tracking nodes (freestanding_gc_tracking.py). Mutators
# keep lists per shard; the full-table owner temporarily merges them here.
# The atomic pool count is one shared reservation budget capped at 4096;
# collector-side deferred frees remain outside that reusable pool.
define_global_ptr_null("pcc_gc_tracked_node_pool")
define_global_i32("pcc_gc_tracked_node_pool_count", 0)
define_global_i64("pcc_gc_table_lock_owner_token", 0)
# Tracking mutators own one address-selected shard. The full-table owner
# temporarily joins those heads/pools into the legacy globals for collection
# and cold snapshots. One extra stride permits an explicitly aligned base.
define_global_i64("pcc_gc_table_lock_depth", 0)
define_global_i64_array(
    "pcc_gc_tracked_shards",
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
)
define_global_i32("pcc_gc_forwarding_population", 0)
# GC0 adds to and subtracts from the tracked count for every container it
# allocates and frees, from every thread. Whatever shared its cache line
# missed on each of those updates, and which words did depended on where
# the linker happened to place this module's data. Keep the count, and the
# growth baseline read with it, on lines of their own.
define_global_i32("py_gc_tracked_count", 0)
# Tracked objects left after the last automatic collection (GC0..GC3): the
# next is scheduled once tracking has grown by max(threshold0, this) since.
define_global_i64("pcc_gc_tracked_after_collect", 0)
define_global_i32("py_gc_collecting", 0)
# Collector-private candidate context; nonzero only under stopped-world GC0.
define_global_i32("pcc_gc_backend0_subset_active", 0)
define_global_ptr_null("pcc_gc_backend0_subset_pairs")
define_global_i64("pcc_gc_backend0_subset_count", 0)
define_global_ptr_null("py_gc_callbacks")
define_global_i32("py_gc_callbacks_firing", 0)

# Shared selector, accounting, pacing, and configuration state.
# The configuration words below are read by every thread on every reference
# count update, barrier and field read, and written only when the backend or
# the probe is chosen. Allocation accounting next to them (debt, live bytes,
# metrics) is written by every thread: sharing a cache line with it made
# each of these reads miss under concurrency. Keep them on lines of their own.
define_global_i32("pcc_gc_backend_selected", 0)
# pcc_gc_refcount_provenance_probe: whether the
# refcount hot path asks pcc_gc_pointer_is_managed before it touches a header.
# 0 trusts the caller (default on the non-moving collectors 0-2), 1 probes and
# counts misses in pcc_gc_unmanaged_refcount_ops (default on the relocating
# collectors 3-4), 2 probes, counts and reports the first miss on stderr, 3
# also aborts on that first miss so the crash report names the site.
# Starts at 1 so a refcount that runs before pcc_gc_config_ensure has read
# PCC_GC_REFCOUNT_PROVENANCE_PROBE keeps the historical check.
define_global_i32("pcc_gc_refcount_provenance_probe", 1)
# 1 while py_incref/py_decref may update an ordinary object's count in place:
# backend 0, no provenance probe and the single-threaded kernel (a plain
# load/store instead of the kernel call).  Set wherever the backend or the
# probe is chosen; 0 before configuration keeps every call on the full path.
define_global_i32("pcc_gc_refcount_fast", 0)
# Backend 0 without the provenance probe: ordinary objects take inline
# atomic reference-count updates (any number of threads), and only a
# count that would reach zero takes the full release path.
define_global_i32("pcc_gc_refcount_atomic", 0)
# Backend 0 without the provenance probe: pcc_gc_pin/pcc_gc_unpin return at
# once. GC0 never moves an object and its cycle collector reads no pin; only
# the tracing and relocating collectors (GC1-4) consult the pin bit. Set
# with pcc_gc_refcount_atomic; 0 before configuration keeps the full path.
define_global_i32("pcc_gc_pins_inert", 0)
# Collectors 0-3 without the provenance probe: an address lease guards
# nothing there (none moves an object, and none can be replaced once an
# object exists), so pcc_gc_foreign_lease_acquire answers 0 before reading
# its slot. Slot calls read this word inline instead of calling. Set with
# the selected backend; 0 before configuration keeps every lease call.
define_global_i32("pcc_gc_leases_inert", 0)
# Effective ownership mode, derived only from the selected collector:
# GC0 counts references; GC1-4 trace. The exported ABI name is retained.
# Configuration runs before the first managed allocation publishes a header.
define_global_i32("pcc_gc_backend4_no_refcounts", 0)
define_global_i32("pcc_gc_config_initialized", 0)
# Conservative before configuration; moving work publishers enable before
# exposing an edge/set entry and refresh after retirement under the graph lock.
define_global_i32("pcc_gc_read_barrier_enabled", 1)
define_global_i32("pcc_gc_backend0_frame_roots_enabled", 0)
# 1 while compiled frames register nothing: the configuration is parsed,
# backend 0 is selected and its frame roots are off. Republished wherever
# one of those three words is stored, so a frame entry and the AArch64 short
# path load one word instead of three.
define_global_i32("pcc_gc_frame_registry_inactive", 0)
# pcc_gc_unmanaged_refcount_ops.  i64 because it
# counts every refcount operation that missed provenance, which on a long-lived
# service is not bounded by anything an i32 covers.
define_global_i64("pcc_gc_unmanaged_refcount_ops", 0)
define_global_i32("pcc_gc_refcount_provenance_probe_reported", 0)
define_global_i32("pcc_gc_metric_store", 0)
define_global_i32("pcc_gc_metric_load", 0)
define_global_i32("pcc_gc_metric_safepoint", 0)
# Recorded collector-call totals can exceed 2**31 during one compiler link.
# These are independent scalar symbols, not offsets within a packed layout.
define_global_i64("pcc_gc_metric_step", 0)
define_global_i64("pcc_gc_metric_max_pause_us", 0)
define_global_i64("pcc_gc_metric_pause_count", 0)
define_global_i64("pcc_gc_metric_pause_sum_us", 0)
define_global_i32("pcc_gc_metric_pause_hist0", 0)
define_global_i32("pcc_gc_metric_pause_hist1", 0)
define_global_i32("pcc_gc_metric_pause_hist2", 0)
define_global_i32("pcc_gc_metric_pause_hist3", 0)
# Complete collection-owned stopped-world windows, separate from inclusive
# step/call timing. These are recorded before the collector resumes mutators.
define_global_i64("pcc_gc_metric_collection_stw_count", 0)
define_global_i64("pcc_gc_metric_collection_stw_sum_us", 0)
define_global_i64("pcc_gc_metric_collection_stw_max_us", 0)
define_global_i64("pcc_gc_metric_collection_stw_hist0", 0)
define_global_i64("pcc_gc_metric_collection_stw_hist1", 0)
define_global_i64("pcc_gc_metric_collection_stw_hist2", 0)
define_global_i64("pcc_gc_metric_collection_stw_hist3", 0)
# Completed local sweep phases; only candidate timing excludes its entry lock wait.
define_global_i64("pcc_gc_metric_sweep_candidate_calls", 0)
define_global_i64("pcc_gc_metric_sweep_candidate_nodes", 0)
define_global_i64("pcc_gc_metric_sweep_candidate_work", 0)
define_global_i64("pcc_gc_metric_sweep_candidate_sum_us", 0)
define_global_i64("pcc_gc_metric_sweep_pass0_calls", 0)
define_global_i64("pcc_gc_metric_sweep_pass0_nodes", 0)
define_global_i64("pcc_gc_metric_sweep_pass0_work", 0)
define_global_i64("pcc_gc_metric_sweep_pass0_sum_us", 0)
define_global_i64("pcc_gc_metric_sweep_pass1_calls", 0)
define_global_i64("pcc_gc_metric_sweep_pass1_nodes", 0)
define_global_i64("pcc_gc_metric_sweep_pass1_work", 0)
define_global_i64("pcc_gc_metric_sweep_pass1_sum_us", 0)
define_global_i64("pcc_gc_metric_sweep_pass2_calls", 0)
define_global_i64("pcc_gc_metric_sweep_pass2_nodes", 0)
define_global_i64("pcc_gc_metric_sweep_pass2_work", 0)
define_global_i64("pcc_gc_metric_sweep_pass2_sum_us", 0)
define_global_i64("pcc_gc_metric_sweep_recheck_calls", 0)
define_global_i64("pcc_gc_metric_sweep_recheck_nodes", 0)
define_global_i64("pcc_gc_metric_sweep_recheck_work", 0)
define_global_i64("pcc_gc_metric_sweep_recheck_sum_us", 0)
# Completed contended outer graph-lock acquisitions, classified by call site.
define_global_i64("pcc_gc_metric_graph_wait_other_count", 0)
define_global_i64("pcc_gc_metric_graph_wait_other_sum_us", 0)
define_global_i64("pcc_gc_metric_graph_wait_other_max_us", 0)
define_global_i64("pcc_gc_metric_graph_wait_barrier_count", 0)
define_global_i64("pcc_gc_metric_graph_wait_barrier_sum_us", 0)
define_global_i64("pcc_gc_metric_graph_wait_barrier_max_us", 0)
define_global_i64("pcc_gc_metric_graph_wait_allocation_count", 0)
define_global_i64("pcc_gc_metric_graph_wait_allocation_sum_us", 0)
define_global_i64("pcc_gc_metric_graph_wait_allocation_max_us", 0)
define_global_i64("pcc_gc_metric_graph_wait_store_count", 0)
define_global_i64("pcc_gc_metric_graph_wait_store_sum_us", 0)
define_global_i64("pcc_gc_metric_graph_wait_store_max_us", 0)
# Byte accounting must cover a heap and allocation debt beyond 2 GiB.
# The public byte-count APIs are i64; retain that width in raw state too.
define_global_i64("pcc_gc_debt_bytes", 0)
define_global_i32("pcc_gc_backend4_auto_step_pending", 0)
# Set by pcc_gc_relocate_copy around its allocation: a GC4 copy target
# comes from pcc_allocator_alloc_object_outside_evacuation.
# A finished mark waiting for mutator-owned cleanup, distinct from a moving
# collector's pending generation/relocation work (which must not block marking).
# GC1/GC2's collector thread traced a cycle's gray work and leaves the final
# remark to a mutator, which finishes the mark and sweeps it in one stopped
# world, so the sweep keeps the remark's verdict.
define_global_i64("pcc_gc_live_bytes", 0)
# Bytes the last completed tracing mark found reachable (Go's heap marked):
# the pacing threshold grows with it, not with garbage awaiting its sweep.
define_global_i32("pcc_gc_pause", 1000)
define_global_i32("pcc_gc_stepmul", 10000)
define_global_i64("pcc_gc_debt_threshold_override", 0)
define_global_i32("pcc_gc_explicit_collect_active", 0)
define_global_i32("pcc_gc_gray_count", 0)

# Backend-2 concurrent and backend-3 generational state.
define_global_i32("pcc_gc_minor_heap_size", 1048576)
define_global_i32("pcc_gc_minor_alloc_max", 256)
define_global_i32("pcc_gc_minor_allocations", 0)
define_global_i32("pcc_gc_minor_collections", 0)
define_global_i32("pcc_gc_minor_bytes", 0)
define_global_i32("pcc_gc_cms_worker_starts", 0)
define_global_i32("pcc_gc_cms_queue_pushes", 0)
define_global_i32("pcc_gc_cms_worker_drains", 0)
define_global_i32("pcc_gc_cms_mutator_assists", 0)
define_global_i32("pcc_gc_cms_worker_traces", 0)
define_global_i32("pcc_gc_minor_arena_refills", 0)
define_global_i32("pcc_gc_minor_arena_bumps", 0)
define_global_i32("pcc_gc_minor_arena_fallbacks", 0)
define_global_i32("pcc_gc_cms_worker_stops", 0)
define_global_i32("pcc_gc_cms_wb_flushes", 0)

# Backend-4 relocation, page, store-buffer, and remembered-set metrics.
define_global_i32("pcc_gc_relocation_forwards", 0)
define_global_i32("pcc_gc_relocation_barrier_forwards", 0)
define_global_i32("pcc_gc_relocation_pin_rejects", 0)

# Raw linked structures and root registries shared by all five backends.
define_global_ptr_null("pcc_gc_last_alloc")
define_global_ptr_null("pcc_gc_identity_head")
define_global_ptr_null("pcc_gc_backend4_reset_object_cursor")
define_global_i64("pcc_gc_object_list_revision", 0)
# Which mark bitmap of each object-slab GC page the current or next mark
# sets (freestanding_gc_page_marks): 0 or 1.
define_global_i64("pcc_gc_page_mark_epoch_word", 0)
# Count of pages whose recycle was deferred (flag at page+104) while a
# dealloc trash cascade was active; lets the post-drain sweep no-op in O(1)
# when nothing deferred instead of walking the live page list per cascade.
# GC4's object slabs being evacuated: nodes {GC page, next}. Separate from
# the logical-zpage list, whose readers use the zpage layout.
# Exact evacuation_page_find answer, shared only under the graph lock or
# without threads; invalidated by every reseed_page_revision list mutation.
# The remembered pages by page address, and whether the pages and that index
# still cover every remembered slot (an allocation failure ends both).
define_global_ptr_null("pcc_gc_object_node_free_head")
define_global_i32("pcc_gc_object_node_free_count", 0)
define_global_i32("pcc_gc_mark_active", 0)
define_global_i32("pcc_gc_cycle_requested", 0)
# The finish commit at which a walk found no sweep candidate; -1: none yet.
# The finish commit at which GC4 last selected relocation pages; -1: none.
# GC1/GC2 automatic collections leave proven-dead plain objects to the
# collector thread: allowed while such a sweep runs its stopped world, state
# 1 while deferred objects remain, the array the sweep recorded them in, its
# length and the next entry to free, the classes it kept (retired later in a
# stop) and the objects the thread freed.
# The nodes a count-free GC1-GC3 mark's finish found white, their count
# (-1: the list could not grow and is not used), the mapped capacity, and
# the finish commit that filled them (-1: none; the sweep retires it).
# The stopped-world epoch the last mark finished in (-1: the world ran).
# The stopped-world epoch the current mark's roots were seeded in. A final
# remark in that same stop already has those roots.
# Finalizers (__del__, generator close) that ran user code; a sweep
# rechecks reachability after PASS 0 only when this moved.
define_global_i64("pcc_gc_user_finalizer_runs", 0)
define_global_i32("pcc_gc_root_count", 0)
define_global_ptr_null("pcc_gc_root_slots")
define_global_ptr_null("pcc_gc_frame_head")
# Start of the linked precise stack-map table, published by a table-enabled
# executable's main through pcc_gc_register_precise_stackmap_table.
define_global_ptr_null("pcc_gc_precise_stackmap_table")
# Mapped managed objects collected by the step-3 differential stack walk.
define_global_ptr_null("pcc_gc_stack_diff_values")
define_global_i64("pcc_gc_stack_diff_count", 0)
define_global_i64("pcc_gc_stack_diff_capacity", 0)
# Address index of the published table: (start, end, function offset)
# triples sorted by start; count -1 marks a table that failed validation.
define_global_ptr_null("pcc_gc_stackmap_index")
define_global_i64("pcc_gc_stackmap_index_count", 0)
define_global_i64("pcc_gc_stackmap_location_base", 0)
# PCC_GC_STACKMAP_ROOTS, read once: -1 unread, 1 the stack walk roots the
# frames of a published table, 2 the same with a report.
define_global_i32("pcc_gc_stackmap_roots_setting", -1)
# The final pcc_gc_stackmap_roots_mode answer for mapped frame entries:
# 0 no valid table yet (they register), 1 the stack walk roots them. A
# validated index stays valid and a published table that fails validation
# aborts, so a published 1 never changes.
define_global_i32("pcc_gc_stackmap_walk_state", 0)
# Every task registers and unregisters its continuation and scheduler
# roots: these three words are written under the graph lock by every
# thread. The stack-map words above are read at each mapped frame entry and
# the scan cursors below belong to collections. Keep the registry heads on
# lines of their own.
define_global_ptr_null("pcc_gc_continuation_root_head")
define_global_ptr_null("pcc_gc_scheduler_root_head")
define_global_i64("pcc_gc_root_registry_revision", 0)
define_global_i64("pcc_gc_backend3_frame_root_scan_slot", -1)
define_global_ptr_null("pcc_gc_backend3_frame_root_scan_cursor")
define_global_ptr_null("pcc_gc_backend3_continuation_root_scan_cursor")
# Nonzero while a GC3 step runs with another mutator outside an application
# gate: oldification then promotes young objects in place instead of copying.
define_global_i32("pcc_gc_backend3_oldify_suspended", 0)
# Single-source compiler proof: 0 unproved, 1 admitted, 2 permanently revoked.
define_global_i64("pcc_gc_backend3_scheduler_root_scan_slot", -1)
define_global_ptr_null("pcc_gc_backend3_scheduler_root_scan_cursor")
define_global_ptr_null("pcc_gc_object_head")
define_global_ptr_null("pcc_gc_trace_cursor")
# 1 from the moment a forwarding edge is installed until a pass has rewritten
# every heap and root reference (pcc_gc_backend4_remap_and_retire_unlocked).
# No world resumes while it is set (pcc_gc_relocation_settle_stopped): a
# running thread loads a slot without asking whether its value moved.
define_global_i32("pcc_gc_backend4_remap_owed", 0)
define_global_ptr_null("pcc_gc_backend3_young_head")
define_global_ptr_null("pcc_gc_backend3_promotion_head")
define_global_ptr_null("pcc_gc_backend3_promotion_tail")
