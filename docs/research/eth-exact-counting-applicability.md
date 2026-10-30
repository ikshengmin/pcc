# ETH exact-counting paper: applicability to PCC

Assessment: 2026-10-27. **Research-only; no production change or speedup claim.**

## Decision

No algorithm in the reviewed paper is currently a justified replacement for
PCC's Stage1 compilation, worker admission, native linking, or stack-map
processing. Its principal construction solves fixed-width Boolean **exact
counting**, not those existing deterministic scans and transformations.
Retain this as a negative applicability result; do not add a production SAT
solver or change compiler budgets. The isolated exact-arithmetic checks below
exercise actual paper constructions, not a proposed PCC optimization.

This decision does not assert that the mathematical result is false. It
separates the claimed asymptotic theorem from a useful compiler implementation.

## Reviewed sources and verification scope

- [Paper, October 25 revision][paper]: 67 printed pages, at OpenAI math commit
  `7ef5c0cc5ca4b0e072b7a050fca09ab24bc6fa3a`.
  The paper's `build/` contains `main_full_version.tex` and
  `quantitative-{circuits,contraction,reduction}.tex`; these were read alongside
  the PDF, including rendered parameter pages 17 and 58.
- [Repository notice][notice]: these are model-produced research materials at
  differing verification stages. That general caveat must not be mistaken for
  absence of a formalization of this particular paper.
- [Lean scope][lean-scope] explicitly covers canonical fixed-width CNF exact
  counting and SAT with exponent
  `O_k(n (log log n)^2 / sqrt(log n))` and polynomial visited tape space at
  every execution prefix. The October 25 Appendix D bounds are stronger than
  the bound identified in that scope statement.
  [Final.lean][lean-final] supplies a primitive-machine statement and
  finite-cutoff fallback; the [Comparator configuration][comparator] names
  that theorem, permits `propext`, `Quot.sound`, `Classical.choice`, and sets
  `enable_nanoda: false`. These source files were inspected, not executed.
- PCC source: `ikshengmin/pcc` commit
  `218867de8e71686aef2f3617fc4d658bd1680942`. The source snapshot and
  individually fetched research files were checked against that revision.

The review combines source inspection, an independent review of the component
implementations, and bounded executable exact-oracle checks. It found no
in-domain component failure or established fatal proof error. It did not
independently certify the full theorem, run Lean/Comparator, execute the full
fast counting algorithm, or benchmark PCC.

## What the algorithm actually requires

Theorem 1.1 (p2) claims deterministic exact #k-SAT in
`(n+m)^O_k(1) * 2^O_k(n log log n / sqrt(log n))` bit operations,
or `(n+m)^O_{k,D}(1) * 2^O_{k,D}(n / sqrt(log n))` when
`m <= Dn` with fixed D, using polynomial bits of space. Both k and, in the
second bound, D are fixed. This is not a polynomial-time bound. The paper
itself warns (p2, also section 1.1.3, p5) that a general polynomial reduction
does not preserve subexponential dependence on the original input size.

The supplied constructive routes are:

1. **Shared measurements and exact interpolation** (sections 2.4-2.6,
   pp10-13; Proposition 3.4, p25). Construct rational replacement forms;
   enumerate bounded integer measurement tuples and local Boolean assignments;
   use inclusion-exclusion to recover truncated Taylor coefficients; recover
   the count by Hermite interpolation. The enumeration cost contains
   `C_q * (2D+1)^(2 tau n) * 2^(2n/r)`, where
   `C_q = sum_{j<=min(q,n)} 2^j binomial(n,j)`.
   Here D bounds the row l1 norm of the measurement matrix (hence the
   absolute measurement range), not the clause-density constant.
2. **Deterministic sparse parity reduction** (sections 6.1-6.4, pp40-44).
   Expander shifts and local codes are found by exhaustive searches.
   Lemma 6.2 evaluates up to `3n (2m)^P` candidate systems, with
   `P = 20000 B^2 mu^2`, `mu = floor(log2 m)+1`.
   This is constructive, not a missing SAT oracle: the earlier common-zero
   routine evaluates each candidate without knowing the final count.
   Its asymptotic absorption does not make these searches inexpensive.
3. **Recursive Boolean tensor contraction** (Appendix D, pp53-64).
   Theorem D.1 requires a bipartite Boolean-index network with at most Cs
   edges, degree at most `ceil(s^0.9)`, and uniformly evaluable entries in
   `(Z / 2^P0 Z)[V] / (V^ell)`, with `P0, ell <= Cs`.
   It is not a general real/complex tensor-contraction optimization.

