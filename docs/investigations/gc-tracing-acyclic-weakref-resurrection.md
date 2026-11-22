# Tracing clears callback weakrefs before acyclic resurrection

The unchanged generator operand fixture succeeds on CPython and GC0.
GC1–4 all exit 1 at
`tests/fixtures/native/constructor/generator_root_groups.py:280`,
after the finalizer has stored the object in a live list but before its
first weakref identity check succeeds. The grouped helper is not needed
to reproduce this.

[The smaller owned-native probe](../../tests/fixtures/native/constructor/weakref_resurrection_boundary.py)
removes the entire generator, operand group and exception path. A single
ELF was tested with every backend, on Linux x86-64 pcc0, against CPython
3.15. All controls run an explicit collection before observing both the
event order and whether the original weakref points to the resurrected
object.

| Shape | CPython and GC0, first collection | GC1–4, first collection |
| --- | --- | --- |
| No strong cycle | finalizer only; original weakref still resolves | weak callback then finalizer; original weakref cleared |
| Target has a self-cycle | weak callback then finalizer; ref cleared | same |
| Target has no cycle, but a dead cycle still owns it | weak callback then finalizer; ref cleared | same |

All three shapes finish with one finalizer and one weak callback. On the
acyclic route CPython and GC0 deliver that callback only after the
resurrection list is cleared. This is an ordering and identity difference,
not a disagreement about how soon refcounts should run a finalizer.

## Where they diverge

`pcc_gc_tracing_sweep_unreachable` calls `_sweep_weak_callbacks` before
`_sweep_finalizers`. The early walk in `py_weakref_queue_tracing_candidates`
uses `_tracing_target_dies`: membership in the white sweep candidate set
plus the existing pin and extension exclusions. It does not distinguish a
zero-owner-style drop from a cyclic-isolate drop. The callback-bearing
weakref is nulled and queued before the finalizer runs. The post-finalizer
root recheck keeps the resurrected *object* but cannot undo a user callback
and must not reattach a cleared weakref. The callback-free and newly
created late-list weakrefs have a separate silent-clear protocol.

## Next narrow design check

Do not move every weak callback after finalizers, restrict the fix to
generators, or classify only the strongly connected component of the
target. The cycle-owned case disproves the latter: the target is acyclic
but must still use cyclic weakref ordering. Compare a candidate-only
strong-edge in-degree peel with an independent cycle-and-descendant
oracle. The simple peel models the shape distinction, not yet the
lifecycle: callback reentrancy, cascaded edge retirement, explicit roots
created during finalization, reused candidate identity, and late-created or
unreachable weakrefs need separate tests before changing sweep ordering.
Any added traversal must be confined to the relevant unreachable graph
and measured separately; there is no runtime change or performance claim.

## Reproduce

With an existing source-matched, threaded runtime archive and the regular
Linux guard, build the fixture using
`compile_python(source, output, backend="self", ir_scaffold_mode="on", runtime_archive=archive)`.
Run the same output with each `PCC_GC_BACKEND` value and each argument
`plain`, `self-cycle`, `downstream`. Run the original fixture under the
same CPython interpreter and arguments for reference. No pcc1 or macOS
execution.

## Candidate graph model

`tests/python/gc/test_tracing_weakref_candidate_peel.py` compares a
zero-internal-in-degree peel with independent transitive reachability
from each cycle. They agree on all 66,066 directed graphs with 1–4
vertices, including self-loops. Additional cases cover duplicate strong
slots, non-candidate children, no eligible weak target, an incomplete
slot row, a stale stopped epoch, and exhaustion in the middle of the
peel. The model outputs no candidate classifications on either snapshot
or budget failure. Nine guarded Linux tests pass.

This is only a pre-callback topology model. It does not authorize using
a stale verdict across a window when mutators run. In particular, the
acyclic owner of a candidate child may resurrect both; processing the
child independently because it is in the peeled region could still
fire a weak callback or finalizer too soon. Verify that separately in an
owned native fixture before proposing a sweep change. A production
budget refusal must preserve the public collection contract; the model
does not choose a runtime fallback.

## An acyclic owner's finalizer can rescue a dependent child

