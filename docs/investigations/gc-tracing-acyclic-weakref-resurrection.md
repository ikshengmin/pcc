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

## Native layered chain scaling screen

Linux x86-64, owned pcc0 runtime from review commit
[`b708d89`](https://github.com/ikshengmin/pcc/commit/b708d891bf83ed395081251b8b3d1e280af08fc2).
The linked native fixture seals a chain of 4, 16 or 64 finalizable objects,
in reverse order, after its allocating thread exits. Each node's finalizer
does constant user work. The fixture starts timing after the initial mark
verdict is sealed and before calling the real sweep, so the time includes
topology, all of its stop transitions and reclamation. A separate registered
root contains 0, 4,096 or 16,384 unrelated live empty lists with no weakrefs.
Five fresh-process trials for every combination ran in shuffled order. Every
run finalized and reclaimed exactly its sealed candidate count with zero
full rechecks; every run entered exactly one callback window per candidate.

| Collector | Extra live lists | Median sweep at 4 / 16 / 64 candidates (µs) |
| --- | ---: | --- |
| GC1 | 0 | 78 / 238 / 1,379 |
| GC1 | 4,096 | 86 / 280 / 1,325 |
| GC1 | 16,384 | 103 / 253 / 1,376 |
| GC3 | 0 | 80 / 266 / 1,198 |
| GC3 | 4,096 | 94 / 287 / 1,353 |
| GC3 | 16,384 | 83 / 286 / 1,395 |

With no extra heap, the 16→64 step took 5.8× / 4.5× (GC1/3) for 4× the
candidates. This is not a passing linear-scale result. Additional rooted
lists were not a large multiplier in this particular fixture; the timings
nevertheless do not qualify unrelated-heap independence. In particular,
they do not exercise unrelated weakrefs. The code still checks the entire
bounded candidate vector at every callback stop, and this chain still
has one stop per object even without fallback. The next experiment needs
to remove the repeated candidate walk or demonstrate that its work is
bounded by the observed roots, then repeat the matrix. Do not treat this
as the no-observer five-pair `pcc_perf` gate or a production promotion.

The exact source-matched archive has to be built first. Reproduce from the
review branch with a fresh output directory, under the usual Linux reserve:

```sh
out=$(mktemp -d)
PCC_RUNTIME_ARCHIVE=/absolute/path/to/libpy_runtime_pcc_py.a \
  uv run python scripts/run_process_tree_sample.py \
    --timeout 90 --max-tree-rss-bytes 419430400 \
    --linux-physical-preflight-reserve-bytes 7516192768 \
    --result "$out/guard.json" --samples "$out/rss.tsv" \
    --stdout "$out/results.txt" --stderr "$out/errors.txt" \
    -- uv run python tests/python/measure_gc_acyclic_layers_scale.py "$out"
```

### Root extent and explicit-pin follow-up

The allocator-verified extent hull rejects an empty conservative root
cut before testing each candidate extent. A candidate reached from current
roots retires its sweep flag during the gray-queue drain, not in a second
candidate-wide scan. Verify two independent guards: an unaligned raw
interior pointer written into the suspended caller must still rescue its
owner, and a newly pinned candidate must keep its owned descendants.
The second case was red on the prior review runtime for both GC1 and GC3:
the pinned owner survived but its child ran its finalizer. The old general
sweep-candidate predicate excluded pins before the root check. Both cases
pass in the new native candidate.

Seven alternating native A/B pairs per combination on Linux compared the
same sealed chain fixture and its original review runtime to this change.
At 64 candidates, zero unrelated live lists, GC1 median paired time fell
by 39% (7/7 faster), GC3 by 18% (5/7 faster); with 16,384 live lists the
reductions were 47% (6/7) and 37% (7/7). Runs used the same exact candidate
counts and still entered one callback window per candidate, with no full
recheck. These are narrow native screens, not the required five-pair
`pcc_perf` gate. Four-candidate GC3 was not a clear win. The explicit-pin
check still visits the bounded vector at each callback stop and the
linear-scale gate remains open.

### First confirmed old-owner store paths

The native layered fixture performs each write inside the acyclic head's
real `__del__`, to an old marked owner already held by a registered root.
The value is an otherwise dying sibling obtained through a live weakref.
Neither the callback's native temporary nor a direct root retains the
sibling at the stop. On GC1 and GC3, the following writers all preserved
the sibling, produced the same finalizer/weakref events as CPython, and
took exactly one same-cut full fallback because the current direct roots
could not explain the write-barrier shade:

- List append through `py_list_append`.
- Dictionary value insertion and replacement through `py_dict_set` with
  an integer key; replacement overwrites a separately initialized entry.
- Native instance field assignment through `py_instance_set_field`.
- Replacement of an initialized list item through `py_list_set`.
- Set insertion through `py_set_add`.
- Dictionary bulk update through `py_dict_update`, list slice replacement
  through `py_list_set_slice`, and set bulk update through `py_set_update`.
  These three use a source sealed in the dead graph before the callback:
  the fixture does not create a temporary live source or pre-shade its
  value in the callback.

These are nine concrete paths, not a complete barrier audit. Additional
field layouts, external native writers and publication not mediated by
these helpers need separate coverage. These tests do not establish safe batching across
concurrent weakref readers.

A separate C caller now exercises the public `PyDict_SetItem`,
`PyList_Append` and `PyObject_SetAttrString` entries with managed owners
and an otherwise dying candidate value. GC1/3 preserve the value and
match CPython's events, using one expected fallback per indirect
publication. These are C-ABI controls, not a substitute for loading an
extension or testing an independently owned CpyHandle, direct field
writes outside these APIs, or arbitrary foreign code.

The independent foreign-handle control uses a real `py_cpy_handle_new`
with an opaque payload and explicit release callback. An acyclic
finalizer reaches the handle through its candidate child edge and
acquires a public `Py_INCREF` owner in an unregistered C slot. On GC1
and GC3 the payload remains usable after the sweep, with no full
recheck or release. When `Py_DECREF` withdraws that C owner inside the
same callback, the one ambiguity fallback reclaims both candidates
and releases the exact payload once. This tests independent CpyHandle
ownership inside the new window; it does not test an owner's arbitrary
raw third-party field as a managed reachability edge.

A separate arm allocates a GC-capable native extension instance using
`PyType_FromSpec` and `PyType_GenericAlloc`, colors the rooted instance
black before the callback, and installs the dying candidate into its
custom struct field using `pcc_gc_store_ptr`. GC1/3 match CPython's
events with one full fallback: the native `tp_traverse` actually sees
the non-null field through `Py_VISIT`, and the weak target survives as
the exact published object. This covers the shared barrier contract for
that field, not arbitrary raw C assignments or the currently stubbed
`PyObject_GC_Track` API. The Linux focused run passed all 107 checks.
Darwin was not run.

### Bounded stopped stack capture

The layered review path now copies and sorts only stopped caller and
parked-mutator words inside the proven candidate address hull. An empty
cut allocates nothing. The proof requires nonmoving, fixed candidate
extents without backing spans; a missing hull keeps the complete cut.
On ambiguity the world remains stopped and the legacy oracle gets a fresh
**unfiltered** cut, since an old live object's address can fall outside
the hull. No change to ordinary root capture, singleton or GC4 admission.
The native caller-only old-list test writes a candidate into a list
accessible exclusively from an unregistered caller slot, then verifies
the CPython event order and one full fallback. All 109 Linux focused
checks passed, including the caller interior pointer and parked-thread
controls. Darwin was not run.

The owned chain probe ran seven alternating same-input pairs per cell
against the exact prior review runtime (168 fresh processes). At 64
candidates the median paired time reduction was 27% (GC1) and 31% (GC3)
without unrelated data, and 32% (GC1) and 30% (GC3) with 16,384 rooted
lists, 7/7 wins in each cell. Every run still finalized and reclaimed
exactly 64 candidates without fallback in those cells, using one window
per finalizer. At four candidates wins were mixed in some cells. This
isolates the cut cost; it is neither the pcc_perf no-observer A/B nor
proof of linear scaling or safe same-layer batching.

### Same-layer peer weak-reference observation

Two independent candidates enter the same topological layer. During A's
finalizer a preexisting pthread dereferences B's weakref and keeps the
result solely in a volatile C stack slot (no registered root, store or
pin). GC1/3 capture the parked peer's word and do not finalize B; the
peer reads it again after the world resumes. This agrees exactly with
the CPython retained-reference oracle, with zero full rechecks.

In the `peer-depart` arm the peer drops B and terminates before the next
stop; both implementations report B dead and invoke each finalizer and
weak callback once. The CPython script explicitly drops A before B,
yielding `a-del a-weak b-del b-weak b-dead`. The sealed tracing cohort
has both independent objects dead together and yields
`a-del b-del b-weak a-weak b-dead` (zero full rechecks). CPython's B
reference is held until the script's second release, so the total order
is not an equivalent ownership boundary. The maintainer approved the
proposed rule on 2026-11-22 (reply: “可以”): for independent siblings in
the same layer compare event counts, each object's finalizer-before-
weak-callback order and the final verdict; where there is a dependency
keep exact CPython event ordering. The native arm also checks the
agreed layer barrier, with both finalizers before either weak callback.
This is not evidence that arbitrary user effects allow batching in one
running-world interval.
