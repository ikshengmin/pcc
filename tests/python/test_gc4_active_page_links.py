"""Live-page predecessor invariants over actual runtime function bodies.

Contract: docs/project-intent.md shared GC roots and ordinary object lifetime.
The host model checks bounded raw accesses, all publication/removal paths and
linear structural work; it is not timing or native concurrency evidence. The
archive probe separately executes the production entry points and ABI layout.
"""
from __future__ import annotations

import ast
from itertools import permutations
from pathlib import Path

import pytest

from tests.python.test_gc4_first_reservation_zero import Model


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime/py"
PREFIX = "pcc_gc_backend4_"
PAGE_BYTES = 384
PREV = 376
NEXT = 56


class PageModel(Model):
    def __init__(self):
        super().__init__("candidate")
        self.reads = []
        self.owners = {}
        self.cascade = 0
        self.trash = 0
        self.link_failure = False
        self.ns["c_ptr"] = int
        for name in (
            "freestanding_gc_zpage_mechanics.py",
            "freestanding_gc_zpage_lifecycle.py",
            "freestanding_gc_forwarding_retirement.py",
        ):
            tree = ast.parse((RUNTIME / name).read_text(encoding="utf-8"))
            tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
            for node in tree.body:
                node.decorator_list = []
            exec(compile(tree, str(RUNTIME / name), "exec"), self.ns)
        self.ns.update({
            "pcc_gc_zpage_owner_index_upsert": self.upsert,
            "pcc_gc_zpage_owner_index_upsert_preallocated": self.upsert,
            "pcc_gc_zpage_owner_index_find": lambda owner: self.owners.get(owner, 0),
            "pcc_gc_zpage_owner_index_remove": lambda owner: self.owners.pop(owner, 0),
            "pcc_gc_object_index_find": lambda owner: 0,
            "pcc_gc_object_known_size": lambda owner: 0,
            "pcc_dealloc_cascade_active": lambda: self.cascade,
            "pcc_dealloc_trash_pending": lambda: self.trash,
        })

    def load(self, p, o, n):
        if hasattr(self, "reads"):
            self.reads.append((p, o, n))
        return super().load(p, o, n)

    def upsert(self, owner, node):
        if self.link_failure:
            return -1
        self.owners[owner] = node
        return 1

    def zero(self, size):
        p = self.malloc(size)
        self.memset(p, 0, size)
        return p

    def get(self, name):
        return self.ns["global_load_ptr"](PREFIX + name)

    def put(self, name, value):
        self.ns["global_store_ptr"](PREFIX + name, value)

    def word(self, page, offset):
        return self.load(page, offset, 8)

    def write(self, page, offset, value):
        self.store(page, offset, value, 8)

    def locked_call(self, name, *args):
        self.lock()
        try:
            return self.call(name, *args)
        finally:
            self.unlock()

    def track(self, publication="try", size=4096):
        if publication == "try":
            owner = self.call("try_zpage_alloc", size, 0)
            self.store(owner, 12, 65536, 4)
            node = self.locked_call("zpage_track_alloc", owner, size)
        elif publication == "legacy":
            owner = self.zero(size)
            node = self.locked_call("zpage_track_alloc", owner, size)
        else:
            assert publication == "selected"
            owner = self.zero(size)
            prepared = self.call("zpage_track_page_prepare", 0, owner, size)
            node_io, page_io = self.zero(8), self.zero(8)
            self.write(node_io, 0, self.call("zpage_node_prepare"))
            self.write(page_io, 0, prepared)
            node = self.locked_call(
                "zpage_track_alloc_selected", owner, size,
                node_io, page_io, 0, 0, -1,
            )
            assert self.word(node_io, 0) == self.word(page_io, 0) == 0
        assert node
        page = self.word(node, 8)
        assert len(self.mem[page]) == PAGE_BYTES
        return owner, page, node

    def assert_live(self, expected):
        page, prev, found = self.get("page_head"), 0, []
        while page:
            assert page not in found, "live-page cycle"
            assert self.word(page, PREV) == prev
            found.append(page)
            prev, page = page, self.word(page, NEXT)
        assert found == expected

    def detach(self, owner):
        return self.locked_call("zpage_detach_for_relocation", owner)