The same linked fixture now also runs `child-first`, `parent-first`,
and `parent-cycle`. CPython and GC0 report just `parent-finalize` after
the first acyclic collection and the child's original weakref still
resolves to the parent's child. The child finalizer and weak callback
run only after the resurrected parent is dropped. GC1–4 instead report
`child-weak, parent-finalize, child-finalize` during the first
acyclic collection, in both allocation orders. A self-cyclic parent
makes that same early order correct for all five and CPython. Run
all six argument modes in each of the six processes (36 executions).
Linux pcc0 and threaded source archive; no macOS or pcc1.

The source reason is separate from the early weakref test:
`_sweep_finalizers` queues every pending candidate into a flat list
and dispatches them in one reopened user-code window. The only
reachability recheck follows the *whole* list. Merely peeling the
acyclic graph or changing the order of its entries cannot make the
already-queued child's finalizer safe. A later design must retire
ownership in dependency order and revalidate a child's eligibility
after user code, preserving the existing cyclic-isolate batch.

## Post-callback stopped boundary (test model)

The new model treats a user callback window as invalidating the current
eligibility decision. A fresh stopped view protects the child that the
parent's finalizer made reachable. If the parent stays white, the child
still does not qualify until that strong edge is actually retired.
Two apparently independent candidates cannot be assumed batch-safe:
a callback can publish a root to its peer through an external
observation. The model also rejects stale views, defers a newly formed
cycle to the cyclic path, and keys finalizer state on logical allocation
identity rather than an address alone. Six model cases plus the nine
graph checks pass on guarded Linux; this is not a native stop-window,
lease, code change, or throughput result. Next map the model to the
existing collector visitors, root/lease boundaries, and candidate
storage without adding an unbounded full-heap rescan per callback.

## Existing runtime pieces and their limits

The Linux owned native
`tests/python/test_gc_tracing_candidate_slot_inventory_native.py`
runs on GC1–4. A true stopped-world section fills the real list with
three real object-index records, checks the unsealed/sealed/retired
states and verifies that the stop epoch isn't held after resume. The
test chooses the records manually; it does not color the heap or
claim that native GC4 emits such a list by default. The regular stopped
finish only fills this list on GC1–3. Failure to allocate it sets a
negative count and the existing sweep falls back to a heap walk.
The finish commit number alone does not authorize raw node access
after resuming mutators.

The same probe uses bounded one-position calls to
`pcc_gc_visit_object_slots_slice`. Real list storage with 32 reserved
slots and three occupied slots visits three roles, retaining the
duplicate strong edge twice. A real weakref visits its referent as
weak/derived (role 3) and its callback as owning (role 1); a real
instance visits its class owner as role 2. Retain roles 1 and 2, not
role 3, and resolve references before comparing candidate identity.
The slice refuses generic C-extension storage. The whole-object visitor
may then fall back to the C-extension slot API; do not treat an unknown
layout as an empty leaf.

The existing foreign-address lease takes an already rooted owning slot.
It may be a no-op on GC1–3, so the slot, not the lease token, provides
reachability. For GC4 a lease may forbid movement; its holder must
reload after a locking or parking boundary. Do not lease every dead
candidate and then mistake the artificially reachable result for
resurrection.

Finally, the current recheck seeds actual roots, then does one whole-heap
walk to protect every noncandidate's referents and another to clear
rescued candidate bits. Simply invoking it between each finalizer
would multiply that work. Reuse its behavior as a correctness oracle;
separately specify how a bounded version sees roots published during
callbacks, conservative stack/register changes and noncandidate owners
created or mutated after the verdict. No collector ordering was changed.

## Review options

The common sweep must preserve two paths. For the cyclic isolate and
its retained descendants, retain the existing early callback batch,
finalizer phase, resurrection check and late silent clear. For the
acyclic portion, start only a valid root of the dead owning graph
with no remaining incoming candidate edge. Run only its finalizer
behind an owning root and the correct address lease. Remove the
dispatch's temporary root before the next stopped mark, then preserve
a rescued object and its original weakrefs, or retire its outgoing
strong edges and deliver the old callback-bearing weakrefs that are
now truly due. Do not use the late silent list as the pending queue
for those preexisting callbacks. A descendant is eligible only after
its owning edge retires, and every user-code window invalidates an
earlier reachability verdict. Any reordering relative to the cyclic
phase must be checked against mixed graphs in the native control.

