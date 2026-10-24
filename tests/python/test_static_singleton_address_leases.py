"""Only the five image-static singleton addresses have an unconditional lease."""

import pytest

from tests.python.test_foreign_address_leases import Model, _mode_setter
from tests.python.test_explicit_machine_arithmetic_boundaries import _run_native_all_gc

STORAGE = ("py_none_storage", "py_true_storage", "py_false_storage",
           "py_notimplemented_storage", "py_ellipsis_storage")


def test_only_exact_static_header_names_are_admitted_to_the_raw_boundary():
    from pcc.frontends.python.pipeline_freestanding import freestanding_gc_runtime_global_imports
    source = "from pcc.unsafe import global_addr\n" + "\n".join(
        'global_addr("' + name + '")' for name in STORAGE
    )
    assert freestanding_gc_runtime_global_imports(source) == set(STORAGE)
    for name in ("py_None", "py_user_object", "py_none_storage_copy"):
        assert not freestanding_gc_runtime_global_imports(
            'from pcc.unsafe import global_addr\nglobal_addr("' + name + '")'
        )


@pytest.mark.parametrize("backend", range(5))
@pytest.mark.parametrize("active", (0, -1, 9223372036854775807))
@pytest.mark.parametrize("storage", STORAGE)
def test_image_static_lease_needs_no_header_probe_or_mode_guard(backend, active, storage):
    model = Model(backend, initialized=False)
    slot = 20000
    model.store(slot, 0, storage)
    model.store("pcc_gc_refcount_atomic", 0, int(backend == 0))
    model.store("pcc_gc_foreign_lease_active", 0, active)
    model.ns["pending_exception"] = pending = object()
    def unexpected(*args):
        raise AssertionError("static address entered object or mode protocol")
    for name in ("load_i32", "atomic_load_i64", "atomic_cas_i64", "pcc_gc_config_ensure",
                 "pcc_py_gc_minor_graph_lock", "pcc_gc_resolve_root_slot_unlocked",
                 "pcc_gc_granule_is_object_start", "pcc_gc_managed_pointer_index_seen"):
        model.ns[name] = unexpected
    before = dict(model.memory)
    assert model.acquire(slot) == 0
    assert model.release(slot, 0) == 0
    assert model.memory == before and model.ns["pending_exception"] is pending


@pytest.mark.parametrize("storage", STORAGE)
@pytest.mark.parametrize("after", range(5))
def test_static_lease_survives_mode_change_without_consuming_another_owner(storage, after):
    model = Model(0)
    model.store("pcc_gc_refcount_atomic", 0, 1)
    slot = 20000
    model.store(slot, 0, storage)
    assert model.acquire(slot) == 0
    assert _mode_setter(model)(after) == 0
    other = model.object(1000, node=after != 0, flags=262144)
    token = model.acquire(other)
    assert token == (2 if after == 0 else 1) and model.active() == 1
    assert model.release(slot, 0) == 0 and model.active() == 1
    assert model.release(other, token) == 0 and model.active() == 0


@pytest.mark.parametrize("tag", (0, 1, 33, 34))
def test_a_heap_immortal_with_a_singleton_type_still_needs_provenance_and_a_guard(tag):
    model = Model(0)
    model.store("pcc_gc_refcount_atomic", 0, 1)
    slot = model.object(node=False, flags=1, tag=tag)
    assert model.acquire(slot) == 2 and model.active() == 1
    assert _mode_setter(model)(4) == -1
    assert model.release(slot, 2) == 0 and model.active() == 0


PROGRAM = '''import threading
from pcc import i64
from pcc.extern import c_abi_export, c_int32, c_int64, c_ptr, extern
from pcc.unsafe import global_addr, load_ptr, ptr_eq, stack_alloc, store_ptr
acquire = extern("pcc_gc_foreign_lease_acquire", (c_ptr,), c_int64)
release = extern("pcc_gc_foreign_lease_release", (c_ptr, c_int64), c_int64)
collect = extern("pcc_gc_collect", (c_int32,), c_int64)
@c_abi_export("probe_static_leases")
def probe() -> i64:
    slot = stack_alloc(8)
    index: i64 = 0
    while index < 1000:
        store_ptr(slot, 0, global_addr("py_none_storage"))
        token0: i64 = i64(acquire(slot))
        if token0 != 0:
            return 1
        if index == 0:
            collect(0)
        if release(slot, token0) != 0:
            return 20
        if ptr_eq(load_ptr(slot, 0), global_addr("py_none_storage")) == 0:
            return 10
        store_ptr(slot, 0, global_addr("py_true_storage"))
        token1: i64 = i64(acquire(slot))
        if token1 != 0:
            return 2
        if index == 0:
            collect(0)
        if release(slot, token1) != 0:
            return 21
        if ptr_eq(load_ptr(slot, 0), global_addr("py_true_storage")) == 0:
            return 11
        store_ptr(slot, 0, global_addr("py_false_storage"))
        token2: i64 = i64(acquire(slot))
        if token2 != 0:
            return 3
        if index == 0:
            collect(0)
        if release(slot, token2) != 0:
            return 22
        if ptr_eq(load_ptr(slot, 0), global_addr("py_false_storage")) == 0:
            return 12
        store_ptr(slot, 0, global_addr("py_notimplemented_storage"))
        token3: i64 = i64(acquire(slot))
        if token3 != 0:
            return 4
        if index == 0:
            collect(0)
        if release(slot, token3) != 0:
            return 23
        if ptr_eq(load_ptr(slot, 0), global_addr("py_notimplemented_storage")) == 0:
            return 13
        store_ptr(slot, 0, global_addr("py_ellipsis_storage"))
        token4: i64 = i64(acquire(slot))
        if token4 != 0:
            return 5
        if index == 0:
            collect(0)
        if release(slot, token4) != 0:
            return 24
        if ptr_eq(load_ptr(slot, 0), global_addr("py_ellipsis_storage")) == 0:
            return 14
        index += 1
    return 0
def worker(results, index):
    results[index] = probe()
def main():
    results = [-1, -1]
    first = threading.Thread(target=worker, args=(results, 0))
    second = threading.Thread(target=worker, args=(results, 1))
    first.start()
    second.start()
    first.join()
    second.join()
    assert results == [0, 0]
    print("STATIC_LEASE_THREADS_OK")
main()
'''


@pytest.mark.integration
def test_static_singleton_leases_native_threads_all_gc(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
):
    _run_native_all_gc(tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
                       PROGRAM, "STATIC_LEASE_THREADS_OK")