@pytest.mark.parametrize("publication", ["try", "legacy", "selected"])
@pytest.mark.parametrize("order", list(permutations(range(4))))
def test_actual_publications_and_every_removal_order(publication, order):
    model = PageModel()
    records = [model.track(publication) for _ in range(4)]
    expected = [page for _, page, _ in reversed(records)]
    model.assert_live(expected)
    for index in order:
        owner, page, node = records[index]
        assert model.detach(owner) == node
        expected.remove(page)
        model.assert_live(expected)
        assert model.word(page, PREV) == 0
        assert model.detach(owner) == 0
        # Cached pages share +56, but cannot alter the live list on a miss.
        snapshot = model.state()
        model.locked_call("zpage_unlink_page", page)
        assert model.state() == snapshot


def test_unlink_preserves_next_and_ignores_null_private_and_repeated_pages():
    model = PageModel()
    records = [model.track() for _ in range(3)]
    tail, middle, head = [page for _, page, _ in records]
    private = model.call("zpage_track_page_prepare", 0, 0, 4096)
    model.write(private, NEXT, head)
    for page in (0, private):
        before = model.state()
        model.locked_call("zpage_unlink_page", page)
        assert model.state() == before
    for page, expected in ((middle, [head, tail]), (head, [tail]), (tail, [])):
        nxt = model.word(page, NEXT)
        model.locked_call("zpage_unlink_page", page)
        assert model.word(page, NEXT) == nxt
        assert model.word(page, PREV) == 0
        model.assert_live(expected)
        before = model.state()
        model.locked_call("zpage_unlink_page", page)
        assert model.state() == before


def test_nonmember_with_nonmatching_predecessor_is_not_unlinked():
    model = PageModel()
    _, live, _ = model.track()
    private = model.call("zpage_track_page_prepare", 0, 0, 4096)
    model.write(private, PREV, live)
    before = model.state()
    model.locked_call("zpage_unlink_page", private)
    assert model.state() == before
    model.assert_live([live])


@pytest.mark.parametrize("count", [1, 8, 64, 256])
def test_unlink_total_raw_page_reads_are_linear_not_triangular(count):
    model = PageModel()
    # Real publication bodies populate every predecessor. Remove oldest first,
    # the adversarial order for the historical scan from the newest head.
    pages = [model.track()[1] for _ in range(count)]
    reads = 0
    for page in pages:
        model.reads.clear()
        model.locked_call("zpage_unlink_page", page)
        page_reads = [(base, off) for base, off, _ in model.reads
                      if base in pages]
        assert len(page_reads) <= 3
        reads += len(page_reads)
    assert reads <= 3 * count
    model.assert_live([])


@pytest.mark.parametrize("forwarded", [False, True])
def test_deferred_sweep_unlinks_only_after_cascade_and_forwarding_finish(forwarded):
    model = PageModel()
    records = [model.track() for _ in range(4)]
    pages = [page for _, page, _ in reversed(records)]
    model.cascade = 1
    for owner, page, _ in records:
        if forwarded:
            model.write(page, 96, 1)
        model.detach(owner)
    model.assert_live(pages)
    assert model.get("deferred_recycle_pages") == (0 if forwarded else 4)
    model.call("sweep_deferred_recycles")
    model.assert_live(pages)
    if forwarded:
        for _, page, _ in records:
            model.locked_call("note_forwarding_removed_on_page", page)
        assert model.get("deferred_recycle_pages") == 4
    model.cascade = 0
    model.call("sweep_deferred_recycles")
    model.assert_live([])
    assert model.get("deferred_recycle_pages") == 0
    assert all(model.word(page, PREV) == 0 for _, page, _ in records)


