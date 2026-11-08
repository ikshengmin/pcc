"""Exact evacuation-list memo under existing graph-lock ownership.

The host model executes production function ASTs with deterministic memory shims.
It proves list answers, invalidation and avoided node reads, not native threading.
The separate native probe exercises graph-lock handoff and real add/remove/reset.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


RUNTIME = Path(__file__).resolve().parents[2] / "pcc" / "runtime" / "py"
PAGE = "pcc_gc_backend4_evacuation_find_memo_page"
NODE = "pcc_gc_backend4_evacuation_find_memo_node"
MEMO_REVISION = "pcc_gc_backend4_evacuation_find_memo_revision"
REVISION = "pcc_gc_backend4_reseed_page_revision"


class EvacuationModel:
    def __init__(self, threaded: int, depth: int):
        self.threaded = threaded
        self.depth = depth
        self.globals = {}
        self.memory = {}
        self.freed = set()
        self.reads = []
        self.next_address = 4096
        self.fail_malloc = False
        self.ns = {
            "null": lambda: 0,
            "ptr_is_null": lambda p: int(p == 0),
            "ptr_eq": lambda a, b: int(a == b),
            "global_addr": lambda name: name,
            "global_load_ptr": lambda name: self.load(name, 0),
            "global_store_ptr": lambda name, value: self.store(name, 0, value),
            "load_ptr": self.load,
            "load_i32": self.load,
            "load_i64": self.load,
            "store_ptr": self.store,
            "store_i32": self.store,
            "store_i64": self.store,
            "malloc": self.malloc,
            "free": self.free,
            "pcc_threads_enabled": lambda: self.threaded,
            "pcc_py_gc_minor_graph_lock_depth": lambda: self.depth,
            "_backend4_clear_active_page": lambda page: None,
        }
        names = {
            "_evacuation_page_head",
            "_set_evacuation_page_head",
            "_backend4_evacuation_page_find",
            "_backend4_evacuation_page_add",
            "_backend4_evacuation_page_remove",
            "_backend4_evacuation_page_add_preallocated",
        }
        source = RUNTIME / "py_gc_backend.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        tree.body = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        assert {node.name for node in tree.body} == names
        for node in tree.body:
            node.decorator_list = []
        exec(compile(tree, str(source), "exec"), self.ns)

    def load(self, base, offset):
        self.reads.append((base, offset))
        if isinstance(base, str):
            return self.globals.get(base, 0)
        assert base not in self.freed
        return self.memory[base].get(offset, 0)

    def store(self, base, offset, value):
        if isinstance(base, str):
            self.globals[base] = value
        else:
            assert base not in self.freed
            self.memory[base][offset] = value

    def malloc(self, size):
        if self.fail_malloc:
            return 0
        address = self.next_address
        self.next_address += size + 16
        self.memory[address] = {}
        return address

    def free(self, address):
        assert address not in self.freed
        self.freed.add(address)

    def call(self, name, *args):
        return self.ns["_backend4_evacuation_page_" + name](*args)

    def memo(self):
        return tuple(self.globals.get(name, 0) for name in (PAGE, NODE, MEMO_REVISION))


@pytest.mark.parametrize("threaded,depth", [(0, 0), (1, 1), (1, 2)])
def test_exact_memo_reuses_hits_and_misses_and_invalidates(threaded, depth):
    model = EvacuationModel(threaded, depth)
    page = model.malloc(376)
    other = model.malloc(376)
    assert model.call("add", other) == 1
    assert model.call("find", page) == 0
    model.reads.clear()
    assert model.call("find", page) == 0
    assert not any(isinstance(base, int) for base, _ in model.reads)

    assert model.call("add", page) == 1
    found = model.call("find", page)
    assert found != 0
    assert model.load(found, 0) == page
    # A stale/reset page flag is not an authoritative list membership test.
    model.store(page, 108, 0)
    model.reads.clear()
    assert model.call("find", page) == found
    assert not any(isinstance(base, int) for base, _ in model.reads)

    model.call("remove", page)
    assert found in model.freed
    assert model.call("find", page) == 0
    assert model.memo() == (page, 0, model.globals[REVISION] + 1)
    assert model.call("find", other) != 0
    assert model.call("find", page) == 0


def test_preallocated_add_and_failed_add_preserve_exact_invalidation():
    model = EvacuationModel(1, 1)
    page = model.malloc(376)
    available = model.malloc(8)
    node = model.malloc(16)
    model.store(available, 0, node)
    assert model.call("find", page) == 0
    assert model.call("add_preallocated", page, available) == 1
    assert model.call("find", page) == node
    memo = model.memo()
    assert model.call("add_preallocated", page, available) == 0
    assert model.memo() == memo
    model.call("remove", page)
    assert model.call("find", page) == 0
    revision = model.globals[REVISION]
    model.fail_malloc = True
    assert model.call("add", page) == 0
    assert model.globals[REVISION] == revision
    assert model.call("find", page) == 0


@pytest.mark.parametrize("depth", [1, 2])
def test_locked_results_match_uncached_list_with_duplicate_and_stale_flags(depth):
    def scenario(model):
        page = model.malloc(376)
        other = model.malloc(376)
        results = [model.call("find", page), model.call("find", 0)]
        assert model.call("add", page) == 1
        first = model.call("find", page)
        results.extend([first, model.call("find", page)])
        # Preserve the actual list answer even with duplicate nodes / flag 0.
        model.store(page, 108, 0)
        assert model.call("add", page) == 1
        second = model.call("find", page)
        assert second != first
        results.append(second)
        model.call("remove", page)
        assert model.load(page, 108) == 0
        assert model.call("find", page) == first
        results.extend([model.call("find", page), model.call("find", other)])
        model.call("remove", page)
        results.append(model.call("find", page))
        globals_without_memo = {
            name: value for name, value in model.globals.items()
            if name not in (PAGE, NODE, MEMO_REVISION)
        }
        return results, globals_without_memo, model.memory, model.freed

    assert scenario(EvacuationModel(1, depth)) == scenario(EvacuationModel(1, 0))


def test_unlocked_threaded_lookup_never_reads_or_publishes_shared_memo():
    model = EvacuationModel(1, 0)
    page = model.malloc(376)
    assert model.call("add", page) == 1
    # Deliberately wrong memo proves this path follows the list, not the cache.
    model.globals.update({PAGE: page, NODE: 0, MEMO_REVISION: 2})
    before = model.memo()
    for _ in range(2):
        model.reads.clear()
        assert model.call("find", page) != 0
        assert not any(base in (PAGE, NODE, MEMO_REVISION) for base, _ in model.reads)
        assert model.memo() == before
    model.reads.clear()
    assert model.call("find", 0) == 0
    assert model.reads == []


def test_lock_handoff_revalidates_after_another_thread_changes_the_list():
    model = EvacuationModel(1, 1)
    page = model.malloc(376)
    assert model.call("find", page) == 0
    model.depth = 0
    # The next serialized graph owner adds the page, then releases ownership.
    model.depth = 1
    assert model.call("add", page) == 1
    model.depth = 0
    model.depth = 1
    found = model.call("find", page)
    assert found != 0
    model.call("remove", page)
    model.depth = 0
    model.depth = 1
    assert model.call("find", page) == 0
    assert found in model.freed


@pytest.mark.parametrize("filename,name", [
    ("py_gc_backend.py", "pcc_gc_reset_relocation_set"),
    ("py_gc_backend.py", "_backend4_evacuation_page_add"),
    ("py_gc_backend.py", "_backend4_evacuation_page_remove"),
    ("py_gc_backend.py", "_backend4_evacuation_page_add_preallocated"),
    ("freestanding_gc_relocation_selector.py", "_backend4_add_candidate_node"),
    (
        "freestanding_gc_relocation_copy.py",
        "pcc_gc_backend4_relocate_copy_preallocated_unlocked",
    ),
])
def test_evacuation_list_mutators_keep_revision_invalidation(filename, name):
    source = (RUNTIME / filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    body = ast.get_source_segment(source, function)
    assert REVISION in body
    assert "revision + 1" in body


@pytest.mark.integration
def test_native_threaded_evacuation_memo_handoff_and_reset(
    tmp_path, threaded_pcc_runtime_archive,
):
    import textwrap

    from tests.owned_c_execution import compile_owned_c_with_runtime
    from tests.python.process_timeout import run_process_group_timeout

    source = tmp_path / "gc4_evacuation_memo.c"
    executable = tmp_path / "gc4_evacuation_memo.out"
    source.write_text(textwrap.dedent(
        r"""
        #include "py_runtime.h"
        #include <pthread.h>
        #include <stdint.h>
        #include <stdio.h>
        #include <stdlib.h>

        extern void pcc_py_gc_minor_graph_lock(void);
        extern void pcc_py_gc_minor_graph_unlock(void);
        extern int64_t pcc_py_gc_minor_graph_lock_depth(void);
        extern void *pcc_gc_backend4_evacuation_page_find(void *);
        extern int64_t pcc_gc_backend4_evacuation_page_add(void *);
        extern void pcc_gc_backend4_evacuation_page_remove(void *);
        extern void *pcc_gc_backend4_evacuation_find_memo_page;
        extern void *pcc_gc_backend4_evacuation_find_memo_node;
        extern int64_t pcc_gc_backend4_evacuation_find_memo_revision;
        extern int64_t pcc_gc_backend4_reseed_page_revision;

        enum { THREADS = 4, ROUNDS = 64 };
        static void *pages[THREADS];

        static int memo_matches(void *page, void *node) {
            return pcc_gc_backend4_evacuation_find_memo_page == page
                && pcc_gc_backend4_evacuation_find_memo_node == node
                && pcc_gc_backend4_evacuation_find_memo_revision
                    == pcc_gc_backend4_reseed_page_revision + 1;
        }

        static void *worker(void *opaque) {
            void *page = opaque;
            if (pcc_current_thread_id() <= 0) return (void *)(intptr_t)1;
            for (int round = 0; round < ROUNDS; round++) {
                int error = 0;
                pcc_py_gc_minor_graph_lock();
                pcc_py_gc_minor_graph_lock();
                if (pcc_py_gc_minor_graph_lock_depth() != 2) error = 2;
                if (pcc_gc_backend4_evacuation_page_find(page) != 0
                    || !memo_matches(page, 0)) error = 3;
                if (pcc_gc_backend4_evacuation_page_add(page) != 1) error = 4;
                void *node = pcc_gc_backend4_evacuation_page_find(page);
                if (node == 0 || !memo_matches(page, node)) error = 5;
                if (pcc_gc_backend4_evacuation_page_find(page) != node) error = 6;
                pcc_py_gc_minor_graph_unlock();
                pcc_gc_backend4_evacuation_page_remove(page);
                if (pcc_gc_backend4_evacuation_page_find(page) != 0
                    || !memo_matches(page, 0)) error = 7;
                pcc_py_gc_minor_graph_unlock();
                if (error) {
                    pcc_thread_unregister_current();
                    return (void *)(intptr_t)error;
                }
            }
            pcc_thread_unregister_current();
            return 0;
        }

        struct Mutation { void *page; int add; };

        static void *mutate_one_page(void *opaque) {
            struct Mutation *mutation = opaque;
            if (pcc_current_thread_id() <= 0) return (void *)(intptr_t)1;
            int error = 0;
            pcc_py_gc_minor_graph_lock();
            if (mutation->add) {
                if (pcc_gc_backend4_evacuation_page_add(mutation->page) != 1) {
                    error = 2;
                }
            } else {
                pcc_gc_backend4_evacuation_page_remove(mutation->page);
            }
            pcc_py_gc_minor_graph_unlock();
            pcc_thread_unregister_current();
            return (void *)(intptr_t)error;
        }

        static int change_on_another_thread(void *page, int add) {
            struct Mutation mutation = {page, add};
            pthread_t thread;
            void *error = 0;
            if (pthread_create(&thread, 0, mutate_one_page, &mutation)) return 1;
            return pthread_join(thread, &error) != 0 || error != 0;
        }

        int main(void) {
            if (pcc_gc_set_backend(PCC_GC_KIND_COLORED_RELOCATING) != 0) return 10;
            if (!pcc_threads_enabled() || pcc_current_thread_id() <= 0) return 11;
            for (int i = 0; i < THREADS; i++) {
                pages[i] = calloc(1, 376);
                if (!pages[i]) return 12;
            }
            pthread_t threads[THREADS];
            for (int i = 0; i < THREADS; i++) {
                if (pthread_create(&threads[i], 0, worker, pages[i])) return 13;
            }
            for (int i = 0; i < THREADS; i++) {
                void *error = 0;
                if (pthread_join(threads[i], &error) || error) return 14;
            }
            /* A cached miss must survive physical unlock, then be rejected
               after another graph owner inserts the same page. */
            pcc_py_gc_minor_graph_lock();
            if (pcc_gc_backend4_evacuation_page_find(pages[0]) != 0
                || !memo_matches(pages[0], 0)) return 18;
            pcc_py_gc_minor_graph_unlock();
            if (change_on_another_thread(pages[0], 1)) return 19;
            pcc_py_gc_minor_graph_lock();
            void *node = pcc_gc_backend4_evacuation_page_find(pages[0]);
            if (!node || !memo_matches(pages[0], node)) return 20;
            void *memo_page = pcc_gc_backend4_evacuation_find_memo_page;
            void *memo_node = pcc_gc_backend4_evacuation_find_memo_node;
            int64_t memo_revision = pcc_gc_backend4_evacuation_find_memo_revision;
            pcc_py_gc_minor_graph_unlock();
            /* No other worker remains: a safe sequential unlocked lookup must
               retain its old traversal path and leave the shared memo alone. */
            if (pcc_gc_backend4_evacuation_page_find(pages[1]) != 0) return 21;
            if (pcc_gc_backend4_evacuation_find_memo_page != memo_page
                || pcc_gc_backend4_evacuation_find_memo_node != memo_node
                || pcc_gc_backend4_evacuation_find_memo_revision != memo_revision) {
                return 22;
            }
            /* The positive cached node is then freed by a different owner. */
            if (change_on_another_thread(pages[0], 0)) return 23;
            pcc_py_gc_minor_graph_lock();
            if (pcc_gc_backend4_evacuation_page_find(pages[0]) != 0
                || !memo_matches(pages[0], 0)) return 24;
            pcc_py_gc_minor_graph_unlock();
            pcc_py_gc_minor_graph_lock();
            if (pcc_gc_backend4_evacuation_page_add(pages[0]) != 1) return 15;
            if (!pcc_gc_backend4_evacuation_page_find(pages[0])) return 16;
            pcc_py_gc_minor_graph_unlock();
            pcc_gc_reset_relocation_set();
            pcc_py_gc_minor_graph_lock();
            if (pcc_gc_backend4_evacuation_page_find(pages[0]) != 0
                || !memo_matches(pages[0], 0)) return 17;
            pcc_py_gc_minor_graph_unlock();
            for (int i = 0; i < THREADS; i++) free(pages[i]);
            puts("gc4-evacuation-memo-threaded-ok");
            return 0;
        }
        """
    ).lstrip(), encoding="utf-8")
    compile_owned_c_with_runtime(
        source, executable, runtime_archive=threaded_pcc_runtime_archive,
    )
    result = run_process_group_timeout([str(executable)], timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "gc4-evacuation-memo-threaded-ok"
