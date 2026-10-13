# PCC current status

Updated 2026-10-14. Source: `ikshengmin/pcc` master (`shenming` remote).
Durable receipts for this period (patches, profiles, A/B scripts, repros):
`/Users/jiamo/pcc-recovery-archive/2026-10-13/README.md`. The 2026-10-07 status
(source preservation, the remote agent's backups) is in git history at
`38357808:docs/status.md`. All ten goals below remain open.

## Recent source changes

- `0f6fa1d9` function-local from-import aliases follow control flow.
- `bbe27219` merged Mach-O `__text` splits at input boundaries (32 MiB chunks)
  so AArch64 thunk islands interleave.
- `510ed001` compile-time-only from-imports (typing, abc, ...) no longer get a
  runtime module global; `TypeAliasType(...)` metadata no longer raises NameError.
- `e62cef29` raw i64 module globals updated through `global n; n += ...` store
  the unboxed value instead of rooting a boxed pointer in the i64 cell.
- `97f3d52f` `self.__class__(kw=..., **mapping)` keeps the parser's operand
  order, so the constructor-unpack path compiles it.
- `3a2530be` the freestanding-directive scan returns early without a marker and
  indexes a character list instead of the source str.
- `28ce3e92` restores the 6 s worker-prior fixture deadline (see decision 2).
- `1c980956` the ELF link reuses a relocatable `.pcc_stackmaps` table's
  validation verdict for its relocated copy (finalize 44.8 s -> 0.9 s on CI).
- `fbe49ada` codegen treats every raw-pointer unsafe intrinsic result as raw;
  fixes the Windows `os.path.abspath` 0xC0000005 (verified on CI Windows).
- `f35eb06f` pcc's json appends/slices whole strings (native loads 2.3x,
  dumps 2.2x faster) and joins escaped surrogate pairs.
- `a21fa121` pcc's json decoder reads NaN/Infinity/-Infinity (Stage2 blocker).
- `3aa13b2a` vthread effect analysis walks each callable once per module pass.
- `68493fcf` str iteration with a byte cursor in the sequence iterator, typed
  `for` loops and typed comprehensions (all three were quadratic on non-ASCII
  strings; the comprehension also left its target without a registered root,
  so `[c.upper() for c in s]` did not compile).
- `de777b21` value-position `enumerate(...)` publishes into the caller's output
  root: `list(enumerate(xs))` compiles again (broken since `97b6bced`,
  2026-10-03) and an owned temporary iterable is released (it leaked every item).
- `263d12af` CI limits (next section): Stage1 watchdog 6000 s, push gate is
  Stage1 plus the five-collector native canary, full chain by manual dispatch,
  strict closure 3600 s (not run on macOS), fixture peer wait 15 s.
  `85825b40` counts its 26 new gate tests in HOST_COUNTS (all three
  worker-prior jobs of run 38436916173 failed on the stale count).
- `52ca3f80` native `os.replace`/`os.unlink`/`os.rmdir` raise CPython's errno
  subclass with errno, strerror, filename(s) (was a bare OSError, so
  `except FileNotFoundError` missed a missing file); the worker RSS publication
  retries an errno-only EACCES on Windows (pcc has no `winerror` yet). Cause of
  the Windows native prior-worker failure in run 38437580834.
- `98ee6010` comprehension targets at module scope and in native lambdas are
  registered roots (`[w.upper() for w in words]` at module scope did not
  compile; the targets were not GC roots there).

## CI (follow each pushed commit)

CI limits changed in `263d12af` (maintainer request: "至少能成功"). The push
gate on Linux/Windows is now `bootstrap_platform.py --gc all --stage 1`: Stage1
and the native canary under all five collectors. The Stage2/Stage3 fixed point
is unchanged as a definition and runs by manual `workflow_dispatch` with
`full_chain: true`; it cannot finish in a 6 h hosted job today (Stage2 section).
The workflow still has no `continue-on-error`. Stage1 watchdog 6000 s on all
platforms (projections below), macOS job 180 min, its 45-minute target
recorded as `darwin_target` instead of asserted. Worker-prior strict closure
3600 s (Linux projections ~2400-2500 s from its progress receipts), not run on
macOS (needs ~8.6 GB, runner admits ~3.1 GB). Fixture peer wait 15 s
(decision 2, applied).

