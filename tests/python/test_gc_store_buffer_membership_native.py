"""Exact GC4 store-buffer owner index over a real self-emitted index object.

The host C compiler only assembles/links the harness, as in
test_gc_remembered_membership_native.py. A pcc1 GC4 compile walked an
8,384-entry store buffer on every object free and counted distinct owners
quadratically on every enqueue; these are the queries that replace both.
"""

from pathlib import Path
import shutil
import subprocess

from pcc.backend.self_backend_dispatch import emit_self_asm
from pcc.frontends.python import pipeline
from pcc.frontends.python.pipeline_targets import host_target_triple


ROOT = Path(__file__).resolve().parents[2]


def test_store_buffer_membership_real_self_emitted_index_object(tmp_path):
    compiler = shutil.which("cc")
    assert compiler, "native index-object test needs the host C harness linker"
    source = ROOT / "pcc/runtime/py/freestanding_gc_index_table.py"
    ir = tmp_path / "gc_index.ll"
    pipeline.compile_python(
        str(source), str(ir), emit_llvm_only=True, libpython_mode="off",
        python_library=True, target_triple=host_target_triple(),
    )
    assembly = tmp_path / "gc_index.s"
    assembly.write_text(emit_self_asm(ir.read_text(encoding="utf-8")), encoding="utf-8")
    harness = tmp_path / "store_buffer.c"
    harness.write_text(r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

void pcc_gc_store_buffer_member_note(void *);
void pcc_gc_store_buffer_member_taint(void);
int64_t pcc_gc_store_buffer_member_maybe(void *);
void pcc_gc_store_buffer_member_forget(void *);
int64_t pcc_gc_store_buffer_member_owner_count(void);
void pcc_gc_store_buffer_member_clear(void);
extern int64_t pcc_py_gc_store_buffer_member_cap;
extern int64_t pcc_py_gc_store_buffer_member_incomplete;
static int fail_allocation;
static uint64_t keys[1024];
/* Deterministic native allocator doubles, not the production allocator. */
static union { max_align_t align; unsigned char bytes[1 << 20]; } arena;
static size_t arena_used;
#define CHECK(expression) do { if (!(expression)) { \
    fprintf(stderr, "line %d: %s\n", __LINE__, #expression); exit(1); \
} } while (0)

void *calloc(size_t count, size_t size) {
    if (fail_allocation) return NULL;
    CHECK(size == 0 || count <= SIZE_MAX / size);
    size_t bytes = count * size;
    size_t align = _Alignof(max_align_t);
    size_t start = (arena_used + align - 1) / align * align;
    CHECK(start <= sizeof(arena.bytes) && bytes <= sizeof(arena.bytes) - start);
    void *ptr = &arena.bytes[start];
    memset(ptr, 0, bytes);
    arena_used = start + bytes;
    return ptr;
}
void *malloc(size_t size) { return calloc(1, size); }
void free(void *ptr) { (void)ptr; }
void *pcc_allocator_object_side_slot(void *ptr, int64_t create) {
    (void)ptr; (void)create; return NULL;
}
void *pcc_allocator_granule_object_slot(void *ptr) { (void)ptr; return NULL; }

