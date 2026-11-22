# GC3 remembered barrier: ownerless publication coverage

Reviewed for [GC3 #8](https://github.com/ikshengmin/pcc/issues/8#issuecomment-7075091903).
This is a source audit, not a runtime bug report: no nursery is enabled.
A raw initialization is not necessarily a root, and a name ending in
`_gc0` is not a collector dispatch restriction.

| Writer family | Destination and publication | Activation witness still required |
| --- | --- | --- |
| `pcc_gc_store_ptr` nonmoving branch | Receives the managed owner but hands only slot, value and backend to `_store_root_nonmoving_tracing`. Its active-major and legacy object-index gates must not gate the minor insertion barrier. Actual root-store siblings deliberately have no heap owner. | Store into an old reachable container with major idle; remove the young value's independent roots; verify two minors, with no unrelated clean-card visit. Include a real frame-root store as a distinct control. |
| `py_gen_frame_set` raw list-backed frame | Direct list items store under frame/container lock; the value-only shade has no frame or item-array owner. The inline path is separate. | Force raw frame selection and verify two minors, slot overwrite and card retirement. |
| `pcc_gc_try_init_ptr_locked`: list append | `_append_existing_capacity_gc0` and `_append_grow_gc0` allow nonmoving count-free mode. The destination is off-object; the list length makes the slot visible after the store. Growth can replace the backing, so deriving the owner from its raw address is not safe. `_list_get_gc0` instead writes a temporary result. | Old list, both spare-capacity and replacement-array paths; retain through two minors, overwrite/clear and observe retirement. |
| `pcc_gc_try_init_ptr_locked`: dictionary insertion | `_dict_gc0_admitted_locked` can admit GC3. `_dict_set_gc0_locked` fills the off-object entry's key and value before publishing them through count/index fields; partial initialization can roll back. `_dict_get_gc0_locked` instead copies into a result slot. | Isolated young key and value under old dict; check insertion and failed second slot, then replacement and removal. Verify no card leak from rolled-back publication. |
| Private sequence materialization and sort | `_dict_gc0_materialize` builds tuples and a list, including a separate raw list-pointer store for items. `_sorted_gc0` fills an off-object result array. Both admit nonmoving count-free mode. Private backing is not an old edge until its owner is old and published; late publication or old/black allocation during major GC needs an explicit owner boundary. | Force old/black construction or promotion only once that policy is chosen. Verify tuple/list child graphs and no intermediate publication on refusal. |
| Persistent C roots / memoryview export | C-owner shade in `pcc_gc_cpy_handle_change_c_owners` accompanies persistent root/pin ownership. A GC3 address lease alone is a no-op; the memoryview export uses a separate registered root. These are root/handoff boundaries, not dirty old-card stores. | Hold exports and C handles across two minors, then release them and prove reclamation. If excluded from nursery, prove promotion before raw address exposure. |

Many other value-only notifications cover temporary roots or completed native
results, not owning heap writes. Do not turn every such notification into a card;
audit the actual destination and root lifetime. The table does not close the
alternate raw C, inline/off-object tables, detach or bulk-writer inventory.

The focused source test runs the dictionary admission helper and initializer
from the real runtime source with GC3 flags and a simulated old dictionary entry.
It observes the destination store and a value-only shade. An independent graph
oracle sees the young object, whereas the hypothetical sparse minor without the
owner barrier does not. It does not run a dictionary insertion in a native
nursery or demonstrate a failure of today's collector.

The Linux pcc0 native control holds the major mark open, puts a new heap
string key and list value into an old dictionary, and checks both page bits
and the stored identity. A subsequent replacement shades its value, while
an unwritten list stays white. Calling the real first-store helper separately
on a detached slot admits the pointer and shades the value: success does not
identify an owning container. Paired with the source test this validates the
existing major shade, not minor remembered cards or partial-insert rollback.
Mac and pcc1 were not executed.

The transaction witness runs the current `_dict_set_gc0_locked` body on a
GC3 host fixture with a forced refusal of its second initializer. This
leaves the entry, index bucket and item count unpublished. On success both
slots and the index are committed under the table lock, with the dictionary
owner still available. An observer at that commit point feeds the exact
owner and both values to the test-only remembered model, retaining both
values through two minors. An owner-aware barrier belongs in the
transaction, not its value-only initializer or the unlock/growth path.
An implementation must establish that a stopped collector cannot see the
committed fields without their card. This does not install or time that
barrier. The focused source/model and adjoining dict tests passed 28 cases.

The GC3 raw list-backed generator frame witness executes
`py_gen_frame_new` and `py_gen_frame_set` natively under a controlled
active major. First store and replacement preserve identity and shade just
the stored child; clearing sets the slot to NULL. The source witness
observes the backing-store write and value-only shade between acquisition
and release of the frame's own container lock. The owner is available at
that transaction, not inside the shade function. Neither witness covers
the inline frame path, a minor, or card retirement. Linux pcc0: three
native writer controls and three source witnesses passed; Mac not run.

The shared-store witnesses execute native `pcc_gc_store_ptr` into a heap
owner and `pcc_gc_store_root` into a separately registered scheduler slot.
Both publish, shade an indexed white value during the controlled major, and
clear independently. A source-dispatch witness confirms both currently
reach `_store_root_nonmoving_tracing` with only slot/value/backend. A second
executes that helper: idle major or unindexed value gives no shade; the
indexed active-major control shades. The nursery path must feed a heap
owner and perform its header-bit check independently of this legacy index
gate. Registered roots stay root-only. Linux pcc0: four native controls;
five source witnesses. No Mac, pcc1, performance or actual nursery run.

### Inert managed-store classifier

The test-only freestanding probe takes the existing backend decision and the
actual heap owner as arguments. After rejecting non-GC3, root/NULL and tagged
arguments, it reads the owner's flags, then the value's flags only if the
owner is non-young. The predicate intentionally does not require a legacy
OLD bit: GC3's current indexed objects have neither age bit. The owned
Linux executable tests all four inactive backends with addresses which
would fault if dereferenced, and old/young, young/young, old/old, NULL and
immediate inputs. The IR has two atomic flags loads with no calls, stores,
index, radix or active-mark read. Four inactive-backend cases in this
standalone probe do not constitute a production control A/B.

The predicate is not a barrier. An eventual production caller has to pair
the dirty-card enqueue with edge publication in the same no-park
transaction. A detached slot cannot supply an owner; roots remain on the
root path. A stale address, partial handoff, or reused owner/card also
cannot be validated by the header bits alone. The candidate is not linked
into the runtime and no ordinary call, load, or store changes today.

## Collector-entry root baseline

`freestanding_gc_conservative_roots._capture_thread_words` refreshes the
stopped owner before enumerating the snapshot.
`freestanding_thread_kernel_pthread._thread_snapshot_roots` captures
current registers and copies from the SP of that capture, not from a saved
collection-entry boundary. The test runs both existing capture/refresh bodies
with supplied machine-state fixtures. A fake object word below the entry SP
and a current collector register enter the result; a caller stack word is
retained. The independent entry oracle also includes a saved caller-register
word, illustrating why just trimming the stack is insufficient. This is not
a native register probe or a claim that the modeled register was lost in a
specific production collection.

Before activating a GC3 minor, qualify entry capture at the public and
automatic collection boundaries, preserving the outermost capture through
nested entries. Keep existing other-owner polling snapshots unchanged.
Check collector temporaries that really must become roots through existing
explicit slots/leases, including callbacks and weakref/finalizer queues.
The native gate needs both a stale collector-only candidate that dies and
independent caller-stack, callee-saved-register and exact-slot controls
that survive, with a supported stack ABI. An integration pass on only one
host does not qualify Darwin.

The targeted new baseline and two zero-forwarding groups pass (51 cases).
The wider existing forwarding fixture was not green: on the unchanged
source/test preimage, GC3 case `[order0-0-3]` again refused forwarding
installation before reaching the scan. This is not a result of the new
tests. No native collector or performance gate was run in this round.

The Linux x86-64 live-register test now links an ELF using PCC-owned objects
and executes the same capture intrinsic as the runtime. Six caller
callee-saved register sentinels must appear in the captured registers or the
copied stack (where the compiler may save them). Both register-allocation
settings passed, checking the SP and buffer canaries. Four emitted-object
targets and the host-only intrinsic guard also passed. No production
collection, callback-acquired root, or macOS execution was tested here.

The native GC3 callback-acquired root control allocates a referent in a
cyclic object's finalizer, checks that the finalizer is inside the outer
collection with the world resumed, and publishes the referent to a
registered off-stack owner. The weakref reads it after the finalizer, after
the outer collect returns, and after another explicit collect. The second
mode also checks that a nested `gc.collect()` does not reenter tracing.
The nonrecursive result matches CPython. This checks the current explicit
owner path; it does not exclude extra conservative copies of the address,
qualify an entry cutoff, or exercise a nursery.

Reproduction with a fresh source-matched threaded archive:
`PCC_WITH_THREADS=1 PCC_RUNTIME_ARCHIVE=<archive> uv run pytest -x -v -m
integration tests/python/test_gc_callback_live_roots.py::test_native_gc3_finalizer_acquired_root`.
Compare the ordinary mode using
`tests/python/test_gc_callback_live_roots.py::test_finalizer_acquired_root_reference`.
Linux pcc0 was executed; macOS and pcc1 were not.

In the later scaling gate, report old index visits and payload-span
enumeration separately from stack words and clean-card visits.

## Major-cycle handoff: proposed initial GC3 policy

Recommendation: close admission to unindexed young slabs for the whole major
transaction (handoff, root seeding, active mark, candidate commit and sweep).
After the existing backend dispatch, GC3 alone checks admission; GC0–2 and GC4
must not pay for it. Checking only `pcc_gc_mark_active` is insufficient: it
becomes true only after a seed claim succeeds, and it clears at candidate
commit before the sweep and its user-code windows finish.

Use the same ordering as `pcc_gc_register_object_cell`: sampling admission and
publishing the completed allocation's bounds are in a no-park region. Close
admission in the stopped world; detach nursery cursors, drain pending
publication and publish all existing nursery cells to the ordinary index in
place, before the first major seed can take a snapshot. Report handoff as major
pause work, bounded by the configured nursery budget; never hide it as a
minor. Do not silently proceed on a partial handoff: keep admission closed
and preflight the required metadata before starting the handoff. If it cannot
complete, use a lossless fallback or fail in the same way as ordinary required
GC metadata allocation. Never sweep unindexed cells as if handoff succeeded.

While nursery admission is closed, allocate on the current indexed old path.
During active mark, preserve its born-black registration and the existing
shade barrier for stores into initialized objects. A publication staged
before the mark is not automatically safe just because registration now
colors it black: `freestanding_gc_alloc_stage._publish` shades allocations
whose staging epoch predates the current mark. Keep this existing rule.
Commit-time sweep candidates must exclude objects allocated after the verdict;
reopen the nursery only after candidate ownership and callback windows end.
The ABI, handle/lease roots, and collector-entry scan are unchanged.

The serial admission tests cover reservation crossing the handoff, a
born-black allocation with an edge to a pre-existing white object, and keeping
the nursery closed through commit and sweep. Adjacent allocation staging tests
exercise the current source implementation's same-epoch and prior-epoch paths.
Neither is a native race test. Before activation, make a native two-mutator
test stop at three points: reservation before publication, after handoff
before active marking, and after commit while a finalizer window is open.
Verify exact registered-object identity and survival, child shading, sweep
exclusion and the reopening boundary, under the existing GC0–4 controls.

Existing-path Linux pcc0 control: with the active-mark boundary deliberately
held open, two ordinary new list allocations publish distinct, stable index
records, black headers and set page mark bits. Appending through both backing
storage growth and spare-capacity stores shades a pre-existing white child and
sets its page bit. A third, unwritten white object stays unmarked. The probe
does not start a real competing GC cycle or establish nursery handoff ordering.
Run with a current threaded owned archive:
`PCC_WITH_THREADS=1 PCC_RUNTIME_ARCHIVE=<archive> uv run pytest -x -v -m
integration tests/python/test_gc3_indexed_allocation_native.py`.
Mac and pcc1 were not executed.
The separate native scan-budget case `[3-0]` failed at its
`unexpected_head` fixture check, before the scan. Its code, fixture and
runtime are unchanged by this round. Do not count it as a passing control
or as evidence about the new indexed-allocation test.
