# Investigation: native stack-map structural scans decode unused record fields

## Status

Resolved locally on 2026-09-16. The optimization is retained after correctness
and bounded native performance checks. The 30-second compile gate remains open.

## Problem Description

A successful native compile of the function smoke takes roughly 39.5 seconds,
with about 38.3 seconds in the owned link path. A fresh successful sample has
35,412 on-CPU samples: relocatable merge is inclusive in 64.88%, NativeObject
construction in 21.56%, and precise_stackmap._take in 13.29% (overlapping shares).
The resulting executable contains 4,095 stack-map functions and 83,927 safepoint
records, in a 2,818,760-byte payload. Small source size does not imply a small
runtime metadata validation workload.

`function_address_offsets` and `_scan_stack_map_payload` unpacked all ten
fields of each fixed record, including uint64 IDs which become heap integers
in native pcc, although these structural passes consume only the uint16
location count. The full semantic publication validator is a separate owner.

## Repro

Base HEAD: `5b318560c9d30a45a291d633058d439e051550c9`, with the preceding TLS and
allocator-cache repairs. Evidence root: `/private/tmp/pcc-link-round3-doulct8v`.

Control source identity:
`0396b8b5bfb2ff29a16015c27ad06a9f33de840944634b69f0ca5a19046164e4`.
Candidate source identity:
`0e2b9312c468b3fd21c4bab8e4ba7d627fa2750c074cdf96384b5e80c9ee9870`.
Only `pcc/backend/precise_stackmap.py` differs in these compiler manifests.
Control pcc1 SHA-256:
`086bbd927abd2d49b8b761e21e1c31ff32432cf92228e9c92b38aaa9a3c1a27b`.
Both compilers link the same sealed runtime SHA-256:
`ef787f7c6e173d16e7d65b82208267e6bacb0da6631ae935b91c4de468a290c4`.

`profile/receipt.json`, `profile/environment.json`, `profile/cpu.folded` and
`profile/attribution.json` bind the successful control profile and emitted
execution. `baseline-hot-ir.json` records real post-pass IR: the two scanners
already have 192/193 PHIs; remaining allocas include address-observable GC root
slots. This is redundant decoding work, not a missing optimization-pass claim.

## Test [CONFIRMED]

- `scan-focused.stdout`: three boundary tests passed: both architectures,
  257-location counts, bytes/bytearray/memoryview/bytes-subclass behavior,
  truncation at every byte of the record stream, location limits, and reserved
  fields still rejected by the final validators.
- `native-scan.stdout`: two pcc0/pcc1 compile/run cases passed in 119.83 seconds.
  They compile the actual production module, copied unchanged into an isolated
  package context, and execute the changed count/bounds/error paths under GC0–4.
- `adjacent.stdout`: 107 stack-map, native-object and Mach-O link tests passed.

## Proposals

Use a bounded record walker for the two structural scans. Exact bytes and
bytearray inputs read only the two count bytes; other buffer types/subclasses
retain struct decoding so overridden Python indexing cannot change buffer
semantics. Preserve per-record bounds, aggregate count limits and the complete
final semantic validator. No validation is skipped.

## Update: 2026-09-16

`_skip_safepoint_records` implements the shared walker. The candidate compiler
was built in 375.23 seconds, within the unchanged 420-second construction
limit. Its 30-second native function smoke still timed out, so
`stage1/manifest.json` remains ERROR and the compiler is an uninstalled
experimental artifact. No new-source fixed point or full release qualification
is claimed.

The normal compile A/B entry requires successful build receipts; this control
has only a diagnostic link receipt from the preceding round. The bounded
comparison therefore reuses its existing process-group and time-parser helpers,
records source/compiler/runtime/environment identities, uses balanced warmups
and three alternating pairs, and validates every image under GC0–4. It is a
local diagnostic comparison, not a formal A/B ACCEPT receipt. The source input
is fixed, object/IR caches are off, direct PCO/default owned passes are enabled,
and host Python/compiler/runtime-cc helpers are denied. Every run must produce
the same executable bytes. The enclosing sampler owns the performance lock,
8 GiB tree-RSS guard and durable logs.

## Related test review

The field-cache pthread regression now selects the threaded C/pcc-Python
archives, alternates both shared heap strings in both threads and checks final
reference counts. Original values 11 and 22 were tagged integers, which bypass
reference counting; the original two threads also read different values, so the
specific claimed shared-int refcount race did not establish an old failure.
The stronger test now actually exercises the reference-count contract. The
single-thread borrowed-name regression retains default runtime archives.

## Report

`threaded-final.stdout`: **5 passed, 3 deselected in 36.07 seconds** after
explicitly selecting GC0 for the refcount test. The deselected pre-existing
class-relocation checks are not claimed green.

Candidate pcc1 SHA-256:
`9cc6ebc04367f40a7c6cb9cfed13abd1ec6a95280fa386a1c9745f392dabe745`.
`comparison/receipt.json` contains both warmups and all measured commands,
environments, counters, output hashes and native execution results:

| Pair | Control compile | Candidate compile | Wall reduction | Instruction reduction |
|---|---:|---:|---:|---:|
| 1 | 39.43 s | 36.47 s | 7.51% | 9.70% |
| 2 | 39.26 s | 35.59 s | 9.35% | 9.72% |
| 3 | 39.72 s | 36.21 s | 8.84% | 9.69% |

Paired median reductions: wall **8.84%**, user+sys **9.13%**, instructions
**9.70%**. Maximum process RSS is effectively unchanged (about 946 MB).
`/usr/bin/time -lp` hardware counters describe the native coordinator; the
process-tree watchdog additionally caps and samples its workers. No gateway
throughput or whole-bootstrap speedup is inferred from these numbers.

All eight images (including balanced warmups) are byte-identical, SHA-256
`97e9a6b1aba38db32da41b6c2ee28e5d47fb82402760edfff6d3099377241b9c`.
Every image executed under GC0–4 and printed 42: 30 measured executions plus
10 warmup executions. Original/native buffer boundary tests also execute the
actual modified scanners; image equality alone is not their semantic proof.

The actual compile remains **35.59–36.47 seconds**, above 30 seconds. The
candidate's cold compiler construction completed, but its build receipt remains
ERROR at the native smoke deadline. Full pytest/integration qualification,
five-GC self-host fixed point, tool-ownership migration and gateway benchmarks
are still unfinished. No commit, push or installation occurred. The user asked
for one additional round; work pauses after this repair and measurement.
