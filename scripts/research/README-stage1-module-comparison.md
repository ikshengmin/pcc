# Compare existing Stage1 module evidence

`compare_stage1_modules.py` reads **existing normal worker-timing logs**. It
does not compile, execute native code, change PCC, or reconstruct missing IR.
The older bootstrap-profile report consumes per-stage JSON; this small reader
joins the actual indexed-kernel, worker and phase records by module name.

```sh
python scripts/research/compare_stage1_modules.py \
  --before /outside/repo/before-stage1.log --before-revision BEFORE_COMMIT \
  --after /outside/repo/after-stage1.log --after-revision AFTER_COMMIT \
  > /outside/repo/module-comparison.json
```

Optional repeated `--module NAME` selects a paired subset. Revision labels are
caller-supplied, **not verified by this offline tool**. Supply independently
verified source/artifact identities. Duplicate attempts, missing record sets,
invalid clocks and target/route changes fail closed. Unpaired modules remain
explicit. Durations are cumulative worker observations, not CPU time or build
wall time; `function_setup_ns` is already inside `function_emit_inclusive_ns`.

## Observed normal Linux AArch64 artifacts

Compared `5838ec54` and `45442f96`: 484 complete paired modules, no unpaired
modules. The `call_expression_lowering.py` and `class_gen.py` input blobs are
identical at both revisions and at the audited `e8bfba85` source. Other compiler
changes and run variation remain confounders: this is **not isolated causal A/B**.

| Scope | IR instructions before -> after | Codegen seconds before -> after |
|---|---:|---:|
| `call_expression_lowering` | 282,980 -> 195,413 | 67.881 -> 36.977 |
| `class_gen` | 568,245 -> 566,890 | 97.326 -> 95.056 |
| All 484 modules | 24,590,276 -> 23,929,523 | 3,992.073 -> 3,827.504 |

Call-expression stackmap planning fell 34.039 -> 9.893 seconds, while blocks
rose 66,416 -> 67,058. Its static machine-call records fell 173,090 -> 114,712;
these are **not dynamic calls**. The largest module, `class_gen`, lost only
0.24% of IR instructions, versus 30.9% for call-expression and 2.69% globally.
Do not extrapolate the favorable module to the whole compiler.

All 484 after-modules used `indexed-native-sections` with zero capture fallback,
text-render time and assembly-parse time. The earlier text-IR experiment's parse
savings therefore do not describe this production path. After totals include
398.011 seconds verification, 682.286 stackmap planning and 1,399.388 inclusive
function emission, against 3,827.504 codegen worker-seconds. Verification is
about 10.4% of that denominator, not evidence of a redundant verifier.

The logs contain no full IR or per-safepoint live-state counts. They cannot
establish remaining cleanup equivalence, exact context-allocation savings,
or native/whole-build speedup for a new candidate.

## Tests

Eleven focused parser/control tests passed under existing CPython 3.15.0rc1,
pytest 9.1.1 and xdist 3.8.0 (not the repository's interpreter/pytest pins),
using `uv run`, `-x`, six workers and loadgroup. Both real logs also parsed and
compared successfully. Test fixtures are log records, not claimed compiler IR.

```sh
uv run pytest -x -vv --tb=short tests/python/test_compare_stage1_modules.py
```

Keep raw logs, output JSON, receipts and hash manifests outside the repository.
