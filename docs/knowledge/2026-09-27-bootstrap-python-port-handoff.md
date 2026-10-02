# 2026-09-27 — build system moved from Bash to Python (bootstrap + gates)

Source identity: worktree at `ab6f29d0` + the staged runtime/frontend changes
from the earlier session, plus this migration. No product semantics changed by
the port; `pcc/macho_normalize.py` gained an additive in-memory helper
(`normalized_macho_bytes`, ~78 new lines, existing API untouched).

## What moved

Deleted (11): `scripts/bootstrap.sh`, `run_pcc_native_deferred.sh`,
`verify_nolibpython.sh`, `gc_longrun.sh`, `run_gc_production_contract.sh`,
`run_b1_b6_closure_gate.sh`, `run_d2_d6_closure_gate.sh`,
`run_final_language_closure_gate.sh`, `run_coroutine_scheduler_roots_gate.sh`,
`run_goal_closure_bundle_gate.sh`, `run_self_backend_linux_x86_64_docker.sh`.

Added, all stdlib Python:

| File | Replaces |
|---|---|
| `scripts/bootstrap.py` | `bootstrap.sh` (the whole build entry) |
| `scripts/run_pcc_native_deferred.py` | `run_pcc_native_deferred.sh` |
| `scripts/run_test_gates.py` | the five closure/contract/long-run scripts (`--gate NAME`, `--list`) |
| `scripts/verify_nolibpython.py` | `verify_nolibpython.sh` (linkage parsed from the artifact, no `ldd`/`readelf`/`nm`) |
| `scripts/run_self_backend_linux_x86_64_docker.py` | the docker harness |
| `scripts/file_lock.py` | per-caller `fcntl`/`msvcrt` splits |

