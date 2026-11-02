"""Stopped mutators retain raw roots even after blocking-entry frames return."""
from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/freestanding_thread_kernel_pthread.py"


class SnapshotMemory:
    def __init__(self):
        self.words = {}
        self.record = 0x8000
        self.low = 0x1000
        self.high = self.low + 64
        self.next = 0x10000
        self.freed = []
        self.allocations = []
        self.epoch = 1
        self.globals = {"pcc_tls_thread_record_py": self.record,
                        "pcc_thread_records_head_py": self.record,
                        "pcc_thread_conservative_roots_enabled": 1}
        self.words[self.record] = 0
        self.words[self.record + 32] = self.high - 16
        self.words[self.record + 48] = 0
        self.words[self.record + 56] = 0
        self.words[self.record + 64] = 0
        for offset in range(0, 64, 8):
            self.words[self.low + offset] = 0xABC000 + offset
        self.namespace = {
            "i64": int, "global_addr": lambda name: name,
            "global_load_ptr": self.globals.__getitem__,
            "atomic_load_i32": lambda name, offset, order: self.globals[name],
            "ptr_add": lambda base, offset: base + offset,
            "ptr_to_int": int, "ptr_is_null": lambda ptr: ptr == 0,
            "load_i64": lambda base, offset: self.words[base + offset],
            "load_ptr": lambda base, offset: self.words[base + offset],
            "store_i64": self.store, "store_ptr": self.store,
            "null": lambda: 0, "malloc": self.allocate, "free": self.freed.append,
            "memcpy": self.copy, "capture_gc_registers": self.capture,
            "pcc_platform_abort": self.abort,
            "pcc_thread_world_stop_epoch_held": lambda: self.epoch,
            "call_void_ptr2": lambda callback, word, context: callback(word, context),
        }
        tree = ast.parse(SOURCE.read_text())
        selected = []
        names = {"_thread_snapshot_roots", "_thread_record_mark", "pcc_thread_visit_conservative_words"}
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id.startswith("_THREAD_"):
                    selected.append(node)
            if isinstance(node, ast.FunctionDef) and node.name in names:
                node.decorator_list = []
                selected.append(node)
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), self.namespace)

    def store(self, base, offset, value):
        self.words[base + offset] = value

    def allocate(self, size):
        result = self.next
        self.next += size + 256
        self.allocations.append(size)
        return result

    def copy(self, destination, source, size):
        assert size % 8 == 0
        for offset in range(0, size, 8):
            self.words[destination + offset] = self.words[source + offset]

    def capture(self, buffer):
        for offset in range(0, 128, 8):
            self.words[buffer + offset] = 0xDEF000 + offset
        return self.low

    def abort(self):
        raise RuntimeError("invalid stack snapshot")


@pytest.mark.parametrize("state", (1, 2))
def test_published_park_and_exit_snapshots_survive_source_stack_reuse(state):
    memory = SnapshotMemory()
    memory.namespace["_thread_record_mark"](state, 0x1238)
    for offset in range(0, 64, 8):
        memory.words[memory.low + offset] = 0
    captured = []
    count = memory.namespace["pcc_thread_visit_conservative_words"](
        lambda word, context: context.append(word), captured,
    )
    assert count == 24
    assert captured == [0xDEF000 + offset for offset in range(0, 128, 8)] + [
        0xABC000 + offset for offset in range(0, 64, 8)
    ]
    assert memory.words[memory.record + 16] == state
    assert memory.words[memory.record + 24] == 0x1238


def test_disabled_snapshots_leave_the_existing_park_protocol_unchanged():
    memory = SnapshotMemory()
    memory.globals["pcc_thread_conservative_roots_enabled"] = 0
    memory.namespace["_thread_record_mark"](1, 0x1238)
    assert memory.allocations == []
    assert memory.words[memory.record + 56] == 0
    assert memory.words[memory.record + 16] == 1


def test_snapshot_storage_reuses_capacity_but_replaces_live_words():
    memory = SnapshotMemory()
    capture = memory.namespace["_thread_snapshot_roots"]
    capture(memory.record)
    first = memory.words[memory.record + 48]
    memory.words[memory.low] = 0xFACE
    capture(memory.record)
    assert memory.allocations == [64]
    assert memory.words[first] == 0xFACE
    memory.words[memory.record + 32] += 16
    memory.words[memory.low + 64] = 1
    memory.words[memory.low + 72] = 2
    capture(memory.record)
    assert memory.allocations == [64, 80]
    assert first in memory.freed
    assert memory.words[memory.record + 56] == 80