Exact arithmetic is addressed, not omitted: Appendix B (pp47-51) bounds
common denominators, coefficient bit lengths and fraction-free elimination;
Appendix D.4.1-D.4.4 (pp57-61) tracks modular precision, canonical lifts and
Hermite recovery losses. Polynomial space is obtained by sequential/depth-first
enumeration, not by retaining all assignments. Neither polynomial space nor
bit complexity establishes a small memory footprint; floating-point
substitution would require a different correctness argument.

## Concrete practicality checks

These are necessary-condition failures for the **written parameter choices**,
not impossibility results for future improved implementations.

- Equations (2.19), p17, and (4.17), p34, use
  `lambda=ceil(log2 n)`, `nu=ceil(log2 lambda)`,
  `r=floor(sqrt(lambda)/nu^2)`, and `Q=floor(lambda/(128 nu))`.
  Even at `n=2^64`, `lambda=64, nu=6, r=0, Q=0`
  (the overview has `R=32`). These fail `r>=2` and `Q>R`;
  section 5.5, p39, explicitly falls back to all `2^n` assignments when
  its numerical checks fail. Small toy runs therefore do not demonstrate
  the advertised fast regime.
- Appendix D.4.2, p58, sets `epsilon=1/(128*1024)=2^-17` and
  `u0=floor(epsilon*lambda)`, where now `lambda=ceil(log2 s)`.
  If `lambda<131072`, then `u0=0`, violating Lemma D.5's `u>=1`
  requirement (p60). Its stronger stated proof-regime condition
  `lambda^(4/5)<=u0` in equation (D.5) implies the necessary lower bound
  `lambda>=2^85`; that is not a sufficient threshold for all other checks.
  The text expressly handles the finite smaller range by direct summation.
  This is compatible with an asymptotic theorem, but not a practical PCC
  acceleration as written.

## Fit against actual PCC paths

All source links below are pinned to the audited PCC revision.

| Rank / decision | Actual owner | Why the paper does not supply a replacement |
| --- | --- | --- |
| P0: retain Stage1 attribution, no counting migration | [Stage1 driver][stage1] launches host Python `-m pcc --backend self --python-libpython off`; `cli_bootstrap._observed_compile_python` calls `pipeline.compile_python`. | The requested work is compilation and native artifact construction, not exact enumeration of satisfying assignments. SAT encoding and its variable/bit expansion have not been supplied or justified. |
| No direct fit: resource scheduling | [`run_worker_commands`][workers] -> `worker_process_pool.run_resource_worker_processes` -> [`worker_resource_plan.choose_task`][choose]. | This is online, dependency-ready byte admission with observed reservations, exclusive calibration/retry and largest-fitting selection. The paper's “groups/schedules” assign algebraic variables/factors; they do not optimize this runtime objective or model changing RSS. |
| No direct fit: native link and stack maps | [`macho_exec.prepare_executable_object`][prepare] -> [`macho_link.link_relocatable_native`][merge] -> [`precise_stackmap.merge_stack_map_payloads`][stackmap]. | These inspect symbols/relocations, preserve deterministic ordering, join payloads, intern root vectors and rewrite indices. The current stack-map path already caches source ranges and shared byte vectors; it does not count Boolean solutions. |
| No demonstrated fit: register allocation | [`allocate_aarch64_block_registers`][allocator] -> `_linear_scan_assign` / `_linear_scan_assign_pools`. | The actual implementation is interval allocation with spill slots, call/PHI/reload restrictions and ABI register pools, not exact unrestricted graph coloring. Corollary 1.3's graph-coloring count (pp2-3) is not a drop-in replacement. |
| No demonstrated fit: IR optimization | [`compiled_owned_passes`][owned-passes] invokes `pcc.ir.optimization.mem2reg.mem2reg_text`, plus local cleanup and simplification. | Dominators, dominance frontiers, SSA renaming and reachability are CFG computations; the actual owned dispatcher does not invoke a Boolean-search kernel. The older native-IR comment in the pass driver is not the implementation selected by this dispatcher. |
| Low-priority conceptual transfer only | Sections 2.4 and 3: condition on a small shared interface, then process independent groups sequentially. | A useful design principle, but PCC's interfaces and summaries must preserve its own semantics. No new bounded interface, missing algorithm, or measured saving was established here. It does not justify a production change. |

