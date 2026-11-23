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
| Persistent C roots / memoryview export | C-owner shade in `pcc_gc_cpy_handle_change_c_owners` accompanies persistent root/pin ownership. A GC3 address lease may return token zero. The dedicated memoryview cache helper could register a root but the public path does not dispatch to it. These are root/handoff boundaries, not dirty old-card stores. | Hold exports and C handles across two minors, then release them and prove reclamation. If excluded from nursery, prove promotion before raw address exposure. |

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

### GC3 buffer export: live dispatch versus candidate helper

`pcc_PyMemoryView_GET_BUFFER` reaches `_memoryview_owned_buffer`, which
installs a raw cached `Py_buffer` with `internal == NULL`. The distinct
`_memoryview_gc3_owned_buffer` registers a scheduler root before copying
the exporter, but has no call site on this revision. Do not count that
helper, or a model that returns a synthetic positive lease token, as a
live export root. `PyObject_GetBuffer`/`PyBuffer_Release` uses a different
explicit pin/release protocol and needs its own nursery witness.

In the owned Linux native control an explicit scheduler owner keeps the
view live. A bytes exporter is resolved via the public C cache entry. The
same original raw `buf` address is readable after two real full collections,
before any call that could refresh the pointer; the refresh returns the
same cache and payload address. The run printed `dedicated-root=0`.
It does not prove reclamation after releasing the view or protection
without a live view/base. The companion CPython public exporter test is
separate from this private layout check.

The pre-existing `test_memoryview_owned_buffer_lifetime.py` model called
the public entry but expected a dedicated root and triggered failure
injection in the unreachable helper. Six cases in that suite's original
form failed before the fail-fast stop. The corrected tests call the
dormant candidate directly where they inject its failures, keep the
public-entry checks separate, and verify candidate cleanup with a
synthetic token zero as well as positive-token doubles. The corrected
host checks and exporter reference passed (65 cases); this does not turn
the candidate into a live path. Do not admit bytes/bytearray buffer exporters
to a nursery on the basis of these checks. First qualify promotion before
an address escapes, the real persistent owner if one is required, and
exactly balanced export release.

Linux native control for the explicit pin path: one binary acquires exports
from a bytes object and a memoryview of it. The records and view owners are
distinct but their raw payload address matches. Release direct first and
view first are separate processes. The original address is read through two
real full collections; the remaining record and exporter header pin survive
the first release. The last release zeros the public descriptor, empties
the C export list and clears its explicit pin. The broad address-pin query
can still report a stopped-world conservative pin; it does not prove a leak.
The baseline cache control passed alongside both release orders (three
native nodes). This neither guarantees immediate reclamation nor qualifies
raw addresses from nursery storage. The runtime is unchanged.

### First weak reference is an event, not a type exclusion

The source witness exercises the current `py_weakref_new` body in four
paths: unsupported target, allocation failure, normal chain, and late
finalizer/callback chain. Unsupported targets do not resolve or allocate.
A failed weakref allocation never publishes a referent. A successful
allocation writes the weak target before linking either chain. A future
GC3 in-place promotion needs the resolved, still-rooted target, after
admission and before that first publication. Since weakref allocation
may park, the implementation must reload/validate the rooted target
when it resumes; it cannot carry raw nursery state blindly across
allocation. Repeated weak references to a now-old target need no new
promotion. If promotion fails, leave the weak reference unpublished;
never change weak-target reachability into a strong edge.

The public control observes referent identity through two weakref
creations and explicit collections, leaves an untreated sibling intact
and rejects an unsupported target. It does not measure nursery promotion.
Test mode does not change runtime dispatch.

### Promotion publication contract

The existing `pcc_gc_generational_promote_young_if_known` starts with a
known-object check and can attempt `pcc_gc_generational_oldify_copy`.
Neither is sufficient for indexless nonmoving nursery objects. The
proposed operation validates young provenance and reserves the old
record and needed card, then publishes in place under one transaction
before dropping young admission. On lack of metadata, the weakref or
raw export must not publish its target. A card overflow likewise leaves
the object young and requests a safe major instead of sweeping it.
A repeat request for an already-promoted object is inert. A self-edge
does not create a remembered edge after in-place promotion.

Four new serial tests exercise these cases (19 focused host checks).
They do not supply a native promotion implementation, lock proof or A/B.

### What existing allocator provenance does and does not provide

