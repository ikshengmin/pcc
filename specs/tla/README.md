# Ownership transfer and marking: a bounded TLA+ design model

## Status and intended use

`FlowGCTransfer.tla` specifies the atomic owner-transfer/marking subset of
[`flow_gc_protocol_model.py`](../../scripts/research/flow_gc_protocol_model.py).
It is a reviewable starting point for TLA+ checking, **not a machine-checked
proof of PCC, and not a replacement for native five-GC qualification**. No
production implementation is changed or imported by this work.

The current verification status is deliberately split:

- TLA+ actions, variable assignments, invariant definitions and configuration
  syntax received manual review. No concrete defect was identified.
- **SANY and TLC were not run.** Java 21.0.12.1 is present, but no `lean`, `lake`,
  `tlc` or `tla2tools` command was found in PATH, and no `tla2tools.jar` was found
  in a depth-six search of `/usr`, `/opt` and the available workspaces. This is
  an inventory result, not proof that no checker exists anywhere. No software
  was installed or downloaded, and no network or permission setting changed.
- A separately written Python transition mirror was executed and compared
  against every complete schedule in the original model. **This does not parse
  TLA+, prove translation equivalence or validate native locking.**
- Before a cleanup-only dependency repair, the original 26 model tests plus
  10 new tests passed on cloud CPython 3.15.0rc1; the new 10 also passed on
  CPython 3.12.14. After the repair, **36 focused tests from the current 38-test
  inventory passed** on 3.15.0rc1, including the new cleanup/reentry regressions
  and short barrier witness. The two large marking-enumeration tests were not
  repeated because their inspected paths did not change. These are host-only
  standard-library tests, not PCC compilation or native execution.

## Scope and invariants

Both configurations enumerate 1–3 ordered ownership credits and every mapping
of those credits to **two** possible object identities. Aliased credits remain
distinct owners. This includes binary alias patterns, not the three-distinct-
objects pattern for three credits. The output stays rooted. Referents are
leaves. The abstraction is sequentially consistent and has one marking cycle.

Actions are `ScanOutput`, `ScanFields`, `ScanRoot(i)`, `Transfer`, `Sweep`,
`Publish` and terminal `Done`. The collector scans fields only after discovering
the output and sweeps only after all scans. Mutator transfers remain in source
order, but may occur before, between or after scans, including after sweep.
`Publish` is final experiment bookkeeping after all scans and transfers; it is
not a claim that production publication waits for a collection cycle.

The checks distinguish:

- `TypeOK`: finite domains and state shape.
- `OwnershipConserved`: each credit occurs in exactly one root or field.
- `PrefixVisible`: only the initialized prefix contains transferred credits.
- `AllOwnedVisible`: every credit is visible in a root or initialized field.
- `ScanOrder`: output discovery precedes field scanning; all scans precede sweep.
- `PublishedComplete`: final bookkeeping follows complete initialization.
- `NoLostLiveObjects`: the sweep snapshot contains no live, unmarked identity.

`NoLostLiveObjects` is intentionally vacuous before sweep. A partial mark need
not contain every live object. The safety check becomes meaningful at `Sweep`,
and the recorded failure remains visible afterward. The configurations do not
restrict exploration to states satisfying an invariant. `ASSUME` constrains
constant domains only; it does not assume the desired safety result.

No fairness or eventual completion property is claimed. The explicit terminal
self-loop avoids treating normal experiment completion as a deadlock. Arbitrary
stuttering is allowed by the specification. Multiple cycles, allocation,
failure cleanup, finalizers, weak references, resurrection, old/young generations,
relocation, lease retirement and native memory ordering are outside this model.

## Positive and deliberately broken configurations

`FlowGCTransfer.cfg` sets `UseBarrier = TRUE`.
`FlowGCTransferNoBarrier.cfg` changes only that flag to `FALSE`; domains and
invariants are identical. The second configuration is expected to **fail**
`NoLostLiveObjects`, while conserving every ownership credit. It does not model
a discovered production PCC defect: it intentionally removes a design obligation.

For one credit whose referent is `o1`, the minimal witness is:

1. `ScanOutput`: discover the rooted output.
2. `ScanFields`: scan the empty output and make it black in the abstraction.
3. `Transfer`: atomically move credit 1 from source root to output field.
4. `ScanRoot(1)`: see the now-empty root.
5. `Sweep`: record `missed = {o1}` without the barrier, or `{}` with it.

