# PCC Linux x86_64 cloud continuation — 2026-10-02

The user explicitly paused local development and authorized transfer of the
current source to the parent task's cloud Linux x86_64 environment. No further
local source changes or compiler/test builds may start during this pause.
This is evidence/navigation, not a replacement for Project Intent, compiler
contracts, AGENTS.md, or the full remediation scope in the earlier handoff.

## Source and safe pause

- Actual HEAD: `983254526ea28bc62190b6b20bde6d260754f705`, one migration commit
  after the earlier handoff's `ab6f29d0`. No commit, push, PR or deployment was
  performed here.
- Initial status: only the untracked earlier remediation handoff. Current local
  modifications are `pcc/frontends/python/codegen/exception_lowering.py` and
  `tests/python/test_native_exception_class_expressions.py`; both are preserved.
- All children started here have exited: runtime watchdog session 47264,
  smallest native pytest 65890, focused native watchdog 62265, review probe
  74460. No build or test remains running. Only snapshot/Library handoff work
  continued after the pause instruction. No unknown/user process was stopped.
- Observed weekly quota remaining at start and before runtime: 78%.

## Implemented but not qualified

The current HEAD reproduced the exact cell-captured class failure:
`Layer 1 except-clause class expression Subscript not supported`.
The lowering accepted only Name/Attr, while closure conversion emits cell
Subscript. The patch uses normal expression lowering and a protected original
exception while clearing TLS for class selection. Otherwise an expression's
post-call error check sees the pending original exception as a fresh failure.
Returned class temporaries are released; selection errors propagate with context.
New tests cover call selection, index/call errors, context, finally and unmatched
propagation. This patch is incomplete; review findings below remain open.

## Executed evidence

All paths below are relative to the original `/Users/jiamo/my/pcc` workspace.
The portable snapshot includes selected small evidence and original controls;
runtime/binary/cache outputs are excluded. Rebuild them on Linux.

- Red: `build/remediation-20261002/exception-class-red.log`, current-source
  smallest arm64 object regression failed as above (pytest `-x -n0 -vv --tb=short`).
- `exception-class-objects-attempt1.log`: 4/4 target object tests passed, covering
  Darwin arm64, Linux x86_64/aarch64, Windows x86_64. This is object evidence.
- `runtime-v1/result.json`: host pcc0 owned/self runtime build succeeded in
  96.726s; 173 pcc-Python members, production no-handwritten-C policy,
  peak tree RSS 220020736 bytes. Compiler/runtime frozen input identity:
  `eb0e09960b0a7fb4215a42b26a285c9b0a0f6b3cd5a2f3c104d6a22a1a305dad`.
  `frozen-v1-source.json` has 1416 conservative source/tool entries (also some
  pre-existing runtime receipts); it is not the portable archive manifest.
- `exception-cell-native-v1.log`: smallest cell/index program compiled with
  host pcc0/self/no-libpython and actually ran on all five collectors, passed.
- `exception-native-v1/{result.json,stdout.log,stderr.log}`: two pcc0 programs,
  each actually executed on GC0..4; 2 pytest cases passed (ten executions).
  Runtime built from frozen source including the earlier getter changes.
  Command was bounded by the existing own-process Darwin watchdog with
  16 GiB tree cap and shared performance lock:
  `env -u LC_ALL UV_CACHE_DIR=build/remediation-20261001/uv-cache
  UV_PROJECT_ENVIRONMENT=build/remediation-20260930/venv
  PCC_RUNTIME_ARCHIVE=build/remediation-20261002/runtime-v1/libpy_runtime_pcc_py.a
  uv run --no-sync pytest -x -n0 -vv --tb=short -m integration
  tests/python/test_native_exception_class_expressions.py -k 'not pcc1'`.
- The old v31 archive was correctly rejected by provenance verification
  because its py_list source predates the getter fix. The diagnostic setup
  error is not a native failure and not a passed check.
- New pcc1, C/native pcc1 controls, SSA replay and Stage2/3 were NOT started
  before the user's pause. Do not reuse the old 252.4s Stage1 as new evidence.

## Independent review: fix before new qualification

Read-only review found these remaining mechanisms; they were not patched after
the pause, and native repros are still required:

1. Selection error path unconditionally sets replacement context. If selection
   raises the same original object, it forms a self-context cycle. Add the
   existing distinct-object guard and run an exact program.
2. `py_exc_matches` cannot match a dynamically returned tuple of classes.
3. `except ()` triggers the literal tuple branch's `assert cond is not None`
   (confirmed with a bounded host IR probe).
4. Invalid handler classes/tuple members lack the required TypeError validation.
5. A yielding handler-class expression uses a temporary entry alloca rather
   than a generator heap slot; suspension/root lifecycle is not qualified.

## GC4 and platform findings retained

Original `native-threaded-v29/append_gate.py` is byte-equal to current
`concurrent_append_source()`; SHA256
`7653bb8f38c25f8841b5b15903da8d3d2a703a3f64f4edcb1467ebb7f4077b0e`.
Current py_list/py_class getter hashes match the earlier handback receipt.
The original failing threaded program was NOT rerun with the new runtime.

`build/remediation-20261002/gc-audit/iter_handoff_probe.py` independently models
current py_iter cleanup: GC3/4 moving at item/iterator root-unregister gates
returns a stale item in 4/4 red cases; five nonmoving controls pass. A candidate
in-memory AST finish using graph lock, reload, short pin, root teardown and
`pcc_gc_take_pinned_slot` passes 9/9. Production py_iter was NOT modified.
Host-memory results do not establish causality for the original native failure.

Read-only platform/entry audit found current native CLI still invokes external
mkdir/mv for run cache, rm/mkdir for -c/stdin, unzip/tar for archive installation,
ninja/shell and a host-Python eager Meson route. `python -m pcc sync --help`
actually displays C help because prepending --backend breaks first-arg sync
dispatch. Bindgen rejects Linux aarch64/Windows. Current macOS CI is Stage1
only; other three platform jobs request all-GC Stage1→3. Complete job/timing,
installed/native ownership, five-GC equality/performance, EDG and the gateway/
backend remainder in the earlier handoff remain open. Docker socket was denied
in a read-only sandbox probe; no escalation was attempted.

## Linux continuation

Extract the source archive, verify its manifest and executable/link modes, and
read AGENTS.md plus both handoffs and the standing contracts. Use the project's
3.15.0rc1 host selection with `env -u LC_ALL uv run`; every pytest needs `-x`.
Fix and execute the review red shapes first, rebuild a matching Linux owned
runtime, then build Stage1 and execute C/Python function-bearing controls.
Replay the retained SSA builder failure shape, then Stage2→Stage3/raw bytes.
Do not widen to five cold chains before the first dependent boundary passes.
The original native GC4 program, py_iter teardown risk and full remaining scope
must continue afterward. Cloud scheduling/execution is arranged by the parent.
