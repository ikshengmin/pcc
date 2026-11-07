# Persistent gray batches

The initial GC1/GC2 persistent-batch increment is `c6ece4e37ccd308dbd7933e555f84a15d7b627bd`,
based on `39bd633713d928169f18e47160aeb4e0023b0aaa`. The tail-publication
increment builds on it. The auto-enabled mutator consumer increment builds
on `11239ee425b52e6191c3a7c162d09bb02471dd75`. These apply to count-free,
nonmoving tracing; none replaces the collector's gray-credit and final
stopped-world termination protocol.

## Production path

The successful color-CAS branch in `pcc_gc_shade_gray_atomic` appends the
object's address to a thread-local raw batch. Full batches contain 64 entries.
Calls into the runtime safepoint, parked/exited publication and thread
unregister publish a partial batch. Ordinary compiler polls now enter that
same safe runtime boundary for either a stop request or a gray-tail
publication request. The last graph/container/no-park lease exits honor
both requests, but never flush inside a live lease. Runtime-kernel implicit
polls remain disabled. Published batches transfer with release CAS and
acquire exchange; the graph-locked consumer owns shared partial batches.
A consumer can resume work left by another consumer.

Common, mutator and stopped-world drains prefer these entries before the
object-list cursor. A healthy count-free GC1/GC2 CMS worker's ordinary,
non-stopped slice uses only queue entries. Auto-enabled mutator assists and
explicit non-stopped steps use the same queue path. When automatic work is
disabled or threshold0 is zero, explicit steps retain the cursor: the paused
CMS worker can clear publication requests, so such steps cannot depend on
other producers observing that request. At an empty queue with positive
gray credit it requests tail publication and returns without claiming
completion. The worker yields between empty retries. This covers both a
hidden TLS tail and credit reserved before a color CAS. An entry does not own the object. Under collector
exclusion, exact current index membership is checked before reading its
header; a retired address is discarded, while a reused address refers to the
currently registered object. Only a still-gray object retires gray credit.
The existing C-extension hold, pending identity, unlocked callback and
completion checks remain in use.

Cycle tags use acquire/release access because a same-backend reset need not
stop mutators. The stopped-world whitening and finish boundaries also discard
old tokens, including a resurrection recheck that reuses an epoch. Tags alone
do not make reset, color transition and append one atomic transaction.

The request is not an acknowledgement that every producer has flushed.
During healthy non-stopped marking, an eligible consumer clears it when it
resumes consuming a token; if another tail remains hidden, queue exhaustion
with positive credit requests publication again. Stopped cleanup, automatic
work being disabled and worker exit also clear the request without retiring
gray credit. A producer never clears another thread's request. With finite work, the last hidden tail leaves a persistent request
until that producer reaches a legal poll, park or exit. Continuous mutation
does not acquire a new bounded-completion guarantee.

## Remaining work and bounds

- Paused-automatic explicit steps and stopped-world drains retain their cursor. A lost
  token from batch allocation failure sets a cycle-local fallback latch,
  which only stopped-world cleanup clears; CMS then retains the original
  cursor walk as well. The short-lived trace stack remains. Whitening, root
  scanning, final remark and sweep still have their existing heap work;
  whole-collection O(live) is not established.
- The producer never waits for the graph lock. A batch is 544 requested bytes
  and uses the allocator's 1024-byte size class. Its allocation can fail;
  failure leaves the successful gray transition and count intact. Request
  handling adds poll work and can publish short batches; performance must
  be measured for this increment, not inherited from the first one.
- Invalid and already-black returned tokens consume the scheduler's scan
  allowance. Whole-owner scanning can still overshoot the soft slot budget.
  A take can discard multiple stale or exhausted metadata batches, outside
  that token allowance. This is not a hard slice-time or p99 bound.
- GC0, GC3 and GC4 do not enqueue or consume these batches. Their existing
  collector protocols remain separate; common thread entrypoints still call
  the empty-batch flush helper.

