# Frame-root union eligibility: experimental, not enabled

This package changes no production source, default pass, import dispatcher,
CI selection, timeout, worker count, or native/GC behavior. It preserves an
executable held experiment and a **host-tested, unbenchmarked eligibility prototype**.

## Mechanism and limits

The ordinary x86/Win64 frame pass unions every safepoint's root offsets, although
the stack-map planner already shares immutable location tuples. The held v1
helper validates exact frozen records, roots, reloads and builtin integer offsets,
then skips identity-proven repeated groups. It is correct on its finite host
gate but does not qualify as an unconditional performance improvement.

Concrete v1 costs explain the unfavorable cases: each ordinary plan with more
than one record incurs three local imports, type checks, and an identity cache;
unique groups additionally validate every root while removing no root work.
The new `prototype.slot_offsets` separates eligibility from root validation:

1. Scan only exact `PlannedSafepoint` objects and exact tuple metadata. Record
   first-use flags and total versus unique location-item counts. Do not read root
   offsets, reload contents, or opaque properties/iterators.
2. Require positive provable redundant items and an explicit caller-supplied
   `minimum_saved_items` and `minimum_saved_items_per_record`. There is no chosen
   production threshold; test thresholds exercise branches, not tuning.
3. Reuse first-use flags in the admitted ordered union. Exact ordinary integer
   roots/reloads alone are evaluated. Decline before any opaque callback and
   replay the original record loop if necessary.

Strong tuple references and `is` checks protect identity under address reuse or
moving GC. A changed id can only miss and underestimate redundancy. Skipped roots
were already inserted in the same order; all reloads are still visited in their
original order. A rejected prefix has invoked no user code. Allocation failure,
concurrent illicit mutation of frozen dataclasses, and runtime replacement of
their class definitions are not equivalence claims.

This preflight is O(records), with O(records + distinct nonempty groups) memory.
It avoids root-element validation on rejected unique plans but **does not remove
rootless/tiny-plan overhead**. Exact arbitrary-plan prevalence cannot be proved
by looking at a constant-size prefix: all later tuples may be distinct. The
prototype imports the record classes at module load, unlike v1's per-call local
imports; initialization/circular-import/native implications remain unqualified.

## Measured held-v1 result

Authenticated original frame blob: `e63322c5f37b497a98a59c386ce402a8949f3369`.
Held candidate source SHA256:
`2d3114e1f776670b686cc24d27d5a2703757f8be622ea5c6f4ef36bb8a638aef`.
Functional host gate: original 21 + new 23 = **44 passed**, actual default six
workers/loadgroup. A separate fixed paired gate used CPython **3.15.0rc1**, default
enabled GC, no tracing/profiling, identical inputs, nine alternating AB/BA pairs
per shape/mode, and unchanged 120 s / 2 GiB process-tree limits. It completed in
63.585 s with clean reaping; sampled peak tree RSS was 96,002,048 bytes. Correctness
comparisons passed; that is **not** a no-regression performance acceptance.

Medians below are batch time divided by calls (microseconds/call). Paired ratios
are medians of each B/A pair, not ratios of the two separate time medians.

| Shape | Helper A / B (µs) | Helper median B/A | Whole A / B (µs) | Whole median B/A | Whole slower pairs |
|---|---:|---:|---:|---:|---:|
| Actual planner 1 root × 1 call | 0.409 / 3.226 | 7.951 | 115.701 / 118.180 | 1.102 | 7/9 |
| Synthetic rootless 129 records | 4.189 / 13.261 | 3.911 | 2157.648 / 2347.241 | 1.091 | 8/9 |
| Actual planner 8 × 16 | 5.954 / 6.463 | 1.062 | 423.089 / 486.701 | 1.186 | 7/9 |
| Actual planner 64 × 128 | 684.164 / 60.174 | 0.082 | 3083.776 / 2687.354 | 0.890 | 0/9 |
| Synthetic all-unique 64 × 128 | 575.371 / 1124.876 | 2.009 | 2631.136 / 3100.972 | 1.156 | 7/9 |
| Synthetic mixed 64 × 128 | 519.212 / 608.326 | 1.157 | 2884.863 / 2939.833 | 1.019 | 5/9 |