**Option A, correctness arm.** Add an isolated, default-off arm that
reuses the exact existing root recheck after a callback. This has no
new write-side hot-path cost, but its two heap scans per callback
make it unsuitable as an unqualified production route. Bound the
first test to explicitly selected inputs and run CPython parity on
all six current modes, plus mixed regions, callback-created roots,
late-created weakrefs, exception TLS, memory pressure and nested
`gc.collect()`. Measure visits and stopped time independently.
Acquire the callback's existing necessary roots and leases, not a
batch of candidate roots. GC4 requires its own reviewed movement and
world-window treatment; do not quietly widen its current window.
Never satisfy explicit collection by silently postponing work past
its return.

**Option B, candidate-only refresh.** Run one full settlement to start
the sweep. For every callback window seed current explicit and
conservative roots, then visit newly created noncandidate records and
noncandidate owners with any published owning-slot changes. Follow
only newly reached candidates, with their current outgoing strong
edges. Once rescued, retire them from the batch and treat them as
noncandidate owners in later windows. Journal entries require stable
allocation identity or a relocation-aware record; do not retain raw
slot pointers across resume. Retirement, initial publication, storage
replacement, direct C stores, across-thread root publication and
unknown C layouts are real coverage gates. A journal overflow, stale
epoch, owner reuse or unresolved edge invalidates the incremental
verdict and must reach the full oracle before any callback based on
that verdict runs. Do not add a global branch to GC0 or enable the
journal by default before this coverage exists.

**Recommendation for review:** implement Option A as a test-only
correctness arm first, not as a shipping optimization. Use that
oracle to qualify Option B independently rather than combining a
lifecycle rewrite with an unproved barrier. A Linux model visited
six owners/targets at each of 16, 64 and 256 clean old owners and
matched its separate full oracle; omitting one dirty writer produced
the wrong answer, and known-incomplete inputs declined. All 21
related model cases passed. No collector mode, macOS behavior or
performance claim was changed.

The first executable reference-arm slice is
`tests/python/test_gc_acyclic_candidate_peel_native.py`.
It runs the bounded peel on real list slots during an actual held
stopped-world epoch in all four tracing backends. An acyclic chain
(including duplicate ownership) peels; a list cycle retains its
downstream acyclic list. Insufficient slot budget, edge overflow and a
resumed stop epoch leave the verdict output unchanged. This prototype
takes a fixture-validated, already resolved cohort and uses a small
linear map; it is not a scalable allocator/index or a finalizer
scheduler. Production dispatch, weakref ordering and the original
failing generator case are unchanged.

The next executable reference arm, in
`tests/python/test_gc_acyclic_finalizer_rescue_native.py`, calls the
actual user finalizer from an owned native program with the world
running and then uses the real full stopped reachability recheck.
With a registered resurrection root, both parent and child drop out
of the dead cohort and the child's callback weakref still resolves.
Without that root, both remain candidates and the child finalizer
and weak callback have not run. All six GC1–3 combinations passed,
including 60 repetitions of the same linked artifact.

The fixture scopes raw candidate addresses to completed native frames
and scrubs the retired callback stack before recheck. Its initial
attempt to disable conservative scanning was invalid: a registered
thread without a snapshot aborts the verifier. The successful test
keeps normal stack/register scanning on and differs only in real root
publication. This is not a collector-entry stack filtering change.
GC4 retains its current stopped-world user-code policy and is not
included in this resumed-window reference arm. The fixture does not
yet retire the non-resurrected parent's fields or dispatch the
child's terminal callback; production #9 remains open.

The same reference arm now settles the no-rescue pair. It removes the
real instance field and verifies it is null before opening the child's
finalizer window. A new stopped recheck precedes invalidating the
original callback weakref. The exact event order is parent finalizer,
child finalizer, then weak callback, each outside the stopped world;
duplicate invalidation delivers nothing. The rescue arm still leaves
the original weakref live. The linked Linux pcc0 program passed all
six GC1–3 combinations ten times each. This fixture owns exactly one
parent-to-child edge, does not generalize layout retirement or handle
a reentrant weak callback yet, and is not called by shipped sweep.

