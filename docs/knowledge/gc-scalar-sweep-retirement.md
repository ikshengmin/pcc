# Bounded GC1/2 scalar retirement

This increment moves logical ObjectNode/index retirement and physical release
of a conservative subset of committed dead scalars to the existing CMS worker.
It does not detach the object list, move finalizers, remove the resurrection
recheck, or implement the complete concurrent-sweep design.

## Admission and ownership

The public explicit and automatic collection owners open the existing deferred
free scope. A separate scalar mailbox reserves at most 256 tasks and 64 KiB of
object usable bytes. The raw terminal-free queue retains its existing limits.
No new collector thread or allocator is introduced.

PASS2 tries scalar admission only after the existing weakref invalidation,
PASS0 finalizers, resurrection recheck, and PASS1 slot clearing. Admission needs:

- GC1 or GC2, count-free mode, an outer stopped-world scope, a frozen backend,
  and the existing CMS worker running with an empty scalar mailbox.
- Exact builtin INT, FLOAT or STR, with the completed white candidate verdict
  and ordinary allocation origin.
- No tracking, immortality, static literal, explicit/conservative pin, lease,
  gray/black mark, fresh/minor/zpage state, or existing destruction owner.
- A LIVE object-family granule, with no separate managed-pointer or identity
  index entry. Large raw malloc objects remain synchronous.

A task stores the object, its node, size, tag and result in separate storage.
It never borrows an unretired object's refcount word. The node remains linked
and indexed; node.freeing becomes the negative unique batch token. This means
pending ownership, not the existing retired value 1. The object receives
FINALIZED and DEALLOCATING. Claim debits the existing live-byte ledger exactly
once and emits the ordinary first-death observation through a small helper in
the defining TLS owner. Retained physical bytes have their own counter.

A full mailbox rejects further attempts before taking a graph lock or probing
eligibility. The capacity-rejection counter therefore does not count only
eligible objects. Non-scalar PASS2 types keep their previous destruction path.

## Publication and worker

Both public collection exits seal the mailbox READY before releasing their
actual outer world stop. The registered worker cannot consume it while that
mutator still owns the stopped world. Raw-only callers may close an empty
reservation without sealing. A nonempty unsealed close fails closed.

The worker claims READY and completes the entire bounded logical batch under
one graph-lock hold. That hold also establishes no-park: implicit polls cannot
park with private unretired nodes. The worker revalidates exact node/index/token
ownership and eligibility, then:

1. Checks object-family granule retirement succeeds.
2. Checks object-index removal returns the expected node.
3. Unlinks and pools that node, clearing the task's node pointer immediately.
4. Sets the object refcount word to zero and records successful retirement.

There is no generic typed dispatcher, user callback, container lock, or
managed allocation in this closure. INT/FLOAT/STR destruction has no other
payload release. Ordinary graph-lock/index/node helpers keep their existing
allocator and cursor behavior. The graph lock is released before object raw
free. The mailbox remains DRAINING through the physical release tail, so
another producer cannot overwrite its task storage.

The batch bounds admitted objects and bytes, not a guaranteed elapsed lock
latency. In particular, existing index deletion still follows its probe chain.
A new world stop may wait for the in-progress finite graph-locked batch. No
pause-time or throughput improvement follows merely from changing its owner.

## Reset, shutdown and failures

The ownership token is independent of the mark epoch. A same-GC1/2 reset keeps
its existing success behavior: it does not discard the list or negative node
owners. Mark preparation, finish and root walks skip nonzero-freeing nodes;
DEALLOCATING also rejects shading. The existing backend freeze prevents a
change to another collector after these allocations. No reset waits for a
parked worker and no new reset rejection is added.

Missing, busy, starting or stopping workers prevent new admission. If shutdown
races a sealed batch, the worker's unconditional final graph-lock acquisition
or the matching producer's post-resume close consumes it. A stale producer token
cannot claim another batch. A worker join alone does not prove a different
producer's local raw-release tail has completed; concurrent tests must finish
that producer too.

Changed pins, leases, flags, provenance or identity eligibility leave the exact
pending node, index and task in durable bounded quarantine. Such a batch is not
retried or reused, and its bytes are never physically freed. Structural loss of
node/index/token ownership or an unexpected index-remove result aborts rather
than claiming recoverable quarantine. Generic note/free/pointer-unregister
paths cannot steal a negative-token node. The pointer-unregister and note
callers bypass the new ownership helper when the mailbox is empty; GC0 note
also bypasses it by backend.

This does not make an unrooted stale pointer safe. Legal roots must prevent the
original dead verdict. GC1/2 foreign-lease mode guards and no-op increfs are not
queue roots. A pin detected before consumption causes quarantine; a racing
late pin through an invalid unrooted pointer has no new lifetime guarantee.

## Focused evidence surfaces

- `tests/python/test_gc_scalar_sweep.py` executes production ownership,
  accounting, guards, seal, capacity, quarantine and failure bodies over raw
  memory cells. It is deterministic host-model evidence, not native threading.
- `tests/fixtures/native/gc_scalar_sweep_worker.c` and
  `tests/python/test_gc_scalar_sweep_native.py` exercise all five backend
  selections. Modes 1–8 and 10 use controlled verdicts on real allocations and the
  production CMS loop, with pending node/index/header witnesses, worker thread
  identity, index removal, node-pool reuse and a live allocation canary.
- Mode 9 uses ordinary public collection with real marking, without injecting
  candidates/epochs or directly calling enqueue/drain. It checks positive
  worker retirement on GC1/2. It is still a focused scalar program, not full
  finalizer, weakref, resurrection, generator or PEP 442 qualification.
- The existing terminal-free and collection-owner controls retain separate
  raw/scalar counts. Their old failures are not erased by changing mechanisms.

Counters are individually atomic observations, not a multi-field snapshot.
Physical completion must be checked after the consumer and any producer-local
fallback finish. These controls do not establish native-pcc1 fixed point,
full five-GC lifecycle acceptance, or a workload performance benefit.