def test_pending_reservation_keeps_empty_page_linked_until_last_owner_leaves():
    model = PageModel()
    _, tail, _ = model.track()
    owner, page, _ = model.track(size=2048)
    pending_owner = model.call("try_zpage_alloc", 2048, 0)
    model.store(pending_owner, 12, 65536, 4)
    _, head, _ = model.track()
    model.detach(owner)
    assert model.word(page, 32) == 0
    assert model.word(page, 88) == 1
    model.assert_live([head, page, tail])
    node = model.locked_call("zpage_track_alloc", pending_owner, 2048)
    assert model.word(node, 8) == page
    assert model.word(page, 88) == 0
    model.detach(pending_owner)
    model.assert_live([head, tail])
    assert model.word(page, PREV) == 0


@pytest.mark.parametrize("publication", ["try", "legacy", "selected"])
@pytest.mark.parametrize("size", [8192, 70000])
def test_medium_and_large_page_links_keep_existing_field_offsets(publication, size):
    model = PageModel()
    owner, page, _ = model.track(publication, size)
    assert model.word(page, 8) == size
    assert model.word(page, 16) == (65536 if size == 8192 else 131072)
    assert model.load(page, 24, 4) == (1 if size == 8192 else 2)
    assert model.word(page, 112)
    assert not any(model.mem[page][120:376])
    model.assert_live([page])
    model.detach(owner)
    model.assert_live([])
    assert model.word(page, PREV) == 0


def test_forward_retirement_parks_then_retains_without_stale_predecessors():
    model = PageModel()
    records = [model.track() for _ in range(3)]
    expected = [page for _, page, _ in reversed(records)]
    for owner, page, _ in records:
        model.write(page, 96, 1)
        model.detach(owner)
    model.assert_live(expected)
    for _, page, _ in records:
        model.locked_call("note_forwarding_removed_on_page", page)
        expected.remove(page)
        model.assert_live(expected)
        assert model.word(page, PREV) == 0
    # Source retirement order releases old retained pages before draining new
    # parked pages. The newly retained list cannot be released in that epoch.
    assert model.ns["_release_retained_pages"]() == 0
    model.call("drain_parked_pages")
    assert all(model.word(page, PREV) == 0 for _, page, _ in records)
    released = model.ns["_release_retained_pages"]()
    model.call("finish_retained_page_releases", released)
    for _, page, _ in records:
        assert page in model.freed


def test_cache_limit_large_retention_and_reset_clear_predecessor():
    model = PageModel()
    records = [model.track() for _ in range(10)]
    for owner, _, _ in records:
        model.detach(owner)
    assert model.call("free_page_count_for_class", 0) == 8
    assert model.get("retained_page_head")
    owner, page, _ = model.track(size=70000)
    model.detach(owner)
    assert model.get("retained_page_head") == page
    assert model.word(page, PREV) == 0
    reused = model.locked_call("zpage_pop_free_page", 4096)
    assert model.word(reused, PREV) == 0
    model.write(reused, PREV, page)
    model.call("zpage_reset", reused, 0, 4096)
    assert model.word(reused, PREV) == 0
    model.write(reused, PREV, page)
    model.call("zpage_clear_reusable_state", reused)
    assert model.word(reused, PREV) == 0


@pytest.mark.parametrize("from_free", [0, 1])
@pytest.mark.parametrize("failure", ["none", "take", "link"])
def test_selected_prepare_commit_and_rollback_leave_predecessors_consistent(from_free, failure):
    model = PageModel()
    _, old_head, _ = model.track()
    owner = model.zero(4096)
    prepared = model.call("zpage_track_page_prepare", 0, owner, 4096)
    node_io, page_io = model.zero(8), model.zero(8)
    model.write(page_io, 0, prepared)
    if failure != "take":
        model.write(node_io, 0, model.call("zpage_node_prepare"))
    model.link_failure = failure == "link"
    result = model.locked_call(
        "zpage_track_alloc_selected", owner, 4096,
        node_io, page_io, from_free, 0, -1,
    )
    if failure == "none":
        assert result
        model.assert_live([prepared, old_head])
        assert model.word(page_io, 0) == 0
    else:
        assert not result
        model.assert_live([old_head])
        assert model.word(page_io, 0) == prepared
        assert model.word(prepared, PREV) == 0


