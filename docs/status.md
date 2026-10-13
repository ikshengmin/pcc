# PCC current status

Updated 2026-10-13. Source: `ikshengmin/pcc` master (`shenming` remote).
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
- `3a2530be` the freestanding-directive scan indexes a character list instead
  of the source str (pcc1 str indexing of non-ASCII text is O(index)).
- `28ce3e92` restores the 6 s worker-prior fixture deadline (see decision 2).
- `1c980956` the ELF link reuses a relocatable `.pcc_stackmaps` table's
  validation verdict for its relocated copy (finalize was 44.8 s of the link).
- Codegen treats every raw-pointer unsafe intrinsic result as
  raw (Windows `os.path.abspath` crash, see Known failures).

CI: run 38375711981 (`0f6fa1d9`): every Stage1 job hit the 2400 s deadline, no
compile failures. Run 38391246598 (`510ed001`): see its page; the three
worker-prior gates (Linux x86_64, Windows, macOS) failed again. Run 38405592470
(`3a2530be`): Linux aarch64 Stage1 missed the cap by about 5 s (codegen ended at
2117 s; the link was killed in finalize), so no Stage2 data; macOS
`Build current pcc1` timed out at 2400 s with ~70 of 460 modules done (3-core
runner, `indexed-sidecar` route); worker-prior gates: strict closure timed out
(aarch64, x86_64) or hit the memory cap (macOS), Windows width case crashed.

## Stage1 within 2400 s (goal 1 / platform qualification)

Run 38391246598 (`510ed001`), Linux aarch64 wheel job: Stage1 produced a static
pcc1 in 2389.3 s (11 s under the cap, peak tree RSS 6.4 GB), and that pcc1
compiled the canary in 325.9 s and ran it. The five-GC chain then stopped:
GC0 Stage2 hit the 2400 s stage deadline while still in its per-module export
phase (273 of ~460 modules exported, 3920 worker-seconds, about two workers
because the GC0 and GC1 chains shared the 4-vCPU runner; the largest exports
under pcc1 took 170-350 s each, e.g. `pcc.driver.cli_bootstrap` 348.5 s);
GC1/GC2 were cancelled as peers. The same job at `0f6fa1d9` timed out in the
Stage1 link, so the aarch64 Stage1 margin is a few seconds either way.

From run 38375711981 (`pcc indexed phase totals` in each
`platform-qualification/shared/stage1.log`):

- Linux aarch64 compiled all 460 modules; codegen workers ended at 2117.8 s,
  then the coordinator-only ELF link ran 282 s until the kill (stage inputs
  90 s, resolve/relocate 130 s for 39.6 M relocations, finalize unfinished).
- Linux x86_64 finished 72/460 modules, Windows 83/460. On the same 72 modules
  x86_64 used 8758 s of worker time vs 4385 s on aarch64: identical CPython
  phases run ~2x slower on that runner, and the x86 route assembles text
  (`object_assemble` 2710 s vs 94 s). The structured native-sections route is
  aarch64-only.
- IR volume dominates: in `pcc.backend.macho_link` ~37% of IR lines are error
  and cleanup paths; GC bookkeeping calls (frame leave, store_root, status
  reports, leases) far outnumber work calls.

Levers: IR reduction in codegen (blocked, see decisions), x86 structured
emission, x86 use of the native stack-map planner. Measured link
micro-optimizations were below 5% and are parked in the archive.

The aarch64 Stage1 link is a pure serial tail (all four worker slots stay busy
until the last module, 90 idle slot-seconds in the whole run):
`artifact_link` 286.6 s = stage inputs 89.9 + select 1.8 + classify 15.5 +
allocate 2.6 + resolve/relocate 129.8 + finalize 44.8 + verify 1.6. Finalize
was a second full validation of every input's stack-map table; `1c980956`
removes it (0.295 s -> 0.009 s on 190 runtime objects), which should give about
40 s of margin. The remaining tail is in the SHA-pinned `elf_x86_64.py`
(decision 3).

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

## Open decisions for the maintainer

1. Shape tests that pin the slow lowering. Static keyword binding conflicts
   with tests asserting keyword/default calls use `py_obj_call_slots`
   (e.g. `test_shared_call_binding.py::test_ordinary_binding_classifier_is_pure`,
   `test_binder_contract_matrix.py`, `test_kwargs_method_call_loaded.py`); the
   full list is in the archive. Proposed rule: update such shape assertions only
   when native five-GC execution against CPython covers the behavior, keeping
   what the test meant to check on a shape that still needs the binder.
2. `tests/fixtures/native/worker_size_priors.py` peer-wait deadline 6 s -> 15 s
   for the Linux x86_64 GC4 growth gate. This test change was committed by
   mistake inside the documentation commit `4a3e9246` without review and is
   restored to 6 s in `28ce3e92`; the proposal itself stays open. Evidence:
   with 15 s the x86_64 native-pcc0 gate passed in runs 38400558239 and
   38405592470; with 6 s it failed in 3 of 5 earlier runs and again in
   38411293966 ("missing worker marker", growth case).
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
  Root cause found and fixed (commit after `1c980956`): `_path_abspath_build`'s
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
