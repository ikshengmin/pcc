"""Build-selected managed-publication pressure, serviced only at legal gates.

The owned builder includes this member and injects its call sites only when
PCC_RUNTIME_GC_STRESS_EVERY is positive. Ordinary archives contain neither.
Counters distinguish requested, serviced, completed and deferred work.
"""

from pcc import i64
from pcc.extern import c_abi_export, c_int32, c_int64, c_void, extern
from pcc.unsafe import (
    atomic_cas_i32,
    atomic_load_i64,
    atomic_rmw_i64,
    atomic_store_i32,
    atomic_store_i64,
    define_global_i32,
    define_global_i64,
    define_thread_local_i32,
    global_addr,
    load_i32,
    load_i64,
    store_i32,
    unsigned_rem_i64,
)

__pcc_freestanding__ = True

# The source transform binds this to the receipt's positive build setting.
_PRESSURE_EVERY: i64 = 0
_PRESSURE_LIMIT: i64 = 9223372036854775807

define_global_i64("pcc_gc_pressure_publications", 0)
define_global_i64("pcc_gc_pressure_requests", 0)
define_global_i64("pcc_gc_pressure_serviced", 0)
define_global_i64("pcc_gc_pressure_completed", 0)
define_global_i64("pcc_gc_pressure_deferred", 0)
define_global_i64("pcc_gc_pressure_incomplete", 0)
define_global_i32("pcc_gc_pressure_active", 0)
define_thread_local_i32("pcc_gc_pressure_owner", 0)
define_thread_local_i32("pcc_gc_pressure_image_depth", 0)

pcc_gc_collect = extern("pcc_gc_collect", (c_int32,), c_int64)
pcc_gc_sweep_owed = extern("pcc_gc_sweep_owed", (), c_int64)
pcc_thread_gc_pressure_safe = extern("pcc_thread_gc_pressure_safe", (), c_int64)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)


@c_abi_export("pcc_gc_pressure_image_enter")
def pcc_gc_pressure_image_enter() -> None:
    slot = global_addr("pcc_gc_pressure_image_depth")
    depth: i64 = load_i32(slot, 0)
    if depth < 0 or depth >= 2147483647:
        pcc_platform_abort()
        return
    store_i32(slot, 0, depth + 1)


@c_abi_export("pcc_gc_pressure_image_leave")
def pcc_gc_pressure_image_leave() -> None:
    slot = global_addr("pcc_gc_pressure_image_depth")
    depth: i64 = load_i32(slot, 0)
    if depth <= 0:
        pcc_platform_abort()
        return
    store_i32(slot, 0, depth - 1)


@c_abi_export("pcc_gc_pressure_note_publication")
def pcc_gc_pressure_note_publication() -> None:
    # Scratch mallocs never pass object publication. Compiler/ASCII image
    # registration is separately suppressed; re-registration is not counted.
    if load_i32(global_addr("pcc_gc_pressure_image_depth"), 0) != 0:
        return
    if _PRESSURE_EVERY <= 0:
        pcc_platform_abort()
        return
    previous: i64 = atomic_rmw_i64(
        "add", global_addr("pcc_gc_pressure_publications"), 0, 1, "relaxed"
    )
    if previous < 0 or previous == _PRESSURE_LIMIT:
        pcc_platform_abort()
        return
    if unsigned_rem_i64(previous + 1, _PRESSURE_EVERY) == 0:
        requested: i64 = atomic_rmw_i64(
            "add", global_addr("pcc_gc_pressure_requests"), 0, 1, "release"
        )
        if requested < 0 or requested == _PRESSURE_LIMIT:
            pcc_platform_abort()


@c_abi_export("pcc_gc_pressure_collection_completed")
def pcc_gc_pressure_collection_completed() -> None:
    # Only actual completed full collections can satisfy a pressure poll.
    # Reentrant rejection, OOM and an unfinished trace never call this hook.
    if load_i32(global_addr("pcc_gc_pressure_owner"), 0) != 0:
        previous: i64 = atomic_rmw_i64(
            "add", global_addr("pcc_gc_pressure_completed"), 0, 1, "release"
        )
        if previous < 0 or previous == _PRESSURE_LIMIT:
            pcc_platform_abort()


@c_abi_export("pcc_gc_pressure_tracing_completed")
def pcc_gc_pressure_tracing_completed(start_commits: i64, had_sweep: i64) -> None:
    if load_i32(global_addr("pcc_gc_mark_active"), 0) != 0:
        return
    if pcc_gc_sweep_owed() != 0:
        return
    if had_sweep == 0 and load_i64(global_addr("pcc_gc_tracing_finish_commits"), 0) == start_commits:
        return
    pcc_gc_pressure_collection_completed()


