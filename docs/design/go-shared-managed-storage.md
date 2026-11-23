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