## Auto-enabled mutator validation boundary

`test_gc_gray_worklist_scheduler.py` exercises the actual scheduler and
allocation-assist bodies for both eligible consumer identities. It preserves
the previous cursor, budget, header, C-extension and completion assertions,
including paused-automatic and allocation-failure fallbacks. The legacy
header-interleaving and node-budget fixtures explicitly retain count-free=0;
they do not certify the new queue path.

`test_gc_mutator_gray_queue_native.py` executes real list objects, index
membership, shading and a registered producer's partial TLS batch on GC1
and GC2. Before publication, the mutator completes zero objects and leaves
the cursor, gray credit and completion identity unchanged. The real
`pcc_gc_cms_note_alloc(7)` call adds seven bytes of debt without paying any
back. After a real kernel safepoint publishes the batch, one object becomes
black and discharges 64 bytes. The producer is still alive. The unchanged
C source fails assertion 20 on both collectors with the frozen `11239ee`
runtime and passes with this increment.

That native witness deliberately pauses CMS and stages an active mark under
the stopped world. It retains one gray credit and does not execute a whole
seed/remark/sweep cycle. The explicit allocation-note call does not prove
the allocator-to-note chain, and the explicit safepoint is not a proof of
generated poll insertion. Existing native queue, generated-poll and
five-collector graph-lock/stop/registration/reuse tests also pass with the
new 201-module Linux x86-64 host-pcc0/self runtime. The original build limits,
default eight build jobs and six pytest workers are unchanged.

The wider semantic selection is not all green: GC1 and GC2 lifecycle cases
in `test_gc12_tracing_only.py` print `False []` and fail at `rescued[0]`,
and the sliced CMS handoff fixture has a duplicate `pcc_gc_backend_selected`
definition when linked to the full archive. The frozen parent reproduces
these outcomes. Four final-remark model cases also fail on both versions
because their boundary lacks `pcc_gc_trace_stack_begin`; the separate owned
native GC4 final-remark case passes. The focused selections total 796
distinct passes, seven failures and no skips. An initial missing count-free
fixture field failed before its explicit fallback configuration was added;
that failed run is not counted again as passing coverage. This increment
does not repair the seven remaining failures or establish complete collector
correctness. Seven strict/reference cases requiring unavailable Clang remain
unrun; source/archive and owned-native checks are separate evidence, not
substitutes for those cases.

A fixed-binary parent/current screen completes all 20 Linux x86-64
host-pcc0/self handler runs. Each uses concurrency 100, delay 0 and 400
rounds: 40,000 measured requests, 200 warmup requests and 40,000 latency
samples. Alternating the pair order by cell gives current/parent QPS ratios
for GC0-4 of 1.016/1.094/1.003/0.968/1.032 at one worker and
1.008/0.966/0.863/1.033/0.962 at four workers. No run is retried or excluded.
In particular, GC1/GC2 four-worker throughput does not improve in this
screen; GC2's roughly 13.7% negative observation needs the fixed-binary
seven-pair check. This checkpoint is not performance-qualified. A single
pair cannot establish stable gains, regressions or attribution, and
container observations do not certify an idle physical host. The screen
does not measure outer GC pause p99, peak GC heap or collection counts.
It is not HTTP, native-pcc1, Mac or the older 10,000-request workload.

## Recorded tail-publication checkpoint

The following describes the earlier tail-publication checkpoint, not the
auto-enabled mutator extension above.

For the tail-publication increment, the Linux x86-64 owned host-pcc0/self
runtime builds all 201 modules within the unchanged 90-second/1-GiB
process-tree guard and default eight build jobs. Its focused host/model,
IR, archive and native selections have 658 distinct passing test cases.
That is not a whole-language or whole-collector pass count.

