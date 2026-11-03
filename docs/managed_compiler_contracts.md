# Shared managed compiler contracts

## Implemented scope

`pcc/support/managed_objects.py` owns immutable compiler plans. It does not
register runtime descriptors, allocate objects, choose an object header, or
make managed native lowering available. Java and Go keep distinct language
identities and semantics. Constructing a plan never establishes runtime support.

- `ManagedTypeIdentity(language, origin, key, declaration=None)` preserves
  source identity. The optional hashable session declaration token preserves
  Go nominal identity, including two different declarations with equal names.
  It is not serialized or converted to a process address. Java's current
  single-project source namespace and unresolved bootstrap namespace remain
  distinct; this is not yet a loader-aware JVM runtime type identity.
- `ScalarFieldPlan` and `ReferenceEdgePlan` describe payload-relative facts.
  Immutable type/itable metadata is scalar metadata, not a traced object edge.
  Reference roles remain `owned`, `borrowed`, and `update_only`, matching the
  existing collector distinction. They are not interchangeable in no-RC mode.
- `ManagedLayoutPlan` retains unknown offsets/extents as `None`. `pointer_map`
  requires exact target pointer width and known geometry, validates alignment,
  overlap and extent, and returns separate role maps. No bitmap or object
  descriptor is emitted. Repeated fixed elements are allowed; nested
  variable-size elements need another contract and are rejected.
- `allocation_extent` only checks count/stride arithmetic. It does not include
  an invented header, allocate, initialize, publish or root anything.
- `ManagedValuePlan` carries storage and unmet language obligations. Every
  managed/unresolved plan still needs descriptor binding, publication, roots,
  barriers, safepoint reload, registration and object ABI. Even a fully known
  payload map does not discharge these requirements.
- `ManagedSpanPlan` distinguishes immutable static bytes from a managed
  base-owner/offset/length relationship. It is not a raw derived pointer or a
  runtime rooting certificate. A managed owner keeps its unresolved lifetime
  requirements; a static span cannot claim a managed owner.

Java class models and checked reference expressions consume the common plans.
Existing primitive-only payload offsets remain available. Mixed reference
fields, array object headers, bootstrap Object/String definitions, String
materialization/interning, roots and native managed representation are still
unresolved. UTF-16 content, including isolated surrogates, remains in the
existing String semantic owner and is not mistaken for an object identity.
Constructor bindings, source evaluation order and prior unsupported cases stay
with their existing owners.

Go static-string lowering consumes `static_string_plan` to size the immutable
byte backing and length field. Its existing private pointer/length ABI is
unchanged. Other adapters describe prospective scalar boxes, pointer provenance,
String ownership and error-interface metadata/value distinctions without
lowering them. Aggregate trace/copy plans are explicitly unresolved. The
existing nil-only error word remains a null transport, not a full interface.
Method sets, itables, typed-nil behavior, interface conversion/equality and
boxing copy semantics remain language-owned obligations.

## Existing runtime boundaries, not new implementations

At the audited source revision, `pcc/runtime/include/pcc_shared_managed.h` is an
opt-in, default-off Linux x86_64 GC0 bridge. Its v1 descriptor separates scalar
bytes and reference counts; it is not a general mixed-field bitmap/type table.
`py_shared_managed.py` implements the private tuple-shaped envelope. Its stable
handle contains object plus registration; the separate root-registry node holds
a slot address and links. A registration token is not object ownership or type
identity. These compiler plans do not widen that bridge's guard.

Existing Python object headers, tag-dispatched walkers, pointer load/store
barriers, rooted slot handoffs and allocation publication remain their current
owners. Adding a Java/Go tag to a Python-class tag range would be unsafe because
its walker assumes a Python class pointer and Python instance layout.

All five collector modes need descriptor/edge/root integration before managed
native admission: GC0 RC/cycle, GC1 incremental, GC2 concurrent, GC3 generational,
and GC4 colored relocating. Count-free mode is currently admitted only for
GC1/2/4; GC0/3 do not become supported by these plans. No-RC changes root discovery
and release accounting, not pointer-map correctness or object identity.

The abstract stackmap format contains derived/base information, but the current
runtime mapped-root validator admits only its narrower stack-indirect pointer
locations and rejects derived/base entries. Dynamic strings therefore need a
rooted base and offsets, with addresses rematerialized after safepoints; they
cannot assume interior-root rewriting. GC3 and GC4 relocation/promotion walkers
also need explicit support for new payload shapes.

Existing foreign leases require an already traced owner and do not themselves
create roots. Foreign callbacks, nested transitions, carrier/logical-thread
ownership and thread-start argument/result roots require explicit contracts.
Neither raw bridge pointers nor cached data addresses certify these boundaries.

## Next implementation slice and acceptance

The next shared-runtime batch must bind one minimal descriptor and allocation
kind to the existing collector, root, barrier and safepoint owners. It must first
specify header/payload coordinates, immortal type identity, exact reference
maps, initialized-object publication, owner transfer, per-mode relocation and
no-RC behavior. Java object identity/String semantics and Go interface copy,
typed-nil and equality cannot be replaced by a common language-neutral value ABI.
Runtime registration, target ABI and layout remain deliberately unresolved here.

The current change is compiler-only. Shared validation, language-adapter tests,
a supported Go static-string native program and its exact-source official-Go
comparison are authored. Java's exact original object/array/String program has
an independent JDK-only observation; PCC managed execution remains rejected.
All new execution is UNRUN and delegated to CI. No local compiler, test runner,
Mac, production GC/backend or Python RC/escape path was used or changed.
