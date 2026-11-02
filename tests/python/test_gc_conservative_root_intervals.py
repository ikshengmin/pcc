"""Conservative roots use owned extents, including overlapping payloads."""
from __future__ import annotations

import ast
import os
from pathlib import Path
import platform
import random
import subprocess

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/freestanding_gc_conservative_roots.py"


class IntervalMemory:
    def __init__(self):
        self.words = {}
        self.marked = set()
        self.active = set()
        self.namespace = {
            "i64": int, "ptr_add": lambda base, offset: base + offset,
            "ptr_is_null": lambda pointer: pointer == 0, "ptr_to_int": int,
            "null": lambda: 0,
            "load_i64": self.load, "load_ptr": self.load,
            "store_i64": self.store, "store_ptr": self.store,
            "pcc_gc_object_node_is_active": lambda node: int(node in self.active),
            "pcc_gc_mark_root_gray_if_known": self.marked.add,
            "pcc_platform_abort": self.abort,
        }
        tree = ast.parse(SOURCE.read_text())
        selected = []
        for node in tree.body:
            if isinstance(node, ast.AnnAssign):
                selected.append(node)
            elif isinstance(node, ast.FunctionDef):
                node.decorator_list = []
                selected.append(node)
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), self.namespace)

    def load(self, base, offset):
        # A mistaken dereference of an arbitrary candidate fails here.
        return self.words[base + offset]

    def store(self, base, offset, value):
        self.words[base + offset] = value

    def abort(self):
        raise RuntimeError("invalid extent")

    def install(self, intervals):
        for index, (start, end, owner) in enumerate(intervals):
            self.namespace["_append_interval"](0x10000, index, owner, start, end - start)
            self.words[owner] = owner + 0x400000
            self.words[owner + 56] = 0
            self.active.add(owner)
        self.namespace["pcc_gc_conservative_sort_intervals"](0x10000, len(intervals))
        self.words[0x20000] = 0x10000
        self.words[0x20008] = len(intervals)


@pytest.mark.parametrize("count", (0, 1, 2, 3, 4, 5, 16, 51))
def test_marked_owners_match_independent_interval_membership(count):
    rng = random.Random(8723 + count)
    intervals = []
    for index in range(count):
        start = rng.randrange(1000, 1500)
        intervals.append((start, start + rng.randrange(1, 200), 0x30000 + index * 128))
    memory = IntervalMemory()
    memory.install(intervals)
    for candidate in (0, -1, 9223372036854775807, *range(950, 1750)):
        memory.marked.clear()
        memory.namespace["pcc_gc_conservative_mark_word"](candidate, 0x20000)
        expected = {owner + 0x400000 for start, end, owner in intervals if start <= candidate < end}
        assert memory.marked == expected, (candidate, intervals)


def test_end_is_exclusive_and_dead_owners_are_never_revived():
    memory = IntervalMemory()
    memory.install([(0x5000, 0x5010, 0x30000), (0x5008, 0x5020, 0x30100)])
    memory.active.remove(0x30000)
    memory.namespace["pcc_gc_conservative_mark_word"](0x5009, 0x20000)
    assert memory.marked == {0x430100}
    assert memory.words[0x30000 + 56] == 0
    assert memory.words[0x30100 + 56] == 1
    memory.marked.clear()
    memory.namespace["pcc_gc_conservative_mark_word"](0x5020, 0x20000)
    assert memory.marked == set()


@pytest.mark.parametrize("base,size", ((0, 16), (-1, 16), (1000, 0), (1000, -1),
                                      (9223372036854775800, 16)))
def test_invalid_extents_abort_without_publishing_an_entry(base, size):
    memory = IntervalMemory()
    with pytest.raises(RuntimeError, match="invalid extent"):
        memory.namespace["_append_interval"](0x10000, 0, 0x30000, base, size)
    assert memory.words == {}


