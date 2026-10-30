"""Execute the scalar pin ABI and hot-state spacing with the owned runtime."""

import os
from pathlib import Path
import subprocess

from tests.owned_c_execution import compile_owned_c_with_runtime

ROOT = Path(__file__).resolve().parents[2]


def test_hot_atomic_storage_preserves_pin_abi_across_five_collectors(
    tmp_path, pcc_runtime_archive,
):
    fixture = ROOT / "tests/fixtures/native/threaded_path_ownership/pin_abi.c"
    source = fixture.read_text()
    assert source.count("int main(void)") == 1
    source = "#include <stdint.h>\nstatic int backend;\n" + source.replace(
        "EXPECTED_BACKEND", "backend",
    ).replace("int main(void)", "int pin_case(void)")
    # Refer to the live scalar neighbours, not the padding symbols: a future
    # linker that discards padding must still fail this actual layout check.
    source += r'''
extern int32_t pcc_gc_metric_safepoint;
extern int64_t pcc_gc_metric_step;
extern int64_t pcc_gc_metric_shards[];
extern int32_t g_pcc_py_gc_minor_graph_lock;
extern void *pcc_allocator_side_arena;
extern void *pcc_gc_tracked_shard_address(int64_t index);
extern void *pcc_gc_tracked_primary_index_cells(void *object);
enum { TRACKING_SHARDS = 32, TRACKING_STRIDE = 128, PRIMARY_RECORD_OFFSET = 32 };
extern int64_t pcc_gc_foreign_lease_active;
extern int64_t pcc_py_gc_scheduler_root_free_count;
extern int64_t pcc_gc_backend4_candidate_fresh_skips_g;
static int separate(void *a, void *b) {
    uintptr_t x = (uintptr_t)a, y = (uintptr_t)b;
    return x > y ? x - y >= 128 : y - x >= 128;
}
/* The pin balance shards span 64 strides of 128 bytes. */
static int outside_shards(void *a) {
    uintptr_t x = (uintptr_t)a, base = (uintptr_t)pcc_gc_metric_shards;
    return x + 128 <= base || x >= base + 64 * 128;
}
int main(void) {
    if (!outside_shards(&pcc_gc_metric_safepoint)) return 60;
    if (!outside_shards(&pcc_gc_metric_step)) return 61;
    if (!separate(&g_pcc_py_gc_minor_graph_lock, &pcc_allocator_side_arena)) return 62;
    for (int64_t index = 0; index < TRACKING_SHARDS; index++) {
        uintptr_t shard = (uintptr_t)pcc_gc_tracked_shard_address(index);
        if ((shard & (TRACKING_STRIDE - 1)) != 0) return 63;
        if (index > 0 && shard != (uintptr_t)pcc_gc_tracked_shard_address(index - 1)
                                  + TRACKING_STRIDE) return 66;
        /* For these small synthetic addresses the production hash is index.
           This ABI selects metadata only; it never dereferences the key. */
        void *key = (void *)((uintptr_t)index << 4);
        void *primary = pcc_gc_tracked_primary_index_cells(key);
        if ((uintptr_t)primary != shard + PRIMARY_RECORD_OFFSET) return 67;
        if (!separate(&g_pcc_py_gc_minor_graph_lock, primary)) return 68;
    }
    if (!separate(&pcc_gc_foreign_lease_active, &pcc_py_gc_scheduler_root_free_count)) return 64;
    if (!separate(&pcc_gc_foreign_lease_active, &pcc_gc_backend4_candidate_fresh_skips_g)) return 65;
    for (backend = 0; backend < 5; backend++) {
        if (pcc_gc_set_backend(backend) != 0) return 80;
        int result = pin_case();
        if (result != 0) return result;
    }
    return 0;
}
'''
    src = tmp_path / "pin_storage.c"
    binary = tmp_path / "pin_storage"
    src.write_text(source)
    compile_owned_c_with_runtime(
        src, binary, runtime_archive=Path(pcc_runtime_archive),
    )
    run = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=20,
        env=dict(os.environ, PATH="/nonexistent"),
    )
    assert (run.returncode, run.stdout, run.stderr) == (
        0, "pin-public-abi-and-metric-ok\n" * 5, "",
    )