The Linux owned C fixture uses ordinary GC3 allocator cells, not a bump
nursery. It does not register either cell in the tracing index. The
exact published address is recognized by both `pcc_gc_granule_is_object_start`
and `pcc_gc_pointer_is_managed`; an interior address, a reserved cell with
a complete object-looking header, and a raw malloc with the same header
are rejected. Retiring one published cell invalidates its cached positive
without invalidating the other. These are lifecycle checks, not claims
about mark/sweep survival for unregistered objects.

`pcc_allocator_object_cell_span` returns no span until the per-cell
record array exists, so it cannot be used as a record-free nursery
iterator. A nursery must separately enumerate its published slab bounds
or bitmap. Its cursor must distinguish generations when a slab is reused;
address identity alone in this baseline only describes the currently
allocated object.

The proposed serial model freezes a publication tail and carries a slab
epoch in its cursor. A later publish cannot extend the frozen slice;
an unpublished reservation and an interior address are rejected. Iteration
skips dead and promoted cells, but a promoted cell still prevents bulk
reset while live, preserving its address. Reuse advances the epoch, and
a stale cursor reports an error rather than masquerading as exhaustion.
Four modeled cases plus adjacent tests passed (18 total). A real iterator
must use bounded word bitmaps, acquire/release publication, and a safe
handoff, not the model's unbounded integers.

The standalone owned-C prototype uses two bounded bitmap words and a
release-published tail. Linux pcc0 execution skips holes across cells
63/64, excludes cell 127 from a previously frozen range, refuses bulk reuse
while an in-place promoted cell remains, and reports a stale epoch after
reuse. The cursor-only case executes on one thread.

The publish/stop case adds a real registered PCC mutator. It reserves but
does not publish a test cell, waits for the collector's actual stop request,
and polls inside a no-park region. The collector returns from the stop only
after the complete payload and release tail are visible and the mutator is
parked at no-park exit. A second cell published after resume cannot extend
the frozen cursor. The unprotected control parks during the first poll;
the stopped collector sees a zero tail and rejects the partial cell.
Three owned native Linux cases passed. This proves the protocol at the
prototype boundary, not production allocator integration, promotion or a
weak-memory result on another architecture.

Two further executions use real reserved object cells rather than just
synthetic payload words. During a protected stop, the complete first cell
is visible to the exact allocator and managed-pointer queries without a
tracing record, while the reserved second cell is unknown. The unprotected
stop still sees a written type tag but no payload or allocator provenance.
After resume both cells become managed; explicit retirement removes both
positives. All five native fixture variants passed under Linux pcc0. The
test tail/bitmap itself is still test-owned; ordinary allocation,
collection and performance behavior are unchanged.

A separate owned native probe manually prepares a valid bytes cell without
a tracing record. The ordinary index registration rejects its legacy
YOUNG bit. With a registered root and fixture-controlled normalization
to the existing GC3 old representation, the same address gains an
ordinary record and span; a repeat registration returns the same
record. It keeps identity and bytes through two full collections.
This is the single-mutator baseline only. Actual promotion needs a
preflighted record, a remembered-edge reserve and a non-parking
publication transaction, not a direct header write from the fixture.
The preparation variant rejects an unaligned request and then prepares
storage for the real object. A simulated refusal of the separate card
reserve leaves the target young and rooted, absent from the tracing index
and the stopped major walk. Retrying preparation adds no bytes to
`pcc_allocator_metadata_mapped`; ordinary commit publishes the same
record exactly once in the major walk. All three native lifecycle cases
passed under Linux pcc0. This does not inject actual mmap failure or
exercise a real remembered-queue reservation.

Record preparation can still map 96 bytes per cell for a whole slab at
the first old record, even when only one cell is promoted. Empty keys
in those mapped spans are tested by the ordinary major iterator. For a
future measured gate compare equal promotion counts clustered in few slabs
versus scattered over many, and report `pcc_allocator_metadata_mapped`
separately from peak RSS and scan/visit counts.

The native owner-slot check creates a real allocator-backed list and bytes
cell, publishes allocator provenance for both, and publishes a tracing
record for only the list, at its original address. The stopped world's
existing strong-slot visitor reads the exact off-object item slot and
the indexless young child; the child still has no tracing record
after the visit. This identifies the data available for card
preparation without introducing a barrier, card queue or minor.
One guarded Linux pcc0 native case passed.

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

## Private dictionary items publication during active major mark