class FrozenWordMemory(IntervalMemory):
    def __init__(self, snapshots):
        super().__init__()
        self.snapshots = snapshots
        self.visits = 0
        self.allocations = []
        self.freed = []
        self.next_address = 0x800000
        self.head = 0
        self.graph_locks = []
        self.namespace.update(
            stack_alloc=lambda size: 0x700000,
            malloc=self.allocate,
            free=self.freed.append,
            global_load_ptr=lambda name: self.head,
            pcc_thread_world_stop_epoch_held=lambda: 1,
            pcc_thread_visit_conservative_words=self.visit,
            pcc_py_gc_minor_graph_lock=lambda: self.graph_locks.append(1),
            pcc_py_gc_minor_graph_unlock=lambda: self.graph_locks.pop(),
        )

    def allocate(self, size):
        address = self.next_address
        self.next_address += size + 256
        self.allocations.append((address, size))
        return address

    def visit(self, callback, context):
        snapshot = self.snapshots[min(self.visits, len(self.snapshots) - 1)]
        self.visits += 1
        if snapshot is None:
            return -1
        for word in snapshot:
            callback(word, context)
        return len(snapshot)

    def heap(self, extents):
        """Install collector-owned metadata, never memory at candidate addresses."""
        for index, (base, size, payloads, active) in enumerate(extents):
            node = 0x30000 + index * 128
            self.words.update({node: base, node + 8: size, node + 16: 0,
                               node + 48: 0, node + 56: 0})
            if index:
                self.words[node - 128 + 16] = node
            else:
                self.head = node
            if active:
                self.active.add(node)
            if payloads:
                owner = 0x100000 + index * 128
                self.words[node + 48] = owner
                self.words[owner + 64] = 0x200000 + index * 1024
                for position, (start, length) in enumerate(payloads):
                    span = 0x200000 + index * 1024 + position * 64
                    self.words.update({span + 8: start, span + 16: length,
                                       span + 40: span + 64 if position + 1 < len(payloads) else 0})

    def scan(self):
        self.namespace["pcc_gc_gray_conservative_thread_roots"]()


@pytest.mark.parametrize("word_count", (0, 1, 2, 3, 8, 51, 256))
@pytest.mark.parametrize("owner_count", (0, 1, 19, 257))
def test_frozen_word_index_matches_independent_heap_membership(word_count, owner_count):
    rng = random.Random(8723 + word_count + owner_count)
    words = [rng.randrange(950, 1750) for _ in range(word_count)]
    words += words[:5] + [0, -1, 9223372036854775807]
    extents = [
        (rng.randrange(1000, 1500), rng.randrange(1, 100),
         [(rng.randrange(1000, 1500), rng.randrange(1, 200)) for _ in range(index % 3)],
         index % 7 != 3)
        for index in range(owner_count)
    ]
    memory = FrozenWordMemory([words])
    memory.heap(extents)
    memory.scan()
    expected = {
        base for base, size, payloads, active in extents if active
        and any(start <= word < start + length
                for start, length in [(base, size), *payloads] for word in words)
    }
    assert memory.marked == expected
    for index, (base, size, payloads, active) in enumerate(extents):
        rooted = active and any(start <= word < start + length
                                for start, length in [(base, size), *payloads] for word in words)
        assert memory.words[0x30000 + index * 128 + 56] == int(rooted)
    # Scratch capacity follows the frozen words, regardless of heap population.
    positive_words = sum(0 < word < 9223372036854775807 for word in words)
    assert memory.allocations[0][1] == max(8, 8 * positive_words)
    assert memory.freed == [memory.allocations[0][0]]


def test_word_index_retains_all_overlapping_owners_and_unaligned_interior_roots():
    memory = FrozenWordMemory([[0x5009, 0x5020, 9223372036854775806]])
    memory.heap([
        (0x1000, 16, [(0x5000, 16)], True),
        (0x2000, 16, [(0x5008, 24)], True),
        (0x3000, 16, [(0x5010, 16)], True),
        (9223372036854775800, 7, [], True),
        (0x4000, 16, [(0x5000, 64)], False),
    ])
    memory.scan()
    assert memory.marked == {0x1000, 0x2000, 9223372036854775800}


def test_moving_step_refreshes_pins_without_marking_or_retaining_old_raw_roots():
    memory = FrozenWordMemory([[0x2009]])
    memory.heap([(0x1000, 16, [], True), (0x2000, 16, [], True)])
    memory.words[0x30000 + 56] = 1
    visit = memory.visit

    def locked_visit(callback, context):
        assert memory.graph_locks == [1]
        return visit(callback, context)

    memory.namespace["pcc_thread_visit_conservative_words"] = locked_visit
    memory.namespace["pcc_gc_pin_conservative_thread_roots"]()
    assert memory.words[0x30000 + 56] == 0
    assert memory.words[0x30080 + 56] == 1
    assert memory.marked == set()
    assert memory.graph_locks == []


@pytest.mark.parametrize("snapshots", ([None], [[1000], None], [[1000], []], [[1000], [1000, 1001]]))
def test_missing_or_changing_snapshots_fail_before_marking(snapshots):
    memory = FrozenWordMemory(snapshots)
    memory.heap([(1000, 16, [], True)])
    with pytest.raises(RuntimeError, match="invalid extent"):
        memory.scan()
    assert memory.marked == set()


