# 2026-09-27 — pcc1 change verification handoff (unary fold / raw truthiness / null callee)

Source identity: worktree at `ab6f29d0` + staged (uncommitted) changes that were
present at 19:56 on 2026-09-27. `git diff --cached --check` clean. No source
edits were made by this verification; only three new test files were added.

## Verified this session

1. **Stage1 → Stage2 → Stage3 chain passes on the current worktree.**
   - `PCC_BOOTSTRAP_MAX_TREE_RSS_BYTES=8589934592 bash scripts/bootstrap.sh --stage 1 --out-dir build/bootstrap-verify1`
     → `stage=1 elapsed_ms=566359`, pcc1 `sha256 5b3e5990…`, 340,420,616 B.
   - `… bash scripts/bootstrap.sh --stage 3 --reuse-stage1 --out-dir build/bootstrap-verify1`
     → stage2 426,442 ms; stage3 420,201 ms;
     `OK — pcc2 and pcc3 are byte-identical. Self-host gate passed.`
     pcc2 = pcc3 = `sha256 86f606d1…`, 337,825,096 B.
   - Logs: `build/verify-20260927/stage1b.log`, `stage23.log`.
   - Environment note: the guard's default 16 GiB cap cannot start on this host
     (2 GiB dynamic swap, reclaimable 46.7 GiB < 2×(16+8) GiB) and fails closed
     with `swap is already pressured`. The documented 8 GiB cap waives it;
     observed peaks were 1.49 GB (stage1) and 5.83 GB (stage2).
   - pcc1 smoke (native emit + run):
     `-1 0 -1 0 7 0` / `1 1` from folded literals and an extern `c_int`
     condition.

2. **Unary integer-literal folding** (`pcc/py_frontend/codegen/unary_call_lowering.py`).
   A/B against a pristine `HEAD` worktree (`/tmp/pcc-verify-head`): freestanding
   `return -1` / `~0` / `+7` on a machine-typed condition is
   `RuntimeError: freestanding ordinary Python int unary arithmetic cannot
   preserve arbitrary precision` on HEAD and `ret i64 -1 / -1 / 7` on the
   worktree. Non-literal operands (`-x`) still fail closed on both.
   New test `tests/python/test_native_int_literal_unary_folding.py` is
   differential against CPython across GC0..GC4 through pcc0 and pcc1.

3. **Machine-integer truthiness** (`pcc/py_frontend/codegen/coercion_lowering.py`).
   Motivating shape is an extern call typed `DynType` in an i32 lane:
   HEAD fails the freestanding route with
   `PyPipelineError: freestanding module emitted managed-runtime reference …
   @py_int_from_i64`; the worktree emits `icmp ne i32 %extern.…ret, 0`
   (`truthy_raw`). New test `tests/python/test_native_machine_int_truthiness.py`
   covers the freestanding IR shape plus native execution through pcc0/pcc1
   (GC0..GC4, CPython-differential via an `abs` equivalent).
   The same hunk's `zero`-constant widening (`v.type` instead of `_I64`) was not
   reachable from any shape tried — all observed `IntType` conditions arrive as
   i64, where the constant is unchanged.

4. **Null-callee call** (`pcc/backend/self_backend_parse.py` +
   `pcc_null_callee_trap` in `freestanding_runtime_debug.py`).
   HEAD: `BackendUnavailable: self backend malformed call in 'f'/'entry':
   %r = call i64 null(i64 %x)`. Worktree: parses, call text is
   `pcc_null_callee_trap`; emitted object references `_pcc_null_callee_trap`;
   the archive defines it; a linked program that takes the path aborts with
   `[NULL_CALL]` (exit 134). Checked in as
   `tests/python/test_self_backend_null_callee_call.py` (parse + object symbol +
   Darwin-arm64 link/run integration test).
   Caveat: any `null(` callee now becomes a runtime abort instead of a
   compile-time refusal, live paths included.

5. **Runtime archive rebuild** happened from the changed freestanding sources on
   first compile: repo archive `pcc/py_runtime/libpy_runtime_pcc_py.a` (20:11)
   and the content-addressed pytest archive (20:01), both manifest-verified.

6. **Presence in the built stages**: `strings` finds `@pcc_null_callee_trap`
   (1×), `truthy_raw` (2×) and the unary guard message (1×) in pcc1, pcc2 and
   pcc3 — the three changes are compiled into the chain, not only the host.

7. **Related suites**: 60 passed
   (`test_bootstrap_performance_manifest`, `test_self_backend_unreachable_parse`,
   `test_self_backend_indexed_call_plane`, `test_self_backend_single_line_define`,
   `test_self_backend_darwin_tls`, `test_ir_to_obj_owned_emitter`, `test_pcc1_gate`,
   plus the new files' non-integration tests).

## Pre-existing failures (not regressions — identical on pristine HEAD)

- `tests/python/test_freestanding_module.py`: 18 of 41 fail because they expect
  `define i64 @f…` while codegen now emits `define external i64 @f… "no-builtins"`
  (assert mismatch or `IndexError` on the split). The failure set is byte-identical
  on HEAD and the worktree; the file therefore gives no coverage for the
  freestanding shapes and should be refreshed.
- `tests/python/test_owned_external_tls.py`: exactly 9 failing cases (x86_64 TLS
  symbol typing; BSD/Windows archive symbol table) — matches the known-open list.
- `tests/python/test_package_install.py`: 4 fail on the worktree, a subset of the
  5 that fail on HEAD (HEAD additionally fails
  `test_pcc1_package_install_labels_unproven_prebuilt_payload`). The CLI shape
  reports `PCC-PKG-OWNED-MESON-SOURCE-REQUIRED` / `owned_meson_source_required`
  with `build_backend: pcc-native-meson`.

## Not run / still open

- Linux aarch64 runtime cross build (reported working before; runtime sources
  unchanged since 19:44) and the Linux x86_64 `py_capi_stdio_runtime` blocker,
  Windows cross-build items, macOS pcc1 Python 3.11 ↔ host 3.15 wheel issue,
  5-GC chain: untouched by this verification.
- Broad `tests/integration` suite and five-GC bootstrap: not run (final
  qualification scope, known 6 h limit).

## Repro

```bash
cd /Users/jiamo/my/pcc
PCC_BOOTSTRAP_MAX_TREE_RSS_BYTES=8589934592 bash scripts/bootstrap.sh --stage 1 --out-dir build/bootstrap-verify1
PCC_BOOTSTRAP_MAX_TREE_RSS_BYTES=8589934592 bash scripts/bootstrap.sh --stage 3 --reuse-stage1 --out-dir build/bootstrap-verify1
PCC_CURRENT_PCC1=$PWD/build/bootstrap-verify1/pcc1 uv run pytest \
  tests/python/test_native_int_literal_unary_folding.py \
  tests/python/test_native_machine_int_truthiness.py \
  tests/python/test_self_backend_null_callee_call.py -x -n0 -q
# add -m integration for the pcc1 / link+run cases
```

A/B baseline used for the "was it broken before" claims:
`git worktree add --detach /tmp/pcc-verify-head HEAD`, then import with
`PYTHONPATH=/tmp/pcc-verify-head` (or `python -S`) so the worktree's `pcc`
package wins over the editable install.