@pytest.mark.parametrize("publication", ["try", "selected"])
def test_unused_prepared_free_page_remains_off_live_list(publication):
    model = PageModel()
    owner, prepared, _ = model.track()
    model.detach(owner)
    if publication == "try":
        # The competing allocator wins after this thread pops/resets the free
        # page. Publish it through a real allocator, then reuse its first slot.
        def competitor(m):
            if m.locks == target_lock:
                m.hook = None
                other_owner, other_page, _ = m.track(size=128)
                m.competitor_page = other_page
                m.competitor_owner = other_owner
        target_lock = model.locks + 2
        model.hook = competitor
        assert model.call("try_zpage_alloc", 128, 0)
        model.assert_live([model.competitor_page])
    else:
        popped = model.locked_call("zpage_pop_free_page", 4096)
        assert popped == prepared
        model.call("zpage_reset", prepared, 0, 4096)
        _, live, _ = model.track(size=128)
        owner = model.zero(128)
        node_io, page_io = model.zero(8), model.zero(8)
        model.write(node_io, 0, model.call("zpage_node_prepare"))
        model.write(page_io, 0, prepared)
        assert model.locked_call("zpage_track_alloc_selected", owner, 128,
                                 node_io, page_io, 1, live, -1)
        model.assert_live([live])
        assert model.word(page_io, 0) == 0
    assert model.get("free_page_head") == prepared
    assert model.word(prepared, PREV) == 0
    before = model.state()
    model.locked_call("zpage_unlink_page", prepared)
    assert model.state() == before


def test_page_layout_and_unlink_have_no_unbounded_walk():
    allocation = (RUNTIME / "freestanding_gc_zpage_allocation.py").read_text()
    assert allocation.count("malloc(384)") == 3
    assert "malloc(376)" not in allocation
    assert allocation.count("store_ptr(head, 376, page)") == 3
    lifecycle = ast.parse((RUNTIME / "freestanding_gc_zpage_lifecycle.py").read_text())
    unlink = next(node for node in lifecycle.body if isinstance(node, ast.FunctionDef)
                  and node.name == PREFIX + "zpage_unlink_page")
    assert not any(isinstance(node, (ast.For, ast.While)) for node in ast.walk(unlink))


