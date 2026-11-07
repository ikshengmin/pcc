# Persistent gray batches

This is the first GC1/GC2 worklist increment, based on `39bd633713d928169f18e47160aeb4e0023b0aaa`.
It applies to count-free, nonmoving tracing. It does not finish the worklist
migration or replace the collector's termination protocol.

## Production path

The successful color-CAS branch in `pcc_gc_shade_gray_atomic` appends the
object's address to a thread-local raw batch. Full batches contain 64 entries.
Calls into the runtime safepoint, parked/exited publication and thread
unregister publish a partial batch. A fast compiler poll that does not enter
the runtime does not flush it. Published batches are transferred with release CAS and acquire
exchange; the graph-locked consumer owns shared partial batches. A consumer
can resume work left by another consumer.

Both common drains and `pcc_gc_tracing_step_cycle` prefer these entries before
the object-list cursor. An entry does not own the object. Under collector
exclusion, exact current index membership is checked before reading its
header; a retired address is discarded, while a reused address refers to the
currently registered object. Only a still-gray object retires gray credit.
The existing C-extension hold, pending identity, unlocked callback and
completion checks remain in use.

Cycle tags use acquire/release access because a same-backend reset need not
stop mutators. The stopped-world whitening and finish boundaries also discard
old tokens, including a resurrection recheck that reuses an epoch. Tags alone
do not make reset, color transition and append one atomic transaction.

## Remaining work and bounds

- The ordinary cursor is still the fallback for an unpublished running
  thread's tail and for batch allocation failure. The original short-lived
  trace stack remains. Removing normal cursor scans needs a complete flush
  and termination handshake; whole-collection O(live) is not established.
- The producer never waits for the graph lock. A batch is 544 requested bytes
  and uses the allocator's 1024-byte size class. Its allocation can fail;
  failure leaves the successful gray transition and count intact.
- Invalid and already-black returned tokens consume the scheduler's scan
  allowance. Whole-owner scanning can still overshoot the soft slot budget.
  A take can discard multiple stale or exhausted metadata batches, outside
  that token allowance. This is not a hard slice-time or p99 bound.
- GC0, GC3 and GC4 do not enqueue or consume these batches. Their existing
  collector protocols remain separate; common thread entrypoints still call
  the empty-batch flush helper.

## Validation boundary

The Linux x86-64 owned host-pcc0/self runtime builds all 201 modules within the
unchanged 90-second/1-GiB process-tree guard and default eight build jobs.
The new host tests execute actual queue, shader, membership, common-drain,
whole-owner visitor and scheduler bodies, including released/reused addresses,
OOM fallback, budget carry-over and C-extension epoch invalidation.

The owned native queue harness executes four producers and two consumers on
each of GC1 and GC2, accounts for all 16,396 tokens, and checks stopped-world
entry after helper and worker exit. Runtime thread unregister, rather than
an explicit queue flush in the callback, publishes the final partial batches.
Archive checks verify the unique queue owner, TLS ownership and actual
cross-object users. The existing five-GC graph-lock/stop/registration/reuse
harness also passes with this runtime.

Useful focused commands, with a current verified runtime archive supplied by
the repository fixtures:

- `uv run pytest -x -q tests/python/test_gc_gray_worklist.py tests/python/test_gc_gray_worklist_scheduler.py`
- `uv run pytest -x -q -m integration tests/python/test_gc_gray_worklist.py::test_owned_native_gray_batches_mpsc_tls_and_consumer_handoff`
- `uv run pytest -x -q -m integration tests/python/test_gc_graph_lock_wait_timing.py::test_graph_wait_native_five_gc_handoff_stop_registration_and_reuse`

These are component results, not full collector qualification. The unchanged
`test_native_generator_finalization.py` fails at `next(rescued[0])` on GC1
with both the candidate and the frozen parent runtime; GC0 passes and that
test does not reach GC2-4. Old counted-mode configuration fixtures also remain
inconsistent with the parent's count-free defaults; their assertions are not
weakened here. Mac execution, native self-host and seven rotated frozen A/B
pairs with collection counts, outer-pause p99 and peak heap remain unrun.
