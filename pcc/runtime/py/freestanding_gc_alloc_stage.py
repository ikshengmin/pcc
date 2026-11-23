"""Per-thread staging of allocation registrations for count-free GC1/GC2.

Registering a new object links its node into the object list and inserts it
into the object index, both under the graph lock. A mutator therefore took
that lock for every allocation and waited for every other holder: another
mutator, or the collector thread while it frees a finished mark's dead
objects. Here an allocating thread appends the object to its own ring
instead, without a lock, and whoever next holds the graph lock for a reason
that needs the list or the index registers the staged objects in a batch.

Each ring has one producer (its thread) and one consumer at a time (the
graph-lock holder). The producer writes an entry and then publishes its tail
with a release store; a consumer reads the tail with an acquire load, so it
sees complete entries only. Rings are never freed: a thread that exits
releases its ring for the next thread, so a consumer that walks the registry
never meets freed memory.

A staged object is not yet in the object list or the object index. Three
rules keep that invisible to the collector:

* A stopped world begins by registering every staged object
  (pcc_gc_alloc_stage_drain_stopped, from pcc_stop_the_world). Whitening,
  root discovery, remark and the sweep's list walks all run in a stopped
  world, so they see every object allocated before it. The world's owner
  registers its own allocations directly while it holds the world.
* A locked object-index lookup that misses registers the staged objects and
  looks again (pcc_gc_alloc_stage_miss_drain). An object can only reach
  another thread through memory its allocating thread wrote after staging
  it, so by the time a lock holder asks about that address the entry is in
  the ring.
* An unlocked write barrier that does not find the value in the index skips
  the shade. The value was allocated during the active mark (the mark began
  in a stopped world, which registered everything older) and is registered
  black, so it owes no marking work. Registration compares the entry's
  mark epoch with the current one and shades an object that predates the
  active mark, should that ever differ.

The thread kernel compiles this module's callers without implicit stop
polls, and this module is freestanding: no thread parks between reading the
mark phase and publishing its entry.
"""

from pcc import i64
from pcc.extern import c_abi_export, c_int64, c_ptr, c_void, extern
from pcc.unsafe import (
    atomic_load_i32,
    atomic_load_i64,
    atomic_rmw_i64,
    atomic_store_i64,
    define_global_i32,
    define_global_i64,
    define_global_ptr_null,
    define_thread_local_ptr_null,
    global_addr,
    global_load_ptr,
    global_store_ptr,
    int_to_ptr,
    is_tagged_int,
    load_i32,
    load_i64,
    load_ptr,
    malloc,
    null,
    ptr_add,
    ptr_is_null,
    ptr_to_int,
    store_i32,
    store_i64,
    store_ptr,
)


__pcc_freestanding__ = True


pcc_py_gc_minor_graph_lock = extern("pcc_py_gc_minor_graph_lock", (), c_void)
pcc_py_gc_minor_graph_unlock = extern("pcc_py_gc_minor_graph_unlock", (), c_void)


# Ring layout. The registry link and the owner word change under the graph
# lock only; the consumer cursor and the producer cursor sit on their own
# cache lines, away from each other and from the entries.
_RING_NEXT = 0
_RING_OWNED = 8
_RING_HEAD = 64
_RING_TAIL = 128
_RING_ENTRIES = 192
_RING_CAPACITY = 256
_RING_MASK = 255
_RING_BYTES = 8384

_ENTRY_OBJECT = 0
_ENTRY_SIZE = 8
_ENTRY_EPOCH = 16
_ENTRY_BYTES = 32

# Only this module reads its thread-local ring.
define_thread_local_ptr_null("pcc_gc_alloc_stage_local_ring")
define_global_ptr_null("pcc_gc_alloc_stage_rings")
define_global_i32("pcc_gc_alloc_stage_enabled", 0)
# Set by the one consumer while it registers entries: registration looks up
# the index itself and must not start a second drain.
define_global_i32("pcc_gc_alloc_stage_draining", 0)
define_global_i64("pcc_gc_alloc_stage_published", 0)
define_global_i64("pcc_gc_alloc_stage_full_drains", 0)


@c_abi_export("pcc_gc_alloc_stage_drain_locked")
def pcc_gc_alloc_stage_drain_locked() -> i64:
    """Register every staged object; returns how many.

    The caller holds the graph lock, which makes it the only consumer. Each
    entry leaves its ring before the next is read, so the nested lookups of
    a registration never see it twice.
    """
    if load_i32(global_addr("pcc_gc_alloc_stage_draining"), 0) != 0:
        return 0
    store_i32(global_addr("pcc_gc_alloc_stage_draining"), 0, 1)
    published: i64 = 0
    ring = int_to_ptr(
        atomic_load_i64(global_addr("pcc_gc_alloc_stage_rings"), 0, "acquire")
    )
    while ptr_is_null(ring) == 0:
        head: i64 = atomic_load_i64(ring, _RING_HEAD, "relaxed")
        tail: i64 = atomic_load_i64(ring, _RING_TAIL, "acquire")
        while head != tail:
            entry = ptr_add(
                ring, _RING_ENTRIES + (head & _RING_MASK) * _ENTRY_BYTES
            )
            obj = load_ptr(entry, _ENTRY_OBJECT)
            size: i64 = load_i64(entry, _ENTRY_SIZE)
            epoch: i64 = load_i64(entry, _ENTRY_EPOCH)
            head = head + 1
            atomic_store_i64(ring, _RING_HEAD, head, "release")
            published = published + 1
        ring = load_ptr(ring, _RING_NEXT)
    store_i32(global_addr("pcc_gc_alloc_stage_draining"), 0, 0)
    if published != 0:
        atomic_rmw_i64(
            "add", global_addr("pcc_gc_alloc_stage_published"), 0, published,
            "relaxed",
        )
    return published


@c_abi_export("pcc_gc_alloc_stage_thread_exit")
def pcc_gc_alloc_stage_thread_exit() -> None:
    """Register this thread's staged objects and release its ring before its
    thread-local storage disappears."""
    ring = global_load_ptr("pcc_gc_alloc_stage_local_ring")
    if ptr_is_null(ring) != 0:
        return
    pcc_py_gc_minor_graph_lock()
    pcc_gc_alloc_stage_drain_locked()
    store_i64(ring, _RING_OWNED, 0)
    pcc_py_gc_minor_graph_unlock()
    global_store_ptr("pcc_gc_alloc_stage_local_ring", null())