int main(void) {
    CHECK(pcc_gc_store_buffer_member_owner_count() == 0);
    CHECK(pcc_gc_store_buffer_member_maybe(&keys[0]) == 0);
    for (int i = 0; i < 100; i++) pcc_gc_store_buffer_member_note(&keys[i]);
    pcc_gc_store_buffer_member_note(&keys[0]);
    CHECK(pcc_gc_store_buffer_member_owner_count() == 100);
    CHECK(pcc_gc_store_buffer_member_maybe(&keys[0]) == 1);
    CHECK(pcc_gc_store_buffer_member_maybe(&keys[99]) == 1);
    CHECK(pcc_gc_store_buffer_member_maybe(&keys[150]) == 0);
    pcc_gc_store_buffer_member_forget(&keys[0]);
    CHECK(pcc_gc_store_buffer_member_maybe(&keys[0]) == 0);
    CHECK(pcc_gc_store_buffer_member_owner_count() == 99);
    pcc_gc_store_buffer_member_forget(&keys[500]);
    CHECK(pcc_gc_store_buffer_member_owner_count() == 99);

    /* A failed table growth loses history: every query is "maybe" until clear. */
    int64_t cap = pcc_py_gc_store_buffer_member_cap;
    fail_allocation = 1;
    for (int i = 100; i < 1024 && pcc_py_gc_store_buffer_member_incomplete == 0; i++)
        pcc_gc_store_buffer_member_note(&keys[i]);
    fail_allocation = 0;
    CHECK(pcc_py_gc_store_buffer_member_cap == cap);
    CHECK(pcc_py_gc_store_buffer_member_incomplete == 1);
    CHECK(pcc_gc_store_buffer_member_owner_count() == -1);
    CHECK(pcc_gc_store_buffer_member_maybe(&keys[1000]) == 1);
    pcc_gc_store_buffer_member_note(&keys[1001]);
    pcc_gc_store_buffer_member_forget(&keys[5]);
    CHECK(pcc_gc_store_buffer_member_maybe(&keys[5]) == 1);
    pcc_gc_store_buffer_member_clear();
    CHECK(pcc_py_gc_store_buffer_member_incomplete == 0);
    CHECK(pcc_gc_store_buffer_member_owner_count() == 0);
    CHECK(pcc_gc_store_buffer_member_maybe(&keys[5]) == 0);

    /* A dropped entry taints the epoch the same way. */
    pcc_gc_store_buffer_member_note(&keys[7]);
    pcc_gc_store_buffer_member_taint();
    CHECK(pcc_gc_store_buffer_member_owner_count() == -1);
    CHECK(pcc_gc_store_buffer_member_maybe(&keys[8]) == 1);
    pcc_gc_store_buffer_member_clear();
    CHECK(pcc_gc_store_buffer_member_maybe(&keys[7]) == 0);
    puts("store-buffer-membership-native-ok");
    return 0;
}
''', encoding="utf-8")
    binary = tmp_path / "store_buffer"
    built = subprocess.run(
        [compiler, "-std=c11", "-fno-builtin", str(assembly), str(harness),
         "-o", str(binary)],
        capture_output=True, text=True, timeout=30,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "store-buffer-membership-native-ok\n"


def test_store_buffer_entry_index_real_self_emitted_index_object(tmp_path):
    # Buffered entries per owner (a multiplicity) and per recorded slot
    # (presence) answer the enqueue duplicate check, owner fanout and the
    # drain's "owner still pending" without walking either queue.
    compiler = shutil.which("cc")
    assert compiler, "native index-object test needs the host C harness linker"
    source = ROOT / "pcc/runtime/py/freestanding_gc_index_table.py"
    ir = tmp_path / "gc_index.ll"
    pipeline.compile_python(
        str(source), str(ir), emit_llvm_only=True, libpython_mode="off",
        python_library=True, target_triple=host_target_triple(),
    )
    assembly = tmp_path / "gc_index.s"
    assembly.write_text(emit_self_asm(ir.read_text(encoding="utf-8")), encoding="utf-8")
    harness = tmp_path / "entry_index.c"
    harness.write_text(r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>

void pcc_gc_store_buffer_entry_note(void *, void *);
void pcc_gc_store_buffer_entry_forget(void *, void *);
int64_t pcc_gc_store_buffer_owner_entries(void *);
int64_t pcc_gc_store_buffer_slot_pending(void *);
void pcc_gc_store_buffer_slot_move(void *, void *);
void pcc_gc_store_buffer_index_taint(void);
void pcc_gc_store_buffer_index_clear(void);
extern int64_t pcc_py_gc_store_buffer_index_incomplete;

#define CHECK(e) do { if (!(e)) { printf("FAIL %d %s\n", __LINE__, #e); return 1; } } while (0)

static int64_t owners[64];
static int64_t slots[1100];

void *pcc_allocator_object_side_slot(void *ptr, int64_t create) {
    (void)ptr; (void)create; return NULL;
}
void *pcc_allocator_granule_object_slot(void *ptr) { (void)ptr; return NULL; }

int main(void) {
    CHECK(pcc_gc_store_buffer_owner_entries(&owners[0]) == 0);
    CHECK(pcc_gc_store_buffer_slot_pending(&slots[0]) == 0);
    for (int i = 0; i < 1000; i++) pcc_gc_store_buffer_entry_note(&owners[i % 10], &slots[i]);
    CHECK(pcc_gc_store_buffer_owner_entries(&owners[3]) == 100);
    CHECK(pcc_gc_store_buffer_slot_pending(&slots[999]) == 1);
    CHECK(pcc_gc_store_buffer_slot_pending(&slots[1000]) == 0);
    /* An owner-wide (NULL slot) entry counts toward its owner only. */
    pcc_gc_store_buffer_entry_note(&owners[20], NULL);
    pcc_gc_store_buffer_entry_note(&owners[20], NULL);
    CHECK(pcc_gc_store_buffer_owner_entries(&owners[20]) == 2);
    pcc_gc_store_buffer_entry_forget(&owners[20], NULL);
    CHECK(pcc_gc_store_buffer_owner_entries(&owners[20]) == 1);
    for (int i = 3; i < 1000; i += 10) pcc_gc_store_buffer_entry_forget(&owners[3], &slots[i]);
    CHECK(pcc_gc_store_buffer_owner_entries(&owners[3]) == 0);
    CHECK(pcc_gc_store_buffer_slot_pending(&slots[13]) == 0);
    CHECK(pcc_gc_store_buffer_slot_pending(&slots[14]) == 1);
    /* A relocated owner payload moves its recorded slots. */
    pcc_gc_store_buffer_slot_move(&slots[14], &slots[1000]);
    CHECK(pcc_gc_store_buffer_slot_pending(&slots[14]) == 0);
    CHECK(pcc_gc_store_buffer_slot_pending(&slots[1000]) == 1);
    CHECK(pcc_py_gc_store_buffer_index_incomplete == 0);
    /* Forgetting an entry the index never recorded is an inconsistency:
       every answer becomes unknown until the queues are cleared. */
    pcc_gc_store_buffer_entry_forget(&owners[3], &slots[3]);
    CHECK(pcc_gc_store_buffer_owner_entries(&owners[9]) == -1);
    CHECK(pcc_gc_store_buffer_slot_pending(&slots[5]) == -1);
    pcc_gc_store_buffer_entry_note(&owners[5], &slots[1001]);
    pcc_gc_store_buffer_index_clear();
    CHECK(pcc_py_gc_store_buffer_index_incomplete == 0);
    CHECK(pcc_gc_store_buffer_slot_pending(&slots[1000]) == 0);
    CHECK(pcc_gc_store_buffer_slot_pending(&slots[1001]) == 0);
    CHECK(pcc_gc_store_buffer_owner_entries(&owners[5]) == 0);
    /* A path that cannot keep the index exact taints it explicitly. */
    pcc_gc_store_buffer_entry_note(&owners[7], &slots[7]);
    pcc_gc_store_buffer_index_taint();
    CHECK(pcc_gc_store_buffer_slot_pending(&slots[7]) == -1);
    pcc_gc_store_buffer_index_clear();
    CHECK(pcc_gc_store_buffer_slot_pending(&slots[7]) == 0);
    puts("store-buffer-entry-index-native-ok");
    return 0;
}
''', encoding="utf-8")
    binary = tmp_path / "entry_index"
    built = subprocess.run(
        [compiler, "-std=c11", "-fno-builtin", str(assembly), str(harness),
         "-o", str(binary)],
        capture_output=True, text=True, timeout=30,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "store-buffer-entry-index-native-ok\n"