@pytest.mark.parametrize("base,size", ((0, 16), (-1, 16), (1000, 0), (1000, -1),
                                      (9223372036854775800, 16)))
def test_word_scan_rejects_invalid_owned_extents_even_without_candidates(base, size):
    memory = FrozenWordMemory([[]])
    memory.heap([(base, size, [], True)])
    with pytest.raises(RuntimeError, match="invalid extent"):
        memory.scan()
    assert memory.marked == set()


NATIVE_DRIVER = r'''
extern void pcc_gc_gray_conservative_thread_roots(void);
extern void pcc_gc_pin_conservative_thread_roots(void);
void *pcc_gc_object_head;
static long nodes[3][11];
static long owner[9];
static long span[6];
static long storage[512];
static long candidates[8];
static long candidate_count;
static long marked;
static long visits;
static long graph_depth;
void pcc_py_gc_minor_graph_lock(void) { graph_depth++; }
void pcc_py_gc_minor_graph_unlock(void) { graph_depth--; }
void *malloc(long size) { return size <= 4096 ? storage : (void *)0; }
void free(void *value) { (void)value; }
void pcc_platform_abort(void) { __builtin_trap(); }
long pcc_thread_world_stop_epoch_held(void) { return 1; }
long pcc_gc_object_node_is_active(void *node) { return ((long *)node)[4] == 0; }
void pcc_gc_mark_root_gray_if_known(void *object) {
    if ((long)object == 8192) marked |= 1;
    if ((long)object == 12288) marked |= 2;
    if ((long)object == 16384) marked |= 4;
}
long pcc_thread_visit_conservative_words(void *callback, void *context) {
    for (long i = 0; i < candidate_count; ++i)
        ((void (*)(void *, void *))callback)((void *)candidates[i], context);
    visits++;
    return candidate_count;
}
int main(void) {
    nodes[0][0] = 12288; nodes[0][1] = 32; nodes[0][2] = (long)nodes[1];
    nodes[1][0] = 8192; nodes[1][1] = 16; nodes[1][2] = (long)nodes[2];
    nodes[2][0] = 16384; nodes[2][1] = 16; nodes[2][4] = 1;
    nodes[1][6] = (long)owner; owner[8] = (long)span;
    span[1] = 12280; span[2] = 32;
    pcc_gc_object_head = nodes[0];
    long single_candidates[6] = {8199, 8208, 12290, 12312, 16384, -1};
    long expected[6] = {1, 0, 3, 2, 0, 0};
    candidate_count = 1;
    for (int i = 0; i < 6; ++i) {
        candidates[0] = single_candidates[i]; marked = 0;
        pcc_gc_gray_conservative_thread_roots();
        if (marked != expected[i]) return i + 1;
    }
    /* Execute sorting and duplicate compaction, including both interior roots. */
    candidates[0] = 12312; candidates[1] = 8199; candidates[2] = 0;
    candidates[3] = 12290; candidates[4] = -1; candidates[5] = 8199;
    candidates[6] = 8208; candidates[7] = 9223372036854775807L;
    candidate_count = 8; marked = 0;
    pcc_gc_gray_conservative_thread_roots();
    if (marked != 3) return 8;
    candidates[0] = 8199; candidate_count = 1; marked = 0;
    pcc_gc_pin_conservative_thread_roots();
    if (marked != 0 || graph_depth != 0) return 9;
    if (nodes[0][7] != 0 || nodes[1][7] != 1 || nodes[2][7] != 0) return 10;
    /* Each stopped snapshot is visited once to size and once to fill words. */
    return visits == 16 ? 0 : 7;
}
'''


@pytest.mark.skipif(platform.system() != "Darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 execution")
def test_owned_interval_index_executes_full_build_scan_and_disposal(tmp_path, monkeypatch):
    from pcc.backend.macho_exec import link_executable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from tests.python.test_owned_external_tls import _c_ir

    # This isolated raw-memory kernel probe has no thread runtime. The complete
    # runtime lane retains its production poll policy and real stopped world.
    monkeypatch.setenv("PCC_IMPLICIT_THREAD_SAFEPOINTS", "0")
    output_ir = tmp_path / "intervals.ll"
    compile_python(str(SOURCE), str(output_ir), emit_llvm_only=True,
                   python_library=True, backend="self")
    target = "arm64-apple-darwin"
    kernel = emit_owned_object(output_ir.read_text(), target)
    driver = emit_owned_object(_c_ir(NATIVE_DRIVER, target), target)
    binary = tmp_path / "intervals"
    binary.write_bytes(link_executable([kernel, driver]))
    binary.chmod(0o755)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15,
                            env=dict(os.environ, PATH="/nonexistent"))
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