The informal safe-case argument is a useful review aid, not a checked theorem:
at sweep each credit was either still in its source when that root was scanned,
already in a field when fields were scanned, or transferred after the field scan
and shaded by the barrier. All objects are leaves, so shading an identity can be
abstracted as marking it. Transfers after sweep preserve the visible identities.

## Python cross-check and actual results

[`flow_gc_tla_crosscheck.py`](../../scripts/research/flow_gc_tla_crosscheck.py)
implements the action relation independently in Python, explores unique states
with breadth-first search, checks the named structural invariants at every
reachable state, and replays every complete schedule against the pre-existing
`mark_schedule` oracle in both barrier modes. Python event indices are zero-based;
TLA+ credits and slots are one-based. `Done` self-loops are not counted as Python
transitions. Neither implementation is generated from the other or from TLA+.

Observed on CPython 3.15.0rc1, before the unrelated cleanup repair:

| Python mirror | Initial states | Unique states | Nonstuttering transitions | Terminal states | Safety-violating states |
| --- | ---: | ---: | ---: | ---: | ---: |
| Barrier enabled | 14 | 1,358 | 3,588 | 14 | 0 |
| Barrier removed | 14 | 1,728 | 4,234 | 44 | 70 |

The schedule comparison checked **41,358 complete schedules per variant**:
0 positive failing schedules, 2,550 negative failing schedules, and 0 differences
in the two Python implementations' final missed-identity sets. Intermediate-state
equivalence is not checked by that comparison. These are model cases, not failure
probabilities. Schedules and unique states are different measurements. The state
counts above are **Python results, not TLC output**; TLC counts remain unknown.

Dependency provenance is explicit rather than assigning an old run to new bytes:

- The exhaustive receipt used `flow_gc_protocol_model.py` SHA256
  `eacb472a9de2571d686c5f1af51beef0b251e8671ac80afb5daf31aa41756d71`.
- The final dependency is SHA256
  `afc3b4f1cc1f0bda0633a4fb9af9e59a02fef9b43da14b5753159621cd3aff89`.
  Its changes are `Construction.cleanup` and initialization of an empty
  `callback_errors` list, unused by marking. The remaining Python AST was
  compared and is identical, including transfer, visibility, publication,
  `interleave`, `mark_schedules`, `mark_schedule` and `finite_mark_experiment`.
- The final dependency received the 36-test focused rerun described above,
  not a second exhaustive schedule run. Both disabled exhaustive tests had
  passed against the recorded earlier dependency.

From the repository root, set `PYTHON` to an already available host interpreter
(3.12 or newer) and `UV_CACHE_DIR` to a writable cache directory. This explicit
override is appropriate for these standard-library research models, not for
qualifying the production compiler on a different Python version:

```sh
export UV_PYTHON_DOWNLOADS=never
uv run --offline --no-project --python "$PYTHON" \
  -m unittest discover -s tests/python -p 'test_flow_gc*.py' -v
uv run --offline --no-project --python "$PYTHON" \
  scripts/research/flow_gc_tla_crosscheck.py
```

The cloud receipt used `UV_PYTHON_DOWNLOADS=never`, a writable task-local
`UV_CACHE_DIR`, and `uv run --offline --no-project --python "$PYTHON" ...` with
the existing 3.15.0rc1 binary. The checkout requests final 3.15.0, which was not
available in the normal interpreter search. The explicit host override avoids
an implicit download and is not represented as qualification on final 3.15.0.

## Running the real TLA+ checks later

Use an already approved and available `tla2tools.jar`. The following commands are
reproduction instructions, **not commands run for the results above**. They do
not download a checker. Run from `specs/tla`, choose a writable output directory,
and retain the jar hash, Java version, exact model/configuration hashes, command,
exit status and complete checker output.

```sh
test -f "$TLA2TOOLS_JAR"
sha256sum "$TLA2TOOLS_JAR"
java -version
java -cp "$TLA2TOOLS_JAR" tla2sany.SANY FlowGCTransfer.tla
java -jar "$TLA2TOOLS_JAR" -workers 1 -metadir "$RESULTS/safe" \
  -config FlowGCTransfer.cfg FlowGCTransfer.tla
java -jar "$TLA2TOOLS_JAR" -workers 1 -metadir "$RESULTS/no-barrier" \
  -config FlowGCTransferNoBarrier.cfg FlowGCTransfer.tla
```

