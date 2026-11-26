"""Cold live dictionary table histogram, independent of GC materialization."""
from pathlib import Path

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.runtime_body_model import load_functions

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "pcc/runtime/py/py_obj_gc.py"


class Snapshot:
    def __init__(self, *, owns_world=False, stop_failure=False):
        self.words = {}
        self.next = 1 << 20
        self.owns_world = owns_world
        self.stop_failure = stop_failure
        self.starts = self.resumes = 0
        self.slab = []
        self.index = []
        self.usable = {}
        self.read_bases = []
        self.out = self.stack(1792)
        self.ns = {key: value for key, value in vars(abi).items() if key.isupper()}
        self.ns.update({
            "i64": int, "load_i64": self.load, "load_i32": self.load,
            "load_ptr": self.load, "store_i64": self.store,
            "stack_alloc": self.stack, "ptr_add": lambda base, off: base + off,
            "int_to_ptr": int, "ptr_eq": lambda a, b: a == b,
            "ptr_is_null": lambda p: p == 0, "memset": self.memset,
            "pcc_thread_owns_stopped_world": lambda: int(self.owns_world),
            "pcc_stop_the_world": self.stop, "pcc_resume_world": self.resume,
            "pcc_allocator_object_cells_enumerate_slice": (
                lambda *args: self.enumerate(self.slab, 24, *args)),
            "pcc_gc_managed_pointer_index_enumerate_slice": (
                lambda *args: self.enumerate(self.index, 16, *args)),
            "malloc_usable_size": self.raw_usable,
        })
        load_functions(SOURCE, {"pcc_gc_live_dict_storage_snapshot"}, self.ns)

    def load(self, base, off=0):
        return self.words.get(base + off, 0)

    def store(self, base, off, value):
        self.words[base + off] = value

    def stack(self, size):
        p = self.next
        self.next += size + 64
        return p

    def memset(self, ptr, value, size):
        assert value == 0
        for off in range(0, size, 8):
            self.store(ptr, off, 0)

    def stop(self):
        self.starts += 1
        if self.stop_failure:
            return -1
        assert not self.owns_world
        self.owns_world = True
        return 0

    def resume(self):
        self.resumes += 1
        assert self.owns_world
        self.owns_world = False

    def raw_usable(self, ptr):
        assert self.owns_world
        self.read_bases.append(ptr)
        return self.usable[ptr]

    def object(self, capacity, *, tag=abi.PY_TYPE_DICT, refcount=1,
               flags=abi.PY_FLAG_GC_TRACKED, valid=True, indexed=False,
               usable=None):
        obj = self.stack(56)
        indices = self.stack(capacity * 8)
        entries = self.stack(capacity * 24)
        self.store(obj, abi.PYOBJECTHEADER_TYPE_TAG_OFFSET, tag)
        self.store(obj, abi.PYOBJECTHEADER_FLAGS_OFFSET, flags)
        self.store(obj, abi.PYOBJECTHEADER_REFCOUNT_OFFSET, refcount)
        self.store(obj, abi.PYDICTOBJECT_CAPACITY_OFFSET, capacity)
        self.store(obj, abi.PYDICTOBJECT_INDICES_OFFSET, indices if valid else 0)
        self.store(obj, abi.PYDICTOBJECT_ENTRIES_OFFSET, entries if valid else 0)
        self.usable[indices], self.usable[entries] = (
            usable if usable is not None else (capacity * 8, capacity * 32))
        (self.index if indexed else self.slab).append(obj)
        return obj

    def enumerate(self, objects, done, out, cap, required, excluded, cursor, budget):
        assert self.owns_world
        assert cap == 256 and budget >= cap
        assert required == abi.PY_FLAG_GC_TRACKED
        assert excluded == abi.PY_FLAG_GC_DEALLOCATING
        pos = self.load(cursor, 8)
        written = 0
        while pos < len(objects) and written < cap:
            obj = objects[pos]
            flags = self.load(obj, abi.PYOBJECTHEADER_FLAGS_OFFSET)
            if flags & required == required and not flags & excluded:
                self.store(out, written * 8, obj)
                written += 1
            pos += 1
        self.store(cursor, 8, pos)
        self.store(cursor, done, int(pos == len(objects)))
        return written

    def sample(self):
        return self.ns["pcc_gc_live_dict_storage_snapshot"](self.out)

    def bucket(self, capacity):
        lane = capacity.bit_length() - 3
        return tuple(self.load(self.out, lane * 32 + off) for off in (0, 8, 16, 24))


def test_exact_unmaterialized_snapshot_from_both_allocator_owners_and_multiple_batches():
    m = Snapshot()
    for _ in range(270):
        m.object(4)
    m.object(8, indexed=True)
    m.object(1024, usable=(8192, 28624), indexed=True)
    m.object(4, tag=abi.PY_TYPE_LIST)
    m.object(4, flags=0, valid=False)
    m.object(4, flags=abi.PY_FLAG_GC_TRACKED | abi.PY_FLAG_GC_DEALLOCATING,
             valid=False)
    m.object(4, refcount=0, valid=False)
    assert m.sample() == 0
    assert m.bucket(4) == (270, 540, 270 * 128, 270 * 160)
    assert m.bucket(8) == (1, 2, 256, 320)
    assert m.bucket(1024) == (1, 2, 32768, 8192 + 28624)
    assert m.bucket(16) == (0, 0, 0, 0)
    assert (m.starts, m.resumes, m.owns_world) == (1, 1, False)
    assert len(m.read_bases) == 544


