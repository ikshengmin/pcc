# Pure projection-demand optimization: separate research result

Inspired by [Agentic Verified Optimization](https://arxiv.org/html/2610.30188v1):
keep the reference fixed, prove the model rewrite, then validate its relation to
source separately. This is not an Aqueduct reproduction or a proof of PCC.

## Exact source and transformation

Frozen baseline `85b0e08cd28e143d87607a5cd76e8f7c350cabd7`, function
`pcc/ir/optimization/integer_fold_contract.py::fold_llvm_integer_compare`.
File SHA256: `8591c99dbd7eb97ae73b6151ecc1bab7def1d995a64c0b983e2200ba52df302a`.
The reference computes two signed projections for all ten valid predicates.
The candidate postpones them to `slt/sle/sgt/sge`; `eq/ne/ult/ule/ugt/uge`
use only the existing unsigned values. All helpers and guards stay unchanged.

`compare_rewrite.py` checks the complete reference hash before creating two
isolated function namespaces. It neither changes the source file nor patches
production imports. Its output diff is review-only, never automatically applied.

## Evidence layers, deliberately separate

1. **Lean model:** `FoldCompare.lean` proves equality for every predicate, natural
   width and integer inputs, even for arbitrary total projection functions. An
   integer modulo/sign model is an instance. An explicit demand-count model
   proves signed projections never increase and equality removes two for any
   positive width. A wrong equality-to-inequality change is refuted by a concrete
   checked theorem. The two equivalence theorems and wrong-rewrite rejection
   have no axiom dependencies. The two cost lemmas use Lean's standard
   `propext`; no custom axiom or `sorry` is supplied.
2. **Actual Python source copies:** 8790 exhaustive-small-width/boundary cases,
   including negative/zero widths, invalid predicates and 257-bit values,
   produced zero differences. Observed signed-helper calls are 2→0 for the six
   unsigned/equality predicates and 2→2 for the four signed predicates.
3. **Host microbenchmark:** seven alternating-order samples of 2000 calls each
   on CPython 3.15.0rc1 gave median ratios 2.64x unsigned/equality, 1.02x signed,
   1.41x mixed. Raw samples vary substantially; signed has **no reliable gain**,
   and these ratios are not portable predictions. There was no robust timing
   regression established, but per-sample regressions occur in the noise.
4. **Pipeline reachability:** the caller is optional text `instsimplify`, not
   the independently observed normal Stage1 configuration (`python_ir_passes=off`,
   direct-indexed). `real_ir_reachability.py` checks one real lowered seed with
   independent pass namespaces: **zero calls** to this compare-fold helper in
   either variant. Output IR was byte-identical and both outputs verified.
   The pass changed other instructions, not this target. One pass timing is
   not a speedup estimate: there is **no measured end-to-end benefit** here.

**Transfer gap:** no proof connects Python `int`/bit operations to the Lean
model, no proof covers arbitrary effectful Python objects, subclassed widths,
resource exceptions, the PCC compiler or native runtime. The generic projection
theorem avoids assuming mathematical facts about unchanged helper internals;
it still assumes total/pure projections. Testing bridges this gap only for its
corpus. No native, full-compiler or Stage1 performance improvement is claimed.

## Reproduce

Use the environment variables in `README.md`, plus `LEAN` pointing to an
already available Lean executable. Recorded tool: official Lean **4.35.0**,
commit `eb6cafb7290e8bd5f2937522d8d804f58f02bafc`. No mathlib is needed.

```sh
flock "$LOCK" timeout 40s "$LEAN" "$RESEARCH/pcc_ttf_verified/FoldCompare.lean"
flock "$LOCK" timeout 40s uv run --offline --no-project --python "$PYTHON" \
  python -m pcc_ttf_verified.compare_rewrite --source-root "$BASE" \
  --output "$OUT/compare-rewrite"
flock "$LOCK" timeout 40s uv run --offline --no-project --python "$PYTHON" \
  python -m pcc_ttf_verified.real_ir_reachability --source-root "$BASE" \
  --output "$OUT/real-ir-reachability"
uv run --offline --no-project --python "$PYTHON" python -m pytest \
  -x -vv --tb=short tests/research/test_pcc_verified_compare.py
```

Tool provenance: [official v4.35.0 release](https://github.com/leanprover/lean4/releases/tag/v4.35.0),
Linux archive SHA256
`7289597a44543246c9227b8cae501767e095ce17f6d448b95fc4a8dc97268f6d`,
verified after its one local download. Only workspace `bin`/`lib` contents were
extracted; no elan/global PATH change, mathlib or subsequent download was used.
