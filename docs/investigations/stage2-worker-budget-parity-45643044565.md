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