Stage1 projections (`stage1_projection.py` in the archive: per-module times
scaled against the complete `1c980956` runs): Linux aarch64 2338-2362 s
measured; Linux x86_64 ~2550-2650 s on fast runners (codegen ends ~2310 s, the
ELF link was cut during relocation) and ~4800-4900 s on slow ones (1.82-1.90x
per module); Windows x86_64 ~3850-4500 s (1.45-1.69x plus the COFF link);
macOS arm64 ~4500-5000 s (per task 0.77x of Linux aarch64, but 1.0-1.7
admitted workers on 3 cores; three workers would make it ~2300 s).

- Run 38423410429 (`a21fa121`), Linux aarch64: Stage1 2361 s. GC0 Stage2
  (2 workers per chain): export 459 tasks 1426 task-s, done at 717 s; summary
  now passes (56 tasks, 385 task-s, done at 973 s); preload-delta done at
  1220 s; codegen: the exclusive calibration of `unsafe_lowering` took 653 s
  (Stage1: 80.5 s), then `self_backend_parse` ran 109 functions in 157 s of
  frontend time (Stage1: 11.2 s for the same functions, 14x) until the 2400 s
  stage deadline. GC1/GC2 were cancelled after the GC0 failure.

- Linux aarch64 Stage1 now fits: 2338.1 s at `1c980956` (run 38411293966,
  link 243.7 s), 2362 s at `fbe49ada`. At `3a2530be` it had missed by ~5 s.
- Stage2 (GC0, aarch64, run 38411293966): the export phase completed for all
  459 tasks by 808 s (1608 task-s, ~2 workers); before `3a2530be` it reached 273
  modules in 2400 s. The summary phase then failed reading the AST wire of
  `layer1_support` (`1e309` -> `Infinity`; fixed in `a21fa121`). Slowest exports:
  `type_infer` 197 s and `unsafe_lowering` 98.7 s (quadratic iteration of
  non-ASCII source in the directive scan, pending fix), `c_parsetab` 55 s
  (not yet profiled: a local sample with the `0f6fa1d9` pcc1-profiling binary
  only showed the scan `3a2530be` already removed; a current pcc1 is needed);
  the median module took a few seconds.
- Linux x86_64 Stage1 is runner-dependent: at `1c980956` all 460 modules were
  compiled by 2383 s and only the link missed the cap; at `0f6fa1d9` only 72
  were compiled. On those 72 modules every phase, including untouched CPython
  frontend work, was 1.7-2.0x faster in the later run (runner difference; the
  CPU model is not recorded in the receipts).
- Windows x86_64 Stage1: 83/460 modules in 2400 s. macOS Stage1: ~70/460
  modules (49% of the work) in 2400 s, limited by memory admission (below).
- Run 38423410429 (`a21fa121`): Stage1 completed modules at the 2400 s cap:
  Linux x86_64 73 (slow runner again), Windows 91, macOS 80 (average 1.33
  workers). Linux aarch64 above.
- The 2400 s Stage1 cap is ours, not GitHub's: `scripts/ci_worker_size_prior_gate.py`
  passes `--timeout 2400` to `bootstrap_platform.py` (Linux/Windows) and sets
  `PCC_BOOTSTRAP_STAGE_TIMEOUT=2400` on macOS (also in
  `.github/workflows/pcc1-package-parity.yml`). GitHub's own limit is 6 h per
  job on hosted runners; the native wheel job already uses `timeout-minutes: 360`
  for Stage1 plus the five-GC Stage2/Stage3 chains.
- Worker-prior gates: strict closure times out (aarch64, x86_64) or hits the
  memory cap (macOS); Windows now passes default-xdist (2023), windows-exit and
  the native width cases; the GC4 growth case fails intermittently on Windows
  and Linux x86_64 with "missing worker marker" (decision 2). Same picture at
  `a21fa121`: strict closure 1800 s timeouts at 9.2 GB peak (Linux), macOS
  `MEMORY_LIMIT` (3.3 GB cap) at 707 s, Windows GC4 growth child gave up after
  exactly the 6 s deadline.

## Stage1 within 2400 s (goal 1 / platform qualification)

- aarch64: codegen workers keep all four slots busy until ~2100 s (90 idle
  slot-seconds per run), then the coordinator-only link runs ~244 s: stage inputs
  ~91 s (object parse/validation in the SHA-pinned `elf_x86_64.py`, stack-map
  validation ~1/3), resolve/relocate ~130 s for 39.6 M relocations, classify
  ~16 s. Margin is 40-60 s and depends on the runner.