A scoped search of the audited `pcc/` and `scripts/` snapshot found no
SAT/CNF/SMT solver call in these paths; `llvm.*.sat` matches were saturating
arithmetic intrinsics, not satisfiability. This is a scoped source observation,
not a claim about every possible future compiler feature.

### Closest candidate: optimizer equivalence checks, still no demonstrated gain

The existing [`compare_rewrite.py`][fold-experiment] is an opt-in experiment,
not a production compiler pass. Its `experiment -> load_pair ->
check_equivalence(differential_cases(...))` path executes two hash-locked
source copies of `fold_llvm_integer_compare`. The candidate moves
`lhs_s = signed_value(lhs_u, width)` and the corresponding `rhs_s` conversion
from unconditional execution into the signed-comparison branch. Thus
`eq/ne/ult/ule/ugt/uge` avoid both conversions; `slt/sle/sgt/sge` retain them.
It does not change an equality into inequality: that separate deliberately
wrong mutation tests whether the same oracle rejects an error.

Its existing 8,790 cases comprise 3,400 exhaustive pairs at widths 1-4 and
5,390 boundary/invalid-input cases at ten widths, including 256. The
[existing evidence][fold-evidence] also distinguishes a universal pure
integer model from these source-copy checks, and reports zero calls to the
target in one real lowering seed. That zero is not a universal coverage claim;
nor is a host-function timing an end-to-end Stage1 gain. The observed normal
Stage1 configuration in that evidence uses passes-off/direct-indexed output.

For a fixed width and a pure straight-line kernel, one could encode
`reference(input) != candidate(input)` and count counterexamples. But even a
binary width-w bitvector input contributes 2w Boolean variables, plus gate
and output-constraint variables (a bounded-fan-in Tseitin encoding adds
O(g) variables for g gates). At width 256 there are at least 512 input bits
before this expansion, still far below the paper's feasible parameter regime.
Finding one counterexample suffices for rejection; exact counting is extra
work unless the count itself serves a measured need. A valid encoding must
also preserve return status, width validation, signed/unsigned normalization
and invalid predicates. Wider optimizer claims require overflow/poison,
invalid shifts/division, control flow and GC semantics; arbitrary Python
objects can additionally introduce effects or exceptions that a pure
bitvector model does not capture.

This is therefore a possible *problem shape*, not an improvement over the
existing finite checks plus pure-model reasoning. Partitioning tests by width
and predicate is already present, so the paper's grouping principle is not a
new implementation contribution here. No PCC equivalence oracle was replaced
and no new compiler benchmark is justified by this review.

## Executed algorithm checks and remaining dependencies

The following files are standard-library-only, import-safe, isolated research
code; they import no PCC production modules:

- [`eth_exact_counting_components.py`][components] and [its tests][component-tests].
- [`eth_tapered_hermite.py`][tapered] and [its tests][tapered-tests].
- [`eth_tensor_modes.py`][modes] and [its tests][mode-tests].

