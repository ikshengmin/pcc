"""A committed GC4 copy preserves reachedness in its real page bitmap.

The existing continuation fixture supplies a selected-list PRECONDITION, not
production selection. Production public copy, payload, forwarding and page
mark/color bodies run through runtime_body_model.load_functions. The bounded
allocator/index/lock providers remain doubles; slab providers describe two
cells, two bitmap words and both epochs explicitly instead of taking the
loader's non-slab defaults. Allocation mirrors its existing color/mark rule.
Both counted and count-free modes explicitly run production py_incref and
py_decref. Checked-count tails are instrumented bounded doubles only in
counted mode; calling either in count-free mode fails the test. Empty
continuations have no owned child slots, so no counted retain-plan double
can silently stand in for count-free payload ownership.

This is copy-transaction unit coverage only. It does not establish a normal
constructor-to-selector path, native movement, allocator concurrency, source
retirement, finalizer behavior or private-target OOM disposal. The original
native positive movement oracle remains unchanged and must pass separately.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.python.runtime_body_model import load_functions
from tests.python.test_gc3_norc_source_retirement import PY
from tests.python.test_gc4_copy_continuation_roots import _continuation


WHITE, GRAY, BLACK = 8, 16, 32
SWEEP_CANDIDATE = 1024
PAGE = PY / "freestanding_gc_page_marks.py"


def _page_copy(*, active, epoch, count_free, shared_page=True, generation=0,
               slab=True):
    # No raw continuation payload is needed for this independent mark
    # contract. The full public-copy transaction still runs on production
    # functions, including the prepared forwarding installer.
    f = _continuation(absent_chunk=True, registered=False)
    m = f.m
    # _continuation inherits GC3(counted=True). Set the mode deliberately;
    # GC4 configuration leaves both GC0-only count fast paths disabled.
    m.store("pcc_gc_backend4_no_refcounts", 0, int(count_free))
    m.store("pcc_gc_refcount_atomic", 0, 0)
    m.store("pcc_gc_refcount_fast", 0, 0)
    checked_counts = []
    original_incref_tail = m.ns["_py_incref_checked_tail"]
    original_decref_tail = m.ns["_py_decref_checked_tail"]

    def incref_tail(obj):
        assert not count_free, "count-free forwarding entered a checked RC tail"
        checked_counts.append(("incref", obj))
        original_incref_tail(obj)

    def decref_tail(obj):
        assert not count_free, "count-free copy cleanup entered a checked RC tail"
        checked_counts.append(("decref", obj))
        original_decref_tail(obj)

    m.ns.update(_py_incref_checked_tail=incref_tail,
                _py_decref_checked_tail=decref_tail)
    load_functions(PY / "py_obj.py", {"py_incref", "py_decref"}, m.ns)
    with m.locked():
        assert m.ns["pcc_gc_relocation_payload_slot_count_locked"](f.source) == 0
        assert m.ns["pcc_gc_object_is_address_pinned"](f.source) == 0
    m.ns["pcc_gc_retain_plan_prepare_locked"] = lambda *args: pytest.fail(
        "empty continuation unexpectedly used a child retain-plan double")
    m.store("pcc_gc_mark_active", 0, int(active))
    m.store("pcc_gc_gray_count", 0, 0)
    m.store("pcc_gc_page_mark_epoch_word", 0, epoch)
    m.store(f.source, 12, (m.load(f.source, 12) & ~56) | BLACK | SWEEP_CANDIDATE)
    source_span, target_span = m.allocate(8), m.allocate(8)
    source_page, target_page = m.allocate(96), m.allocate(96)
    if shared_page:
        target_span, target_page = source_span, source_page
    pages = {source_span: source_page, target_span: target_page}
    cells = ({f.source: (source_span, 63), f.target: (target_span, 64)}
             if slab else {})
    for page in set(pages.values()):
        m.zero(page, 0, 96)
        m.store(page, 0, generation)
        m.store(page, 16, 48)
        # Unrelated cells in BOTH words and epochs must survive the copy.
        for which in (0, 1):
            m.store(page, 64 + which * 16, 1 << 5)
            m.store(page, 72 + which * 16, 1 << 9)
    m.ns.update({
        "pcc_allocator_object_cell_span": lambda obj: cells.get(obj, (0, 0))[0],
        "pcc_allocator_object_cell_number": lambda span, obj: cells[obj][1],
        "pcc_allocator_record_span_page": pages.__getitem__,
        "pcc_allocator_record_span_mark_words": lambda span: 2,
    })
    load_functions(PAGE, {
        "pcc_gc_page_mark_epoch", "pcc_gc_page_bitmap",
        "pcc_gc_page_mark_assign", "pcc_gc_page_mark_set",
        "pcc_gc_page_mark_test", "pcc_gc_object_is_white",
        "pcc_gc_object_is_black",
    }, m.ns)
    mark = m.ns["pcc_gc_page_mark_set"]
    assign = m.ns["pcc_gc_page_mark_assign"]
    assign(f.source, 1)
    # Reused cells can carry an old object's mark; fresh registration clears
    # or sets it according to mark_active before the copy transaction begins.
    assign(f.target, 1)
    allocations, committed_marks = [], []
    original_allocate = m.ns["pcc_gc_alloc"]

    def allocate(size, tag, flags):
        target = original_allocate(size, tag, flags)
        color = BLACK if active else WHITE
        m.store(target, 12, (m.load(target, 12) & ~56) | color)
        assign(target, int(active))
        allocations.append(m.ns["pcc_gc_page_mark_test"](target))
        return target

    def committed_mark(obj):
        assert obj == f.target and m.depth > 0
        # A failed transaction must never publish this reachedness. Check
        # the actual forwarding edge, not a success stub or a copied flag.
        assert f.source in m.forwardings
        assert m.load(m.forwardings[f.source], 8) == f.target
        committed_marks.append(obj)
        return mark(obj)

    m.ns.update(pcc_gc_alloc=allocate, pcc_gc_page_mark_set=committed_mark)
    return SimpleNamespace(f=f, pages=pages, source_page=source_page,
                           target_page=target_page, allocations=allocations,
                           committed_marks=committed_marks, epoch=epoch,
                           count_free=count_free, checked_counts=checked_counts)


def _assert_success_count_mode(case):
    f, m = case.f, case.f.m
    assert m.load("pcc_gc_backend4_no_refcounts") == int(case.count_free)
    assert m.load(f.source, 0) == 1
    # Copy still transfers the source's header count directly in BOTH modes.
    # The extra forwarding retain exists only in counted mode. This does not
    # claim that count-free copy performs no header-count writes at all.
    assert m.load(f.target, 0) == (2 if case.count_free else 3)
    expected = [] if case.count_free else [("incref", f.target)]
    assert case.checked_counts == expected
    # Exercise the real public release of the returned private-copy handle.
    # In count-free mode it must not reach the counted cleanup provider.
    m.ns["py_decref"](f.target)
    assert m.load(f.target, 0) == 2
    expected = ([] if case.count_free else
                [("incref", f.target), ("decref", f.target)])
    assert case.checked_counts == expected
    assert not f.deallocated and f.target not in m.freed


def _bitmap_words(case):
    m = case.f.m
    return {(page, which, word): m.load(page, 64 + which * 16 + word * 8)
            for page in set(case.pages.values())
            for which in (0, 1) for word in (0, 1)}


@pytest.mark.parametrize("count_free", [False, True])
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("epoch", [0, 1])
@pytest.mark.parametrize("shared_page", [False, True])
@pytest.mark.parametrize("generation", [0, 1])
def test_committed_copy_marks_only_target_current_epoch(
    active, epoch, shared_page, generation, count_free
):
    case = _page_copy(active=active, epoch=epoch, shared_page=shared_page,
                      generation=generation, count_free=count_free)
    f, m = case.f, case.f.m
    before = _bitmap_words(case)
    assert m.ns["pcc_gc_page_mark_test"](f.source) == 1
    assert m.ns["pcc_gc_relocate_copy"](f.source, 48) == f.target
    assert case.allocations == [int(active)]
    assert case.committed_marks == [f.target]
    assert m.ns["pcc_gc_page_mark_test"](f.target) == 1
    assert m.ns["pcc_gc_object_is_white"](f.target, m.load(f.target, 12)) == 0
    assert m.ns["pcc_gc_object_is_black"](f.target, m.load(f.target, 12)) == int(not active)
    assert m.load(f.target, 12) & 56 == (GRAY if active else BLACK)
    assert m.load("pcc_gc_gray_count") == int(active)
    assert m.load(f.source, 12) & SWEEP_CANDIDATE == 0
    assert m.load(f.target, 12) & SWEEP_CANDIDATE == 0
    assert m.ns["pcc_gc_page_mark_test"](f.source) == 1
    # The old target mark was cleared on allocation and restored only at
    # commit. No other mark, epoch, generation or density changes.
    assert _bitmap_words(case) == before
    assert m.load("pcc_gc_page_mark_epoch_word") == epoch
    for page in set(case.pages.values()):
        assert m.load(page, 0) == generation
        assert m.load(page, 16) == 48
    assert m.load("pcc_gc_relocation_set_head") == 0
    assert f.relocation in m.freed and m.depth == 0
    _assert_success_count_mode(case)


@pytest.mark.parametrize("count_free", [False, True])
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("epoch", [0, 1])
@pytest.mark.parametrize("failure", [
    "forwarding-plan", "validation", "payload", "forwarding-install",
])
def test_uncommitted_copy_does_not_publish_target_mark(
    active, epoch, failure, count_free
):
    case = _page_copy(active=active, epoch=epoch, generation=1,
                      count_free=count_free)
    f, m = case.f, case.f.m
    before = _bitmap_words(case)
    source_flags = m.load(f.source, 12)
    if failure == "forwarding-plan":
        # Exercise the real plan allocator's OOM path, not a success/failure
        # replacement for the public-copy transaction.
        m.fail_size = 72
    elif failure == "validation":
        m.ns["pcc_gc_relocation_payload_plan_validate_locked"] = lambda *args: 0
    elif failure == "payload":
        m.ns["pcc_gc_relocate_copy_payload_prepared_locked"] = lambda *args: 0
    else:
        m.ns["pcc_gc_forwarding_plan_index_commit"] = lambda *args: -1
    assert m.ns["pcc_gc_relocate_copy"](f.source, 48) == 0
    assert case.allocations == [int(active)] and case.committed_marks == []
    expected = dict(before)
    target_word = (case.target_page, epoch, 1)
    if not active:
        expected[target_word] &= ~1  # Registration cleared target cell 64.
    assert _bitmap_words(case) == expected
    assert m.load(f.source, 12) == source_flags
    assert m.ns["pcc_gc_page_mark_test"](f.source) == 1
    assert m.load("pcc_gc_page_mark_epoch_word") == epoch
    assert m.load("pcc_gc_relocation_set_head") == f.relocation
    assert f.relocation not in m.freed
    assert not m.forwardings and not m.reverse and not m.identities
    assert m.load("pcc_gc_backend4_no_refcounts") == int(count_free)
    if count_free:
        # Do not emulate counted disposal or hide the outstanding private
        # target abort defect. These cases establish only no early page mark
        # and actual count-free dispatch, not complete rollback reclamation.
        assert case.checked_counts == []
    else:
        assert case.checked_counts == [("decref", f.target)]
        assert f.deallocated == [f.target] and f.target in m.freed
    assert m.depth == 0


@pytest.mark.parametrize("count_free", [False, True])
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("epoch", [0, 1])
def test_non_slab_copy_keeps_header_color_fallback(active, epoch, count_free):
    case = _page_copy(active=active, epoch=epoch, slab=False,
                      count_free=count_free)
    f, m = case.f, case.f.m
    before = _bitmap_words(case)
    assert m.ns["pcc_gc_relocate_copy"](f.source, 48) == f.target
    assert case.allocations == [-1] and case.committed_marks == [f.target]
    assert m.ns["pcc_gc_page_mark_test"](f.target) == -1
    assert m.load(f.target, 12) & 56 == (GRAY if active else BLACK)
    assert m.ns["pcc_gc_object_is_white"](f.target, m.load(f.target, 12)) == 0
    assert m.ns["pcc_gc_object_is_black"](f.target, m.load(f.target, 12)) == int(not active)
    assert m.load("pcc_gc_gray_count") == int(active)
    assert _bitmap_words(case) == before
    _assert_success_count_mode(case)