The Linux pcc0 owned fixture calls the real native private items builder
directly and asserts fast status 1, rather than allowing the public
adapter to hide a fallback. A source dictionary whose key and value are
white is observed under the fixture-held active mark. The returned
list and its private tuple are distinct indexed black allocations with
page marks. The tuple's original key and value are gray with page marks;
an unrelated white object remains white. Diagnostic exclusion of the
fast path returns 0 with no output or child shading before the same
input is accepted with diagnostics disabled. This is an early gate
control, not a simulation of failure after a partial tuple store.

Reproduce using a source-matched threaded runtime archive and Linux pcc0:
`PCC_RUNTIME_ARCHIVE=<archive> uv run pytest -m integration
tests/python/test_gc3_indexed_allocation_native.py` (five native controls).
This fixes the boundary for a future owner-aware publication hook.
It does not run a nursery, a minor or Darwin, and changes no runtime
dispatch. The private sorted-list writer is separate.

## Private sorted-list publication during active major mark

The same Linux pcc0 fixture requires actual fast-helper success for a
small list taking insertion sort and an 18-element tuple taking reverse
merge sort with scratch storage. The published list is a new indexed
black object; the sorted slots hold the original object identities
and every copied child is gray with its page bit set. The source order
and an unrelated white object's color do not change. A mixed list
forces a non-plain comparison refusal: the input marks prove the
private copy ran, but the result slot stays null and no exception is
published. This distinguishes a post-copy refusal from an admission
gate; it does not assert immediate reclamation of the private part.

The eight native cases use the same source-matched archive command
above. The raw merge scratch is not an old owning container: it holds
a transient permutation while the original children remain rooted in
the output array. No nursery/minor, production code change, macOS,
pcc1 or throughput claim.

## Inline generator factory dispatch is not a second GC3 heap writer

The owned fixture calls `py_gen_inline_frame_new` in separate GC0–4
processes. GC0 creates the inline generator layout; GC1–4 create an
ordinary list-backed frame. In every case a different backend is
refused after the frame's managed allocation, and get/set/take
preserve identity and clear the taken slot. With a fixture-held active
mark, GC3's real list fallback shades the stored white child in the
header and page bitmap without shading an unrelated object. The
defensive inline traced accessor was not manufactured or declared
reachable on GC3; changing factory or backend-freeze policy would
reopen that part of the writer inventory.

Thirteen native Linux pcc0 cases pass using the same focused archive
command above, five of them the per-backend layout test. No performance,
GC3 minor, pcc1 or macOS run.

## Grouped generator restores currently take the scalar branch

The compiled consumer still attempts grouped take, but the current
public runtime entry always declines, as does grouped clear. The old
shapes test incorrectly required return 1 for a GC3 heap group; its
unchanged Linux archive reproduced exit 2. The corrected test checks
decline without output or input changes across group boundaries and
with a C-compatible handle; it reloads the registered owner before
each scalar take. Under GC3 active mark, declined take and clear
leave gray credit, pin, depth, store count and cells unchanged. The
real scalar route then empties those cells and its registered caller
results remain valid across explicit collection.

The separate forced-relocation fixture was left unchanged. Its
baseline exits 6 on GC3 (oldification did not copy a value) and 3 on
GC4 (the relocation set selected nothing), both before reaching
group take. Those failures are not evidence about the grouped
helper or today's actual forwarding. Source-matched Linux pcc0;
no macOS, pcc1, actual minor, activation or throughput result.

With the same archive, the unchanged
`test_no_log_generator_group_finalization_and_cleanup_matches_reference`
gets through the earlier modes and reproduces a separate GC1
`resurrection` disagreement with CPython at
`tests/fixtures/native/constructor/generator_root_groups.py:280`:
`watch() is resurrected[0]`. A direct rerun of that exact linked
executable failed at the same assertion. That test was not weakened;
the focused decline/scalar witness passed independently.

### Dormant stopped promotion transaction

The test-invoked entry verifies a published exact object cell on GC3 and
refuses to run without stopped-world ownership or during major mark or
sweep. It reserves the existing per-slab object record and checks a
caller-owned card slot before changing the header. That card represents
the whole promoted owner; it conservatively stays dirty until a future
minor can examine the live slots. With neither a fallible preflight nor
another mutator in the commit interval, the entry reuses actual cell
registration, records the card and flips the age under one no-park
lease. An occupied card leaves the cell young and unpublished in the old
index; retry uses the same allocated record. The address is unchanged.

