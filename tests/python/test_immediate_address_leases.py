"""Address-free words need neither a graph lock nor a mode lease."""

import pytest

from tests.python.test_explicit_machine_arithmetic_boundaries import (
    _run_native_all_gc,
)
from tests.python.test_foreign_address_leases import Model


@pytest.mark.parametrize('backend', range(5))
@pytest.mark.parametrize('word', (0, 1, 3, -1, 9223372036854775807))
@pytest.mark.parametrize('active', (0, -1, 9223372036854775807))
def test_immediate_lease_avoids_object_and_mode_protocol(backend, word, active):
    model = Model(backend)
    model.store('pcc_gc_refcount_atomic', 0, int(backend == 0))
    model.store('pcc_gc_foreign_lease_active', 0, active)
    slot = 20000
    model.store(slot, 0, word)
    pending_exception = object()
    model.ns['pending_exception'] = pending_exception

    def unexpected(*args):
        raise AssertionError('address-free value entered object/mode protocol')

    for name in ('pcc_gc_config_ensure', 'pcc_py_gc_minor_graph_lock',
                 'atomic_load_i64', 'atomic_cas_i64', 'load_i32',
                 'pcc_gc_resolve_root_slot_unlocked'):
        model.ns[name] = unexpected
    before = dict(model.memory)
    assert model.acquire(slot) == 0
    assert model.release(slot, 0) == 0
    assert model.memory == before
    assert model.ns['pending_exception'] is pending_exception
    assert model.acquire(0) == -1


PROGRAM = '''import threading
from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, extern
from pcc.unsafe import int_to_ptr, null, stack_alloc, store_ptr
acquire = extern("pcc_gc_foreign_lease_acquire", (c_ptr,), c_int64)
release = extern("pcc_gc_foreign_lease_release", (c_ptr, c_int64), c_int64)
@c_abi_export("probe_immediate_leases")
def probe() -> i64:
    slot = stack_alloc(8)
    index: i64 = 0
    if acquire(null()) != -1:
        return 1
    while index < 10000:
        store_ptr(slot, 0, null())
        token: i64 = i64(acquire(slot))
        if token != 0 or release(slot, token) != 0:
            return 2
        store_ptr(slot, 0, int_to_ptr(7))
        token = i64(acquire(slot))
        if token != 0 or release(slot, token) != 0:
            return 3
        store_ptr(slot, 0, int_to_ptr(-1))
        token = i64(acquire(slot))
        if token != 0 or release(slot, token) != 0:
            return 4
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
    print("IMMEDIATE_LEASE_THREADS_OK")
main()
'''


@pytest.mark.integration
def test_immediate_leases_native_threads_all_gc(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
):
    _run_native_all_gc(
        tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler,
        PROGRAM, 'IMMEDIATE_LEASE_THREADS_OK',
    )