The reference arm's nested mode enters with a pending outer exception.
Each real finalizer and weak callback sees clear callback TLS, and its
nested `gc.collect()` returns without reentering the outer sweep. The
weak callback itself observes its weakref already cleared. On return,
the exact original exception is again pending. The 12 Linux GC1–3
control and nested combinations pass; each of the six nested
combinations additionally passes ten executions of the same artifact.
The tested root remains registered across the callback; this evidence
does not extend to a callback mutating arbitrary weakref links or
qualify GC4's window.

## Native full-recheck control: fixed callback graph, growing live heap

The reference executable now also takes `rescue scale <128|512|2048>`.
Only an unrelated registered heap grows; each measured interval is one
stopped recheck that rescues the same parent and child. Its existing
phase counter reports 286, 1,054 and 4,126 visits on each of GC1–3,
exactly two visits per additional live record. Five runs per point had
median phase times of 115/227/895 us on GC1, 107/229/872 us on GC2 and
109/246/781 us on GC3. These Linux counter timings are controls, not
HTTP A/B evidence. The native test asserts the visit slope and rescue
count, not a timing range. The pointer-bearing first-verdict check runs
in a separate frame so additional metric locals cannot keep the second
candidate alive through the conservative recheck. The 12 lifecycle
controls and nine scaled executions all pass.

The isolated `rescue bounded <size>` arm uses the real registered
scheduler root published by the parent finalizer and the shared
bounded slot visitor. It validates the reacquired stop epoch and
reports the same two rescued candidates as the full oracle, at two
visited owners regardless of size on GC1–3. The stale epoch and zero
slot budget refuse without changing the report. This native test
admits only its known callback and fixed live graph; it does not
justify ignoring other roots, stores or allocations in production.
No shipped barrier, weakref semantics or sweep dispatch changed.

The `rescue journal <size>` arm has the real finalizer append itself
to a preexisting registered list outside the candidate cohort. No root
slot changes. The deliberately omitted journal sees zero rescues, but
the full oracle rescues both objects. Explicitly recording the changed
list in the reference arm lets the bounded visitor find both in three
visits at each size and each of GC1–3. This uses actual list publication
and an explicit test journal, not proof that all production authors
would log that store. With the explicit-root and lifecycle arms there
are now 30 Linux native process executions. No default behavior changed.

The `allocated` and `fallback` arms allocate the list during the real
finalizer and do not register its local as a root. The full oracle must
still preserve the list's referents for this sweep, since the new list
is outside the disposal cohort. The bounded arm finds the same rescued
pair only when the allocation's owner is in the explicit journal. A
known-incomplete journal refuses without producing a verdict; the
existing full recheck then protects both. This includes nine native
points per source (three sizes and GC1–3), for 48 process executions
including the previous lifecycle controls. No runtime allocation
journal completeness is asserted.

The classifier is now implemented in the freestanding tracing sweep
object as `pcc_gc_tracing_classify_callback_cohort`. The native driver
no longer contains a duplicate classifier. The entry takes a supplied
stopped, known-candidate cohort of at most eight objects and writes
verdicts only after a complete bounded slot walk and stop-epoch check.
It declines GC0/GC4, active forwarding, duplicate/noncandidate or
pinned objects, stale epoch and an exhausted budget. The owning-edge
multiplicity matrix is local scratch; nothing is published to collector
metadata. The normal sweep does not call the new symbol. Three owned
Linux native tests passed with a freshly built archive, plus eight ABI
chunking tests. This is not the shipped #9 fix: a real caller must
establish complete cohort provenance and qualify callback handoff and
the bounded refresh after it; GC4 remains separately gated.

The runtime classifier now distinguishes an initial zero-incoming
head from the acyclic children released later by peeling. The test
selection frame ends before `pcc_gc_tracing_dispatch_callback_head`
runs the real finalizer on the chosen head. The dormant dispatcher
takes only the selected object and the caller-bound verdict/epoch,
not the cohort. It declines a dependent child, stale epoch or
non-pending finalizer before opening the window, then uses the
existing finalizer ownership and window protocol. The terminal child
can dispatch only after the test retires its parent's real edge
and classifies the remaining candidate. All three focused native tests
passed with an exact Linux pcc0 archive. There is no production caller
and no new collection-time instruction on existing paths. The
runtime still needs an authoritative complete cohort and root/store
coverage before default use.

