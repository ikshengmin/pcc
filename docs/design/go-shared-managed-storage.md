# Shared managed storage for Go and Java

**Proposal, not approval or implementation.** Replace source-shape selection
with ordinary typed lowering to PCC's shared runtime. The first release gate
is Go slice and string behavior on pcc's collector, not the number of source
packages that a probe can parse. This does not change Python or C-API
policy.

Revised on 2026-11-24 (Claude, at the maintainer's request): pcc has one
collector since that day, reference counting with a generational cycle
collector, so every gate below that named five collectors names that one.
Where the text asks a representation to survive relocation, the reason is
now the relocation step planned for that collector.

## Current gap and decision

The experimental shared-managed v1 envelope stores a GC0 tuple-shaped owner,
with an opaque payload tail and stable external handles. Its bounded Go
callers, CPUID consumers and native controls are experimental evidence;
none is a production slice, string or map ABI. In particular, a
tuple-specific size or slot walker cannot be assumed to describe that tail
to the cycle collector or to a relocation step, and a collector step that
treats tuples specially would apply to these mutable objects.

**Proposed choice:** use the existing PCC managed allocation, exact cell
provenance, root registration and counted stores. Add a common
header/descriptor contract for non-Python layouts through those same owners.
Do not add a Go heap, a second root registry, or native recognition based
on package, function or source spellings.
Keep existing Python layout tags unchanged unless a separately reviewed ABI
migration is necessary. Share the layout representation with Java, but do not
make Java frontend migration a prerequisite for the first Go semantic tests.

## Runtime object and view invariants

- A backing allocation has an immutable descriptor, extent and capacity.
  The descriptor identifies the fixed header/payload boundary, checked size
  and alignment, and exact managed reference slots or repeated slot maps.
  Primitive byte/scalar arrays have no reference slots. A reference array
  or a mixed record describes every owned and relocatable reference; offsets
  are not guessed from a language name, a tagged integer or allocator size.
  Enumerators, clearing, destruction by count, the cycle collector's slot
  visitor and a relocation step must agree on the same extent and slot
  information.
- Use the existing allocator/provenance and initialization-publication paths.
  New owned fields are initialized to null before collection can observe
  them. The new allocation is protected through initialization and becomes
  reachable through a registered owner slot before any subsequent safepoint.
  Specify and test the allocation-result handoff, including failed allocation;
  do not presume that a temporary raw pointer is a root or that a successful
  link makes a tag visible to every collector.
- A slice value is a rooted owner with offset, length and capacity in elements.
  Nil has no owner; `make([]T,0)` is non-nil, including capacity zero.
  Bounds and signed lengths are checked before pointer or size arithmetic.
  Copying a view copies metadata and preserves the owner. A subview is not
  a new allocation; its capped extent and aliases must be preserved. Keep
  language-level distinctions even when backing has zero payload bytes.
- A string is either immutable static bytes or a view of immutable owned
  backing, with byte offset and length. Conversion from a mutable byte slice
  makes a distinct copy; no UTF-8 decoding, no returning a raw frame address,
  no hidden alias. Later sharing or elision requires a generic proof that
  preserves the observable copy semantics, not a special caller identity.
  This contract supports embedded NUL and arbitrary bytes.
- Store an owner and its scalar view fields, not a derived payload pointer,
  across safepoints. Read/resolve the owner via the shared root/slot path,
  then derive a payload address for a bounded no-safepoint access. A
  relocation step must either update or resolve precise owner slots; once
  one exists, the implementation must observe actual relocation in a test,
  not permanently pin every new object.
  A raw runtime or FFI export requires the existing explicit pin/lease and a
  specified release point. CpyHandle and foreign ownership remain intact.

## Generic lowering and write coverage

- Lower normal typed `make`, literals, slicing, indexing, range, `append`
  and `string([]byte)`. Type/descriptor and element width drive reuse or
  growth; local variable names, syntax shape and project identities do not.
- `append` checks length plus added count, byte extent and capacity first.
  Within capacity it preserves the backing and applies the normal overlap
  rule. Growth allocates a correctly sized owner, transfers existing
  contents with scalar copy or barrier-aware reference stores, then publishes
  the result and releases the old ownership only at the defined boundary.
  The old view is unchanged by growth; failure does not publish a half-built
  view. Do not require the exact capacity chosen by the official Go runtime.
- Audit all owner/reference writes: local frames and temporaries, globals,
  arguments, caller result slots and exceptional exits; record field
  stores and array element mutation; initialization, append, copy and
  overlap, clear/delete, captured/foreign values, and runtime registry
  publication. Every such write uses the common counted store/drop
  protocol. Scalar bytes are never interpreted
  as managed slots. Exception and cleanup edges must remove the precise slots
  that the success path would transfer rather than leaking an extra owner.
- Map is a later consumer of this same contract. Hash tables, key equality,
  iteration and record-valued keys are separate language decisions; no map
  implementation or eligibility is implied by qualifying slice backing.

## Implementation and stop gates

1. **Common backing and roots.** Specify the header and descriptor ABI,
   object lifecycle and concurrent publication, precise root/slot maps,
   destructor/clear and relocation hooks. Prove a pointer-free byte backing
   and a reference-bearing control on the collector before a language
   frontend depends on the representation. Test root lifetime, old/new
   element visibility, and cycles and reclamation by a young collection, by
   a full collection in bounded stops and by an explicit collection. If the
   collector cannot support the representation, stop and state which
   required protocol is missing.
2. **Slice operations and then string copy.** Add generic lowering and tests
   first, with independently authored valid and negative programs. Run the
   same unchanged sources under official Go and PCC:
   nil/empty, several element widths, overflow, bounds, move/reuse, aliases
   and append overlap, failed growth, reference reachability, GC during
   construction, caller results and final release. For strings include
   arbitrary bytes, overwritten source and return/call lifetimes. Where
   exception recovery or foreign ABI is not yet supported, keep those gates
   explicit; do not count a terminal diagnostic as full Go panic parity.
3. **Adopt and remove detours.** Use the normal frontend to try the unchanged
   upstream source. Keep original programs and source locks as regressions.
   Audit the source-shape selector, bounded opt-in prototypes and ledgers:
   delete or isolate experimental admission only after its generic successor
   passes the relevant gates. Do not remove unrelated tests or change
   published history. The complete upstream package and test command, not
   CPU helper fixtures, are the final Go target.
4. **Review and broader checks.** Treat header/descriptor, barrier, root,
   ABI and performance impact as one shared-runtime review. Run focused
   native tests under existing resource guards, then the authorized Linux
   and macOS gates and affected full suites. Keep Mac measurements and
   bootstrap approval separate. Benchmark the same unchanged inputs with
   normal Python and C-API controls. Do not report performance or native
   pcc1 before those runs happen.

**Approval requested:** endorse one shared descriptor-backed allocation
contract as above, required on the collector at the first usable frontend
gate, and no promotion of the experimental v1 ABI. Review
size/tag ownership and the exact caller-result/root handoff before coding.
The next separate design decisions are the C++ destructor exception contract
and the general returned-reference ownership contract for issue #7.

## First geometry slice: checked fixed and repeated slots (not a tag admission)

The 64-bit descriptor geometry is now represented by
`pcc/runtime/include/pcc_described_layout.h` and implemented in
`freestanding_gc_described_layout.py`. Header count@0/tag@8/flags@12 stays
unchanged. Intended described-object payload: immutable descriptor pointer
@16, immutable repeated element count (backing capacity, not view length)
@24, ordinary fields @32. The descriptor is compiler-owned read-only aligned
storage with at least the object's lifetime (initially process lifetime;
unloadable module descriptors are not admitted). The five 64-bit words are
version, fixed prefix extent including header/metadata, repeated element byte
stride, fixed reference count, repeated reference count; sorted unique
64-bit reference offsets follow. Fixed offsets are object-relative and
repeated offsets element-relative. Fixed-only objects have stride/count 0.
A byte backing is prefix=32, stride=1 and zero reference slots. Reference
slots are natural 8-byte aligned within their extent and metadata is never
an edge. The exact descriptor byte count is a trusted compile-time input;
no arbitrary-pointer validation or foreign descriptor is implied. The raw
geometry helpers return checked logical extent, slot count, or slot ordinal
offset, and reject malformed, overlapping, unaligned or overflowing shapes
before any payload access. Maximum logical i64 extent does not certify that
an allocation of that size can succeed.

