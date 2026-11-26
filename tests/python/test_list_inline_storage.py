"""Small list payloads own exactly one allocation in the nonmoving route."""
from pathlib import Path
import pytest

from tests.python.test_foreign_address_leases import _functions

RUNTIME = Path(__file__).resolve().parents[2] / "pcc/runtime/py"


class Factory:
    def __init__(self, inline, fail=None):
        self.inline = inline
        self.fail = fail
        self.memory = {100: None}
        self.managed = []
        self.raw = []
        self.owner = 1000
        self.buffer = 2000
        def alloc(size, tag, flags):
            self.managed.append((size, tag, flags))
            if self.fail == "object":
                return None
            for i in range(0, size, 8):
                self.memory[self.owner+i] = None
            return self.owner
        def malloc(size):
            self.raw.append(size)
            return None if self.fail == "raw" else self.buffer
        self.ns = {
            "i64": int, "C_POINTER_SIZE": 8,
            "PYLISTOBJECT_SIZE": 40, "PY_TYPE_LIST": 6,
            "PYLISTOBJECT_LENGTH_OFFSET": 16, "PYLISTOBJECT_CAPACITY_OFFSET": 24,
            "PYLISTOBJECT_ITEMS_OFFSET": 32,
            "pcc_gc_alloc": alloc, "malloc": malloc,
            "store_ptr": lambda p,o,v: self.memory.__setitem__(p+o,v),
            "store_i64": lambda p,o,v: self.memory.__setitem__(p+o,v),
            "load_ptr": lambda p,o: self.memory.get(p+o),
            "load_i32": lambda p,o: self.inline,
            "global_addr": lambda name: name,
            "ptr_add": lambda p,o: p+o,
            "ptr_is_null": lambda p: p is None,
            "null": lambda: None,
            "memset": lambda p,v,n: [self.memory.__setitem__(p+i,None) for i in range(0,n,8)],
        }
        _functions(RUNTIME / "py_list.py", {"_list_allocate_unpublished"}, self.ns)

    def run(self, requested):
        return self.ns["_list_allocate_unpublished"](100, requested)


@pytest.mark.parametrize("requested", [0, 1, 2, 4])
def test_nonmoving_factory_small_payload_is_in_owner(requested):
    m = Factory(inline=1)
    assert m.run(requested) == 0
    assert m.managed == [(72, 6, 0)] and m.raw == []
    assert m.memory[100] == m.owner
    assert m.memory[m.owner+16] == 0 and m.memory[m.owner+24] == 4
    assert m.memory[m.owner+32] == m.owner+40
    assert all(m.memory[m.owner+40+i*8] is None for i in range(4))


@pytest.mark.parametrize("inline,requested,raw_size", [(0, 0, 32), (0, 2, 32), (1, 5, 40)])
def test_external_route_keeps_separate_storage_and_capacity(inline, requested, raw_size):
    m = Factory(inline=inline)
    assert m.run(requested) == 0
    assert m.managed == [(40, 6, 0)] and m.raw == [raw_size]
    assert m.memory[m.owner+32] == m.buffer
    assert m.memory[m.owner+24] == max(4, requested)


def test_failed_object_has_no_published_payload_and_large_raw_failure_remains():
    m = Factory(inline=1, fail="object")
    assert m.run(2) == -1 and m.raw == [] and m.memory[100] is None
    m = Factory(inline=1, fail="raw")
    assert m.run(5) == -1 and m.raw == [40]
    assert m.memory[100] == m.owner and m.memory[m.owner+16] == 0
    assert m.memory[m.owner+32] is None


@pytest.mark.integration
def test_inline_list_native_growth_slice_sort_collection_and_fallback(tmp_path, threaded_pcc_runtime_archive):
    import os
    import subprocess

    from tests.owned_c_execution import compile_owned_c_with_runtime

    source = Path(__file__).resolve().parents[2] / "tests/fixtures/native/list_inline_storage.c"
    exe = tmp_path / "inline_list"
    compile_owned_c_with_runtime(source, exe, runtime_archive=threaded_pcc_runtime_archive)
    # The existing provenance-check mode keeps the graph-lock/external route.
    # Both modes must retain the exact length/capacity and storage-lifetime ABI.
    for probe, inline in (("0", "1"), ("2", "0")):
        run = subprocess.run([str(exe), inline], capture_output=True, text=True, timeout=30,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_REFCOUNT_PROVENANCE_PROBE=probe))
        assert (run.returncode, run.stdout, run.stderr) == (0, "INLINE_LIST_CONTRACT_OK\n", ""), run
