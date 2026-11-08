# Bounded GC1/2 scalar retirement

This increment transfers logical index/provenance retirement and physical
release of committed dead scalars to the existing CMS worker. It detaches only
that proven-dead subset, not the live heap. It does not move finalizers, remove
resurrection checks, or implement complete concurrent sweep.

## Admission and ownership

PASS2 admits only exact, untracked builtin INT/FLOAT/STR object-family
allocations after the ordinary weakref invalidation, PASS0 finalizers,
resurrection recheck and PASS1 slot clearing. GC1/2 count-free mode, a frozen
backend, an outer stopped-world owner and a running CMS worker are required.
Tracking, immortality, static literals, pins, leases, gray/black marks,
fresh/minor/zpage allocation, identity/provenance indexes and another
freeing owner exclude the object. A LIVE object-family granule is mandatory;
large raw malloc objects keep their synchronous path.

One stopped-world epoch owns the pending chain. Admission calls the existing
ObjectNode unlink exactly once, which updates active-list links, cursors and
revision. PASS2 saves its successor before admission. The detached node retains
its object, index and negative independent batch-token owner. Its private
next@16, usable-size@40 and original-tag@64 fields are no longer active or young
list links; size@8, pin state@56 and foreign leases@80 retain their meanings.
No queue-node allocation or unretired object-header borrowing occurs.

Claim marks FINALIZED/DEALLOCATING, debits live bytes once and publishes the
ordinary first-death observation through the producer's TLS owner. A later
epoch cannot append while this reservation is building, ready, draining or
quarantined. Busy admission uses ordinary synchronous destruction.

Consumer work and retained backlog have separate bounds:

- Each chunk examines at most 256 nodes and 64 KiB of object usable bytes,
  including entries that fail validation. A single object larger than 64 KiB
  stays synchronous, so the first node of a chunk always fits.
- One epoch can retain at most 65,536 nodes and 4 MiB of object usable bytes.
  Saturation uses synchronous fallback rather than allocating queue storage.
  The corresponding 88-byte nodes, allocator overhead and index occupancy
  also remain resident; 4 MiB is not a process-RSS or total-heap limit.
- Pending physical bytes are not logical live bytes. They remain counted until
  physical release, including quarantined objects. The existing pacing ledger
  is not silently redefined. The raw terminal-free queue keeps its old limits.

Capacity rejections counted before header/index probes are rejected attempts,
not a count of objects proven eligible. Non-scalar destruction stays unchanged.

## Publication and bounded consumption

Both public collection exits seal READY before releasing their world stop.
The registered worker cannot consume while that producer still owns STW.
An empty raw-only scope can close without sealing; nonempty unsealed close
fails closed.

Each normal CMS iteration claims one chunk under the graph lock/no-park region.
For every examined node it verifies the negative token and exact object-index
identity before inspecting the header. Remaining work is globally anchored
before recycling any node. Valid entries undergo:

1. Successful object-family granule retirement.
2. Exact object-index removal.
3. Pool-only node release, without unlinking the already detached node again.
4. Refcount-word clearing and transfer of the raw object to fixed scratch.

The pool-only helper returns overflow rather than freeing a node under the
graph lock. Overflow nodes and object blocks are physically freed after
unlock. A chunk can additionally release at most 256 overflow nodes of
88-byte requested size; these are outside the object-usable byte budget. Pooled nodes never remain in scratch and may immediately be reused by
another allocation. No typed destructor, callback, managed allocation or new
thread runs in the consumer.

DRAINING covers the complete raw-release tail. Only afterward are released
count/bytes subtracted and READY, EMPTY or QUARANTINED published. Remaining and
quarantined chains are always anchored when unlock may park. A new allocation
belongs to the active heap, never the detached death chain.

Productive scalar chunks avoid the CMS loop's otherwise idle one-millisecond
sleep. Each iteration still services existing marking/pacing and reaches the
ordinary safepoint. Shutdown and a matching stopped-worker producer close
finish all remaining chunks, unlocking between them. They continue by READY
state, not by successful retirement count: a fully quarantined first chunk
must not strand valid later entries. A DRAINING owner is never stolen or spun
on. A stale producer token cannot consume another epoch.

These are bounds on work and retained storage, not a maximum elapsed pause.
Index deletion still walks its existing probe run. Throughput and latency
require separate matching measurements.

## Reset and failure behavior

The token is independent of mark epochs. Same-GC reset and mark preparation
operate on the active heap and do not erase detached negative owners or their
private metadata. Backend freeze prevents collector changes. Generic note,
free and pointer-unregister guards still reject a negative owner through the
object index even though its node is no longer on the active list.

Changed eligibility or failed granule retirement moves the node to a separate
globally anchored quarantine chain. Its index, token and retained-byte charge
remain; it is never retried, pooled or physically freed. Other valid pending
entries continue. The reservation remains quarantined after pending work ends,
so failures cannot accumulate across newly admitted epochs. Structural owner
loss or an unexpected index-removal result aborts instead of treating uncertain
ownership as a recoverable failure.

Missing, busy, starting or stopping workers prevent new admission. The final
worker lock acquisition orders behind an admitted producer. Either that worker
or the matching producer's post-resume fallback completes accepted work. A
worker join alone does not establish completion of another producer's local
raw tail; its owner must also finish.

Legal roots must prevent the original dead verdict. This mechanism does not
make an unrooted stale pointer safe. A detected changed pin causes quarantine;
a racing late pin through an invalid pointer gets no new lifetime guarantee.

## Evidence surfaces and limits

- `tests/python/test_gc_scalar_sweep.py` executes production bodies over raw
  cells, including chunk/backlog boundaries, chain separation, partial and
  all-failure chunks, reset, stale close, node reuse and pool overflow.
- `tests/python/test_gc_note_object_freeing.py` checks negative-owner and lease
  order, first-death observation and accounting, and moving-backend controls.
- `tests/fixtures/native/gc_scalar_sweep_worker.c` and
  `tests/python/test_gc_scalar_sweep_native.py` run the actual CMS loop and owned
  runtime under all five backend selections. Controlled-verdict modes expose
  chunk boundaries, stop, repeat epochs, pool overflow and quarantine. The
  public-collection mode uses real marking without injected candidate bits.
- Existing terminal-free and collection-owner lifecycle controls remain
  distinct. Scalar execution is not complete PEP 442/weakref/resurrection
  acceptance and does not erase pre-existing lifecycle failures.

Counters 13–18 record chunk count, peak examined chunk objects/bytes, physically
released overflow nodes and peak pending object count/bytes. Counter reads are
individually atomic, not an atomic multi-field snapshot. Interpret completion
only after the consumer and any producer-local fallback have finished.

Source review, model tests and native focused execution establish different
facts. None alone establishes native-pcc1 fixed point, full five-GC lifecycle
acceptance or a performance gain.
