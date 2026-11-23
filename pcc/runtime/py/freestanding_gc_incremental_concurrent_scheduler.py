"""Raw tracing allocation pacing and rooted GC4 safepoint scheduling."""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_cas_i32,
    atomic_cas_i64,
    atomic_load_i32,
    atomic_load_i64,
    atomic_rmw_i32,
    atomic_rmw_i64,
    atomic_store_i32,
    define_thread_local_i32,
    function_addr,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    null,
    ptr_eq,
    ptr_is_null,
    stack_alloc,
    store_i32,
    store_i64,
    store_ptr,
    unsigned_div_i64,
)


__pcc_freestanding__ = True



pcc_gc_metric_shard = extern("pcc_gc_metric_shard", (), c_int64)
pcc_gc_config_ensure = extern("pcc_gc_config_ensure", (), c_int64)


@c_abi_export("pcc_gc_tracing_record_pause")
def pcc_gc_tracing_record_pause(start_us: i64, end_us: i64) -> None:
    if start_us <= 0:
        return
    if end_us < start_us:
        return
    pause: i64 = end_us - start_us
    if pause <= 0:
        pause: i64 = 1
    store_i64(
        global_addr("pcc_gc_metric_pause_count"),
        0,
        load_i64(global_addr("pcc_gc_metric_pause_count"), 0) + 1,
    )
    store_i64(
        global_addr("pcc_gc_metric_pause_sum_us"),
        0,
        load_i64(global_addr("pcc_gc_metric_pause_sum_us"), 0) + pause,
    )
    if pause < 100:
        store_i32(
            global_addr("pcc_gc_metric_pause_hist0"),
            0,
            load_i32(global_addr("pcc_gc_metric_pause_hist0"), 0) + 1,
        )
    elif pause < 1000:
        store_i32(
            global_addr("pcc_gc_metric_pause_hist1"),
            0,
            load_i32(global_addr("pcc_gc_metric_pause_hist1"), 0) + 1,
        )
    elif pause < 10000:
        store_i32(
            global_addr("pcc_gc_metric_pause_hist2"),
            0,
            load_i32(global_addr("pcc_gc_metric_pause_hist2"), 0) + 1,
        )
    else:
        store_i32(
            global_addr("pcc_gc_metric_pause_hist3"),
            0,
            load_i32(global_addr("pcc_gc_metric_pause_hist3"), 0) + 1,
        )
    current: i64 = load_i64(global_addr("pcc_gc_metric_max_pause_us"), 0)
    if pause > current:
        store_i64(global_addr("pcc_gc_metric_max_pause_us"), 0, pause)


@c_abi_export("pcc_gc_record_explicit_pause")
def pcc_gc_record_explicit_pause(start_us: i64, end_us: i64) -> None:
    pcc_gc_tracing_record_pause(start_us, end_us)


@c_abi_export("pcc_gc_record_collection_stw_pause")
def pcc_gc_record_collection_stw_pause(start_us: i64, end_us: i64) -> None:
    # The collection owner records while the world is still stopped. Unlike
    # the legacy step metric, this covers its complete mark/sweep/remap window
    # once, including nested steps, but excludes stop rendezvous and callbacks
    # outside the stopped world. Do not add the two inclusive metrics together.
    if start_us <= 0 or end_us < start_us:
        return
    pause: i64 = end_us - start_us
    if pause <= 0:
        pause = 1
    # Individual slots also admit concurrent telemetry reads. They are not a
    # joint snapshot; callers reset/sample at a quiescent measurement boundary.
    atomic_rmw_i64("add", global_addr("pcc_gc_metric_collection_stw_count"), 0, 1, "relaxed")
    atomic_rmw_i64("add", global_addr("pcc_gc_metric_collection_stw_sum_us"), 0, pause, "relaxed")
    maximum = global_addr("pcc_gc_metric_collection_stw_max_us")
    previous: i64 = atomic_load_i64(maximum, 0, "relaxed")
    while pause > previous:
        observed: i64 = atomic_cas_i64(maximum, 0, previous, pause, "relaxed", "relaxed")
        if observed == previous:
            break
        previous = observed
    bucket = global_addr("pcc_gc_metric_collection_stw_hist3")
    if pause < 100:
        bucket = global_addr("pcc_gc_metric_collection_stw_hist0")
    elif pause < 1000:
        bucket = global_addr("pcc_gc_metric_collection_stw_hist1")
    elif pause < 10000:
        bucket = global_addr("pcc_gc_metric_collection_stw_hist2")
    atomic_rmw_i64("add", bucket, 0, 1, "relaxed")


    # GC1 marks on its collector thread, concurrently with every mutator;
    # allocation sites record debt there (pcc_gc_cms_note_alloc).


# Each thread publishes its allocation debt (and allocation count) to the
# shared words in batches of at least this many bytes, as Go's mcache keeps
# local statistics: one atomic add per batch, not two per allocation on
# lines every allocating thread writes. Pacing reads lag by at most a batch
# per thread.
ALLOC_DEBT_BATCH_BYTES = 32768
define_thread_local_i32("pcc_gc_tls_alloc_debt", 0)
define_thread_local_i32("pcc_gc_tls_alloc_pushes", 0)


@c_abi_export("pcc_gc_note_alloc")
def pcc_gc_note_alloc(bytes: i64) -> None:
    backend: i64 = 0
    if load_i32(global_addr("pcc_gc_config_initialized"), 0) != 0:
        pass
    else:
        backend = pcc_gc_config_ensure()
    if bytes < 0:
        bytes: i64 = 0
    atomic_rmw_i64(
        "add", global_addr("pcc_gc_metric_shards"), pcc_gc_metric_shard() + 8, 1, "relaxed"
    )
