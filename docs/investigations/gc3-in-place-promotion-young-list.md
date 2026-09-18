# Investigation: GC3 in-place promotion leaves an old owner on the young list

## Status

Resolved locally on 2026-09-16: the C mirror now matches the existing
pcc-Python path. A separate native C object-execution failure remains open
under [#171](https://github.com/allstoalls/pcc/issues/171#issuecomment-5693390381).

## Problem Description

`test_class_lookup_reloads_relocated_method_and_class` returned 10 under the
C runtime / GC3: a young method behind an old class did not move after a minor
collection. The identical C ABI probe passed against pcc-Python runtime.

The C `pcc_gc_promote_young_object` path which promotes a non-movable minor-arena
object in place cleared YOUNG and set OLD without unlinking its object node
from the young list. `young_next`/`young_prev` are reused by the old-owner
promotion worklist, whose enqueue guard therefore rejected the owner. Later
remembered class metadata was not scanned. The pcc-Python counterpart in
`freestanding_gc_generational_promotion.py` already unlinks before setting OLD.

## Repro

Base HEAD: `5b318560c9d30a45a291d633058d439e051550c9` plus the preceding worktree
repairs. Evidence: `/private/tmp/pcc-gc3-round4-knjyia_r`.
Control source manifest:
`0e2b9312c468b3fd21c4bab8e4ba7d627fa2750c074cdf96384b5e80c9ee9870`.
Repaired source manifest:
`4c4c259c079b0cedd975ef4d65ae60bd40cd634005c53e2880bf7bda4ccf1852`.
C archive before: `6c002f1529bb201791f4eaff660ee511fb5f885d45589a9db30e78366cf52844`.
C archive after: `8418a2114d63c5fc3cd99d6f5af800f94b4d9307532566602549ed5c9cd0043a`.
Unchanged pcc-Python archive:
`ef787f7c6e173d16e7d65b82208267e6bacb0da6631ae935b91c4de468a290c4`.

```bash
gtimeout 90s env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -x -n0 -vv --tb=short \
  tests/python/test_class_lookup_cache_runtime.py::test_class_lookup_reloads_relocated_method_and_class
```

The actual commands used the immutable pcc-Python archive explicitly and the
existing source-addressed C runtime fixture. Watchdog/environment/log receipts
are `red-watch.json`, `green-watch.json`, `qualification-watch.json` and
`final-watch.json`. External C compilation/linking drives these runtime ABI
checks; it is a labeled oracle, not proof of C compiler ownership.

## Test [CONFIRMED]

`red.stdout`: C GC3 returns 10. `original-probes.json` demonstrates that the
second refill helper advances the minor collection count from 1 to 2 in BOTH
runtimes. The hypothesis that no collection occurred is therefore denied.
The method stays YOUNG in C; pcc-Python forwards it.

`links-probes.json`: the old C class has flags 0x1349 (including OLD and
MINOR_ARENA) but retains a non-null young-list predecessor. The pcc-Python
class has the same flags and cleared links. `fixed-probe.json` shows the
repaired C class with cleared links and a forwarded method, under the same
allocation pressure and without increasing a timeout or collection budget.

## Proposals

Unlink the in-place survivor before changing its generation flags and enqueueing
its referents. This is the missing step already present in the Python mirror.
Do not remove the move assertion, force extra collections until it happens to
pass, or weaken the shared slot/root contract.

## Update: 2026-09-16

Added the missing `pcc_gc_backend3_young_unlink` call to the C minor-arena
in-place branch. The other C copy/non-minor promotion paths already unlink;
no algorithm, budget, layout or pcc-Python runtime change was needed.

The regression now checks that allocation pressure actually increases the
minor-collection counter and that the class is OLD, not YOUNG, and still in
the minor arena. This binds the test to the changed in-place path rather than
a nearby copying-promotion path.

## Report

`final.stdout`: **14 passed in 3.61 seconds** after the final test edit.
Coverage includes the complete class-lookup file (its relocation case executes
C/pcc-Python under GC3/GC4), class metadata healing, frame-root rewriting and
tuple-cycle in-place promotion in both mirrors. The class-lookup shadowing
case also executes all five collectors. Runtime archives were rebuilt in the
existing isolated test cache; the repository's shared archives were untouched.

An extra header-free C integration probe was emitted by the existing native
pcc1 with host compiler/Python delegation disabled. Emission succeeded, but
its executable timed out at 10 seconds with old/fixed C runtimes and with the
pcc-Python runtime. Changing to the system linker did not resolve it. System
cc compiling the same source against the fixed C runtime prints
`gc3 method healed` and exits 0. These controls isolate an additional C object
execution boundary, not a failed GC3 mirror repair; its exact compiler-side
cause and host-pcc behavior have not been determined. See `link-controls.json`,
`native-object-watch.json`, `native_gc3.c`/`.o` and the linked issue comment.
Do not count this additional native gate as passed.

Work stops after this scoped repair as requested. The native C probe, full
integration qualification, five-GC self-host fixed point, 30-second compiler
performance target, complete ownership closure and gateway benchmark goals
remain open. No commit, push, compiler rebuild or installation was performed.