This is not a minor, a nursery allocation route or a production
remembered set: the entry has no runtime caller and no automatic
collection uses the caller's card. No store or root route is changed.
The 20 existing functions in the emitted module kept identical IR
bodies after canonicalizing local labels and matching string literals
by bytes; the other runtime objects are identical.

Linux x86-64 host-pcc0/self passed eight linked cases with a source-
matched owned archive: real list promotion with an indexless strong
child; GC0/1/2/4 refusal without touching the supplied invalid address;
and three slab cases, including prepared-record retry and a rooted
same-address payload surviving two explicit collections. The list
fixture also rejects a running world, interior address, active mark,
pending sweep, and occupied card. Tests compare the owner, record,
header and child identities. No Mac, native-pcc1 or performance claim.

### First nursery admission target: measure real allocations

A single-carrier Linux GC3 gateway run counted successful allocations for
sequential loopback GET `/` with `Connection: close`. The stock pcc0/self
ELF (SHA-256 `58ea650afa64601dbc736b055b84220e268be1e47b93748fe33e50905c1da86c`)
used the stock runtime and
[pcc-gateway b149048](https://github.com/ikshengmin/pcc-gateway/commit/b1490486a993c9528dcbfeecf499daba18ae0ef1).
All four runs returned empty 200s and drained cleanly. Capture each fresh
process with `PCC_GC_BACKEND=3 PCC_LOG=alloc PCC_LOG_FORMAT=json` and a
separate `PCC_LOG_FILE`. Read the delta between fresh processes:

```sh
python scripts/pcc_perf.py alloc-census run-129.jsonl --startup run-1.jsonl --operations 128
python scripts/pcc_perf.py alloc-census run-65.jsonl --startup run-33.jsonl --operations 32
```

| Extra requests | Logged allocations | Tuple | List | Tuple + list |
|---|---:|---:|---:|---:|
| 128 | 56,843 | 20,205 | 10,212 | 53.5% |
| 32 | 14,929 | 5,704 | 2,541 | 55.2% |

These are balanced request/object records by exact size, not a lifetime
sample; dropping both records is still possible. The logger runs on the
real handler, but this simple gateway workload is neither the sealed
`benchmarks/handler/native_stats.py` arm nor C512. Do not use it for timings
or assume that an admitted subset accounts for the full type-level share.

Target tuple and list rather than another leaf-only prototype. Tuples
need builder initialization, replacement via `py_tuple_set_item` and the
public C setter, including paths that currently publish private tuple
slots directly. Lists need append/owned-slot transfer, replace/clear,
extend/insert, slice and backing-array publication under the correct
owner; ordinary C setters and append must either use those stores or
promote before exposure. Keep raw buffer exports and managed Go/Java
stores excluded until their ownership boundaries are validated. A first
review arm must report the actual admitted fraction on its sealed
handler, not the gross upper bound in this table.

### Transport the receiver on ordinary GC3 owning writes

The generic nonmoving GC3 heap store now supplies the actual receiver to
the slot barrier; the root store remains ownerless. Planned writes still
use the same slot barrier inside their enclosing lock. A review-only
stopped observer records an old receiver when its written referent is
young, without consulting the referent's old index or the major mark flag.
Overflow is explicit; no real collector reads this scratch buffer.

The linked Linux control uses an old list with an off-object array and an
old tuple with an inline slot. It records the list through the direct
store and the tuple through its StorePlan; a caller root records nothing,
and an extra store into a full buffer sets overflow without altering its
earlier entries. GC0/1/2/4 reject installation. Five backend runs and
the owned barrier symbol closure passed. The old generic-ownerless
source witness now tests the positive GC3 route. The dictionary witness
is intentionally unchanged: its private initialize path is not included.
The fast list first-store, tuple private builders, foreign escape, the
persistent sparse owner/card store and the actual minor remain gates.

The next review increment covers the two raw list append first stores
that do not use StorePlan. It reuses the owner flags already fetched by
the transaction. After the existing first-store admission succeeds, an
old GC3 list publishes its owner before its new logical length becomes
visible. That covers both writing into spare capacity and transferring
the off-object backing to a larger array. The existing value shade,
locking, and allocation/growth logic are retained. Refused first stores
publish no new card.

The linked list fixture returned 37 on the exact review parent (the
first append advanced its length but recorded no owner). On the new image it
recorded the old list at both first stores and preserved order, length,
capacity and values. All five mode lanes and 18 existing-focused host
models passed. Ordinary tuple owned-root construction already calls
the owner-aware slot barrier; eager dictionary materialization has
separate raw pair/result stores and is not qualified for nursery
admission. No minor or performance result from these tests.

### A stopped sparse cut retains a dirty owner while its young edge remains

The native review scanner consumes owner tokens from the bounded buffer,
not from the old heap. It checks current index membership before
dereferencing an old token and uses the real slot visitor, limited to
list/tuple strong slots. Only exact allocator-published young cells enter
the frontier. It keeps cards with a remaining young edge across cuts,
drops cleared or retired owners, and refuses an unsafe or incomplete
cut. A frontier overflow latches failure rather than authorizing a
partial result. This is still a review entry; it does not allocate or
sweep a nursery and does not yet deduplicate repeated young seeds.

Linux x86-64 pcc0/self: five rounds each with 64, 256 and 1,024 unrelated
indexed clean old lists. Each of the 15 runs used 128 repeated cuts
without intervening writes, exactly two dirty owners and two strong slots
per cut, and zero clean-old visits. Clearing both edges dropped both
cards. Frontier exhaustion, an invalid retired token and a non-stopped
call were separate controls. Medians for the 128-cut batch were 22/23/24
microseconds; one 1,024-list result took 906 microseconds. These are
counts for the dirty scanner, not a qualified pause or complete minor.

### Reclaim an explicitly closed young cohort

On review only, a stopped transaction now produces its sparse frontier in
the same cut, combines it with caller-supplied explicit roots, and closes
the real strong slots of a sorted exact young cohort. The bounded mark
and index queue deduplicate cycles. Membership is binary lookup in this
review vector, not an old-heap scan. Before any reclamation it refuses
uncovered young roots, an incomplete card frontier and opaque or
compatibility-owned edges in a dead cell. Only fixture-owned index-
free exact built-in list/tuple cells are admitted. Retirement clears
actual allocator provenance and releases cell storage and an owned list
backing without running user code.

Five Linux x86-64 pcc0/self processes each kept four real young cells
from a direct rooted cycle and an old dirty edge, retired one real
unreachable cell, and rejected four incomplete/unsafe cuts without
reclaiming it. Emitted native closure calls no managed allocation,
raise or refcount release; compiler polls inside the raw-array walk
are protected by one no-park lease. The prototype does not discover
caller stack, mapped, foreign or scheduler roots and is not called by
an ordinary collector. It uses per-cell retirement, not slab rewind
or a performance claim.

The next review variant binds the existing mapped/registered value
walker for this stopped cut and uses the thread snapshots made at the
initial stop. It does not refresh the stop owner's stack after reading
cohort metadata. An interior machine word inside an admitted list/tuple
payload can retain that object; no ordinary collector invokes this mode.

On Linux x86-64, three fresh runs of each of three controls retained a
real caller's odd interior pointer, an independent registered scheduler
slot, or both respectively. Omitting either source reclaimed just its
target and a dead value loaded into scratch only after the stop. The
closed-graph regression also passed three times. The C fixture does not
qualify native Python mapped-frame coverage, caller-register capture as
a separate control, or raw list backing exposure. Conservative snapshot
precision across automatic entries, full root admission and bump/slab
recycling remain open.

### True bump storage for a review nursery span

The review allocator registers one 64 KiB nursery slab as a distinct
span kind. A single owner reserves the next size-class slot under its
no-park lease, zeroes the slot and initializes it, then release-publishes
the committed frontier. Readers accept only exact starts below the
acquired frontier. There is no per-cell allocator header, index record,
radix insertion or allocator lock in reserve/commit. Ordinary allocation
and pointer admission do not accept this new span kind. The span can be
rewound only in a stopped world with the current generation, no staged
cell, the expected frontier and proof that no survivor remains.

Linux x86-64 pcc0/self linked the actual allocator. A peer could neither
see a staged cell nor reserve from the owner's span; after publication
it received the initialized header. Rejected/cancelled reservations
were never visible. A full slab held 2,048 32-byte cells with only 16
granule keys and zero object records. Live-survivor/stale-generation
rewinds failed; a valid empty rewind reused the same addresses. Linux
ran all five mode lanes and five additional GC3 processes. All 92
existing allocator LLVM function bodies were byte-identical. This is
not ordinary tuple/list allocation, a complete minor, or a throughput
result: real liveness/promotion must supply the proof before rewind.