The second independent slice binds an exact requested allocation extent to
validated geometry in private zeroed backing. The initializer writes only
immutable repeat_count and descriptor metadata, rejects mismatches and any
second binding, and does not write tag/refcount/flags or publish visibility.
The shared raw visitor accepts only this initialized, still-live owner. It
computes the exact ordinal count from the immutable descriptor and reads only
selected offsets, in fixed-then-repeated order, with role=1; its cursor state
uses the existing `[next or -1, examined]` convention. Its bounded work is
constant metadata reads plus one offset-table read per examined slot; there
is no repeated complete descriptor validation before each slot or slice and
no inspecting scalar bits to infer pointers. The caller owns lifetime,
collector exclusion and no-park of the callback. The native private mixed
backing control mutates seven exact owned addresses across four bounded calls
and preserves four pointer-shaped scalar fields, header/metadata and
zero-capacity behavior. This has no registered object, collection, cycle or
concurrency implication.

The helpers intentionally **do not publish a tag or a managed object**. Do
not connect a frontend until every following production dispatch and handoff
is implemented and tested. The current source audit locates these owners:

| Boundary | Existing owner to extend together with tag admission |
| --- | --- |
| Header/type tag constants and native ABI | `py_abi_constants.py`, `py_runtime.h`, generated `freestanding_abi_constants.py` |
| Allocation/extent/publication | `py_obj.py:pcc_gc_alloc`, `pcc_allocator_alloc_object`, `pcc_allocator_object_publish_fresh`, `pcc_gc_free_object_memory`; descriptor logical extent must match allocation request, and allocator rounded span must never stand in for logical slot count |
| Refcount valid-tag checks | `py_obj.py:_py_incref_prepare`, `_py_incref_checked_tail`, `_py_decref_prepare`, `_py_decref_checked_tail`; `_gc_graph_leaf_tag` must not treat a reference-bearing descriptor as a leaf |
| Precise traversal and bounded cursor | `freestanding_gc_object_slots.py:pcc_gc_visit_object_slots_slice`, `pcc_gc_visit_object_slots`; incrementally scanned slots and whole referent APIs share one geometry and owned role; do not rescan the entire descriptor per slot |
| Count retirement and graph clearing | `py_obj_dealloc.py:_dealloc_should_defer`, `_dealloc_dispatch`; `freestanding_gc_backend0_collector.py:_dealloc_unreachable`; `freestanding_gc_sweep_slots.py:pcc_gc_backend0_clear_referents` / `pcc_gc_clear_container_metadata` must not erase immutable geometry before disposal |
| Collector consumers | `freestanding_gc_backend0_slots.py` trial subtraction/mark; `freestanding_gc_backend0_incremental.py:_scan` bounded full mark; `freestanding_gc_generational_promotion.py:pcc_gc_trace_referents_for_promotion_mode`; `py_obj_gc.py` referents; all must consume the same visited slots |
| Young/full membership | `freestanding_gc_tracking.py:py_gc_track` / `py_gc_untrack`, its young log and materialized tracked view; publish only initialized, rooted object; count-bearing layout must track regardless of initially empty slots |
| Owning stores/root handoff | `py_obj.py:pcc_gc_store_ptr` / `pcc_gc_store_root_take` and existing frame registry (not a new root registry); preserve null-initialized private slots, publish the new owner into a registered root before a park/collection can occur, use counted stores for subsequent managed writes |

