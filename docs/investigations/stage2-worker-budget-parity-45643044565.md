# Stage2 worker-budget parity and measured native bootstrap slowdown

## Status

Active. The single-chain bootstrap coordinator now inherits Stage1's worker
ceiling. Full native Stage2/Stage3 and the wall-time completion bar remain
unverified; this is an orchestration fix, not a native codegen speedup.

## Original exact CI evidence

[Run 45643044565](https://github.com/ikshengmin/pcc/actions/runs/45643044565),
clean source `5f101c6160d8301d9d3fd5f1e82e0686e38b7f23` (source identity
`835aaf7a159d3ab70507cf9bb0cb2f85705a166265dad624a8e7faae5d036c12`).
The Stage1 compilers and receipts were published normally on both machines.
The single Stage2/3 pytest node used the original 1,800 s / 16 GiB whole-tree
guard. The owner receipts are artifacts 15040047331 (Linux) and 15040749089
(Windows); the full diagnostic artifacts are 15041691913 and 15043431134,
respectively. These include original `worker-prior-gate/stage1.json`,
`platform-qualification/shared/stage1.log`,
`platform-bootstrap/gc0/stage2.log`, and their resource streams.

| Same-machine measurement | Linux x86-64 | Windows x86-64 |
| --- | ---: | ---: |
| Stage1 compile wall, 621/621 modules (4 workers) | 1466.275 s | 2377.649 s |
| Stage1 compile tree peak | 2,589,310,976 B | 4,144,750,592 B |
| Stage2/3 whole-owner timeout | 1800.310 s | 1800.170 s |
| Whole-owner peak (sampling) | 3,700,494,336 B | 2,264,240,128 B |
| Stage2 last compile resource sample | 1784.023 s | 1763.494 s |
| Stage2 compile tree peak reached | 3,159,224,320 B | 2,110,631,936 B |
| Stage2 completed codegen modules | 2/621 (third active) | 0 (class preload active) |

Both timeouts occurred inside Stage2, not Stage3 and not an RSS-limit
termination. Stage2 did not approach linking. Windows Stage1 itself took
more than 1,800 s; the existing completion bar requires equal-source,
equal-worker, equal-cache wall times, not widening the watchdog.

Coordinator elapsed phase accounting (completed phases; overlaps are not
added together):

| Phase | Linux Stage1 | Linux Stage2 | Windows Stage1 | Windows Stage2 |
| --- | ---: | ---: | ---: | ---: |
| First closure scan | 1.687 s | 94.225 s | 4.439 s | 163.380 s |
| All closure/setup before export manifests | 10.811 s | 189.942 s | 29.181 s | 350.469 s |
| Export worker event span | 23.488 s | 305.635 s | 38.964 s | 538.111 s |
| Summary workers (coordinator phase) | 14.319 s | 214.341 s | 24.968 s | 479.101 s |
| Unique class preload index | not used | 330.465 s | not used | not completed |
| Whole export/summary/preload phase | 39.593 s | 876.817 s | 67.178 s | not completed |

Stage1 export admits 78 shards of up to eight modules, Stage2 621 singleton
exports. Stage1 export and summary run four workers; Stage2 both run two.
Native class preload additionally spawns 43 shards of up to eight roots on
Linux. Each worker reads the same 30,650,332-byte full export graph and
builds the global preload before calculating its root deltas: about 1.32 GB
of repeated serialized graph inputs, not a per-worker RSS measurement.
Windows had retired 23 preload shards when the original timeout intervened.
The actual source owners at the frozen SHA are
`pipeline_frontend_parallel.compile_parallel_uncached` (native singleton export and
oversized serial codegen), `pipeline._preload_deltas_in_workers`, and
`preload_delta_worker.run`.

## Native generation cost is separate from orchestration

On Linux, the Stage1 and Stage2 `pcc.frontends.c.codegen.c_codegen` workers
materialized the same 524,536 indexed records, 100,406 blocks, 1,040
functions and 396,454 instructions, with zero fallback records.
Its admitted worker lifetime was 96.663 s versus 395.045 s; the first
`c_parsetab` worker took 6.591 s versus 37.034 s. The Stage2 oversized
workers use indexed assembly handoff rather than Stage1's direct object
route, so these are the actual pipeline phases, not a controlled emitter
A/B. Within `c_codegen`:

| Phase | Stage1 | Stage2 |
| --- | ---: | ---: |
| Frontend generate | 23.441 s | 147.063 s |
| Backend inclusive | 64.444 s | 222.013 s |
| Function emit inclusive | 35.499 s | 140.040 s |
| Stack-map planning | 16.507 s | 37.214 s |

The independent Windows native prior-worker gate also reached its 400 s
deadline while compiling its small dependency closure. Its compile log in
the full artifact is
`worker-prior-gate/native-pcc1/tests/popen-gw0/test_size_prior_native_process0/prior-worker.exe.compile.stderr`.
The export phase was 23.386 s; codegen worker phase 364.672 s, then runtime
verification. Matched module names from Stage1 versus native prior-worker
coarse phases:

| Module | Frontend s (host → native) | Backend s | Function emit s |
| --- | ---: | ---: | ---: |
| worker_resource_plan | 3.606 → 56.378 | 12.101 → 109.948 | 7.492 → 72.520 |
| worker_process_pool | 3.172 → 57.536 | 11.580 → 116.925 | 7.138 → 79.091 |
| pipeline_frontend_workers | 2.975 → 38.777 | 9.841 → 68.135 | 6.077 → 45.704 |

The Windows control has a different closure and is not a matched Stage2
performance arm. It independently locates the expensive generated frontend
and backend work; object assembly in these three workers is only 2–3 s.
Function-level time attribution and actual short same-input native
candidate/control comparisons remain necessary.

## First correction and verification

`scripts/bootstrap_platform.run_from_shared` discarded `worker_jobs` from
the admitted, source-verified shared Stage1 receipt and always passed
`jobs=2`. `run_matrix` also applied a two-workers-per-collector split to
the sole chain. `_stage_environment` then wrote that width into both
`PCC_PY_FRONTEND_JOBS` and `PCC_SELF_BACKEND_JOBS`. This is a legacy
scheduling restriction, not the resource scheduler admitting only two
because of measured contention.

The single-chain paths now retain the positive integer Stage1 width; the
matrix calculates the Stage1 ceiling once, respecting host CPU availability,
and still bounds simultaneous chains. An admitted receipt with a missing
or invalid width is rejected before executing a native chain. The existing
per-phase admission, oversized serialization, native ownership, disabled
caches, tree limits and deadlines stay unchanged.

Test-first Linux CPython 3.15.0: the added dispatch/real-environment
contracts had 11 failing cases and one passing control on the earlier
implementation. Afterward all 12 pass. The contracts observe actual
Stage1/2/3 dispatch and compiler environment at widths 1, 3 and 4, original
1,800 s / 16 GiB settings, and malformed receipts without a hidden Stage1
rebuild. The complete related harness files passed 334/334 in 21.25 s:

```sh
PCC_TEST_NO_NATIVE_PROVISIONING=1 PCC_NO_AUTO_PCC1=1 python -m pytest -q -n0 \
  tests/python/test_platform_bootstrap_shared.py tests/test_ci_pytest_markers.py \
  tests/test_bootstrap_serial_lease.py tests/python/test_stage2_export_capture.py \
  tests/test_ci_stage1_artifact_export.py tests/python/test_pcc1_platform_admission.py
```

The packet includes real host-owned indexed object emission controls for
ELF AArch64, ELF x86-64 and PE x86-64, not new native pcc1 self-host timing.
Full bootstrap was not run locally. Next CI must supply the equal-ceiling
Stage2/3 terminal verdict. Independently measure fixed-size native export
batches with their memory contract, repeated graph decode and the
generated/backend emit hot owners. Oversized codegen concurrency remains
a separate resource-evidence candidate; do not remove that restriction
without validating peaks and bounded admission.

## Native export startup: bounded small batches

The exact admitted historical Linux Stage1 compiler from run 45643044565,
artifact 15039495663, source 5f101c6160d8301d9d3fd5f1e82e0686e38b7f23,
was materialized against its bundle manifest (compiler SHA-256
1c0456d4a0e90d10fc57b4ebaa500e6a21801612ba94a341a4b33588d5cdc69e).
It executed its actual `--pcc-python-multi-codegen-worker` export command
on eight original repository modules per experiment. This measures worker
shape, not a rebuilt current Stage2 compiler. Each of five alternating
same-input pairs compared eight singleton processes with one eight-module
process; original 90 s / 1 GiB tree guard, no cache or contract changes.
The actual coordinator's merge, function-object uses, re-export edges,
dependency rows and every lifted AST byte were equal in all 15 pairs.
The unmerged export records naturally differ in early needs-object markers;
the actual merge reapplies all uses, so a raw JSON comparison is not the
semantic boundary.

| Original source cohort | Singleton median | Batch median | Wins | Guard peak |
| --- | ---: | ---: | ---: | ---: |
| 31,790 bytes (backend/diagnostics/frontends init and definition helpers) | 2.010 s | 1.336 s | 5/5 | 220,246,016 bytes |
| 62,540 bytes (native deferred, Mach-O support, Metal, artifact inspect, backend config/cache, definition linkage) | 2.915 s | 2.685 s | 5/5 | 289,734,656 bytes |
| larger cohort (resource-plan/process-pool and pipeline helpers) | 4.885 s | 4.679 s | 5/5 | 315,215,872 bytes |

The larger cohort has little benefit, so guarded native export scheduling
batches at most eight sources and at most 64 KiB combined source bytes;
larger or unknown-size sources remain singleton. Existing oversized-first
ordering, the unbudgeted singleton path, codegen singleton policy, byte
admission and hard guards are unchanged. The resource estimator already
takes the sum/max/count of each actual assignment and separates
full-graph and module-indexed observations.

The planning witness failed before and passed after. A real host export
worker regression compares all AST bytes and merged metadata for separate
and batched cross-module function-object use. Three affected worker-policy
files passed 159/159 under the original 90 s/1 GiB guard. The changed
coordinator emitted 2.1 MiB of owned Python-library IR in 3.40 s (85.9 MB
peak), including the actual bounded-batch helper definition. A separate,
unchanged preload-delta test still fails on both preimage and candidate
because the serial index reverses two equivalent key rows
(`test_worker_deltas_build_the_serial_index_byte_for_byte`); retain that
failure to investigate in the next preload unit. No whole Stage2 timing
or current-source pcc1 qualification is claimed; hosted CI is the next
end-to-end check.

## Generated x86 backend: combine scratch retarget and copy forwarding

On the frozen native prior-worker logs above, function emission is 20.707 s
in Stage1 versus 197.315 s in native pcc1 across the three matched Windows
module names (9.53×); setup is 4.497 s and backend finalization negligible
in the native phase. This is separate from the coordinator/preload work.
Host phase sampling of two real x86 Linux indexed compiler modules put
textual post-selection work at 62–64% of function emit, not the assembler.
An exact historical native worker from 5f101c61 emitted the ELF linker
module in 18.836 s versus 2.060 s for the host emitter, with byte-identical
PCO. An isolated original native helper measured scratch retarget
0.365 s and copy forwarding 2.140 s on the same frozen line corpus
(host 0.056/0.386 s). These are local Linux attribution probes, not a
completed Stage2 profile or a Windows performance claim.

The candidate combines the initial scans using the mnemonic and operands
already parsed by forwarding. Scratch-death proof still reads the original
input rather than forwarded text. Represent the removed adjacent copy as a
zero-effect placeholder in its existing block; liveness,
flags, retirement markers and unknown-branch fallback are unchanged.
No optimization, stack map or pointer-root safety check is omitted.

**Frozen input and native method.** Base source 7adf9330; the original pass
file also matches historical 5f101c61 (SHA-256 aef398a9aa976877a157066b80344a4b7b55a732631df239001741dc6770ed48).
The local strict-closure replay's actual indexed modules (distinct from
the Windows timing receipt) are:

| PIDX input | SHA-256 |
| --- | --- |
| pcc.backend.owned_elf_link | fdd47525019c407579cf33564aeaeca8b4a21886a548de40f232d66ff909e07f |
| pcc.frontends.python.pipeline_freestanding | a020852f6846039b721a080361eae7e962a9c07527bac65e792339c50242d5ff |

On the base, wrap the backend's imported retarget input and forwarding
output, then call `emit_indexed_module_file(input, output, "PCO",
optimize=True)` for each sidecar in that order. Join each function's
lines with newline and the functions with U+001E. The 66-function,
148,951-line input has SHA-256
35e9b475838dd0738b49449be2f90584985fdbfa5862eccca0921d18a9e3b93c;
the 4,681,162-byte expected output hash is
df13a29779563409e19b18f70c1ff3b9e2d7a63d30e51bf7159c11c5778b986c.

Seal two three-module helpers with the historical host compiler and same
source-verified native runtime from artifact 15039495663's source
5f101c61 / corresponding run's worker-prior-gate runtime (runtime archive
SHA-256 c03644393b9448123645dc5b17e7015b59ed2ef8834f29fc536bd6576ff16edc).
The baseline pass module adds only a wrapper returning
`forward_register_copies(retarget_scratch_definitions(lines))`; the
candidate pass module is this change. Each helper includes the exact
unchanged original pass as a separately named module, `copy_control`.
The per-function helper reads the frozen corpus, splits at U+001E
and newline, times the arm and the separate control's unchanged two
passes using `time.perf_counter_ns()`, checks both results are equal,
writes all results with the original separators, and prints arm, control
nanoseconds and count. Both are compiled with
`compile_python_multi([helper, arm_source, control_source], binary,
entry_module="bench", module_names=["bench",
"pcc.backend.x86_64_register_copies", "copy_control"], backend="self",
target_triple="x86_64-unknown-linux-gnu", runtime_archive=archive)`,
`PCC_PYTHON_IR_PASSES=off PCC_DIRECT_INDEXED_KERNEL_CAPTURE=0
PCC_DIRECT_INDEXED_KERNEL_EMIT=0 PCC_NO_AUTO_PCC1=1`.
Use the same original 90 s / 1 GiB guarded build and run with
`scripts/run_process_tree_sample.py` and `--performance-lock`. Run each
actual ELF helper with `PATH=/nonexistent PCC_HOST_PYTHON=/usr/bin/false
PCC_HOST_PCC=/usr/bin/false PCC_RUNTIME_CC=/usr/bin/false`, passing the
same corpus and output path. Each of five pairs alternates arm order.

