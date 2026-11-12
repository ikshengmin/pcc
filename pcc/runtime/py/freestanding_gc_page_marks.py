"""GC pages of the object slabs: generation, flags, live bytes, mark bits.

An object slab that holds a registered cell has a record array
(freestanding_allocator.pcc_allocator_object_node_record_ensure). The same
mapping, after the records, holds the slab's GC page: a header and two mark
bitmaps of one bit per cell. Marking alternates between the two bitmaps
(pcc_gc_page_mark_epoch): a mark sets bits in the current one, the finished
mark's verdict reads it, and the sweep that follows clears the other one for
the next cycle, so no mark ever starts by clearing the heap.

The page header:
- PAGE_GENERATION: 0 young, 1 old (GC4 ages slabs; GC1-GC3 slabs stay 0).
- PAGE_FLAGS: PAGE_FLAG_EVACUATING while GC4 evacuates the slab, so no copy
  target is taken from it.
- PAGE_LIVE_BYTES: the bytes of the slab's marked cells when its last mark
  finished, the density GC4 selects pages by.

Objects outside object slabs (raw, large and static storage) have no page
and keep their colour in the object header; every primitive answers -1 for
them so the caller takes that path.
"""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, extern
from pcc.unsafe import (
    atomic_load_i64,
    atomic_rmw_i64,
    atomic_store_i64,
    global_addr,
    is_tagged_int,
    load_i64,
    null,
    ptr_add,
    ptr_is_null,
    store_i64,
)


__pcc_freestanding__ = True


PAGE_GENERATION = 0
PAGE_FLAGS = 8
PAGE_LIVE_BYTES = 16
PAGE_FLAG_EVACUATING = 1
# The allocator's GC_PAGE_HEADER_BYTES and OBJECT_NODE_RECORD_BYTES.
GC_PAGE_HEADER_BYTES = 64
OBJECT_NODE_RECORD_BYTES = 96

pcc_allocator_object_cell_span = extern(
    "pcc_allocator_object_cell_span", (c_ptr,), c_ptr
)
pcc_allocator_object_cell_number = extern(
    "pcc_allocator_object_cell_number", (c_ptr, c_ptr), c_int64
)
pcc_allocator_record_span_page = extern(
    "pcc_allocator_record_span_page", (c_ptr,), c_ptr
)
pcc_allocator_record_span_mark_words = extern(
    "pcc_allocator_record_span_mark_words", (c_ptr,), c_int64
)


@c_abi_export("pcc_gc_page_mark_epoch")
def pcc_gc_page_mark_epoch() -> i64:
    """Which of a page's two bitmaps the current or next mark sets: 0 or 1."""
    return atomic_load_i64(global_addr("pcc_gc_page_mark_epoch_word"), 0, "acquire")


@c_abi_export("pcc_gc_page_mark_epoch_flip")
def pcc_gc_page_mark_epoch_flip() -> None:
    """Start using the other bitmap. Called with the world stopped when a
    mark begins, after the previous cycle's sweep cleared that bitmap."""
    epoch: i64 = pcc_gc_page_mark_epoch()
    atomic_store_i64(
        global_addr("pcc_gc_page_mark_epoch_word"), 0, 1 - epoch, "release"
    )


@c_abi_export("pcc_gc_page_bitmap")
def pcc_gc_page_bitmap(span: c_ptr, which: i64) -> c_ptr:
    """Bitmap ``which`` (0 or 1) of a record span's GC page, or NULL."""
    page = pcc_allocator_record_span_page(span)
    if ptr_is_null(page) != 0:
        return null()
    words: i64 = pcc_allocator_record_span_mark_words(span)
    return ptr_add(page, GC_PAGE_HEADER_BYTES + which * words * 8)


@c_abi_export("pcc_gc_page_of")
def pcc_gc_page_of(obj: c_ptr) -> c_ptr:
    """The GC page header of an object cell, or NULL for any other object."""
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return null()
    span = pcc_allocator_object_cell_span(obj)
    if ptr_is_null(span) != 0:
        return null()
    return pcc_allocator_record_span_page(span)