def test_borrowed_stop_restored_without_extra_resume_and_immortal_visible():
    m = Snapshot(owns_world=True)
    m.object(8, refcount=0, flags=abi.PY_FLAG_GC_TRACKED | abi.PY_FLAG_IMMORTAL)
    assert m.sample() == 0 and m.bucket(8) == (1, 2, 256, 320)
    assert (m.starts, m.resumes, m.owns_world) == (0, 0, True)


@pytest.mark.parametrize("capacity,valid", [(4, False), (6, True), (0, True)])
def test_inconsistent_live_dict_discards_receipt_and_resumes(capacity, valid):
    m = Snapshot()
    m.object(capacity, valid=valid)
    assert m.sample() == -1
    assert (m.starts, m.resumes, m.owns_world) == (1, 1, False)
    assert not m.read_bases


def test_future_interior_layout_is_rejected_before_allocator_header_read():
    m = Snapshot()
    d = m.object(8)
    indices = m.load(d, abi.PYDICTOBJECT_INDICES_OFFSET)
    m.store(d, abi.PYDICTOBJECT_ENTRIES_OFFSET, indices + 8 * 8)
    assert m.sample() == -1 and not m.read_bases
    assert (m.starts, m.resumes, m.owns_world) == (1, 1, False)


def test_inconsistent_usable_size_is_not_reported_as_live_bytes():
    m = Snapshot()
    m.object(8, usable=(16, 32))
    assert m.sample() == -1 and len(m.read_bases) == 2
    assert (m.starts, m.resumes, m.owns_world) == (1, 1, False)


def test_null_output_address_never_stops_world():
    m = Snapshot()
    assert m.ns["pcc_gc_live_dict_storage_snapshot"](0) == -1
    assert (m.starts, m.resumes, m.owns_world) == (0, 0, False)


def test_stw_admission_failure_is_nonfatal_and_does_not_resume():
    m = Snapshot(stop_failure=True)
    assert m.sample() == -1
    assert (m.starts, m.resumes, m.owns_world) == (1, 0, False)


@pytest.mark.integration
def test_native_snapshot_actual_dict_payloads_and_terminal_release(tmp_path, threaded_pcc_runtime_archive):
    import os
    import subprocess

    from tests.owned_c_execution import compile_owned_c_with_runtime
    source = ROOT / "tests/fixtures/native/coordinator_dict_storage.c"
    exe = tmp_path / "coordinator_dict_storage"
    compile_owned_c_with_runtime(source, exe, runtime_archive=threaded_pcc_runtime_archive)
    for probe in ("0", "2"):
        run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30,
                             env=dict(os.environ, PATH="/nonexistent",
                                      PCC_GC_REFCOUNT_PROVENANCE_PROBE=probe))
        assert (run.returncode, run.stdout, run.stderr) == (0, "DICT_STORAGE_PROFILE_OK\n", ""), run


@pytest.mark.integration
def test_native_application_phase_snapshot_address_lane(tmp_path, threaded_pcc_runtime_archive):
    import os
    import subprocess

    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "coordinator_dict_snapshot.py"
    source.write_text(
        "from pcc.frontends.python.pipeline_memory import coordinator_memory_phase\n"
        "def main():\n"
        "    coordinator_memory_phase('manifests-ready')\n"
        "    a = {'key': 1}\n"
        "    b = {'key': len(a)}\n"
        "    coordinator_memory_phase('manifests-ready')\n"
        "    print(len(a) + len(b))\n"
        "main()\n",
        encoding="utf-8",
    )
    exe = tmp_path / "coordinator_dict_snapshot"
    compile_python(str(source), str(exe), backend="self", ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    environment = dict(os.environ, PATH="/nonexistent", PCC_PROFILE_COORDINATOR_MEMORY="0")
    off = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30,
                         env=environment)
    assert (off.returncode, off.stdout, off.stderr) == (0, "2\n", "")
    environment["PCC_PROFILE_COORDINATOR_MEMORY"] = "1"
    on = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30,
                        env=environment)
    assert (on.returncode, on.stdout) == (0, "2\n")
    prefix = "pcc coordinator dict storage phase=manifests-ready status=ok bins="
    lines = [line for line in on.stderr.splitlines() if line.startswith(prefix)]
    assert len(lines) == 2, on.stderr
    bins = []
    for line in lines:
        values = {}
        for item in line[len(prefix):].split(","):
            if item:
                capacity, count, raw, requested, usable = map(int, item.split(":"))
                assert raw == count * 2 and requested == capacity * 32 * count
                assert usable >= requested
                values[capacity] = count
        bins.append(values)
    # Literal construction takes the existing static cap8 and dynamic cap4
    # routes. The C fixture above establishes exact byte and release accounting
    # independently of any compiler-module initialization retained here.
    for capacity in (4, 8):
        assert bins[1].get(capacity, 0) >= bins[0].get(capacity, 0) + 1, on.stderr