| Measure | Sequential median | Shared-scan median | Candidate wins |
| --- | ---: | ---: | ---: |
| Owner | 2.697 s | 2.238 s | 5/5 |
| Unchanged original control | 2.599 s | 2.442 s | 4/5 |
| Whole helper (both + I/O) | 6.138 s | 5.403 s | 5/5 |
| Peak process-tree RSS | 54.8 MB | 53.3 MB | 3/5 |

Raw owner gain 17.0% and pairwise normalized candidate/control gains
7.57%, 10.85%, 18.56%, 5.22%, 9.86% (median 9.86%); the control
reveals host drift but each corrected pair still wins. All ten helper
outputs have the exact expected hash. Both real PCOs emitted by the
current host backend on each side of the change also match byte for byte,
including stack-map payloads (SHA-256
422119dcd8615a1d5eca3ebd9c5880224b2dad607bbcd72fdfadd0f1e3809fab
and 0c41d724cfe38fd62b36fd004a3e418b8d2a5f7fe2a29b725377dd074f58a457).

**Test boundary.** Test-first shared scan entry failed before implementation.
Fourteen new tests (13 explicit fixtures and one 250-case deterministic
mixed-block differential) all pass. The pointer-retirement test now
executes the production shared entry. Both source arms ran the same
ordinary x86 batch under unchanged guard:
`PCC_TEST_NO_NATIVE_PROVISIONING=1 PCC_NO_AUTO_PCC1=1 python -m pytest -q
-n0 -p no:cacheprovider $(find tests/python -maxdepth 1 -name 'test_x86*.py'
! -name '*_native.py' | sort)`.
Base 1392 pass / 12 fail; candidate 1406 pass / the exact same 12 fail
(15 subtests pass, 4 deselected in each). JUnit shows no verdict change for
all 1404 old nodes. The 12 pre-existing failures are still open:
assembler-lifetime symbol metadata/equality (2), indexed-bridge
selector/wire diagnostics (3), register-homes constant GEP (1; old
and new assembly identical), Windows streamed-module text parity (4),
and Windows inline-error edge emission (2). No failure is suppressed or
reclassified as a pass. Full bootstrap, native Windows and Mac execution
are not claimed; the result is a measured local native compiler-owner
optimization, not an end-to-end Stage2 speedup or fixed point.
