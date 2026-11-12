"""Object-family cells keep their object node in a per-cell slab record.

The tracing collectors registered every object by allocating a node and
inserting it into one global object hash under the graph lock. An exact
object-family allocator cell now uses its record in the slab's record array
(``pcc_allocator_object_node_record_find``) as its node, and the record's index-key
word as its index entry; only objects outside the object slabs still take a
pooled node and the hash. This drives the production allocator, registration,
lookups, the object walk (records are reached through their slab spans, not
the object list), the page mark bits and the freeing note of the runtime
archive under all five collectors.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import textwrap


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = REPO_ROOT / "pcc" / "runtime"
PY_DIR = RUNTIME_DIR / "py"

HARNESS = r'''
    #define _GNU_SOURCE
    #include "py_internal.h"
    #include <stdint.h>
    #include <stdio.h>
    #include <stdlib.h>

    extern void *pcc_allocator_alloc_object(int64_t size);
    extern void *pcc_allocator_object_node_record_find(void *ptr);
    extern void *pcc_allocator_object_node_record_ensure(void *ptr);
    extern int64_t pcc_gc_object_index_seen(void *obj);
    extern int64_t pcc_gc_object_index_seen_stable(void *obj);
    extern int64_t pcc_gc_object_node_is_record(void *node);
    extern void *pcc_gc_object_list_head(void);
    extern void *pcc_gc_object_walk_first(void);
    extern void *pcc_gc_object_walk_next(void *node);
    extern void *pcc_allocator_object_cell_span(void *ptr);
    extern int64_t pcc_gc_page_mark_test(void *obj);
    extern int64_t pcc_gc_object_is_white(void *obj, int64_t flags);
    extern int64_t pcc_gc_alloc_stage_drain_stopped(void);
    extern int64_t pcc_py_gc_object_count;
    extern int64_t pcc_gc_live_bytes;

    enum { NODE_OBJECT = 0, NODE_SIZE = 8, NODE_RECORD_SPAN = 24, RECORD_INDEX_KEY = 88 };

    typedef union {
        uint64_t align[4];
        unsigned char bytes[32];
    } ObjectStorage;

    static ObjectStorage foreign;

    static void *field(void *record, int offset) {
        return *(void **)((char *)record + offset);
    }

    /* 1 when a walk over every registered object visits node. */
    static int walked(void *node) {
        for (void *at = pcc_gc_object_walk_first(); at != NULL;
             at = pcc_gc_object_walk_next(at)) {
            if (at == node) return 1;
        }
        return 0;
    }

    static int64_t known(void *obj) {
        return pcc_gc_object_index_seen(obj) + pcc_gc_object_index_seen_stable(obj);
    }

    static void header(void *obj, int32_t tag) {
        *(int64_t *)obj = 1;
        *(int32_t *)((char *)obj + 8) = tag;
        *(int32_t *)((char *)obj + 12) = 0;
    }

    int main(void) {
        int64_t backend = atoi(getenv("PCC_GC_BACKEND"));
        if (pcc_gc_set_backend(backend) != 0) return 10;
        py_gc_init();
        py_gc_disable();
        int64_t hashed = pcc_py_gc_object_count;
        int64_t live = pcc_gc_live_bytes;
        PyObject *obj = pcc_gc_alloc(64, PY_TYPE_TUPLE, 0);
        if (obj == NULL) return 1;
        pcc_gc_alloc_stage_drain_stopped();
        void *record = pcc_allocator_object_node_record_find(obj);
        if (backend == 0) {
            /* GC0 registers no object node: no record array is mapped. */
            if (record != NULL || pcc_gc_object_index_find(obj) != NULL) return 2;
            printf("records-ok\n");
            return 0;
        }

        /* The cell's record is its node and its index entry. */
        if (record == NULL) return 3;
        if (pcc_gc_object_index_find(obj) != record) return 4;
        if (field(record, NODE_OBJECT) != obj) return 5;
        if (*(int64_t *)((char *)record + NODE_SIZE) != 64) return 6;
        if (field(record, RECORD_INDEX_KEY) != obj) return 7;
        if (pcc_gc_object_node_is_record(record) != 1) return 8;
        /* A record is not on the object list: walks reach it by its span. */
        if (pcc_gc_object_list_head() == record) return 9;
        if (field(record, NODE_RECORD_SPAN) != pcc_allocator_object_cell_span(obj)
            || field(record, NODE_RECORD_SPAN) == NULL) return 39;
        if (!walked(record)) return 40;
        /* Registered outside a mark: white in the page bitmap. */
        if (pcc_gc_page_mark_test(obj) != 0) return 41;
        if (pcc_gc_object_is_white(obj, *(int32_t *)((char *)obj + 12)) != 1) return 42;
        if (known(obj) != 2) return 11;
        if (pcc_gc_object_index_find_stable(obj) != record) return 12;
        if (pcc_py_gc_object_count != hashed) return 13;
        if (pcc_gc_live_bytes != live + 64) return 14;

        /* A second registration of the same object changes nothing. */
        pcc_gc_note_object_allocated_sized(obj, 64);
        if (pcc_gc_object_list_head() == record || !walked(record)) return 15;
        if (pcc_gc_object_index_find(obj) != record) return 16;
        if (pcc_gc_live_bytes != live + 64) return 17;

        /* Interior pointers and other cells never alias the record. */
        void *interior = (char *)obj + 16;
        if (pcc_allocator_object_node_record_ensure(interior) != NULL) return 18;
        if (pcc_gc_object_index_find(interior) != NULL || known(interior) != 0) {
            return 19;
        }
        void *other = pcc_allocator_alloc_object(64);
        if (other == NULL) return 20;
        void *other_record = pcc_allocator_object_node_record_ensure(other);
        if (other_record == NULL || other_record == record) return 21;
        if (field(other_record, RECORD_INDEX_KEY) != NULL) return 22;
        if (pcc_gc_object_index_find(other) != NULL || known(other) != 0) return 23;

        /* Freeing retires the key and unlinks the record. */
        pcc_gc_note_object_freeing(obj);
        if (field(record, RECORD_INDEX_KEY) != NULL) return 24;
        if (pcc_gc_object_index_find(obj) != NULL || known(obj) != 0) return 25;
        if (pcc_gc_object_list_head() == record || walked(record)) return 26;
        if (pcc_gc_live_bytes != live) return 27;

        /* The cell's next object registers through the same record. */
        header(obj, PY_TYPE_TUPLE);
        pcc_gc_note_object_allocated_sized(obj, 48);
        if (pcc_gc_object_index_find(obj) != record) return 28;
        if (pcc_gc_object_list_head() == record || !walked(record)) return 29;
        if (*(int64_t *)((char *)record + NODE_SIZE) != 48) return 30;
        pcc_gc_note_object_freeing(obj);
        if (pcc_gc_object_index_find(obj) != NULL) return 31;
        if (pcc_py_gc_object_count != hashed || pcc_gc_live_bytes != live) return 32;

        /* Storage the object allocator does not own keeps a pooled node
           in the object hash. */
        PyObject *outside = (PyObject *)foreign.bytes;
        header(outside, PY_TYPE_TUPLE);
        pcc_gc_note_object_allocated_sized(outside, 32);
        void *node = pcc_gc_object_index_find(outside);
        if (node == NULL || pcc_gc_object_node_is_record(node) != 0) return 33;
        if (field(node, NODE_OBJECT) != outside) return 34;
        /* A pooled node is on the object list and has no span. */
        if (field(node, NODE_RECORD_SPAN) != NULL || !walked(node)) return 43;
        if (pcc_gc_page_mark_test(outside) != -1) return 44;
        if (pcc_allocator_object_node_record_ensure(outside) != NULL) return 35;
        if (pcc_py_gc_object_count != hashed + 1) return 36;
        pcc_gc_note_object_freeing(outside);
        if (pcc_gc_object_index_find(outside) != NULL) return 37;
        if (pcc_py_gc_object_count != hashed) return 38;
        printf("records-ok\n");
        return 0;
    }
'''


def test_object_cells_register_through_their_record(
    tmp_path: Path, pcc_runtime_archive: Path,
) -> None:
    source = tmp_path / "node_records.c"
    output = tmp_path / "node_records"
    source.write_text(textwrap.dedent(HARNESS), encoding="utf-8")
    built = subprocess.run(
        [os.environ.get("CC", "cc"), "-std=c11", "-pthread",
         f"-I{RUNTIME_DIR / 'include'}", f"-I{RUNTIME_DIR / 'src'}",
         str(source), str(pcc_runtime_archive), "-lm", "-o", str(output)],
        capture_output=True, text=True, timeout=60,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    for backend in range(5):
        ran = subprocess.run(
            [str(output)], capture_output=True, text=True, timeout=10,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
        )
        assert ran.returncode == 0, f"GC{backend}: {ran.stdout}{ran.stderr}"
        assert ran.stdout == "records-ok\n"


def _module_constant(path: Path, name: str) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
            and isinstance(node.value, ast.Constant)
        ):
            return node.value.value
    raise AssertionError(f"{path.name} defines no literal {name}")


def test_record_layout_is_the_node_followed_by_its_index_key() -> None:
    record_bytes = _module_constant(
        PY_DIR / "freestanding_allocator.py", "OBJECT_NODE_RECORD_BYTES"
    )
    index_key = _module_constant(
        PY_DIR / "freestanding_gc_index_table.py", "RECORD_INDEX_KEY"
    )
    node_key = _module_constant(
        PY_DIR / "freestanding_gc_object_nodes.py", "RECORD_INDEX_KEY"
    )
    nodes = (PY_DIR / "freestanding_gc_object_nodes.py").read_text(encoding="utf-8")
    # The pooled node is malloc(88): the record holds exactly one node, then
    # the key word.
    assert nodes.count("malloc(88)") == 2
    assert index_key == node_key == 88
    assert record_bytes == index_key + 8