| Paper stage / invariant | Executed check and independent oracle | Boundary or untested dependency |
| --- | --- | --- |
| Eq. (4.10), pp27-30; copy-back (4.14)-(4.15), pp32-33: `P(0)=I`, shared measurements determine out-of-group values | All 16 auxiliary rows, Remark 4.4 golden coefficients, and all 16 assignments of a four-variable cross-group design. | `r=Q=R=2`, one auxiliary copy per factor; not balanced asymptotic schedules and deliberately violates fast-regime `R<Q`. |
| Eq. (2.8)-(2.9), pp11-12; Lemma 3.2 and (3.10)-(3.11), pp23-25: restricted products and inclusion-exclusion recover jets | 60 CNFs: all 44 normalized nonempty one/two-clause multisets over two variables, all eight three-literal polarities, eight directed cross-group/unused-variable cases. Every jet is compared with direct polynomial deformation, recovered count with independent Boolean enumeration. | At most four variables, three clauses, width three. Uses direct clause indicators, not the complete parity-to-sparse-polynomial reduction. Attainable fibers are materialized, not the paper's polynomial-space iterator. |
| Hermite recovery, section 2.6 pp12-13; Appendix B.3 pp50-51: exact common denominators and fraction-free recovery | Fraction Gaussian elimination and LCM/Cramer/Bareiss both recover the count; 81 independent rational polynomials and 593 matrices checked against a permutation-sum determinant oracle. Exact-division assertions cover pivots and singular cases. | Small matrices only; no empirical verification of asymptotic coefficient/space bounds. |
| Sections 5.1-5.2 pp36-37: parity lift and total-degree truncation | `ell=1..5`, lift reconstruction by monic division; values -8..8; all 1,290 tuples of lengths 1..3 over -2..3. Compare truncated product modulo `2^ell` with direct all-even predicate. | Scalar integer specialization; not integrated sparse reduction or full counting pipeline. |
| Sections 6.1-6.4 pp40-44: exhaustive expander/code search and greedy common-zero queries | Static dependency/cost audit only; candidate search invokes the previously constructed counter, not an unavailable oracle. | No expander construction, exhaustive hash search or full sparsification execution. |
| D.9-D.10 pp60-61: tapered Hermite identity, local integrality and guarded precision | 3,132 valuation-node cases; 24 exact identities; 144 modular reconstructions; 8,424 local-integrality coefficients. Node domain `a=1..6`; reconstruction `a=1..3`, precision `p in {1,3,7}`, dense polynomials/Cauchy products, canonical and perturbed lifts. | Rational specialization: extension degree `e=1`, `V` degree zero. Not general extension-ring arithmetic or extension-field Lagrange interpolation. |
| D.12 p62: crossing modes have valuation at least `b+1`; total weight truncation | All crossing pairs for `a=1..3`, active nodes and `zeta in {-2,-1,0,1,2}`: 2,500 pair cases, 20,000 modular-tail checks at `p=1..8`, 17,500 divisibility checks; includes 500 zero-zeta pairs. | Scalar rational coefficients only. |
| D.16 p63: product of child sums equals weighted crossing expansion | 16 one-packet, two-group cases (`L=4`), comparing 592 weighted tuples with 4,096 direct Boolean assignments; four nontrivial tensor-table sets, fixed seed 20261027. | Not the 64-group packing, child-network construction at scale, recursive contraction or polynomial-space traversal. |
| D.5-D.6 pp58-64: admissible parameters, precision and bit/space recurrence | Static check of the stated domain and cutoff arithmetic above. With child wires `M/8` and scale parameter proportional to `lambda/2^d`, the leading per-level exponent shrinks by `sqrt(2)/8`, conditional on the stated premises. | Finite component tests do not certify those premises, the full recurrence, polynomial space or the theorem. |

Negative controls deliberately break input projection, Taylor data, parity
coefficients and total-degree truncation; the corresponding oracles reject
them. Removing guard precision or the alpha scaling is detected in all 24
tapered-recovery cases each. In the tensor test's 12 active cases, retaining
only zero modes is detected in 7, and changing `0^0` to zero in 4; neither
mutation is claimed to fail every input. These are test-sensitivity results,
not counterexamples to the paper.

Independent implementation review checked the projection, copy-back,
unconstrained fiber representative outside each local group, inclusion-
exclusion signs, degree/jet sufficiency and parity truncation against the
paper. No in-domain defect was found. This does not validate the unimplemented
stages or establish that the full claimed theorem is correct.

### Reproduction and resource scope

From repository root, with an existing Python interpreter:

```sh
flock -n research-heavy.lock timeout --signal=TERM --kill-after=5s 60s \
  python3 - <<'PY'
import resource
import unittest
cap = 512 * 1024 * 1024
soft, hard = resource.getrlimit(resource.RLIMIT_AS)
cap = min(x for x in (soft, hard, cap) if x != resource.RLIM_INFINITY)
resource.setrlimit(resource.RLIMIT_AS, (cap, hard))
suite = unittest.defaultTestLoader.discover(
    "tests/research", pattern="test_eth_*.py")
result = unittest.TextTestRunner(verbosity=2, failfast=True).run(suite)
raise SystemExit(not result.wasSuccessful())
PY
```

The actual final run used the existing `/usr/bin/python3` 3.13.5 under this
shared lock, 60-second watchdog and 512 MiB address-space cap: **16 tests
passed**, 6.05 seconds including discovery, peak RSS 13,568 KiB on that Linux
host. These numbers measure this small audit only, not either full algorithm
or PCC speed. This is a stdlib unittest qualification, not a full PCC pytest,
xdist or native CI gate. Default PCC test configuration is unchanged; the
modules run no audits at import. Raw receipts stay outside the repository.

## Next action and stopping condition