- x86_64: needs about the link time (~300 s) on a fast runner; ~3x on a slow
  one. Its route assembles text (`object_assemble` 2352 of 9376 task-s); a
  structured emission route like aarch64's would remove most of that.
- macOS (3-core M1, 7 GB): admission budget 2.29 GB against per-task
  reservations of 1.1-3.6 GB (peaks 0.5-1.9 GB), so for ~1100 s one task ran at
  a time. Three-way parallelism would fit 2400 s and needs ~0.76 GB per task.
- Work volume: in `pcc.backend.macho_link` ~37% of IR lines are error/cleanup
  paths; GC bookkeeping calls far outnumber work calls. Static keyword binding
  (decision 1) cuts IR 5.6-21% on real modules.

## Stage2 / pcc1 throughput (goal 1, goal 6)

- Keyword/default calls to known callees went through the runtime signature
  binder (~50x slower than a direct call). Static binding is implemented but
  uncommitted (archive `patches/static_keyword_binding.patch`, test
  `tests/python/test_static_keyword_binding.py`): five-GC output equals CPython,
  default-object identity preserved, IR 5.6-21% smaller on real modules.
- Since `168e4d06` every call through a module-level native from-import is a
  live module-namespace lookup plus a dynamic call. The live semantics are
  required (`tests/python/test_live_import_namespace_rebinding.py`); a fast path
  needs an identity guard or a namespace watcher. Not started.
- Stage2 export (run 38391246598, GC0): 148 ASCII modules 516 task-s (median
  2.4 s), 124 non-ASCII modules 3055 task-s (max 291 s, the quadratic scan fixed
  in `3a2530be`); Stage1 exported all 460 modules in 66 task-s. A replayed
  export of one ASCII module under pcc1 (archive `exportreplay/`): vthread
  effect analysis 31%, AST JSON wire 26% (json.dumps 22%), parse 19%, lift 6%;
  self time is 46% GC roots/barriers, 18% allocator, 13% refcount, 6% TLS.
  `f35eb06f` makes pcc's json append/slice whole strings (native loads 2.3x,
  dumps 2.2x faster on a 6.4 MB AST wire); the vthread analysis now walks each
  callable once per module pass (pending commit; export tables of 459 modules
  byte-identical, the 460th being the edited module itself).
- In that replay `_py_func_call_kwargs` (the runtime keyword binder) is on
  nearly every call stack; its own self time is 0.2%, its cost shows up as the
  allocation and GC work above. This is the path static keyword binding removes.
- Phase ratio Stage2 (pcc1) / Stage1 (CPython), Linux aarch64 GC0: export
  1426-1598 vs 65 task-s (~22-24x; ~19x without the three quadratic outliers),
  summary 385 vs 37 task-s (~10x, run 38423410429), codegen 8x
  (`unsafe_lowering`, calibrated alone) to 14x (`self_backend_parse`
  functions). Stage1 codegen is 8266 task-s, so Stage2 codegen needs ~66k-116k
  task-s: 4.6-8 h for one collector chain even with all four workers, against
  a 6 h job that also builds Stage1. A five-collector Stage2/Stage3 in CI needs
  pcc1 several times faster on the compiler workload. The measured owner is
  runtime overhead (GC roots/barriers/pins/leases, allocation, refcounting:
  ~85% of self time in the export replay).

## Open decisions for the maintainer

1. Shape tests that pin the slow lowering. Static keyword binding conflicts
   with tests asserting keyword/default calls use `py_obj_call_slots`
   (e.g. `test_shared_call_binding.py::test_ordinary_binding_classifier_is_pure`,
   `test_binder_contract_matrix.py`, `test_kwargs_method_call_loaded.py`); the
   full list is in the archive. Proposed rule: update such shape assertions only
   when native five-GC execution against CPython covers the behavior, keeping
   what the test meant to check on a shape that still needs the binder.
2. Resolved: the worker-prior fixture peer wait is 15 s in `263d12af` (asked
   for together with the other CI limits). Evidence: with 15 s the x86_64
   native-pcc0 gate passed in runs 38400558239 and 38405592470; with 6 s it
   failed in 3 of 5 runs, in 38411293966 and on Windows in 38423410429.
