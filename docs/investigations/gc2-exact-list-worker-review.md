# GC2 short exact-list mark kernel (review branch only)

This candidate is separate from the GC2 pacer comparison branch. It
adds one test-invoked native entry and does not alter ordinary dispatch,
worker count, scheduling, mark ownership, or other collectors' hot paths.

The caller passes an unchanged traced root slot for the duration of the
call, not an unrooted object address. The kernel takes the same
container lock used by built-in list writers and checks the active
epoch while holding that lock. It admits at most 32 positions on an
exact built-in list. The callback uses stable membership and the
existing atomic shade/to-gray publication path. It does not hold the
global graph lock, save raw slots, mutate the owner color, or retire its
gray credit. It returns zero on fallback, including stale/inactive marks,
graph-held callers, unsupported layouts, and failed stable child
membership. A fallback may leave already shaded children gray; their
credits and published work are preserved, and the caller must still
trace the owner with the ordinary path.

This is not an integration entry with a claim of its own. A future
coordinator must provide owner lifetime and per-claim identity, prevent
finish while the scan or producer batch is outstanding, and revalidate
the epoch before retiring any successful work. Existing atomic shade is
allowed to allocate a token batch; its fallback is existing gray-credit
accounting plus the cursor walk, not loss of a marked child. The test
does not inject allocation failure. The small length limit is a review
boundary, not a policy or a measured pause guarantee.

Validation command, with a source-matched owned archive:
`PCC_RUNTIME_ARCHIVE=<archive> uv run pytest -m integration tests/python/test_gc2_short_list_review_native.py`.
The controlled mark fixture checks the original child in two slots
produces one gray credit and a later public append supplies the other.
It also checks rejection while inactive, with the graph lock held,
with the wrong epoch, for a tuple, and for an oversized list. The
other four GC backends are controls. No bootstrap, full suite,
QPS, or macOS execution was attempted on this branch.

Linux x86-64: the owned archive built under the resource guard; three
fresh GC2 subprocesses and GC0/1/3/4 negative controls passed. The ABI
chunk suite passed eight host cases. This does not cover final remark,
full-cycle liveness, helper abort, C extensions, or measured scaling.