Keep the existing Stage1/mainline work first: consume its normal CI/artifact
results, distinguish host Stage1 costs from native compiler replay, and attribute
native merge subphases before choosing another optimization. Existing
[Stage1 module comparison][module-evidence] and
[worker-handoff research][schedule-evidence] already distinguish modeled
schedules from measured wall time; this paper changes neither result.

No production-algorithm experiment is proposed now because no genuine current
problem fit was found. Reopen only if a measured PCC hotspot actually requires bounded
Boolean constraint solving/exact counting, or an improved implementable
construction clears the parameter barrier. Before any experiment, specify the
encoding-size bound, semantics/witness checks, baseline algorithm, full
conversion/solve/reconstruction costs and the unchanged time/memory envelope.
A tiny brute-force comparison could check semantics, not asymptotic speed.

Only the bounded component tests above were run: no compiler/native runs,
dependency installations or PCC performance runs. No budget, timeout, GC/ownership requirement,
scheduler priority or production hook was changed. Native end-to-end gains,
bootstrap fixed point, five-GC qualification and full proof checking remain
unperformed, not passing.

[paper]: https://github.com/openai/math/blob/7ef5c0cc5ca4b0e072b7a050fca09ab24bc6fa3a/preprints/The-Exponential-Time-Hypothesis-is-False-October-25-2026/paper.pdf
[notice]: https://github.com/openai/math/blob/7ef5c0cc5ca4b0e072b7a050fca09ab24bc6fa3a/README.md
[lean-scope]: https://github.com/openai/math/blob/7ef5c0cc5ca4b0e072b7a050fca09ab24bc6fa3a/lean/docs/378.md
[lean-final]: https://github.com/openai/math/blob/7ef5c0cc5ca4b0e072b7a050fca09ab24bc6fa3a/lean/OAI/Algorithms/SubexponentialSAT/SqrtLogSpace/Final.lean
[comparator]: https://github.com/openai/math/blob/7ef5c0cc5ca4b0e072b7a050fca09ab24bc6fa3a/lean/ComparatorChallenges/SubexponentialSAT.json
[stage1]: https://github.com/ikshengmin/pcc/blob/218867de8e71686aef2f3617fc4d658bd1680942/scripts/run_pcc_stage1_build.py#L345-L405
[workers]: https://github.com/ikshengmin/pcc/blob/218867de8e71686aef2f3617fc4d658bd1680942/pcc/frontends/python/pipeline_frontend_workers.py#L884-L912
[choose]: https://github.com/ikshengmin/pcc/blob/218867de8e71686aef2f3617fc4d658bd1680942/pcc/frontends/python/worker_resource_plan.py#L479-L542
[prepare]: https://github.com/ikshengmin/pcc/blob/218867de8e71686aef2f3617fc4d658bd1680942/pcc/backend/macho_exec.py#L603-L741
[merge]: https://github.com/ikshengmin/pcc/blob/218867de8e71686aef2f3617fc4d658bd1680942/pcc/backend/macho_link.py#L1058-L1658
[stackmap]: https://github.com/ikshengmin/pcc/blob/218867de8e71686aef2f3617fc4d658bd1680942/pcc/backend/precise_stackmap.py#L1672-L1956
[allocator]: https://github.com/ikshengmin/pcc/blob/218867de8e71686aef2f3617fc4d658bd1680942/pcc/backend/self_backend_aarch64_darwin_regalloc.py#L1865-L2253
[module-evidence]: ../../scripts/research/README-stage1-module-comparison.md
[schedule-evidence]: ../../scripts/research/README-worker-handoff-schedule.md
[owned-passes]: https://github.com/ikshengmin/pcc/blob/218867de8e71686aef2f3617fc4d658bd1680942/pcc/frontends/python/compiled_owned_passes.py
[fold-experiment]: https://github.com/ikshengmin/pcc/blob/218867de8e71686aef2f3617fc4d658bd1680942/scripts/research/pcc_ttf_verified/compare_rewrite.py
[fold-evidence]: https://github.com/ikshengmin/pcc/blob/218867de8e71686aef2f3617fc4d658bd1680942/scripts/research/pcc_ttf_verified/VERIFIED_FOLD.md
[components]: ../../scripts/research/eth_exact_counting_components.py
[component-tests]: ../../tests/research/test_eth_exact_counting_components.py
[tapered]: ../../scripts/research/eth_tapered_hermite.py
[tapered-tests]: ../../tests/research/test_eth_tapered_hermite.py
[modes]: ../../scripts/research/eth_tensor_modes.py
[mode-tests]: ../../tests/research/test_eth_tensor_modes.py