NATIVE_PROBE = r'''
#include "py_runtime.h"
#include <stddef.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Internal raw ABI. The prefix stays fixed; only the predecessor is appended. */
typedef struct Page {
    void *owner;
    int64_t used, capacity;
    int32_t page_class, generation;
    int64_t count, pinned, remembered;
    struct Page *next;
    int64_t allocated;
    void *span;
    int64_t span_capacity, pending, forwarding;
    int32_t deferred, scratch;
    void *owners;
    int32_t cards[64];
    struct Page *previous;
} Page;
_Static_assert(offsetof(Page, next) == 56, "next ABI");
_Static_assert(offsetof(Page, owners) == 112, "owner-list ABI");
_Static_assert(offsetof(Page, cards) == 120, "card ABI");
_Static_assert(offsetof(Page, previous) == 376, "predecessor ABI");
_Static_assert(sizeof(Page) == 384, "page allocation ABI");

extern Page *pcc_gc_backend4_page_head;
extern Page *pcc_gc_backend4_free_page_head;
extern Page *pcc_gc_backend4_parked_head;
extern Page *pcc_gc_backend4_retained_page_head;
extern _Atomic int64_t pcc_dealloc_cascades_active;
extern int64_t pcc_gc_backend4_deferred_recycle_pages;
extern void pcc_py_gc_minor_graph_lock(void);
extern void pcc_py_gc_minor_graph_unlock(void);
extern void *pcc_gc_backend4_try_zpage_alloc(int64_t, int64_t);
extern void *pcc_gc_backend4_zpage_track_alloc(void *, int64_t);
extern void *pcc_gc_backend4_zpage_track_page_prepare(void *, void *, int64_t);
extern void *pcc_gc_backend4_zpage_node_prepare(void);
extern void *pcc_gc_backend4_zpage_track_alloc_selected(
    void *, int64_t, void *, void *, int64_t, void *, int64_t);
extern int64_t pcc_gc_zpage_owner_index_plan_capacity(int64_t);
extern int64_t pcc_gc_zpage_owner_index_plan_commit(void *, int64_t, int64_t);
extern int64_t pcc_gc_index_slot_size(void);
extern void *pcc_gc_backend4_zpage_detach_for_relocation(void *);
extern void pcc_gc_backend4_zpage_finish_relocation_detach(void *);
extern void pcc_gc_backend4_zpage_unlink_page(void *);
extern void pcc_gc_backend4_sweep_deferred_recycles(void);
extern void pcc_gc_backend4_note_forwarding_removed_on_page(void *);
extern void pcc_gc_backend4_drain_parked_pages(void);
extern void *pcc_gc_backend4_release_retained_pages_unlocked(void);
extern void pcc_gc_backend4_finish_retained_page_releases(void *);

static Page *pages[12];
static void *owners[12];
static void *nodes[12];
static int alive[12];
static Page *baseline;

static int check_live(void) {
    Page *cur = pcc_gc_backend4_page_head, *prev = NULL;
    for (int i = 11; i >= 0; --i) {
        if (!alive[i]) continue;
        if (cur != pages[i] || cur->previous != prev) return 0;
        prev = cur;
        cur = cur->next;
    }
    return cur == baseline && (!baseline || baseline->previous == prev);
}

static int detach(int i) {
    pcc_py_gc_minor_graph_lock();
    void *node = pcc_gc_backend4_zpage_detach_for_relocation(owners[i]);
    pcc_py_gc_minor_graph_unlock();
    if (node != nodes[i]) return 0;
    pcc_gc_backend4_zpage_finish_relocation_detach(node);
    if (i % 3 != 0) free(owners[i]);
    return 1;
}

int main(void) {
    if (pcc_gc_set_backend(PCC_GC_KIND_COLORED_RELOCATING) != 0) return 2;
    baseline = pcc_gc_backend4_page_head;
    int64_t cap = pcc_gc_zpage_owner_index_plan_capacity(16);
    if (cap < 0) return 3;
    void *slots = cap ? calloc((size_t)cap, (size_t)pcc_gc_index_slot_size()) : NULL;
    if (cap && !slots) return 4;
    pcc_py_gc_minor_graph_lock();
    int64_t admitted = pcc_gc_zpage_owner_index_plan_commit(&slots, cap, 16);
    pcc_py_gc_minor_graph_unlock();
    free(slots);
    if (admitted < 0) return 5;
    for (int i = 0; i < 12; ++i) {
        void *prepared = NULL, *node = NULL;
        owners[i] = i % 3 == 0 ? pcc_gc_backend4_try_zpage_alloc(4096, 0)
                               : calloc(1, 4096);
        if (!owners[i]) return 6;
        if (i % 3 == 0) *(int32_t *)((char *)owners[i] + 12) = 65536;
        if (i % 3 == 2) {
            prepared = pcc_gc_backend4_zpage_track_page_prepare(NULL, owners[i], 4096);
            node = pcc_gc_backend4_zpage_node_prepare();
            if (!prepared || !node) return 7;
        }
        pcc_py_gc_minor_graph_lock();
        if (i % 3 == 2) {
            nodes[i] = pcc_gc_backend4_zpage_track_alloc_selected(
                owners[i], 4096, &node, &prepared, 0, NULL, -1);
            if (node || prepared) return 8;
        } else {
            nodes[i] = pcc_gc_backend4_zpage_track_alloc(owners[i], 4096);
        }
        pcc_py_gc_minor_graph_unlock();
        if (!nodes[i]) return 9;
        pages[i] = *(Page **)((char *)nodes[i] + 8);
        alive[i] = 1;
        if (pcc_gc_backend4_page_head != pages[i]) return 10;
        if (!check_live()) return 11;
    }
    Page private_page = {0};
    private_page.next = pages[11];
    pcc_py_gc_minor_graph_lock();
    pcc_gc_backend4_zpage_unlink_page(NULL);
    pcc_gc_backend4_zpage_unlink_page(&private_page);
    pcc_py_gc_minor_graph_unlock();
    if (!check_live() || private_page.next != pages[11]) return 12;

    /* Oldest, middle, newest: immediate detach invokes the hot unlink. */
    int immediate[] = {0, 5, 11};
    for (int j = 0; j < 3; ++j) {
        int i = immediate[j];
        if (!detach(i)) return 13;
        alive[i] = 0;
        if (!check_live() || pages[i]->previous) return 14;
        Page *cached_next = pages[i]->next;
        pcc_py_gc_minor_graph_lock();
        pcc_gc_backend4_zpage_unlink_page(pages[i]);
        pcc_py_gc_minor_graph_unlock();
        if (!check_live() || pages[i]->next != cached_next) return 15;
    }
    /* Forwarding keeps exhausted pages live until the dedicated caller. */
    for (int i = 1; i < 11; ++i) {
        if (!alive[i]) continue;
        pages[i]->forwarding = 1;
        if (!detach(i)) return 16;
        if (!check_live() || pages[i]->deferred != 2) return 17;
    }
    pcc_py_gc_minor_graph_lock();
    for (int i = 1; i < 11; ++i) {
        if (!alive[i]) continue;
        pcc_gc_backend4_note_forwarding_removed_on_page(pages[i]);
        alive[i] = 0;
        if (!check_live() || pages[i]->previous) return 18;
    }
    void *released = pcc_gc_backend4_release_retained_pages_unlocked();
    pcc_gc_backend4_drain_parked_pages();
    pcc_py_gc_minor_graph_unlock();
    pcc_gc_backend4_finish_retained_page_releases(released);
    if (pcc_gc_backend4_parked_head || !pcc_gc_backend4_retained_page_head) return 19;
    pcc_py_gc_minor_graph_lock();
    released = pcc_gc_backend4_release_retained_pages_unlocked();
    pcc_py_gc_minor_graph_unlock();
    pcc_gc_backend4_finish_retained_page_releases(released);
    if (pcc_gc_backend4_retained_page_head) return 20;

    /* Republish a cached page, then drive the real deferred detach and sweep
     * bodies with the raw cascade-count input held active by this probe. */
    void *obj = pcc_gc_backend4_try_zpage_alloc(4096, 0);
    if (!obj) return 21;
    *(int32_t *)((char *)obj + 12) = 65536;
    pcc_py_gc_minor_graph_lock();
    void *node = pcc_gc_backend4_zpage_track_alloc(obj, 4096);
    if (!node) return 22;
    Page *page = *(Page **)((char *)node + 8);
    if (page->previous || page->next != baseline) return 23;
    atomic_store(&pcc_dealloc_cascades_active, 1);
    if (pcc_gc_backend4_zpage_detach_for_relocation(obj) != node) return 24;
    if (page->deferred != 1 || pcc_gc_backend4_deferred_recycle_pages != 1) return 25;
    pcc_py_gc_minor_graph_unlock();
    pcc_gc_backend4_zpage_finish_relocation_detach(node);
    pcc_gc_backend4_sweep_deferred_recycles();
    if (pcc_gc_backend4_page_head != page) return 26;
    atomic_store(&pcc_dealloc_cascades_active, 0);
    pcc_gc_backend4_sweep_deferred_recycles();
    if (pcc_gc_backend4_page_head != baseline || page->previous) return 27;
    if (pcc_gc_backend4_deferred_recycle_pages) return 28;
    puts("page-links:3-publications,3-removal-callers,cache,retirement");
    return 0;
}
'''


@pytest.mark.integration
@pytest.mark.xdist_group("gc4_active_page_links_native")
def test_native_archive_executes_page_publication_and_unlink_paths(tmp_path, pcc_runtime_archive):
    # The unchanged C ABI probe uses the owned frontend, emitter and linker.
    from tests.owned_runtime_c_fixture import link_c_harness_bounded
    from tests.python.process_timeout import run_process_group_timeout

    source = tmp_path / "gc4_active_page_links.c"
    executable = tmp_path / "gc4_active_page_links"
    source.write_text(NATIVE_PROBE, encoding="utf-8")
    built = link_c_harness_bounded(
        source, executable, runtime_archive=pcc_runtime_archive, timeout=30,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    result = run_process_group_timeout([str(executable)], timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "page-links:3-publications,3-removal-callers,cache,retirement\n"