@c_abi_export("pcc_gc_pressure_allowed")
def _pressure_allowed() -> i64:
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) == 0:
        return 0
    if load_i32(global_addr("pcc_gc_backend_frozen"), 0) == 0:
        return 0
    if load_i32(global_addr("py_gc_enabled"), 0) == 0:
        return 0
    if load_i32(global_addr("py_gc_threshold0"), 0) <= 0:
        return 0
    if load_i32(global_addr("py_gc_collecting"), 0) != 0:
        return 0
    if load_i32(global_addr("pcc_gc_explicit_collect_active"), 0) != 0:
        return 0
    return pcc_thread_gc_pressure_safe()


@c_abi_export("pcc_gc_pressure_poll")
def pcc_gc_pressure_poll() -> i64:
    requested: i64 = atomic_load_i64(global_addr("pcc_gc_pressure_requests"), 0, "acquire")
    serviced: i64 = atomic_load_i64(global_addr("pcc_gc_pressure_serviced"), 0, "acquire")
    if requested <= serviced:
        return 0
    if _pressure_allowed() == 0:
        atomic_rmw_i64("add", global_addr("pcc_gc_pressure_deferred"), 0, 1, "relaxed")
        return 0
    if atomic_cas_i32(global_addr("pcc_gc_pressure_active"), 0, 0, 1, "acq_rel", "acquire") != 0:
        atomic_rmw_i64("add", global_addr("pcc_gc_pressure_deferred"), 0, 1, "relaxed")
        return 0
    # Recheck after winning; another pressure poll may just have serviced it,
    # or disable/lock state may have changed while attempting admission.
    requested = atomic_load_i64(global_addr("pcc_gc_pressure_requests"), 0, "acquire")
    serviced = atomic_load_i64(global_addr("pcc_gc_pressure_serviced"), 0, "acquire")
    if requested <= serviced:
        atomic_store_i32(global_addr("pcc_gc_pressure_active"), 0, 0, "release")
        return 0
    if _pressure_allowed() == 0:
        atomic_rmw_i64("add", global_addr("pcc_gc_pressure_deferred"), 0, 1, "relaxed")
        atomic_store_i32(global_addr("pcc_gc_pressure_active"), 0, 0, "release")
        return 0
    before: i64 = atomic_load_i64(global_addr("pcc_gc_pressure_completed"), 0, "acquire")
    store_i32(global_addr("pcc_gc_pressure_owner"), 0, 1)
    pcc_gc_collect(0)
    store_i32(global_addr("pcc_gc_pressure_owner"), 0, 0)
    completed: i64 = atomic_load_i64(global_addr("pcc_gc_pressure_completed"), 0, "acquire")
    result: i64 = 0
    if completed > before:
        # Coalesce requests already pending at admission. Requests published
        # by finalizers/callbacks during this collection remain pending.
        atomic_store_i64(global_addr("pcc_gc_pressure_serviced"), 0, requested, "release")
        result = 1
    else:
        atomic_rmw_i64("add", global_addr("pcc_gc_pressure_incomplete"), 0, 1, "relaxed")
    atomic_store_i32(global_addr("pcc_gc_pressure_active"), 0, 0, "release")
    return result


@c_abi_export("pcc_gc_pressure_counter")
def pcc_gc_pressure_counter(metric: i64) -> i64:
    if metric == 0:
        return atomic_load_i64(global_addr("pcc_gc_pressure_publications"), 0, "acquire")
    if metric == 1:
        return atomic_load_i64(global_addr("pcc_gc_pressure_requests"), 0, "acquire")
    if metric == 2:
        return atomic_load_i64(global_addr("pcc_gc_pressure_completed"), 0, "acquire")
    if metric == 3:
        return atomic_load_i64(global_addr("pcc_gc_pressure_serviced"), 0, "acquire")
    if metric == 4:
        return atomic_load_i64(global_addr("pcc_gc_pressure_deferred"), 0, "acquire")
    if metric == 5:
        serviced: i64 = atomic_load_i64(global_addr("pcc_gc_pressure_serviced"), 0, "acquire")
        return atomic_load_i64(global_addr("pcc_gc_pressure_requests"), 0, "acquire") - serviced
    if metric == 6:
        return atomic_load_i64(global_addr("pcc_gc_pressure_incomplete"), 0, "acquire")
    if metric == 7:
        return _PRESSURE_EVERY
    return -1