The host tests execute actual queue, shader, membership, common-drain,
whole-owner visitor and scheduler bodies, including released/reused addresses,
OOM fallback, reserved-before-CAS credit, request re-arming, budget carry-over
and C-extension epoch invalidation. The ordinary CMS slice model rejects
object-list reads while waiting for a hidden tail; explicit/stopped and OOM
paths keep their original traversal and termination checks.

The owned native queue harness executes four producers and two consumers on
each of GC1 and GC2, accounts for all 16,396 tokens, and checks stopped-world
entry after helper and worker exit. Runtime thread unregister, rather than
an explicit queue flush in the callback, publishes the final partial batches.
The same harness verifies that each of the four last-lease exits publishes a
requested tail, without publishing inside a live lease or treating a producer
flush as a global acknowledgement. Archive checks verify the unique queue
owner, TLS ownership and actual cross-object users. The existing five-GC
graph-lock/stop/registration/reuse harness also passes with this runtime.

`test_gc_gray_flush_poll_native.py` separately compiles and audits the poll
switch both on and off. With polls enabled, a real generated native producer
loop publishes a short tail before being released or exiting, while the stop
flag remains zero. The loop has only intrinsic operations and generated
safepoint calls; this does not claim the entire thread program is allocation
free. The test uses opaque tokens and synthetic GC1/GC2 worklist controls,
checks that CMS has not started, and therefore proves the poll/publication
boundary rather than a real CMS collection's final termination.

Useful focused commands, with a current verified runtime archive supplied by
the repository fixtures:

- `uv run pytest -x -q tests/python/test_gc_gray_worklist.py tests/python/test_gc_gray_worklist_scheduler.py tests/python/test_gc_gray_flush_polls.py tests/python/test_gc_gray_flush_poll_native.py`
- `uv run pytest -x -q -m integration tests/python/test_gc_gray_worklist.py::test_owned_native_gray_batches_mpsc_tls_and_consumer_handoff`
- `uv run pytest -x -q -m integration tests/python/test_gc_gray_flush_poll_native.py::test_generated_allocation_free_loop_publishes_gray_tail_without_stop`
- `uv run pytest -x -q -m integration tests/python/test_gc_graph_lock_wait_timing.py::test_graph_wait_native_five_gc_handoff_stop_registration_and_reuse`

The broader validation is not all green. One existing Darwin-arm64 execution
test actually skips on Linux. Three inline-related failures are reproduced
unchanged on the frozen `c6ece4e` parent: two Windows emitter target mismatches
and a removed single-thread runtime configuration. A combined inline selection
also exceeds the unchanged 1-GiB process-tree limit; its interrupted results
are not counted as passes. The smaller selections preserve default xdist and
resource limits. Four strict/reference checks report a missing Clang
prerequisite. Those failures, unavailable configurations and unexecuted cases
are not converted to passes or weakened assertions.

The initial increment's known generator-finalization failure is not repaired
by this mechanism. The unchanged original test failed at `next(rescued[0])`
on GC1 for both `c6ece4e` and its frozen parent; GC0 passed and that original
test did not reach GC2-4. These are historical results, not a fresh execution
claim for the tail increment. Old counted-mode configuration fixtures also
remain inconsistent with the parent's count-free defaults.

A same-input, single alternating parent/candidate proxy screen completes
all 20 runs across five GC backends and one/four workers. It checks output,
request counts and latency sample counts, not HTTP or native-pcc1 behavior.
The candidate/parent QPS ratios for GC0-4 are 0.919/0.980/1.000/0.923/1.001
at one worker and 1.123/1.053/1.003/0.961/0.924 at four workers. These mixed
single-pair observations do not establish a throughput gain or a stable
regression. GC1-4 do not reach 3x four-worker scaling in this screen.

Mac execution, native self-host, complete collector correctness and seven
rotated frozen A/B pairs for this tail increment with collection counts,
outer-pause p99 and peak heap remain unverified. Performance results for the
initial batch must not be attributed to this increment.