@pytest.mark.parametrize("epoch,initialized", ((-1, True), (1, False)))
def test_running_or_uncaptured_mutators_cannot_be_reported_as_scanned(epoch, initialized):
    memory = SnapshotMemory()
    if initialized:
        memory.namespace["_thread_snapshot_roots"](memory.record)
    memory.epoch = epoch
    seen = []
    assert memory.namespace["pcc_thread_visit_conservative_words"](
        lambda word, context: context.append(word), seen,
    ) == -1
    assert seen == []


@pytest.mark.parametrize("high", (0, 0x1000 - 8, 0x1000 + 1073741825))
def test_invalid_stack_bounds_fail_before_copying(high):
    memory = SnapshotMemory()
    memory.words[memory.record + 32] = high - 16
    with pytest.raises(RuntimeError, match="invalid stack snapshot"):
        memory.namespace["_thread_snapshot_roots"](memory.record)
    assert memory.allocations == []


NATIVE_PROBE = r'''
extern void pcc_thread_enable_conservative_roots(void);
extern long pcc_thread_visit_conservative_words(void *, void *);
extern long pcc_thread_start(void *, void *, void *);
extern long pcc_thread_join(void *, void *);
extern long pcc_thread_blocking_enter(void *);
extern void pcc_thread_blocking_leave(long);
extern void pcc_thread_safepoint(void);
extern long pcc_stop_the_world(void);
extern long pcc_resume_world(void);
extern void pcc_platform_abort(void);
static long ready = 0;
static long quitting = 0;

static void visit(void *word, void *context) {
    long value = (long)word;
    if (value >= 5368709120 && value <= 5368709123)
        *(long *)context |= 1L << (value - 5368709120);
}

static void *worker(void *argument) {
    long index = (long)argument;
    volatile long root = 5368709120 + index;
    long exited = 0;
    if (index < 3) exited = pcc_thread_blocking_enter(__builtin_frame_address(0));
    __atomic_add_fetch(&ready, 1, __ATOMIC_RELEASE);
    while (!__atomic_load_n(&quitting, __ATOMIC_ACQUIRE)) {
        if (index == 3) pcc_thread_safepoint();
    }
    if (index < 3) pcc_thread_blocking_leave(exited);
    return (void *)root;
}

long snapshot_stress(void) {
    pcc_thread_enable_conservative_roots();
    volatile long owner_root = 5368709120;
    void *handles[3];
    for (long i = 0; i < 3; ++i)
        if (pcc_thread_start(&handles[i], worker, (void *)(i + 1)) != 0)
            pcc_platform_abort();
    while (__atomic_load_n(&ready, __ATOMIC_ACQUIRE) < 3) pcc_thread_safepoint();
    if (pcc_stop_the_world() != 0) pcc_platform_abort();
    long bits = 0;
    long words = pcc_thread_visit_conservative_words(visit, &bits);
    if (pcc_resume_world() != 0) pcc_platform_abort();
    __atomic_store_n(&quitting, 1, __ATOMIC_RELEASE);
    for (long i = 0; i < 3; ++i) {
        void *result = 0;
        if (pcc_thread_join(handles[i], &result) != 0) pcc_platform_abort();
        if ((long)result != owner_root + i + 1) return 20 + i;
    }
    if (words <= 0) return 30;
    return bits;
}
'''


@pytest.mark.integration
def test_owned_runtime_captures_owner_parked_and_exited_threads(tmp_path, pcc_runtime_archive):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.python.test_owned_external_tls import _c_ir

    target = host_target_triple()
    helper = tmp_path / "snapshot_probe.o"
    helper.write_bytes(emit_owned_object(_c_ir(NATIVE_PROBE, target), target))
    source = tmp_path / "snapshot_probe.py"
    source.write_text('''from pcc.extern import extern, c_int64
snapshot = extern("snapshot_stress", (), c_int64)
def main():
    print(snapshot())
main()
''')
    output = tmp_path / "snapshot_probe"
    compile_python(str(source), str(output), backend="self",
                   runtime_archive=str(pcc_runtime_archive), object_inputs=(str(helper),))
    for gc in range(5):
        result = subprocess.run([str(output)], capture_output=True, text=True, timeout=20,
                                env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(gc)))
        assert (result.returncode, result.stdout, result.stderr) == (0, "15\n", ""), gc
