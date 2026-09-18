# Investigation: stage1 lazy-import discovery spawns host probes that execute the pcc package

## Status

Resolved locally on 2026-09-17. Red→green regression landed; the 420-second
cold-build gate passed in a guarded run for the first time. The 30-second
function-smoke deadline remains the failing boundary and is the same known
owned-linker cost recorded by the 2026-09-16 rounds.

## Problem Description

The guarded stage1 runs stopped with the coordinator inside
`_expand_recursive_stdlib`'s lazy-import branch: `_host_find_spec_origin`
(pipeline_dependency_closure.py:602-618) spawns one host Python per call and
runs `importlib.util.find_spec`, uncached. Folded stacks from 2,191 exited
probe children showed the probes executing `pcc/__init__.py` package
initialization (`roadmap_deepwire._install_pipeline_profile`), so while the
host compiler bootstraps itself every lazy `pcc.*` import pays a full
interpreter spawn plus the pcc package init inside the probe.

The 60-second faulthandler capture (build interrupted, not a compiler
failure) pinned the stack:
`subprocess.check_output` -> `_host_find_spec_origin:614` ->
`_locate_stdlib_module_source:853` -> `_expand_recursive_stdlib:1511` ->
`_prepare_multi_source_compile_closure:180`.

The lazy branch's declared intent (comment at the loop head) is to admit
only first-class pcc-owned stdlib providers. The generic locator it called
does not implement that boundary.

## Repro

Base HEAD `5b318560` with 43 staged worktree changes preserved. Frozen source
identity of the gate snapshot:
`829364477e6ee2000164d4a2b29e6d99c1a1f4c87c4a7aab3a7899a1ecdedaf2`
(1,139 files).

```bash
gtimeout 30s env -u LC_ALL uv run pytest -x -n0 -vv --tb=short \
  tests/python/test_recursive_stdlib_lazy_lookup.py
```

The regression writes a marker file from a host package `__init__` reachable
only through a function-body import. Before the fix the marker file exists
(a host package executed); after, discovery stays inside the pcc-owned tree
and the marker is never created.

## Test [CONFIRMED]

- `test_recursive_stdlib_lazy_lookup.py` failed before the one-line change
  (marker created) and passes after.
- `test_pcc_stdlib_registry.py` host-provider monkeypatched locators keep
  passing; the eager branch still uses `_locate_stdlib_module_source`.
- Stash-baseline comparison: `test_recursive_stdlib_does_not_expand_native_textwrap`
  and `test_locator_finds_py_stdlib_from_stage_binary_ancestor` fail
  identically with and without the fix (pre-existing, untouched).

## Proposals

Apply the declared boundary: the lazy branch resolves through
`_locate_native_stdlib_module_source` only. Do not cache
`_host_find_spec_origin` results — the eager branch needs host origins and a
name-keyed cache would need an invalidation story; the lazy branch should
never reach the host at all.

## Update: 2026-09-17

`PUT` applied at pipeline_dependency_closure.py:1509. The guarded stage1
build (run_pcc_stage1_build.py, frozen external snapshot, RSS-capped process
watchdog, performance lock) completed the build phase in **352.88 s wall /
1275.78 s user** within the unchanged 420-second gate — first recorded pass.
Compiler: Mach-O arm64, only `/usr/lib/libSystem.B.dylib`, SHA-256
`00f6e5d1edf9ef686604d6621a957ac1631bac67c5dab17d50b10d1dfa002890`,
389 modules, valid `pcc.profile.v1` payload. Ownership evidence:
`PCC_RUNTIME_CC=/usr/bin/false`, in-process owned Mach-O link
(`_owned_macho_link_in_process`), no Make/ar in the build tree.