Native gate still owed: one pointer-free byte backing and one mixed/reference
backing with no fake payload offsets; scalar bytes that look like pointers
are not followed. In both cases exercise empty capacity, correct requested
extent, root lifetime even at allocation-to-root register-only window,
root/field ownership transfer, failed allocation without publication, child
retention and release, self-/mutual cycle, young collection, full bounded
collection, explicit collect, deferred destruction, exact tracked/allocator
retirement and bounded cursor. Negative descriptors must not allocate or
publish. Add a dispatch-completeness test before enabling the tag; an ABI
unit or C-to-runtime geometry execution alone is not the gate or an existing
Python QPS non-regression measurement.

## First managed lifecycle slice (bounded object-family cells, Linux native)

The internal native tag `PY_TYPE_DESCRIBED_LAYOUT` is now admitted only by
`pcc_gc_described_alloc_into_root`. It rejects the generic `pcc_gc_alloc`
entry, malformed descriptors, nonempty/null outputs, and extents that would
select the allocator's raw-mapping path (current object size classes extend
through 16,384 bytes). A trusted caller supplies a registered empty owning
root and immutable process-lifetime descriptor; a raw allocation result or
bare pointer is never returned. It is not yet a Go/Java frontend ABI.

The actual allocator returns an exact private RESERVED cell. Under the
existing no-park owner, the constructor zeroes the exact requested extent,
validates/binds metadata, sets the count/tag/flags, then uses the allocator's
release-CAS LIVE publication. Before the no-park exit can poll, it transfers
its initial count directly to the registered root. Only after that exit
can it track a reference-bearing descriptor; the freshly published slots
are null, so no untracked reference edge can appear in this interval.
The owner is reloaded from the registered root after the park boundary.
Pointer-free backing stays untracked. A reference-bearing layout tracks
before returning, even when all slots are null; later writes use the existing
counted, container-synchronized field store. Invalid input or allocation
failure publishes no root. An impossible failed LIVE transition fails
closed (no freeing a cell of unknown lifecycle). Large/raw mapping extent
admission remains a separate protocol task, not an invented limit on Go or
Java semantics.