3. Source-pinned files: `tests/test_elf_*_source_controls.py` SHA-pin
   `pcc/backend/elf_x86_64.py`; any link-loop change needs the preimages updated.
4. A Windows unhandled-exception reporter (code, image offset, stack scan)
   would change `pcc_windows_start`, whose model test
   (`test_owned_windows_exit.py`) needs a new double. No longer needed for the
   `abspath` crash: cross-compiling for Windows and running under Wine with
   `WINEDEBUG=+relay,+seh` located it (archive README).

## Known failures

- Windows native-pcc0 width case: `0xC0000005` inside `os.path.abspath`
  (phase witness `abspath` in runs 38273917206, 38375711981, 38405592470).
  Root cause found and fixed in `fbe49ada`: `_path_abspath_build`'s
  `windows_full_path(...) if ... else null()` was lowered as an owned managed
  value (retained, rooted) because codegen's `_UNSAFE_RAW_POINTER_RETURNS`
  lacked 14 raw-pointer intrinsics; after `free(buffer)` the exit path released
  the freed buffer. Reproduced and verified locally by cross-compiling for
  `x86_64-pc-windows-msvc` and running under Wine 8.0 in an amd64 container
  (archive README, "Windows os.path.abspath").
- GC4 (`docs/investigations/gc-backend4-forwarding-retirement-starvation.md`):
  explicit `gc.collect()` never retires forwarding entries, so relocated garbage
  stays alive and its `__del__` never runs (fix parked: retirement itself
  over-releases a live replacement, `test_gc_relocation_sweep_verdict.py` code 23
  pre-existing); `tuple(list of finalizable instances)` + collect aborts with
  `BAD_INCREF`; `test_gc_backend4_production.py` fails a source-text assertion.
  The programs of `test_list_iterator_ownership.py` and
  `test_comprehension_iterator_ownership.py` abort with `BAD_INCREF` under GC4
  for every shape (`list(xs)`, `list(reversed(xs))`, `list(enumerate(xs))`,
  enumerate value); GC0-3 pass. The enumerate shapes first report 2472/2483
  `__del__` calls for 2000 objects, i.e. objects are finalized more than once
  (probe: archive `scripts/gc4_list_shapes_probe.py`). `ys = enumerate(build(n))`
  completed under GC4 before the enumerate fix only because it leaked every
  item; now that the items are freed it aborts like the other shapes.
- `str.upper()`/`str.lower()` map only ASCII letters (`"é".upper()` stays
  `"é"`); non-ASCII case mapping needs Unicode tables in the runtime.
- `[... for i, c in enumerate(s)]` over a non-ASCII str still indexes by
  codepoint per item (`py_obj_getitem_i64`), so it stays quadratic; `for` loops
  over `enumerate(s)` are normalised to a plain `for` and are linear.
- `test_py_for_target_representation_join.py`: 4 tests fail at `a21fa121`
  (a `SimpleNamespace` codegen double lacks `_module_globals`; IR-shape
  assertions on dict iteration).
- `test_worker_resource_plan.py::test_macos_failure_workflow_preserves_narrow_guard_evidence`
  fails since `2a3ad7ff` (2026-10-11): it pins the macOS evidence upload paths,
  and that commit added the worker-prior-gate paths.
