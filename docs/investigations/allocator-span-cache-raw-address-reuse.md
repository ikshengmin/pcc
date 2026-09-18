# Investigation: retired raw span cache hides an object at a reused address

## Status

Resolved locally on 2026-09-16: deterministic regression and scoped native
execution pass. Cold-build/performance and full qualification gates remain open.

## Problem Description

While replaying the native function-compile smoke, the sealed pcc1 intermittently
raised `Relocation.type` AttributeError or a secondary TypeError in the owned
Mach-O merge. The failing Relocation still had a valid class field named `type`
and the tagged integer zero in that field. These are additional failures beyond
the preceding thread-local field-cache repair, which is present in the binary.

The allocator span cache filled from every successful radix lookup, including
raw (kind-2) spans. `_granule_retire_slab_locked` retires those bindings before
trim unmaps their storage. Reusing that address for an object slab leaves the
old raw descriptor in the cache. Its range check succeeds and its kind check
returns -1 without consulting the new radix binding. After a positive-object
cache eviction, a LIVE object is incorrectly reported as unmanaged.

The earlier span-cache explanation in
[vthread-asyncio-throughput-gap.md](vthread-asyncio-throughput-gap.md) assumed
bindings were permanent. That assumption now holds only for object-family spans.
The C runtime's granule query is a stub; this cache belongs to the pcc-Python
allocator, so there is no corresponding C cache implementation to patch.

## Repro

Base HEAD: `5b318560c9d30a45a291d633058d439e051550c9`, plus the preceding TLS repair
and the scoped changes recorded here. Frozen source identity:
`0396b8b5bfb2ff29a16015c27ad06a9f33de840944634b69f0ca5a19046164e4`.

Evidence directory:
`/private/var/folders/39/jsw8wb5x1pl86d3tdvzx1l1w0000gn/T/pcc-compile-latency-20260916-bsk8glnv`.
It retains the source manifest, environment, commands, RSS/watchdog receipts,
profile stacks, debugger transcripts, and runtime/object receipts.

Control pcc1 SHA-256:
`f8ac79fa4f259f93d7dd9d49173533455ee5410acb8f0568a48756a01593e512`.
Control runtime SHA-256:
`f89013794c8d3a842ed6d9bc93fb20210d9dfbd6c1d1ccb775aff58eb182e1ac`.
Candidate runtime SHA-256:
`ef787f7c6e173d16e7d65b82208267e6bacb0da6631ae935b91c4de468a290c4`.

Select the candidate archive through `PCC_RUNTIME_ARCHIVE`, then run:

```bash
gtimeout 60s env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -x -n0 -vv --tb=short \
  tests/python/test_gc_granule_map.py::test_granule_cache_survives_raw_address_reuse
```

The test reserves one mapping, registers/probes a raw span, retires it through
the allocator ABI, and registers/publishes an object span at the same address.
Clearing the positive-object cache makes the span-cache lookup deterministic.
Its external C driver is an ABI oracle for the self-emitted runtime, not an
owned-C-compilation gate.

## Test [CONFIRMED]

The control archive returns `granule=-1 managed=0` and exit 9. The repaired
archive returns `granule=1 managed=1` under GC0 through GC4. See
`span-red.stdout` and `span-green.stdout`. The control pytest run preceded the
allocator edit: the fixture correctly rejects an old archive paired with the
new live runtime source. The retained `span-reuse.c` standalone driver and the
control's frozen headers/archive also reproduce the old ABI result directly.

## Proposals

Cache only object-family spans, whose bindings cannot be retired. Confirmed by
the deterministic address-reuse test. Merely clearing a raw entry during trim
would still allow a racing reader to republish the retired descriptor.

## Update: 2026-09-16

`_granule_object_start_uncached` now fills the cache only for kind-1 spans;
all structural and LIVE-state checks remain. The allocator's IR still has
zero allocas and sixteen PHIs in this function; owned default passes are active.

All 171 runtime objects were self-emitted again. The allocator IR was regenerated;
the other 170 source/IR inputs were hash-verified and their output bytes are
identical to the control. The archive is written by pcc's archive writer;
existing verification tools are not an owned cold-runtime construction gate.

The initial failed compile profile contains 22,950 on-CPU samples; 21,253
include `link_relocatable_native`. This attributes that sampled failing interval,
not the whole compile or a valid end-to-end speedup. No timeout was relaxed for
the stage1 gate.

## Report

`qualification.stdout`: **6 passed in 50.11s**. This includes the deterministic
GC0–4 address-reuse regression, the allocator program compiled separately by
pcc0 and pcc1 and executed under GC0–4, self-backend allocator ABI, threaded
allocation/reallocation churn, and raw-slab trim. The LLVM/C harness paths in
the sensitive checks are labeled reference experiments, not ownership gates.

`native-validation/receipt.json`: the new native pcc1 compiled the original
function smoke three times, in **40.427 / 39.484 / 39.516 seconds**. All 15
GC0–4 executions printed `42`; all three executables have the same SHA-256:
`61a8446d0de222bbbf62a55c29a61cb0a0e05a91ec28758257111b1a122dc1ca`.
Host compiler/Python helpers and runtime cc were denied for these replays.
These bounded replays did not reproduce the earlier link exceptions; they do
not prove the absence of every intermittent compiler defect.

The candidate compiler SHA-256 is
`086bbd927abd2d49b8b761e21e1c31ff32432cf92228e9c92b38aaa9a3c1a27b`.
It is a **diagnostic artifact** at `diagnostic-link/pcc1`: the cold stage1
attempt reached final linking but timed out at the unchanged 420-second gate.
Its `stage1/manifest.json` remains ERROR, and its 30-second smoke was not reached.
All 389 PCO inputs were retained, hashed and linked separately through the same
owned in-process linker with the sealed runtime. That link took 81.172 seconds;
`diagnostic-link/receipt.json` binds every input. This is not a successful cold
build receipt, installation, or new-source fixed point.

The successful compile phase receipts attribute **38.31–39.23 seconds** to
`link_self_direct_native_object_driver`, versus **0.654–0.662 seconds** to
frontend codegen. The ~39.5-second compile remains above the 30-second target;
future performance work should start with the owned link path. No speedup is
claimed against the initial failed/instrumented control.

The user asked for one repair round, so work pauses here. Full integrations,
the pre-existing GC3 class-method relocation failure, five-GC self-host fixed
point, ownership migration and gateway performance remain outside this repair.
No commit, push or installation was performed.
