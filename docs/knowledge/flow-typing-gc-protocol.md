# Flow typing as a GC protocol design aid

## Decision and claim boundary

Flow typing is useful for making PCC's **root, ownership-credit, lease and
store-plan protocols explicit**. It is not a replacement GC algorithm and does
not justify removing barriers, root registration, relocation reloads, locking,
weak-reference handling or finalization. No production runtime change is made
by this study; no performance gain or five-GC qualification is claimed.

Reference: Sky Wilshaw and Graham Hutton,
[*Flow Typing: A New Lens on Linearity*](https://zeramorphic.uk/research/2026-flow-typing.pdf),
the supplied 30-page paper; [author's publication page](https://zeramorphic.uk/research.html).
The source comparison is frozen to PCC
[`206553ae8732c91b35ed9d022a0708863cad41fe`](https://github.com/ikshengmin/pcc/tree/206553ae8732c91b35ed9d022a0708863cad41fe).
Later source changes must be inspected before reusing the mapping below.

## What transfers from the paper

- Sections 3–5 interpret a focused access as `S -> (A, A' -> S')`: the caller
  receives a value and a one-use obligation that reconstructs the remaining
  context. This fits owned-root transfer and prepare/commit/finish protocols.
- Live/dead slots and equal branch output contexts are useful vocabulary for
  detecting double consumption, forgotten cleanup and mismatched failure paths.
- Example 6.3 rejects a temporary resource being captured into a later thunk.
  PCC needs a related distinction between ephemeral accesses and capabilities
  that remain valid across suspension, reentry or allocation.
- **An `A/A` contract is not a read-only or identity-preservation contract.**
  The returned resource can be a different value of type A. Remark 3.3 does
  not impose the usual lens laws. Preserve Python identity separately.
- Section 8 explicitly lacks immutable/shared-reference machinery; Example
  6.5 does not allow flow parameters inside ordinary product types. Linear
  *ownership credits* can coexist with aliased Python objects, but do not prove
  exclusive access to those objects. Do not infer `noalias` from disjoint roots.
- Remark 3.3 excludes general traversals. A lens is not automatically a safe
  iterator over a callback-mutated or concurrently modified container.

These are protocol-design inferences, not an implementation of the paper's
calculus or a proof that its theorems extend to PCC's heap and concurrent runtime.

## Existing PCC mechanisms to preserve

1. [Owned-root list construction](https://github.com/ikshengmin/pcc/blob/206553ae8732c91b35ed9d022a0708863cad41fe/pcc/runtime/py/py_list.py#L562-L612)
   consumes distinct registered source *slots*, not distinct object identities.
   Under the graph lock it resolves the source, applies the write barrier,
   installs the destination, empties the source and advances visible length.
   Public tracking/publication follows complete construction. The model's
   ownership credit, initialized prefix and publication are separate obligations.
2. [Moving-root lifecycle](https://github.com/ikshengmin/pcc/blob/206553ae8732c91b35ed9d022a0708863cad41fe/pcc/runtime/py/py_list.py#L225-L258)
   registers, reloads and unregisters authoritative roots. A raw pointer captured
   before a parking edge is not made relocation-safe by having a linear token.
   [Foreign/borrowed leases](https://github.com/ikshengmin/pcc/blob/206553ae8732c91b35ed9d022a0708863cad41fe/pcc/runtime/py/freestanding_gc_root_operations.py#L188-L205)
   also distinguish address stability from owning a reference.
3. [List append](https://github.com/ikshengmin/pcc/blob/206553ae8732c91b35ed9d022a0708863cad41fe/pcc/runtime/py/py_list.py#L641-L707)
   prepares outside the graph lock, reloads and commits inside it, then finishes
   outside it. [Dictionary equality/commit](https://github.com/ikshengmin/pcc/blob/206553ae8732c91b35ed9d022a0708863cad41fe/pcc/runtime/py/py_dict.py#L1812-L1857)
   revalidates storage and the candidate key after arbitrary equality code.
   An owner that stays alive does not imply that its table snapshot stays valid.
4. [Partial-construction cleanup](https://github.com/ikshengmin/pcc/blob/206553ae8732c91b35ed9d022a0708863cad41fe/pcc/runtime/py/py_list.py#L433-L498)
   keeps the header rooted, initializes its visible shape before parking and
   preserves the original exception across destruction. A single success-path
   owner equation is insufficient.
5. [Barrier dispatcher](https://github.com/ikshengmin/pcc/blob/206553ae8732c91b35ed9d022a0708863cad41fe/pcc/runtime/py/freestanding_gc_barrier_dispatcher.py)
   separately maintains marking colors, gray work, old-to-young remembered
   owners and backend-specific store buffers. Those temporal obligations are
   not implied by the paper's context-resource accounting.

## Executable evidence

The standard-library implementation is
[`scripts/research/flow_gc_protocol_model.py`](../../scripts/research/flow_gc_protocol_model.py),
with tests in
[`tests/python/test_flow_gc_protocol_model.py`](../../tests/python/test_flow_gc_protocol_model.py).
It never imports the PCC runtime, compiles native code or runs a real collector.
The initial 26 tests passed under the available cloud CPython 3.15.0rc1
interpreter. Independent review then found a cleanup-model defect: a finalizer
could observe a cleared field inside the still-live prefix. Cleanup now removes
the valid tail and shortens the prefix before releasing its owner; callback
errors are recorded without interrupting cleanup. The suite contains 28 tests,
including two new callback/reentrant-cleanup regressions. After this correction,
only the 8 affected fast tests were rerun, with all 8 tests and 129 subtests
passing. The full 28-test suite was not rerun; the marking experiment below is
unchanged.

Run from the repository root:

```sh
python3 tests/python/test_flow_gc_protocol_model.py
python3 scripts/research/flow_gc_protocol_model.py
```

Use an already installed Python interpreter. These standard-library commands do
not synchronize the project environment, install PCC or invoke its native build
hooks. To rerun only the cleanup regression group, append `ConstructionTests`
to the first command.

The finite marking experiment enumerates one to three ordered mutator transfers,
every binary object-alias pattern, every ordering of root/field scans (the output
is discovered before its fields), and all interleavings with one final sweep.
Each transfer is atomic and conserves ownership. Across **41,358 schedules**,
the abstract insertion-barrier variant has **0 missed live referents**. Removing
only that barrier produces **2,550 failing schedules**. These are bounded model
cases, not exhaustive PCC executions, measured failure probabilities or bugs
found in the production collector.

The smallest counterexample is:

1. Mark the rooted output and scan its initially empty fields, making it black.
2. Atomically move the only child credit from a root into the output.
3. Scan the now-empty source root.
4. Sweep: without the insertion barrier the still-owned child is unmarked.

Other tests cover one-shot return, use-after-return, duplicate/lost credits,
branch-state mismatch, same-type identity replacement, aliased owners, exception
unwinding, ephemeral suspension/callback escape, root registration, stale raw
addresses, every partial prefix up to four inputs with binary aliases, early
publication, double root consumption, invisible fields, finish-under-lock,
reentrant snapshot invalidation, weak edges, cycles and one-shot resurrection.
The finalizer tests check only the stated abstract obligations. They do **not**
model full Python weakref-callback/finalizer ordering or certify PCC semantics.

## Smallest useful next implementation

Add a separate, opt-in verifier for actual lowered IR/API protocols before
attempting a new language type system or deleting retain/release operations:

- Track `EmptyOwned`, `LiveOwned`, `Focused`, `Moved` and `Finished` capabilities
  for **slots/tokens**, with normal and exceptional successor contexts.
- Track `MayGC`, `MayReenter`, `MaySuspend` and `GraphLockHeld` effects separately.
  Ephemeral raw access must end before an unproven effect. A stable rooted/leased
  handle can permit otherwise valid operations; a blanket callback ban is not
  the intended production rule.
- Require exact-once finish, authoritative reload after parking, revalidation
  after reentry, valid initialized prefixes and delayed publication. Keep
  barrier obligations explicit instead of treating them as redundant ownership.
- Keep same-object identity, object aliasing, memory ordering, tracing reachability
  and finalizer/weakref semantics outside the bare linear-credit proof.

This verifier is a proposed next step, **not implemented here**. The model supplies
regression examples and falsifiable safety obligations for it. Runtime adoption
still needs actual lowered-IR execution, exceptions/reentry/yield cases and the
authorized native five-GC differential gates. No elapsed-time, RSS, pause-time,
memory-ordering, liveness/fairness or unbounded correctness claim follows from
the current tests.
