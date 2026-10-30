# Flow-inspired emitted-IR and TLA+ experiments

## What is new, and what is not

`flow_gc_ir_experiment.py` checks **real PCC host-emitted IR**, reusing
`RootSlotContract` and the owned parser/verifier. It adds a narrow shared-return
pinned-cleanup contract; it does not implement the paper's full type system or
replace the existing root analysis. The suffix-sharing optimization already
exists in the baseline. No production file or default pipeline is changed.

`flow_gc_tlc_check.py` runs actual **SANY and TLC**, rather than calling the
handwritten Python transition mirror. The existing TLA+ specification and its
positive/negative configs are unchanged. Neither experiment executes a native
program, a collector, a bootstrap stage, or CI.

## Real-IR boundary and controls

The source fixture uses `import threading`, dynamic nonterminal list splats,
early returns before later root registrations, and both object/void returns.
This reaches `_enter_container_temp_root` and the real shared pinned suffix;
ordinary constant lists could take a different lowering route.

The path is `parse_and_lift` -> `infer_module` -> `L1CodeGen.generate()` (which
includes text rendering) -> owned parse/verify -> indexed-kernel block
projection -> `RootSlotContract` plus the new suffix contract. No fake builder,
handwritten IR positive case, LLVM tool, or native runtime is used.

The contract checks exact per-return physical-slot sequences, current-slot
loads, unpin-before-clear, clearing the same slot, finite suffix chains, and
rooted return reload/clear/LIFO-leave order. The existing checker handles frame
balance and joins. Observations of production slot selection are kept separate
from the generated artifact; tests additionally assert source-derived expected
slot sequences so agreement alone is not the oracle.

Five mutations damage the **generated** IR: delete unpin, delete clear, reverse
their order, bypass a suffix, and route an early return through later cleanup.
All pass the ordinary owned verifier and existing frame-lifetime checker; all
are rejected by the incremental suffix contract. An empty fixture cannot pass.

The control uses the real `_emit_owned_local_cleanup(...,
shared_pinned_target=None)` branch, retaining shared frame exits and every other
lowering decision. Timed shared emission uses unmodified `L1CodeGen`; timed
inline emission has no observation hooks. Both must emit byte-identical text
to their corresponding observed/validated run. Warmups precede five alternating
samples. These are host frontend measurements, not native compiler timings.

## Observed results

Baseline: `85b0e08` on Linux x86-64, CPython **3.15.0rc1**, pytest **9.1.1**,
xdist **3.8.0**. The available interpreter differs from the repository's 3.15.0
pin. The initial thirteen focused tests passed with the default six workers,
loadgroup, and `-x`. After hardening config-boundary validation, all eight
TLC runner/parser tests passed (including five added cases); Java need not be
rerun for this fail-closed input check. Sources were verified against the baseline's Git blobs; the local
source closure was not a complete repository checkout.

The table combines the fixture's two functions, with two early returns per
new persistent root. Times are five-sample medians; S/I means shared/inline.

| Roots/function | Static calls S/I | Blocks S/I | Host emit ms S/I | Parse+verify ms S/I | Suffix-only check ms |
|---:|---:|---:|---:|---:|---:|
| 1 | 143 / 151 | 68 / 66 | 47.80 / 46.87 | 48.92 / 47.49 | 0.86 |
| 2 | 249 / 273 | 116 / 112 | 46.84 / 44.76 | 66.61 / 66.65 | 1.30 |
| 4 | 473 / 553 | 236 / 228 | 61.05 / 78.39 | 112.51 / 119.63 | 2.62 |
| 8 | 969 / 1257 | 572 / 556 | 119.90 / 108.03 | 228.86 / 254.77 | 6.23 |

For this corpus, sharing removes `4*S*(S+1)` static calls but adds `2*S` blocks.
At eight roots, static calls fall 22.9% and parser/verifier median time falls
10.2%. **Host emission is not consistently faster**: the largest case is about
11% slower, while the four-root case is faster. Host times are noisy (eight-root
shared range 86–327 ms, inline 91–204 ms), so these are exploratory observations,
not a stable speedup claim. Total existing-root-plus-suffix check medians are
2.13, 2.51, 4.65, and 15.59 ms; suffix-only timing excludes the frame walk.

