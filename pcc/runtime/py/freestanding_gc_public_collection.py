"""Raw GC configuration and public tracing-collection entrypoints."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    cstr, global_addr, load_i8, load_i32, load_i64, ptr_is_null, store_i32,
    store_i64,
)
from pcc.unsafe import (
    atomic_cas_i32,
    atomic_load_i32,
    atomic_load_i64,
    atomic_rmw_i32,
    atomic_store_i32,
    define_thread_local_i32,
    function_addr,
    global_load_ptr,
    null,
)


__pcc_freestanding__ = True



# Kept in this module so all accesses retain the TLS storage identity.
define_thread_local_i32("pcc_gc_cms_worker_current", 0)


pcc_platform_getenv = extern("pcc_platform_getenv", (c_ptr,), c_ptr)
pcc_platform_write = extern(
    "pcc_platform_write", (c_int64, c_ptr, c_int64), c_int64
)
pcc_platform_abort = extern("pcc_platform_abort", (), c_void)
pcc_threads_enabled = extern("pcc_threads_enabled", (), c_int64)
pcc_thread_enable_conservative_roots = extern("pcc_thread_enable_conservative_roots", (), c_void)
pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)





@c_abi_export("pcc_gc_config_parse_env_i32")
def pcc_gc_config_parse_env_i32(
    raw, default: i64, min_value: i64, max_value: i64
) -> i64:
    if ptr_is_null(raw) != 0:
        return default
    value: i64 = 0
    i: i64 = 0
    seen: i64 = 0
    neg: i64 = 0
    ch: i64 = load_i8(raw, 0) & 255
    if ch == 45:  # '-'
        neg: i64 = 1
        i: i64 = 1
    while True:
        ch = load_i8(raw, i) & 255
        if ch == 0:
            break
        if ch < 48 or ch > 57:
            return default
        value = value * 10 + (ch - 48)
        seen: i64 = 1
        i = i + 1
    if seen == 0:
        return default
    if neg != 0:
        value = -value
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


@c_abi_export("pcc_gc_config_ensure")
def pcc_gc_config_ensure() -> i64:
    if atomic_load_i32(global_addr("pcc_gc_config_initialized"), 0, "acquire") != 0:
        return load_i32(global_addr("pcc_gc_backend_selected"), 0)
    # The same cold-path lock orders configuration, explicit selection and
    # first-allocation freeze. Readers never observe partially chosen policy.
    pcc_py_gc_minor_graph_lock()
    if atomic_load_i32(global_addr("pcc_gc_config_initialized"), 0, "acquire") != 0:
        selected: i64 = load_i32(global_addr("pcc_gc_backend_selected"), 0)
        pcc_py_gc_minor_graph_unlock()
        return selected
    # pcc has one collector: there is nothing to select.
    backend: i64 = 0
    pause: i64 = pcc_gc_config_parse_env_i32(
        pcc_platform_getenv(cstr("PCC_GC_PAUSE")), 1000, 50, 1000
    )
    stepmul: i64 = pcc_gc_config_parse_env_i32(
        pcc_platform_getenv(cstr("PCC_GC_STEPMUL")), 10000, 1, 10000
    )
    stepmul = pcc_gc_config_parse_env_i32(
        pcc_platform_getenv(cstr("PCC_GC_STEP_MUL")), stepmul, 1, 10000
    )
    threshold: i64 = pcc_gc_config_parse_env_i32(
        pcc_platform_getenv(cstr("PCC_GC_DEBT_THRESHOLD")),
        0, 0, 1099511627776,
    )
    minor_heap_size: i64 = pcc_gc_config_parse_env_i32(
        pcc_platform_getenv(cstr("PCC_GC_MINOR_HEAP_SIZE")),
        33554432, 256, 1099511627776,
    )
    minor_alloc_max: i64 = pcc_gc_config_parse_env_i32(
        pcc_platform_getenv(cstr("PCC_GC_MINOR_ALLOC_MAX")),
        16, 16, 1073741824,
    )
    # -1 (unset) trusts the caller. The probe checked that a refcount
    # operation names a managed object, which a counting relocating
    # collector could reach at a pre-move address; every collector but GC0
    # is count-free (tracing_only above) and counts nothing, and GC0 never
    # moves. An explicit setting still audits.
    refcount_probe: i64 = pcc_gc_config_parse_env_i32(
        pcc_platform_getenv(cstr("PCC_GC_REFCOUNT_PROVENANCE_PROBE")), -1, -1, 3
    )
    if refcount_probe < 0:
        refcount_probe = 0
    store_i32(global_addr("pcc_gc_backend_selected"), 0, backend)
    # The configuration is initialized; publish the frame-registry word
    # from the backend just stored and the current frame-root switch.
    registry_inactive: i64 = 0
    registry_inactive = 1
    store_i32(global_addr("pcc_gc_frame_registry_inactive"), 0, registry_inactive)
    store_i32(global_addr("pcc_gc_pause"), 0, pause)
    store_i32(global_addr("pcc_gc_stepmul"), 0, stepmul)
    store_i64(global_addr("pcc_gc_debt_threshold_override"), 0, threshold)
    store_i32(global_addr("pcc_gc_minor_heap_size"), 0, minor_heap_size)
    store_i32(global_addr("pcc_gc_minor_alloc_max"), 0, minor_alloc_max)
    store_i32(global_addr("pcc_gc_refcount_provenance_probe"), 0, refcount_probe)
    # The allocator audits double frees at the same level, from its own word.
    store_i32(global_addr("pcc_allocator_double_free_audit"), 0, refcount_probe)
    refcount_fast: i64 = 0
    if refcount_probe == 0 and pcc_threads_enabled() == 0:
        refcount_fast = 1
    store_i32(global_addr("pcc_gc_refcount_fast"), 0, refcount_fast)
    refcount_atomic: i64 = 0
    if refcount_probe == 0:
        refcount_atomic = 1
    store_i32(global_addr("pcc_gc_refcount_atomic"), 0, refcount_atomic)
    store_i32(global_addr("pcc_gc_pins_inert"), 0, refcount_atomic)
    leases_inert: i64 = 0
    if refcount_probe == 0:
        leases_inert = 1
    store_i32(global_addr("pcc_gc_leases_inert"), 0, leases_inert)
    atomic_store_i32(global_addr("pcc_gc_config_initialized"), 0, 1, "release")
    pcc_py_gc_minor_graph_unlock()
    return backend
