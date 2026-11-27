"""Cold allocator-owned LIVE object-family size histogram."""
from pathlib import Path

import pytest

from tests.python.runtime_body_model import load_functions

ROOT = Path(__file__).resolve().parents[2]
ALLOCATOR = ROOT / "pcc/runtime/py/freestanding_allocator.py"
GC = ROOT / "pcc/runtime/py/py_obj_gc.py"
BINS = 37
BYTES = BINS * 32
MAX = (1 << 63) - 1


def bucket_index(requested):
    if requested <= 512:
        return max(16, (requested + 15) // 16 * 16) // 16 - 1
    usable = 1 << (requested - 1).bit_length()
    return 32 + usable.bit_length() - 11


class Snapshot:
    def __init__(self, *, owns=False, stop_failure=False):
        self.words = {}
        self.next = 1 << 20
        self.owns = owns
        self.stop_failure = stop_failure
        self.starts = self.resumes = self.walks = 0
        self.objects = []
        self.invalid_count = None
        self.inject_overflow = None
        self.out = self.stack(BYTES)
        self.ns = {
            "i64": int,
            "load_i64": self.load,
            "load_ptr": self.load,
            "store_i64": self.store,
            "atomic_load_i64": lambda base, off, order: self.load(base, off),
            "stack_alloc": self.stack,
            "ptr_add": lambda base, off: base + off,
            "ptr_is_null": lambda value: value == 0,
            "int_to_ptr": int,
            "memset": self.memset,
            "pcc_thread_owns_stopped_world": lambda: int(self.owns),
            "pcc_stop_the_world": self.stop,
            "pcc_resume_world": self.resume,
            "pcc_allocator_object_cells_enumerate_slice": self.enumerate,
        }
        load_functions(ALLOCATOR, {
            "pcc_allocator_size_class",
            "pcc_allocator_object_size_class",
            "_live_object_size_snapshot_add",
            "pcc_allocator_live_object_size_snapshot_stopped",
        }, self.ns, local_helpers=False)
        load_functions(GC, {"pcc_gc_live_managed_size_snapshot"}, self.ns,
                       local_helpers=False)

    def stack(self, n):
        p = self.next
        self.next += n + 128
        return p

    def load(self, base, offset=0):
        return self.words.get(base + offset, 0)

    def store(self, base, offset, value):
        self.words[base + offset] = value

    def memset(self, base, value, n):
        assert value == 0
        for offset in range(0, n, 8):
            self.store(base, offset, 0)

    def stop(self):
        self.starts += 1
        if self.stop_failure:
            return -1
        assert not self.owns
        self.owns = True
        return 0

    def resume(self):
        self.resumes += 1
        assert self.owns
        self.owns = False

    def object(self, requested, *, usable=None, marker=-1, state=None):
        obj = self.stack(128)
        if usable is None:
            usable = self.ns["pcc_allocator_object_size_class"](requested)
        if state is None:
            state = self.ns["GC_STATE_LIVE"]
        self.store(obj, self.ns["GRANULE_MAPPING_SIZE_OFFSET"], marker)
        self.store(obj, self.ns["GRANULE_REQUESTED_SIZE_OFFSET"], requested)
        self.store(obj, self.ns["GRANULE_USABLE_SIZE_OFFSET"], usable)
        self.store(obj, self.ns["GRANULE_STATE_OFFSET"], state)
        self.objects.append(obj)
        return obj

    def enumerate(self, out, capacity, required, excluded, cursor, budget):
        assert self.owns
        assert (capacity, required, excluded, budget) == (256,0,0,4096)
        self.walks += 1
        if self.invalid_count is not None:
            return self.invalid_count
        pos = self.load(cursor, 8)
        n = min(capacity, len(self.objects) - pos)
        for i in range(n):
            self.store(out, i * 8, self.objects[pos + i])
        self.store(cursor, 8, pos + n)
        self.store(cursor, 24, int(pos + n == len(self.objects)))
        if self.inject_overflow is not None:
            lane, field, value = self.inject_overflow
            self.store(self.out, lane * 32 + field * 8, value)
        return n

    def sample(self, address=None, size=BYTES):
        if address is None:
            address = self.out
        return self.ns["pcc_gc_live_managed_size_snapshot"](address, size)

    def rows(self):
        return [tuple(self.load(self.out, lane*32+field*8) for field in range(4))
                for lane in range(BINS)]


def expected(requests):
    rows = [[0,0,0,0] for _ in range(BINS)]
    for n in requests:
        row = rows[bucket_index(n)]
        row[0] += 1
        row[1] += n
        row[2] += 48 if 32 < n <= 48 else 80 if 64 < n <= 80 else max(16, 1 << max(0, (n-1).bit_length()))
        row[3] += max(16, (n+15)//16*16)
    return [tuple(row) for row in rows]


def test_exact_boundary_and_multiple_batches_without_managed_object_reads():
    m = Snapshot()
    requests = [0,1,16,17,31,32,33,40,48,49,64,65,72,80,112,128,129,511,512,
                513,1024,1025,2048,4096,8192,16384] + [72]*270
    for n in requests:
        m.object(n)
    assert m.sample() == 0
    assert m.rows() == expected(requests)
    assert (m.starts,m.resumes,m.owns,m.walks) == (1,1,False,2)


@pytest.mark.parametrize("requested,usable,marker", [
    (-1,16,-1),(17,16,-1),(40,128,-1),(72,128,-1),(40,64,0),(16385,32768,-1),
])
def test_inconsistent_object_family_prefix_fails_closed(requested,usable,marker):
    m = Snapshot()
    m.object(requested,usable=usable,marker=marker)
    assert m.sample() == -1
    assert (m.starts,m.resumes,m.owns) == (1,1,False)


def test_empty_receipt_is_all_zero_and_restores_world():
    m=Snapshot()
    m.store(m.out,0,99)
    assert m.sample()==0 and m.rows()==expected([])
    assert (m.starts,m.resumes,m.owns,m.walks)==(1,1,False,1)


def test_unexpected_null_enumerator_cell_fails_closed():
    m=Snapshot()
    m.objects.append(0)
    assert m.sample()==-1 and (m.starts,m.resumes,m.owns)==(1,1,False)


def test_unexpected_reserved_cell_fails_closed():
    m=Snapshot()
    m.object(40,state=0)
    assert m.sample()==-1 and (m.starts,m.resumes,m.owns)==(1,1,False)


@pytest.mark.parametrize("count",[-1,257])
def test_bad_enumerator_count_restores_stop(count):
    m=Snapshot()
    m.invalid_count=count
    assert m.sample()==-1 and (m.starts,m.resumes,m.owns)==(1,1,False)


@pytest.mark.parametrize("field,increment",[(0,1),(1,40),(2,48),(3,48)])
def test_each_counter_overflow_fails_closed(field,increment):
    m=Snapshot()
    m.object(40)
    m.inject_overflow=(bucket_index(40),field,MAX-increment+1)
    assert m.sample()==-1 and (m.starts,m.resumes,m.owns)==(1,1,False)


def test_existing_stop_and_admission_failure():
    m=Snapshot(owns=True);m.object(40)
    assert m.sample()==0 and m.rows()==expected([40])
    assert (m.starts,m.resumes,m.owns)==(0,0,True)
    m=Snapshot(stop_failure=True)
    assert m.sample()==-1 and (m.starts,m.resumes,m.walks)==(1,0,0)


@pytest.mark.parametrize("address,size",[(0,BYTES),(1,BYTES),(1048576,BYTES-1),(1048576,BYTES+8)])
def test_invalid_abi_address_or_size_does_not_stop(address,size):
    m=Snapshot()
    assert m.sample(address,size)==-1 and (m.starts,m.resumes,m.walks)==(0,0,0)


def test_source_scope_keeps_old_dict_abi_and_no_hot_calls():
    import ast
    pipeline=ROOT/"pcc/frontends/python/pipeline_memory.py"
    tree=ast.parse(pipeline.read_text())
    calls=[]
    for node in tree.body:
        if isinstance(node,ast.FunctionDef):
            for part in ast.walk(node):
                if isinstance(part,ast.Call) and isinstance(part.func,ast.Name) and part.func.id=="_managed_size_snapshot":
                    calls.append(node.name)
    assert calls==["_coordinator_managed_size_profile"]
    fn=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=="coordinator_memory_phase")
    text=ast.unparse(fn)
    assert text.index('PCC_PROFILE_COORDINATOR_MEMORY') < text.index('_coordinator_managed_size_profile()')
    assert "_coordinator_dict_storage_profile()" in text


@pytest.mark.integration
def test_native_exact_managed_slab_sizes_and_four_carriers(tmp_path, threaded_pcc_runtime_archive):
    import os, subprocess
    from tests.owned_c_execution import compile_owned_c_with_runtime
    src=ROOT/"tests/fixtures/native/coordinator_managed_sizes.c"
    exe=tmp_path/"coordinator_managed_sizes"
    compile_owned_c_with_runtime(src,exe,runtime_archive=threaded_pcc_runtime_archive)
    for probe in ("0","2"):
        run=subprocess.run([str(exe)],capture_output=True,text=True,timeout=40,
                           env=dict(os.environ,PATH="/nonexistent",PCC_GC_REFCOUNT_PROVENANCE_PROBE=probe))
        assert (run.returncode,run.stdout,run.stderr)==(0,"MANAGED_SIZE_PROFILE_OK\n",""),run


@pytest.mark.integration
def test_native_phase_flag_off_silent_and_on_histogram(tmp_path, threaded_pcc_runtime_archive):
    import os, subprocess
    from pcc.frontends.python.pipeline import compile_python
    src=tmp_path/"managed_size_phase.py"
    src.write_text(
        "from pcc.frontends.python.pipeline_memory import coordinator_memory_phase\n"
        "def main():\n"
        "    coordinator_memory_phase('manifests-ready')\n"
        "    a = (1,2,3,4,5,6,7)\n"
        "    b = (1,2,3,4,5,6,7,8)\n"
        "    coordinator_memory_phase('manifests-ready')\n"
        "    print(len(a)+len(b))\n"
        "main()\n")
    exe=tmp_path/"managed_size_phase"
    compile_python(str(src),str(exe),backend="self",ir_scaffold_mode="on",
                   runtime_archive=str(threaded_pcc_runtime_archive))
    env=dict(os.environ,PATH="/nonexistent",PCC_PROFILE_COORDINATOR_MEMORY="0")
    off=subprocess.run([str(exe)],capture_output=True,text=True,timeout=30,env=env)
    assert (off.returncode,off.stdout,off.stderr)==(0,"15\n","")
    env["PCC_PROFILE_COORDINATOR_MEMORY"]="1"
    on=subprocess.run([str(exe)],capture_output=True,text=True,timeout=30,env=env)
    assert (on.returncode,on.stdout)==(0,"15\n"),on
    prefix="pcc coordinator managed slab sizes phase=manifests-ready status=ok bins="
    lines=[line for line in on.stderr.splitlines() if line.startswith(prefix)]
    assert len(lines)==2,on.stderr
    assert len([line for line in on.stderr.splitlines() if line.startswith("pcc coordinator dict storage phase=manifests-ready status=ok")])==2
    for line in lines:
        bins={}
        for item in line[len(prefix):].split(","):
            if item:
                upper,count,requested,usable,aligned=map(int,item.split(":"))
                assert count>0 and requested<=aligned<=usable
                assert usable>=count*16 and aligned%16==0
                if upper<=512:
                    assert aligned==upper*count
                bins[upper]=count
        assert bins