The harness then failed at the 30 s function-smoke deadline (unchanged
boundary from 2026-09-16). Isolated rerun of the same smoke: 32.6 s, binary
executes and prints 42 (rc=0). Phase profile of the smoke compile:
`link_self_pcc_driver` 33.49 s of 34.20 s total; frontend phases sum to
~0.3 s. This matches the 2026-09-16 attribution (relocatable merge 64.9%,
NativeObject construction 21.6%, precise_stackmap._take 13.3%; the landed
bounded stackmap walker already recovered 8.8–9.4% wall).

The mid-flight `sample` of pcc1 could not be attributed at function level:
the 340 MB pcc1 image has a sparse symbol table over the linker region, and
`_resolve` collapsed thousands of distinct offsets onto `_pcc_cond_broadcast`.
Offset-level attribution (0x3f36fac–0x41e3150) is recorded in
`/private/tmp/pcc-stage1-profile-20260917-tamox1kq/smoke-profile/` for a
future symbolized pass. Sampling the smoke *output* binary with the
compiler's symbol table was an analyst error; disregard any earlier
`_pcc_cond_broadcast` ranking derived that way.

## Report

The lazy-probe boundary fix is scoped, verified, and recorded. Cold stage1
construction now passes its 420-second gate; the remaining gate failure is
the 30-second smoke against a 32–35 second owned-link cost. C high-runtime
archives lack `_py_list_from_static_items` / `_py_dict_from_static_pairs`
(0 symbols in `libpy_runtime.a`, both present in the pcc-Python archive):
pre-existing C/pcc-Python differential gap, untouched here. No commit, push,
or installation occurred.

## Update 2: 2026-09-17 — native provider keyword-codec boundary (stage2 unblock)

The retained gate pcc1 (00f6e5d1, pre-fix snapshot) failed its stage2 run inside
`codegen[urllib.request]`: host `urllib.request` (url2pathname / redirect
handling) calls `unquote(..., encoding=..., errors=...)` and
`quote(..., encoding="iso-8859-1", safe=...)`, while the native
`pcc/py_stdlib/urllib/parse.py` providers exposed only `(s)` / `(s, safe)`.
Both gaps closed in the current tree:

- `unquote(s, encoding="utf-8", errors="replace")` now accepts the host
  keyword spellings and decodes whole %-escapes as UTF-8 byte sequences with
  a bounded `_decode_utf8_replace` (one U+FFFD per ill-formed subsequence).
  Differential check: 15,000 random byte strings and 7 boundary cases match
  CPython exactly.
- `quote(s, safe="/", encoding=None, errors=None)` accepts the host
  spellings, validates the registered codec names (utf-8/iso-8859-1
  aliases), and still fails closed on unregistered codecs (LookupError) and
  on `bytes` + `encoding` (TypeError), matching host contract.

Native execution proof: host pcc compiles multi-module probes that call both
functions with keyword args; binaries execute rc=0 with correct output
(`/tmp/a b/é`, `é/�/�x`, `/a%20b%3Fc%3Dd`). New regression:
`tests/python/test_native_urllib_parse_no_libpython.py::test_urlquote_keyword_codec_args_no_libpython`
(red→green at the provider boundary; passes with the focused suite).

Gate progression with user approval: the 30 s function-smoke deadline is the
harness argparse default, not a documented constant; the real owned-link cost
is a stable 33–35 s (three isolated confirmations, output correct, rc=0).
Stage1 gate rerun with `--smoke-timeout 90` recorded a full SUCCEEDED receipt
(compiler `fea57cfd…`, only `/usr/lib/libSystem.B.dylib`, 408 s wall) and the
30 s miss is recorded here rather than silently waived. The link-compression
work stays open as its own Performance item.

Attempted and rejected: a per-call symbol-name memo inside
`_read_relocations` (255,304 calls / 15,408 distinct pairs) measured no wall
gain (1.18–1.30 s baseline vs 1.22–1.24 s cached, identical output bytes
`ddcd6991…`) — reverted. Linker work continues through the cProfile-backed
decomposition instead of repeating this shape.
