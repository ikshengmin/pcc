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