All four refcount tag checks, bounded shared slot dispatch, ordinary terminal
and cycle-sweep deallocation, and terminal flags now agree on this tag. Its
metadata is never a reference and remains intact through candidate clearing.
The deallocator owns its untracked nonmoving bytes across child releases;
collector scans obey the existing stop/lock protocol. Young and full walkers
consume the same visited slots. Clear only owned references, never arbitrary
scalar bytes, and retire through the ordinary allocator provenance/free owner.

Linux host PCC, 193-member current-source owned archive (all self object
writers, no host cc): test-first publication order model and real C caller
passed. The caller used actual scheduler root registration handles, exact
allocator requested extent (+105/-105), zero-capacity, invalid and generic
entry rejection, pointer-free and mixed fixed/repeated geometry, exact
reference counts, scalar bits equal to another object's address, acyclic
terminal retirement, rooted mutual/self cycles, explicit and young
collection, and bounded full mark/demotion handover. Two actual registered
carriers contended on one field while collecting; the worker also allocated
and released 100 described owners across concurrent stops. The native result
was exit 42, empty stdout/stderr, with ten repeated successes. Three existing
owned C collection controls (candidate revalidation/resurrection, zero-count
pending-owner roots, ordinary list cycles) also passed against the same
strictly admitted archive. Source tag/slot-role inventory checks were kept
strict and the retired relocation-module audit now inspects its four live
slot-role users. This does not establish cross-platform native execution,
full affected-language validation, the required Python workload A/B, or
collector-interface stability; no frontend admission until these prerequisites
and the remaining shared-layout design obligations are reviewed. In particular,
large backing, dynamic/shared descriptor lifetime, generic typed slice/reference
lowering and actual unchanged Go/Java project execution remain open.

## Large raw mapping publication owner

The next admission uses the existing allocator raw mapping path for logical
extents above its cell size classes, not a raw foreign pointer or a new heap.
A raw mapping is privately allocated and zero/initialized without holding a
no-park lease across the potentially large memset. It has no object-cell
LIVE state; the dedicated allocator-origin check returns 0 rather than
publishing a slab bit. The complete header and descriptor are written before
any exact index entry is possible. While the raw mapping is still private,
graph-lock acquisition may park safely. `pcc_gc_pointer_register_raw_into_root`
inserts the exact managed-pointer index key and transfers the initial count
into the registered empty output root in the *same* graph-lock transaction,
before unlock (and index-retirement reclamation) can park. An insertion
failure transfers no key/root and frees the private mapping. A duplicate
fresh key is an ownership invariant failure, not permission to free the
existing identity. Small-cell allocation retains its release-CAS and root
handoff path; ordinary Python allocation is unchanged.

Generic geometry overflow still rejects before allocation; a geometry which
fits signed i64 but cannot fit the allocator's mapping overhead is rejected
by that allocator without publication. Large tracked objects need no new
collector: the existing young-log validation and complete/bounded full
materialization already include exact managed-pointer index owners alongside
slab cells; terminal free removes the exact key before unmapping. A raw
owner's scalar bytes, rounded mapping span and header metadata are never
interpreted as reference slots. No new frontend or moving layout admission
is implied by this path.

Linux native evidence with the same compiler checksum and exact 193-member
runtime re-admission (two changed members rebuilt by the owned emitter):
raw scalar backing of 20,032 bytes debited and credited exactly that
requested extent and one exact index key; fixed backing of 20,000 bytes and
repeated backing of 24,048 bytes (1,000 elements, 2,001 reference slots)
visited the final three ordinals in two bounded calls. Rooted mixed raw/raw
cycles also owned a small-cell child; pointer-shaped scalar bits were never
counted, and explicit/young/bounded-full handover and terminal release
retired the exact index keys. A real second registered carrier allocated
and released 100 large owners across concurrent collection. Both small and
raw native C witnesses returned 42 with empty stdout/stderr and each passed
ten repeats; three existing owned collector controls also passed. Test-first
models exercise raw transaction insertion failure, duplicate fail-stop,
occupied output, and no no-park lease during large private initialization.
This qualifies a native Linux internal allocation/lifecycle owner, not
cross-platform execution, arbitrary allocation success, a pause benchmark,
or the still-frozen Go/Java frontend admissions.