The stopped bounded refresh now runs through the inert freestanding
entry `pcc_gc_tracing_refresh_callback_cohort`. The owned driver supplies
its callback root or journaled owner and compares the resulting bitmap
with full stopped recheck. The entry validates the stopped cohort and
epoch, rejects incomplete coverage or exhausted work before writing
the report, and never changes collector flags. Three Linux native tests
passed with a fresh matched archive. The coverage claim still comes
from the test driver; a production caller must enumerate real callback
writes and new owners before this path can be enabled.

The `rescue captured <size>` native reference lane exercises an opt-in
runtime entry for writing a real instance field during the resumed
callback. It records the independently live instance only after the
normal locked field transaction. An invalid field or a second distinct
owner is refused without a write; repeating the first owner is allowed.
The ordinary setter and its barrier do not call this entry. The recorded
owner is not a root and the lane cannot assert complete coverage:
bounded refresh refuses without changing its output, and the full
stopped-world recheck rescues both objects. The other reference lanes
still distinguish direct root, old-list publication and newly allocated
owner. No automatic tracking of ordinary stores, new owners or other
threads is claimed by the captured lane.

The `mixed` reference lanes put an independent acyclic parent and child
next to a self-cyclic owner and its acyclic leaf. The runtime binds
dispatch to classification of the exact stopped cohort, not a verdict
supplied by the caller. That entry refuses both the acyclic dependent
and the cycle-retained leaf; the fixture explicitly settles the cyclic
leaf's weakref before its finalizer. The old epoch cannot dispatch a
head after that window. A full stopped recheck precedes classification
and dispatch of the fresh acyclic head. A second full recheck observes
its resurrection or allows the fixture to settle the terminal child.
These are per-cohort invariants; no portable total order between
independent cohorts is claimed. The real sweep still has no caller of
the reference entry.

The exact-target early queue in the inactive mixed arm uses the existing
callback leases. It prepares live refs, ignores callback delivery for a
marked-dead ref, and clears the selected target's callback-bearing refs
before running either callback. Two live refs give a newest-first
check and an unrooted third ref must not run. CPython and GC1–3
independently produce `mixed 21 1 0` (callback IDs, one leaf finalizer,
zero discarded-ref callbacks). This does not bound list traversal
cost, implement late refs in the new arm, or admit GC4.

## HTTP callback observation

A Linux x86-64 replay reused the exact older stock gateway executable from
[the HTTP screen](../gc-gray-worklist.md#gc2-private-root-candidate-http-screen)
(PCC core `ca97bac`, gateway `b1490486`, binary SHA-256
`58ea650afa64601dbc736b055b84220e268be1e47b93748fe33e50905c1da86c`).
Each stock leg used a fresh process with `PCC_LOG=weakref,finalizer`,
`PCC_LOG_FORMAT=json`, and its own `PCC_LOG_FILE`. A native positive
control on `b6bd012` exercised both categories: `finalizer/call`,
`finalizer/done`, and `weakref/callback`; the older executable also
contains both logger categories. With the same zrk 3.0.0 client, warm
C50 for 5 s, then replay GET / C512 for 15 s, using `--closed -t 3
--disable-keepalive --method GET --timeout 8s --format json`. Gracefully
drain the server and read the first value in its `gc2-http` line
(`pcc_gc_tracing_finish_commits`). Each leg was guarded at 90 s / 400 MiB
with the unchanged 7 GiB Linux preflight reserve.

| Collector | w1 2xx / completed marks | w4 2xx / completed marks |
| --- | ---: | ---: |
| GC1 | 6,704 / 28 | 6,060 / 25 |
| GC2 | 7,320 / 29 | 5,928 / 25 |
| GC3 | 6,978 / 28 | 5,977 / 25 |

All six main legs had zero client errors and drain exit 0. No weakref
callback or finalizer log events appeared in any leg. Mark counts include
warmup, timed load and drain; they are **not** exact counts of sweeps.
This is an incidence screen (38,967 timed requests; 160 completed marks)
on a particular route, not an A/B or a proof for other HTTP apps. GC4 was
not measured. The exact per-sweep callback and post-window recheck timer
runtime built, but the instrumented gateway did not compile within the
available memory with the reserve held. There is no measured post-callback
recheck-time fraction, and no claim that initial rechecks cost nothing.