Variation is material: whole shared-8×16 pairs span 0.791–3.300; whole large-shared
span 0.289–0.974; mixed helper spans 0.440–12.561. All pairwise ranges, slower counts,
and planner counts are retained in `held_v1_results.json`, not raw timing logs.
**Decision: HOLD v1.** Only the large shared fixture was consistently faster;
rootless/small/unique whole-pass results do not support enabling it globally.

The three actual-planner fixtures had (records, total items, unique items) of
(2, 1, 1), (17, 128, 8), and (129, 8192, 64). Their whole-pass assembly is finite
ordinary synthetic SysV input, not captured emitted Stage1 functions. These are
not a production census; no Stage1 wall-time, Win64 timing, native benefit, or
memory-saving claim follows. Existing Stage1 phase receipts lack this helper's
invocation-weighted tuple-sharing distribution. Historical source comments about
other shards are not authenticated current frequency evidence.

The functional actual-read closure of 178 paths was compared read-only to head
`2a5e18f4941386f325fab58839ae3662f8532077`: 176 matched, with only the intended v1
frame source/new test overlays. The benchmark's 146 imported paths are within
that closure. The execution support was independently copied qualified 8a38
support, not a full fresh-head/native qualification. Raw immutable evidence stays
outside the repository in `pcc-frame-root-union-945/run-functional` and `run-bench`;
results SHA256 `3519f9eaef1e834c2b0704768128d2a5089cdd27f3302ee5d8b9a5b84e064639`.

## Executable scope, qualification, and remaining work

`held_v1.py` contains runnable baseline and held-v1 helpers. `prototype.py`
contains the complete opt-in eligibility implementation. `test_prototype.py`
contains a finite host-only regression suite: output/insertion ordering, opaque
getters/iterators/mutation/errors, identity collision/movement, real planner
fixtures, both frame ABIs, policy boundaries, metadata-only scanning, and getter
counts. Tests use the real PCC record classes; there is no mock production claim.

The new prototype passed **38/38 host cases**, with actual default six workers /
loadgroup, six identical collection inventories, and complete source-read receipts.
Both whole-frame ABI cases also asserted that the output changed, excluding an
accidental no-op. The corrected gate completed in 20.973 s, sampled peak aggregate
RSS 505,823,232 bytes, within 60 s / 512 MiB; all owned processes were reaped and
the canonical lock independently reacquired/released afterward. Source, runtime,
and tool pins were unchanged. Its 167 actually read paths matched fresh head
`1ab4a98376dc88bad619ce272a97f4f5c6f455bd`: 163 existing source paths plus the four new
experimental Python files. The production frame module was the unchanged original.

The first attempt displayed 38 passing cases but failed its external observer's
session-finish handling of pytest-tagged bytecode paths, so it was **not qualified**.
That failure is preserved. Only the observer was repaired using the existing
trusted fallback; six pure-stdlib mapping checks passed before the separately
approved corrected run. Prototype and test bytes were identical between attempts.
No performance retest, policy tuning, native execution, or production integration
was performed. Reproduction selects the finite research file explicitly:

```sh
uv run pytest -x -vv --tb=short experimental/frame_root_union_eligibility/test_prototype.py
```

That command alone is not permission or a supervisor: admission must separately
freeze current dependency/runtime/tool inputs, retain the default six/loadgroup,
take the canonical heavy lock, and retain the gate's 60 s / 512 MiB bounds.
No further execution, tuning, repeated benchmark, Stage1 build, native probe, or
production enablement is authorized by this package. The finite host correctness
result is not performance qualification; paired timing would require separate
approval. A global benefit requires actual Stage1 frequency/owner evidence, not
extrapolation from these fixtures.