Require a clean SANY result and a completed positive TLC check with no invariant
violation, then confirm the negative run specifically violates
`NoLostLiveObjects`. A parse error, unsupported expression, timeout or unrelated
failure is **not** a successful negative control. Do not hide the negative exit
status behind `|| true` and call the run green. See the
[TLC command-line documentation](https://docs.tlapl.us/using:tlc:start).

Even successful TLC checks would establish only bounded-model safety. Connecting
this specification to compiled PCC requires a separate refinement/implementation
argument and the authorized native validation gates. No Lean proof is supplied;
no `axiom` or `sorry` is used to manufacture a proof claim.

## Frozen production-source correspondence

The source audit reread PCC commit
[`206553ae8732c91b35ed9d022a0708863cad41fe`](https://github.com/ikshengmin/pcc/tree/206553ae8732c91b35ed9d022a0708863cad41fe)
through the connected repository, not from a paper summary. This is a frozen
correspondence review, not a claim about an uninspected later branch tip.

- [`py_list_from_owned_roots`, lines 562–612](https://github.com/ikshengmin/pcc/blob/206553ae8732c91b35ed9d022a0708863cad41fe/pcc/runtime/py/py_list.py#L562-L612)
  requires distinct registered, unleased source slots and a registered empty
  output. Its loop takes the graph lock, resolves the authoritative source,
  applies the slot write barrier, writes the destination, clears the source,
  advances visible length and unlocks. Final tracking/publication and lease
  release occur after all transfers. `Transfer` abstracts that critical section
  as atomic; the model does not establish that the native implementation meets
  that assumption or verify its failure paths.
- [Moving-root helpers, lines 225–258](https://github.com/ikshengmin/pcc/blob/206553ae8732c91b35ed9d022a0708863cad41fe/pcc/runtime/py/py_list.py#L225-L258)
  register, reload and unregister handles. The model assumes roots remain
  registered and does not allow an old raw address to stand in for a live root.
- [Foreign/borrowed lease contracts, lines 188–205](https://github.com/ikshengmin/pcc/blob/206553ae8732c91b35ed9d022a0708863cad41fe/pcc/runtime/py/freestanding_gc_root_operations.py#L188-L205)
  distinguish address stability from ownership. Borrowed slots require an
  external live owner, exclusive slot control and no rebinding until release.
  None of those lease state transitions is proved here.
- [Slot barrier dispatcher, lines 324–402](https://github.com/ikshengmin/pcc/blob/206553ae8732c91b35ed9d022a0708863cad41fe/pcc/runtime/py/freestanding_gc_barrier_dispatcher.py#L324-L402)
  handles backend-specific active marking and old-to-young obligations.
  Backend 1 shades a black owner's new referent during an active cycle;
  backend 2 uses a broader active-cycle condition; backends 3/4 have remembered
  owners/store buffers. `UseBarrier` abstracts leaf insertion shading only,
  **not the full dispatcher or all five collectors**.

## Research context, not transferred guarantees

The linked original abstracts and HTML were checked before choosing this scope:

- [Typed Template Fuzzing, arXiv:2610.31342v1](https://arxiv.org/html/2610.31342v1)
  uses typed templates prepared before a language-agnostic mutation loop. Its
  reported 96% validity is the authors' campaign result, not PCC evidence, and
  valid generated programs do not establish behavioral equivalence.
- [Record Types Everywhere, arXiv:2610.27519v1](https://arxiv.org/html/2610.27519v1)
  describes a record calculus, its Lean 4 mechanization and a verified
  interpreter. Those theorems concern that calculus; they are not proofs about
  PCC's current roots, runtime or compiler.
- [Agentic Verified Optimization, arXiv:2610.30188v1](https://arxiv.org/html/2610.30188v1)
  proves equivalence of Lean reference/optimized implementations. Section 4.2
  validates source-to-Lean ports by differential testing; section 6 identifies
  certifying that migration as further work. Its equivalence proofs do not by
  themselves certify the entire C/C++/Go/Rust-to-optimized-Lean path, and do not
  prove execution-time improvements. The paper's performance results were not
  reproduced here.

The practical choice is therefore narrow: use TLA+ for a reviewable finite
concurrent protocol, and later use a real Lean checker for suitable deterministic
function equivalence. Neither route justifies removing roots, leases or barriers.