Each executed return path still has the same unpin/clear calls; sharing adds
branches. Reduced emitted size may help later compiler work or instruction
cache behavior, but neither runtime benefit nor whole-compiler benefit has been
established. The checker is opt-in and its overhead is not inserted into the
production pipeline.

## Actual TLA+ checks

The experiment used the official stable
[TLA+ tools v1.7.4 release](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4),
TLC 2.19 / tool revision `5a47802`, on OpenJDK 21.0.12.1. The 2.2 MB standalone
jar was checked against the release checksum and kept outside the repository.
No global install is required; the runner never downloads dependencies. Before
starting Java, it parses and validates the actual positive/negative config
boundary (`MaxSources = 3`, `ObjectIds = {o1, o2}`, the complete invariant
list, and only the `UseBarrier` switch). Unsupported or changed configurations
are rejected, rather than being reported with stale bounds; the jar identity
is read from its actual manifest.

With one worker and a 256 MiB heap:

- SANY accepted the unchanged module's syntax and semantics.
- Positive TLC config: 14 initial states, **1,358 distinct states**, 3,616
  generated, zero queued, search depth 11, all seven invariants accepted.
- Barrier-disabled negative: exit 12, `NoLostLiveObjects` violation at depth 6.
  The witness is output scan -> field scan -> transfer -> source-root scan ->
  sweep, leaving a live identity unmarked. TLC stops on this counterexample;
  its 834 distinct states are **not** a full negative-state exploration.
- Current Python mirror exploration independently reports the same 1,358
  positive states. Its 3,588 nonstuttering transitions, 14 initial states and
  14 terminal `Done` self-loops account for TLC's 3,616 generated states.
  Matching counts and the witness are a cross-check, not a refinement proof.

Measured SANY/positive/negative wall times including JVM startup were roughly
1.24/3.55/2.14 seconds. They measure verification cost, not PCC performance.

The model assumes atomic transfers, sequential consistency, leaf referents,
an always-rooted output, and one marking cycle. It does not verify real locks,
memory ordering, relocation, finalizers, or any collector implementation.
The performance-relevant result is a constraint: conserving linear ownership
credits does **not** justify deleting the marking barrier.

## Reproduce without bootstrapping

Use an existing dependency-ready host interpreter and an already downloaded
official jar. Put logs, JSON output, and model-checker scratch outside the repo.
Set a common lock shared with any other research compilation/measurement.

```sh
export PCC_HOST_PYTHON=/absolute/path/to/existing/host/python
export PCC_RESEARCH_HEAVY_LOCK=/absolute/path/to/research-heavy.lock
export PYTHONPATH="$PWD"
export PYTHONDONTWRITEBYTECODE=1

flock "$PCC_RESEARCH_HEAVY_LOCK" timeout --kill-after=5s 90s \
  uv run --no-project --python "$PCC_HOST_PYTHON" python -m pytest -x -vv --tb=short \
  tests/python/test_flow_gc_ir_experiment.py tests/python/test_flow_gc_tlc_check.py

flock "$PCC_RESEARCH_HEAVY_LOCK" timeout --kill-after=5s 90s \
  uv run --no-project --python "$PCC_HOST_PYTHON" python \
  scripts/research/flow_gc_ir_experiment.py \
  --source-revision "$(git rev-parse HEAD)" --samples 5 --max-roots 8

uv run --no-project --python "$PCC_HOST_PYTHON" python \
  scripts/research/flow_gc_tlc_check.py --jar /absolute/path/to/tla2tools.jar \
  --lock "$PCC_RESEARCH_HEAVY_LOCK"
```

Do not interpret these commands as native execution, five-GC qualification,
Stage1/CI success, or permission to change collector/default compiler behavior.