@c_abi_export("pcc_gc_page_mark_test")
def pcc_gc_page_mark_test(obj: c_ptr) -> i64:
    """1 when the current bitmap marks ``obj``, 0 when not, -1 when ``obj``
    is not an object cell (its colour is in its header)."""
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return -1
    span = pcc_allocator_object_cell_span(obj)
    if ptr_is_null(span) != 0:
        return -1
    cell: i64 = pcc_allocator_object_cell_number(span, obj)
    bitmap = pcc_gc_page_bitmap(span, pcc_gc_page_mark_epoch())
    word: i64 = atomic_load_i64(bitmap, (cell // 64) * 8, "acquire")
    if (word & (1 << (cell & 63))) != 0:
        return 1
    return 0


@c_abi_export("pcc_gc_page_mark_set")
def pcc_gc_page_mark_set(obj: c_ptr) -> i64:
    """Mark ``obj`` in the current bitmap with one atomic OR: 1 when this
    call marked it, 0 when it was marked already, -1 when ``obj`` is not an
    object cell."""
    if ptr_is_null(obj) != 0 or is_tagged_int(obj) != 0:
        return -1
    span = pcc_allocator_object_cell_span(obj)
    if ptr_is_null(span) != 0:
        return -1
    cell: i64 = pcc_allocator_object_cell_number(span, obj)
    bitmap = pcc_gc_page_bitmap(span, pcc_gc_page_mark_epoch())
    bit: i64 = 1 << (cell & 63)
    old: i64 = atomic_rmw_i64("or", bitmap, (cell // 64) * 8, bit, "acq_rel")
    if (old & bit) != 0:
        return 0
    return 1


@c_abi_export("pcc_gc_page_marks_clear")
def pcc_gc_page_marks_clear(span: c_ptr, which: i64) -> None:
    """Clear bitmap ``which`` of a record span's page (the sweep clears the
    bitmap the next mark will set)."""
    bitmap = pcc_gc_page_bitmap(span, which)
    if ptr_is_null(bitmap) != 0:
        return
    words: i64 = pcc_allocator_record_span_mark_words(span)
    index: i64 = 0
    while index < words:
        atomic_store_i64(bitmap, index * 8, 0, "relaxed")
        index = index + 1


@c_abi_export("pcc_gc_page_generation")
def pcc_gc_page_generation(page: c_ptr) -> i64:
    return load_i64(page, PAGE_GENERATION)


@c_abi_export("pcc_gc_page_set_generation")
def pcc_gc_page_set_generation(page: c_ptr, generation: i64) -> None:
    store_i64(page, PAGE_GENERATION, generation)


@c_abi_export("pcc_gc_page_evacuating")
def pcc_gc_page_evacuating(page: c_ptr) -> i64:
    return atomic_load_i64(page, PAGE_FLAGS, "acquire") & PAGE_FLAG_EVACUATING


@c_abi_export("pcc_gc_page_set_evacuating")
def pcc_gc_page_set_evacuating(page: c_ptr, evacuating: i64) -> None:
    flags: i64 = atomic_load_i64(page, PAGE_FLAGS, "acquire") & ~PAGE_FLAG_EVACUATING
    if evacuating != 0:
        flags = flags | PAGE_FLAG_EVACUATING
    atomic_store_i64(page, PAGE_FLAGS, flags, "release")


@c_abi_export("pcc_gc_page_live_bytes")
def pcc_gc_page_live_bytes(page: c_ptr) -> i64:
    return load_i64(page, PAGE_LIVE_BYTES)


@c_abi_export("pcc_gc_page_set_live_bytes")
def pcc_gc_page_set_live_bytes(page: c_ptr, live: i64) -> None:
    store_i64(page, PAGE_LIVE_BYTES, live)
