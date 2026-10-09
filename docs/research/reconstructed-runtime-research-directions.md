# Reconstructed runtime research directions

Date: 2026-10-09

Status: **Reconstruction from prior discussion, not validation.**

This note preserves four technical directions and the safety contracts needed
to resume them. The original research artifacts, candidate patches, and test
evidence were unavailable during reconstruction. This document does not restore
their exact contents or establish the status of the current implementation.
Unverifiable historical pass counts and performance figures are omitted.
The gates below are proposed future work, not completed checks.

## 1. Internal lifetime verification inspired by Mojo

Use function-local dataflow and explicit call contracts inside PCC without
changing ordinary Python syntax or observable semantics. Start with a shadow
verifier that diagnoses ownership errors without changing generated code.

Safety contracts:

- Track owners, borrows, roots, leases, and moves across normal and exceptional
  control flow. Detect missing and duplicate cleanup, including at joins.
- Prove object liveness, address validity, and container-slot validity
  separately. An owned strong reference does not prove uniqueness or exclusive
  mutation rights.
- Before removing retain/root operations, describe whether a call captures a
  reference, returns a borrow, re-enters user code, changes an address, or
  suspends. Include closures and values that cross `await` or `yield`.
- Preserve aliases, late-bound closure cells, weak references, finalizers, and
  resurrection. Insufficient proof keeps the existing managed path; it must
  not reject otherwise valid Python.

Next gates: recover or explicitly reimplement the verifier in isolation; review
its state transitions; test positive cases and deliberately invalid generated
IR for normal, exceptional, and suspension paths. Establish native semantic
coverage across all five GC backends before enabling any resulting optimization.

## 2. Scope-owned buffers inspired by Confined Pools

Prioritize a waitset-owned event buffer that can be reused across waits.
Short-lived string scratch storage and temporary big-integer output slots are
separate later candidates, each requiring its own escape analysis.

Safety contracts:

- Reuse storage only after the wait or cancellation has completed and the
  backend no longer retains a pointer into it.
- Define ownership through unlock, re-entry, allocation failure, cancellation,
  and destruction. No concurrent borrower may observe recycled storage.
- Do not assume that virtual-thread pinning proves stable carrier affinity.
  Keep the proposed ownership tied to the waitset or call scope.
- Results that outlive a scratch scope need independent storage. A temporary
  output slot is stack-eligible only when the callee does not retain its address.

Next gates: review buffer lifetime and backend pointer retention; exercise
re-entry, cancellation, exceptions, concurrent waits, and thread migration in
native tests. Measure allocation counts, end-to-end throughput, tail latency,
and peak memory on frozen inputs. A simulation or external allocator
microbenchmark cannot establish PCC runtime gains.

## 3. Gateway cleanup and suspension paths

Investigate repeated exit-path root cleanup and function-construction error
handling as sources of excessive generated IR. Consider shared or bulk cleanup
only where it preserves the original ownership operations and their order.

Safety contracts:

- Preserve root coverage and the required cleanup order on normal, exceptional,
  cancellation, and generator-close exits.
- Keep values needed after suspension alive in persistent state. Do not treat
  suspension as final function exit or release a value needed on resumption.
- Preserve exception propagation and observable finalizer behavior; smaller IR
  alone cannot demonstrate equivalence.

Next gates: review the combined cleanup/suspension changes against one exact
baseline; check generated control flow and ownership balance; execute native
normal, error, cancellation, and resume paths across the five GC backends.
Then compile, link, and run the representative gateway application. Report IR
size, compile time, and runtime behavior separately, including any timeout.

## 4. Warm cache checks and runtime build usability

Investigate unnecessary directory scanning before program-cache lookup.
The proposed direction is to validate actual dependencies and import-resolution
context together with compiler and runtime identities, rather than recursively
scanning unrelated files beside the entry script.

Safety contracts:

- Reject cached results when changes to dependencies, import resolution,
  compiler options, or the matching runtime invalidate them. Avoiding work must
  not weaken invalidation.
- Keep cold-build and cache-hit paths distinguishable. Attribute startup,
  validation, compilation, linking, and execution costs separately.
- Provide concise runtime-build progress and an explicit preparation entry;
  reserve internal pass timing for requested diagnostics.
- Distinguish source-development instructions from installed-package usage.
  Discover the matching runtime archive automatically in the normal workflow
  and report missing or incompatible artifacts clearly.

Next gates: restore or reimplement the candidate with dependency and
import-resolution invalidation tests; verify cold builds and repeated cache
hits using the actual supported entry points and packaging layouts. Measure
the full path on each target platform instead of transferring timing claims
between machines.

## Evidence required to resume

Freeze the baseline, candidate, inputs, effective options, compiler, runtime,
and cache identities. Keep host-model tests, IR/object checks, native execution,
and performance measurements distinct. Preserve raw outcomes and explicitly
record failures, skipped gates, and timeouts. No production-readiness or
performance claim follows from this reconstruction alone.