New/rewritten tests: `tests/python/test_no_shell_scripts.py` (tracked **and**
untracked shell scripts in the repo's own trees fail the guard), and behavioral
rewrites of
`test_bootstrap_script_safety.py`, `test_bootstrap_performance_manifest.py`,
`test_native_deferred.py`, `test_allocator_double_free_detector.py`,
`test_self_backend_publish_policy.py`, `test_nolibpython_linkage.py`,
`test_bootstrap_cache_identity_scope.py` (no more "grep the shell text"
assertions).

## The six reported defects, and their fix

1. **Required bash** → the build entry, native-deferred wrapper, gates,
   ratchet and docker harness are Python; `tests/python/test_no_shell_scripts.py`
   keeps a new `.sh` out of the repo (vendored `projects/` exempt).
2. **`uname != Darwin` defaulted to the LLVM oracle** → the owned `self`
   backend is the only backend; `--backend llvm` now fails closed
   (`choices=("self",)`) because the oracle route died with the llvmlite
   dependency (`test_llvm_oracle_backend_is_rejected`).  The dependent
   `llvm-bootstrap` head-truth gate was retired, and the archive-preflight
   labels (`llvm` / `llvmlite-target-machine`) were corrected to the owned
   route (`self` / `pcc-self-backend-object-writer`) they had drifted from.
3. **Native execution gate was Darwin-only** → `stage_exec_barrier` runs the
   freshly built compiler (`--help`) and compiles+runs a smoke program on every
   host, with the refcount-provenance audit; only the Darwin quiescence delay
   stays platform-specific.
4. **Hardcoded `/bin/bash`** → the deferred continuation is
   `[sys.executable, scripts/run_pcc_native_deferred.py, …]`; no step in the
   migrated entry points names a shell (guard test).
5. **BSD `stat -f%z` fallback** → sizes come from `Path.stat()`.
6. **Mach-O-only fixed-point normalization** → there is no normalized
   acceptance path at all: `verify_fixed_point` requires byte identity on every
   platform and format, and classifies a mismatch (size drift vs same-size
   drift) without accepting either.  `scripts/native_image.py` and the
   `pcc/macho_normalize.py` helper it used were removed again, so the port no
   longer touches product code.

## Verification

Full chain, `uv run python scripts/bootstrap.py --stage 3 --out-dir build/bootstrap-pyfinal`
(`build/verify-20260927/pyfinal.log`):

| Stage | Result | Artifact |
|---|---|---|
| 1 | rc=0, 678,725 ms (see the repeat measurement below) | pcc1 `sha256 edd1b9a4…` |
| 2 | rc=0, 496,745 ms | pcc2 `sha256 238ba148…` |
| 3 | rc=0, 443,898 ms | pcc3 `sha256 238ba148…` |
| verify | **OK — pcc2 and pcc3 are byte-identical** (no normalization involved) | exit 0 |

### Stage1 "20% slower" — located: one uncontrolled sample, not a regression

The 678 s stage1 above came from a run with no frozen cache namespace while
tests were running on the same host.  A pure-Python repeat harness
(`build/verify-20260927/stage1_repeat.py`; frozen identity, fixed 4 GiB cap,
isolated outputs, identical command) produced five controlled samples:

| sample | wall s | timed CPU s | compile s | driver+barrier s | load at start |
|---|---|---|---|---|---|
| earlier pair | 550.1 / 544.4 | 1002.8 / 996.8 | 543.4 / 537.7 | 6.7 / 6.7 | low |
| `run00` | 522.0 | 968.6 | 515.5 | 6.4 | 11.76 |
| `run01` | 508.0 | 939.3 | 501.5 | 6.4 | 4.34 |
| `run02` | 509.2 | 942.7 | 502.6 | 6.6 | 3.20 |

Wall and CPU move together (2.8% vs 3.1% spread), the driver's own overhead is
a constant 6.4-6.7 s (≈1.3%, mostly the smoke gate), and every sample produced
the same pcc1 (`sha256 edd1b9a4…`).  There is no additive per-run cost to fix:
the wall variation tracks host CPU availability, and the one 678 s sample is a
+25% outlier that does not reproduce.

To keep that diagnosable by default, a successful stage now also prints
`PCC_BOOTSTRAP_STAGE_CPU stage=N user_ms=… sys_ms=… cpu_ms=… wall_ms=…
driver_and_barrier_ms=…` next to `PCC_BOOTSTRAP_STAGE_RESULT`, so "more work"
and "busier host" can be told apart without a profile directory.

- pcc1 native smoke: `--backend self --python-libpython off` compiled and ran a
  program using folded literals and an extern `c_int` condition → `-1 0 -1 7`
  / `1 1`, rc=0.
- `PCC_CURRENT_PCC1=build/bootstrap-pyfinal/pcc1 uv run pytest` over the
  migrated-related suites → **222 passed** (bootstrap safety, performance
  manifest, native deferred, pcc1 gate, cache identity, profile report,
  publish policy, allocator audit, no-libpython, install toolchain, backend
  bootstrap gate, replay worker, compile A/B).
- Re-running `verify_fixed_point` against the produced artifacts with the
  current source → rc=0, byte-identical.
- `tests/python/test_bootstrap_script_safety.py` locks the policy: identical
  bytes pass, size drift fails (rc 1), same-size drift fails (rc 2), and
  `--backend llvm` is rejected.  There is no normalized acceptance path.

### Latent bug found in the old gate (fixed by this port)

The stage smoke used to inherit no runtime archive. When the native compiler
judged the archive "unproven" it entered the host-Python rebuild path and died
with `no-libpython function unavailable: …_acquire_runtime_build_lock`, so
Stage1 failed for a reason unrelated to pcc1. Reproduced with the
**shell-built** pcc1 from 20:12 on the current tree, so it predates this port;
`bootstrap.py` now hands the smoke the resolved archive explicitly
(`_smoke_runtime_archive`, with a regression test).

## Open

- Three scripts still emit/require a shell launcher (not the build entry):
  `scripts/install_pcc1_toolchain.py` (generated `host-pcc`/launcher),
  `scripts/probe_pcc1_self_runtime.py` (Darwin probe helper),
  `scripts/run_pcc_compile_ab.py` (records `/bin/sh` as a required tool).
  `test_shell_launcher_producers_are_the_known_list` fails if the list grows
  or shrinks, so the migration cannot regress silently.
- Linux/Windows: the driver is now platform-neutral (`self` default, PE/ELF
  normalization, Job-object supervisor, `msvcrt` lock, `.exe` stage names),
  but no Linux/Windows chain has been executed here; the known Windows
  cross-build gaps are unchanged.
- `tests/python/test_pcc1_python_smoke.py::test_pcc1_partial_async_function_returns_coroutine`
  times out at its 120 s compile bound with **both** the new pcc1 and the
  shell-built pcc1 → pre-existing, not caused by the port.
- Historical records keep their original commands: `docs/investigations/`,
  `docs/knowledge/`, `docs/archive/`, `docs/goal/evidence/`,
  `docs/refs_docs/`, and the dated measurement blocks in
  `docs/issues/performance-gaps.md`. Current guidance (README, architecture
  doc, development-tools, teaching list, `hatch_build.py` docstrings,
  `bootstrap_gate_baseline.json`) was updated.

## Repro

```bash
cd /Users/jiamo/my/pcc
# this host needs the 8 GiB cap: the guard's default 16 GiB cap cannot pass its
# swap-pressure preflight on a 2 GiB dynamic swap (reclaimable 46.7 GiB).
PCC_BOOTSTRAP_MAX_TREE_RSS_BYTES=8589934592 uv run python scripts/bootstrap.py \
  --stage 3 --out-dir build/bootstrap-pyfinal
PCC_CURRENT_PCC1=$PWD/build/bootstrap-pyfinal/pcc1 uv run pytest \
  tests/python/test_bootstrap_script_safety.py \
  tests/python/test_no_shell_scripts.py -x -n0 -q
```