- `test_python_cpython_alignment.py` at `a21fa121`: `super_call` ("slot-call
  operand has no authoritative source or owned result: Call") and
  `import_math_alias` (self IR verifier: an extern call that expects a double
  receives the boxed `call.slot.take` result of `sqrt(9)`; only in the full
  multi-module compile, the single-module IR passes the unboxed float).
- `test_runtime_iterator_roots.py`: every model case fails with
  `NameError: _TUPLE_ITER_START` since `75b2ddd1`; its loader
  (`test_runtime_getter_roots._GetterMemory.load`) execs only the function
  definitions of `py_iter.py`, not the module constant that commit added.
- `tests/python/test_raw_addresses_are_ints.py::test_raw_addresses_execute_as_ints_on_the_self_backend`:
  `py_bytes_new(_str_utf8("hey"), 3)` yields `b'mpl'` (stale pointer from the
  str -> raw-pointer extern path); independent of the 2026-10-13 commits (A/B
  each). Repro: archive `repros/str_utf8_extern_dangling_probe.py`.
- Host tests failing on a clean HEAD snapshot (pre-existing), among others:
  `test_py_multi_file_compile.py` (borrowed-rebind IR shape, raw-int scalar slot
  count, mock of the removed `_link_with_clang`, static-export constructor arg
  vs live imports, CPython fallback IR for relative imports; the other 41 pass),
  `test_intent_constraints.py` (coverage dropped: TestCrossBackendDeterminism),
  `test_fallback_baseline.py` (340 cli_bootstrap fallbacks),
  `test_hoist_global_cell_boundaries.py`, `test_unsafe_atomic_global_store.py`,
  `test_native_module_global_raw_pointer.py`.
- Virtual threads: `test_virtual_thread_park_effect.py::
  test_concrete_may_park_method_chain_resumes_to_python_result` prints `None`
  instead of 42 (fails at `1c980956` already; the may_park analysis marks
  handler, Worker.leaf and Worker.middle correctly, so the loss is in lowering
  or the runtime), and `test_cross_module_may_park_uses_generator_abi_and_resumes`
  fails on the same snapshot. Not bisected.
- Stale or environment-gated tests seen while checking `fbe49ada` (all fail
  identically without it): `test_freestanding_variadic_export.py` (5 of 6,
  "pcc.i64 does not implicitly convert ordinary Python int values"),
  `test_freestanding_stdio.py` (13 of 15, need `PCC_RUNTIME_ARCHIVE`),
  `test_freestanding_platform_process.py::test_runtime_archive_process_symbols_are_owned_by_python_port`
  (py_gc_backend.py calls `pcc_platform_abort`), `test_native_json_provider_matches_cpython.py`
  (reads the removed `pcc/ir/support/json.py`),
  `test_symbolic_stackmap_location_budget.py::test_shared_budget_object_links_and_executes`
  on macOS (executes a Linux ELF).

## Repro commands

```bash
# Per-phase Stage1 totals from a CI artifact
python3 /Users/jiamo/pcc-recovery-archive/2026-10-13/scripts/phase_breakdown.py \
  <artifact>/bootstrap-<platform>/platform-qualification/shared/stage1.log
# Static keyword binding (host IR shape + five-GC native)
env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -x -n0 tests/python/test_static_keyword_binding.py
env -u LC_ALL PCC_NO_AUTO_PCC1=1 uv run pytest -x -n0 -m integration tests/python/test_static_keyword_binding.py
```

Broad pytest sweeps must set `PCC_NO_AUTO_PCC1=1`: `pcc_gate` fixtures otherwise
start an unguarded Stage1 build to provision pcc1.

## Ten-goal acceptance remains open

1. Bootstrap: produce pcc1, run the original C/Python controls, then establish a no-libpython Stage2/Stage3 byte fixed point.
2. Five collectors: finish correctness, concurrency, relocation, GC4 capacity, FRESH_ALLOC, long-term memory, throughput and stability.
3. Platforms: execute macOS ARM64, Linux x86_64, Windows and Linux aarch64 qualification, including the standard macOS 15 3-core M1 / 7 GB / 45-minute job and wheel parity.
4. Ownership: complete compiler/runtime/ABI/provenance, stubs, bindgen, uv-lock and CLI/API consistency without product cc/LLVM/libpython fallbacks.
5. EDG-derived design: complete frontend semantic representation, a single layout authority and per-function compilation-memory management; qualify layered validation and centralized ABI across platforms. Diagnostic RSS guards are not invented acceptance thresholds. External i128 register-edge ABI, x86 dynamic alloca and two-dimensional VLA remain gaps.
6. Build performance: prove Stage2 no slower than Stage1 and a shared five-GC build under six hours. Diagnostic timings do not establish these targets.
7. Tests and cleanup: execute the full original C/Python and integration inventories; finish TLS, archive, freestanding and asynchronous timeout work.
8. Gateway: qualify actual native network/TLS and structured concurrency, then establish performance beyond asyncio.
9. Optimization: qualify tail-call, MADD, peephole, scaffold identity and thread-logging behavior and performance.
10. GUI: complete native app, five-GC, HTTP/TLS, Loader/HMR, typed FFI, interaction and pixel equivalence against Harness 47f943859bef60e4160492346772ded9b24f765a. The existing 160 host passes and 7,412 verified reference blobs do not establish native or pixel completion.
