# Confined waitset buffers and shared action-key work

**Isolated research, not a production optimization.** Baseline:
`85b0e08cd28e143d87607a5cd76e8f7c350cabd7`. No runtime, compiler, ABI,
scheduler-root, GC, or dispatch file is changed. The tests exercise executable
host adapters and actual PCC metadata/code, not just a new design document.

## Stage1 exclusion: do not deploy this cache as a bootstrap optimization

The production gate was checked at `e8bfba85061651bcd014164b0ac07e74f1821266`.
Its `pipeline.py`, `pipeline_modes.py`, and `pipeline_frontend_parallel.py`
are byte-identical to the measured 5838 and original 85b owners. Existing Linux
artifacts for 45442f9 AArch64 and 5838 AArch64/x86-64 each record **484 complete
modules and 484 direct captures/emits**, with Python IR passes explicitly off.

For that route, actual `compile_parallel` bypasses cache planning/loading and
calls `compile_uncached(action_cache_plan=None)`. `route_probe.py` executes
this function with an inert compile boundary: all six cache callbacks remain
at zero, whereas the text-route positive control calls the planner once.
The actual no-op cache loader also returns before touching the action DAG.
Inside `compile_uncached`, `build_action_state` is separately guarded by
`action_cache_plan is not None`; the direct route cannot enter it.

**Thus ActionKeyScope saves 0 seconds / 0% on this observed Stage1 route.**
This is an unreachable-work conclusion, not a measured full-build CPU profile.
The existing compile wall times were 1,312.01 s (454 ARM), 1,366.32 s (5838 ARM),
and 3,021.45 s (5838 x86); artifact worker-duration area is not CPU time.
Do not extrapolate the isolated 22x key-subpath result into these builds.
A text-route cache-enabled workload remains a separate, unmeasured opportunity.

A misleading counter is now covered by a focused regression:
`multi_frontend_action_plan_actions` emits `len(src_paths) * 5` when
`action_plan is None`; **2,420 is not evidence of 2,420 constructed keys**.
Likewise cache-miss counters count pending modules, not executed key lookups.
No full-graph microbenchmark, native build, or production cache change follows
from this exclusion. The read-only probe accepts existing Linux ZIP artifacts
with `--linux-artifact` and never extracts or executes their contents.

The focused host regression passed **12 tests in 1.95 s** with the existing
3.15.0rc1 interpreter (not the repository's 3.15.0 pin). Using the variables
from Reproduction below, its actual isolation flags were:

```bash
flock -w 60 "$LOCK" timeout 30s env PCC_RESEARCH_BASELINE_SOURCE="$BASE" \
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  uv run --offline --no-project --python "$PY" "$PY" -m pytest \
  --noconftest -p xdist.plugin -x -n6 --dist loadgroup -q \
  tests/research/test_confined_jdk_route.py
```

## Sources and existing work

- [Confined Pools](https://inside.java/2026/10/23/confined-pools/) is dated
  October **23**, not October 5 (the supplied October 5 URL returned 404).
  Its arena exclusively owns checked-out memory; carrier pinning covers only
  cache handoff, not the arena's lifetime. Cleanup precedes clearing/return.
  Its small-allocation JMH gains are **not PCC results**.
- [JDK #31755](https://github.com/openjdk/jdk/pull/31755) avoids repeated
  automatic-module readability analysis and uses its existing cache more
  directly. Here the transferable mechanism is reuse within one immutable
  action graph, **not** assuming PCC imports form an all-to-all graph.
- [JDK #31610](https://github.com/openjdk/jdk/pull/31610) deduplicates and batches
  code-root additions by region and pre-sizes tables. This is not permission to
  delay PCC GC-root visibility; no root-registration change is tested here.
- [JDK #32446](https://github.com/openjdk/jdk/pull/32446) returns replaced dead
  C2 pinch-point nodes to a free list, preventing node exhaustion. It is not a
  general "fewer temporary nodes" result; no equivalent PCC exhaustion was
  established here, and no literal pinch-node port is claimed.

The earlier `352e64ee` note is explicitly a reconstruction, not evidence.
Current-source checks avoid duplicating optimizations already present:

- `freestanding_io_waitset._reserve_output` already reuses ready/timeout arrays.
  The remaining event scratch is allocated in `wait_prepare`, retained through
  `wait_block`, decoded in `wait_finish`, and freed by `wait_discard`.
- `py_virtual_thread_poll_io` already skips empty nonblocking polls and uses
  `pcc_vthread_wait_active_py` across the unlocked kernel wait. Therefore the
  empty-wait microtrace below is **not** an existing scheduler hot-path tax.
- AArch64 regalloc already has bounded memoization and Tarjan summaries;
  this experiment does not rerun or rediscover those changes.

## Executed comparisons

### Event scratch: counted calls, no proven speed gain

`waitset.py` AST-loads the actual baseline functions. The candidate substitutes
only event acquisition and release, with a single waitset-owned idle buffer.
Bounds-checked byte arrays replace raw memory and deterministic callbacks replace
kernel syscalls. Registration, stale-generation filtering, timeout arbitration,
event decoding, and the existing output-array allocator execute the real source.

For 100 sequential waits, event-allocation calls fall **100 -> 1** in all four
traces. This counts real source-path calls into the host allocator adapter, not
native `malloc` observations. Concurrent/reentrant borrowers get distinct buffers.

| Trace | Allocation bytes requested, baseline -> pooled | Idle retained before dispose |
|---|---:|---:|
| epoll, empty low-level wait | 2,400 -> 24 | 24 B |
| epoll, 8 ready fds per wait | 10,800 -> 108 | 108 B |
| epoll, 256 quiet fds | 308,400 -> 3,084 | 3,084 B |
| kqueue, 256 quiet fds | 1,641,600 -> 16,416 | 16,416 B |

Dispose releases all retained memory. A 64-byte pool is not a drop-in fit:
epoll uses 24..3,084 bytes and kqueue 96..16,416 bytes. The initial wall-time
observations were noisy and did **not** establish a speed benefit; candidate
lock/zeroing overhead and retention are real negative findings in this adapter.
Python byte-array clearing itself creates temporaries, unlike native `memset`.
The CPU-calibrated batch was similarly inconclusive: paired ratios ranged
0.22..3.51 for ready8 (its median even reversed the first batch). No favorable
batch is selected as a performance conclusion. Separate tracemalloc peaks for
epoll quiet256 were 23,261 -> 30,060 B; kqueue quiet256 42,733 -> 75,399 B.

Ownership checks cover cancellation before backend-return acknowledgement,
prepared-scope exceptions, allocation failure, error/EINTR cleanup, reentry,
simultaneous pool borrowers, wrong owner, a logical task continuing on another
actual host thread, expired leases, zeroing, and independent copy-out results.
This is **not** native scheduler or five-GC qualification. In particular, the
production scheduler's failed mutex-reacquisition path currently discards without
holding that mutex; a future cache return must safely handle that path too.

### Action-key work: actual exported metadata, exact keys

`metadata.py` uses the existing PCC host parser/exporter and
`_build_python_frontend_action_state`, not a synthetic dense graph. The bounded
input is 21 real PCC frontend modules, 48 in-bundle dependency edges and 770,746
bytes of action metadata. Dependencies outside this selected bundle are not a
claim about the full compiler graph. Identity markers explicitly say no runtime
was built. Input source bytes were checked against the baseline tree.

`ActionKeyScope` keeps full sorted dependency facts and unchanged key material.
Its cache dies with one graph scope. A new scope is required for each changed
graph. Baseline `action_key` calls and candidate keys are byte-for-byte equal.

| Work per graph | Baseline -> scoped |
|---|---:|
| Five-stage rebuild keys | 105 -> 105 (identical) |
| Summary digest calls | 515 -> 21 |
| Summary bytes serialized for hashing | 22,909,620 -> 761,976 |
| Dependency visits / edges examined | 410 / 645 -> 82 / 129 |
| All 21 module-IR keys: digest calls | 103 -> 21 |
| All 21 module-IR keys: bytes hashed | 4,581,924 -> 761,976 |

The five-stage shape corresponds to the actual global-identity rebuild plan;
it must not be described as the frontend's first cold-cache path. Both eager
and lazy strategies remain runnable. **Negative control:** eager hashing of all
21 modules made one leaf lookup much worse (one summary -> 21). Lazy hashing
restores one summary/31,315 bytes for that case, with no traversal reduction.
No invalidation field, transitive dependency or stage input is omitted.

Five alternating pairs, five independent graph scopes per timing observation:

| Host action-key subpath | Baseline / lazy median CPU per graph | Paired CPU ratio, median [range] |
|---|---:|---:|
| 105 rebuild keys | 130.78 / 5.83 ms | 22.47 [21.31, 24.09] |
| 21 module-IR keys | 26.76 / 4.49 ms | 6.07 [4.42, 7.67] |
| One leaf key | 0.214 / 0.225 ms | 0.95 [0.69, 1.10] |

These ratios are **host subpath observations**, not compiler/native speedups.
Lazy single-leaf work is essentially parity/slightly worse, not a new win.
The eager single-leaf negative control remained severe: 0.034 [0.032, 0.052].
Separate tracemalloc peaks: rebuild 292,981 -> 282,809 B; module-IR 284,757 ->
281,453 B; single leaf 73,909 -> 74,405 B (eager 282,661 B). Whole-run Linux
high-water RSS was 25,596 KiB; it is not attributable to individual arms.
Earlier eager/lazy and wall-only observations were retained outside the repo;
CPU instrumentation was added because initial wall-time dispersion was large,
not to replace unfavorable observations.

## Reproduce

Use an existing host environment with pytest/xdist. The recorded environment was
Linux x86-64, CPython **3.15.0rc1** (not the repository's `3.15.0` pin), pytest
9.1.1 and xdist 3.8.0. No new software was installed. Put output **outside** the
repository; the metadata/wire, samples and logs are not source deliverables.
The focused suite passed **26 tests**. The first `uv` inventory attempt hit a
read-only default HOME cache; a workspace `UV_CACHE_DIR` resolved that without
changing permissions. Supply it likewise if the default cache is not writable.

The actually executed pytest command ran in the isolated candidate (which had
no `conftest.py`); xdist was already installed and auto-loaded. It did **not**
use `--noconftest` or an explicit plugin flag. The existing interpreter was
`/workspace/scratch/ce254c0a0910/pcc-store-buffer-drain-recovery-52bd/test-venv/bin/python`.
Use an equivalent existing interpreter as `PY`, without installing or resolving
dependencies:

```bash
PY=/absolute/path/to/existing/test-environment/bin/python
BASE=/absolute/path/to/85b0/source
OUT=/absolute/path/to/new/outside-repo-directory
LOCK=/absolute/path/to/shared/research-heavy.lock
mkdir -p "$OUT"
export UV_CACHE_DIR="$OUT/uv-cache" UV_PYTHON_DOWNLOADS=never
PCC_RESEARCH_BASELINE_SOURCE="$BASE" \
  uv run --offline --no-project --python "$PY" "$PY" -m pytest \
  -x -n6 --dist loadgroup -q \
  tests/research/test_confined_jdk_waitset.py tests/research/test_confined_jdk_closures.py
flock "$LOCK" timeout 120s uv run --offline --no-project --python "$PY" "$PY" \
  scripts/research/confined_jdk/metadata.py \
  --source-root "$BASE" --output "$OUT/action-metadata.json"
flock "$LOCK" timeout 180s uv run --offline --no-project --python "$PY" "$PY" \
  scripts/research/confined_jdk/run.py \
  --source-root "$BASE" --metadata "$OUT/action-metadata.json" \
  --iterations 100 --pairs 5 --action-rounds 5 > "$OUT/observations.json"
```

For a checkout-root test invocation, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` together
with `--noconftest -p xdist.plugin` is an isolation adaptation, **not the command
executed for the reported result**. No re-test was performed for that adaptation.
The final metadata-writer hardening binds its marker to selected source content
instead of hardcoding a revision label; the recorded measurements used the
independently verified 85b0 inputs. The final published writer was subsequently
executed against the same frozen inputs, without rerunning the timing campaign:
21 modules / 48 edges were produced, the 728,369-byte export wire was byte-equal
to the earlier artifact, and graph state was equal except for the intentionally
changed compiler marker. Independently recomputing the selected-content marker
matched. Unit controls now exercise stable and changed source bytes and refusal
to overwrite prior evidence. This closes the writer-validation gap, not the
missing native or full-compiler performance evidence.

The bounded Linux runner reduces its inherited address-space limit to at most
512 MiB. Pairs alternate baseline/candidate order; warmups are excluded; graph
scope construction is included; tracing/count wrappers run separately from
timing. It reports wall/process CPU time, paired ranges, adapter allocation
counts, tracemalloc peaks and whole-process high-water RSS (not per-arm RSS).
There is no native throughput, p99, bootstrap, CI, full-graph memory-scaling or
end-to-end compiler improvement claim. Native safety/benefit and the action-key
owner's share of cache-enabled text-route compilation remain unverified;
nothing is auto-enabled.

## Retention policy and unproven lease boundary

`policy.py` is a new **host-only alternative**, not a production integration.
Its aggregate idle-byte budget drops oversized returns and excess cached buffers;
`trim_idle()` models an explicit memory-pressure signal without freeing active
leases. The budget does not bound active buffers, output scratch, bookkeeping,
RSS, or total process memory. The chosen 128-byte test budget is a control, not
a tuned recommendation.

A new actual-source waitset control registers two descriptors, prepares/discards,
removes both, and prepares/discards again. With kqueue geometry, the original
policy retains 160 bytes after the peak; the bounded policy retains 96 bytes,
but requires **two event allocations instead of one**. Epoll's 36-byte highwater
already fits and remains one allocation / 36 retained bytes. The 16,416-byte
outlier is separately exercised directly at the pool boundary. No throughput,
p99 latency, native allocator, or five-GC improvement follows from these counts.

Tests also cover full-capacity clearing before reuse, allocation-free rejection
of invalid budgets, aggregate budget enforcement, pressure trim, active-lease
close rejection, duplicate return before reuse, concurrent returns, and a
concurrent close/return race. A deliberate negative control shows that a raw
`release(pointer)` API **cannot distinguish a stale return once the pointer is
reused**. The existing `Batch.phase` wrapper rejects stale handles, but that is
not native ABA protection. Before native reuse, prove linear discard and no
escaped raw aliases, or add a real generation-bearing lease contract.

The two new metadata tests, thirteen policy controls, and eight record/lifetime
controls originally passed **23 tests, exit 0**, under `-x -n0`. That serial run
was not a default-six-worker qualification: all case bodies passed with six
workers, but execnet teardown failed with `can't start new thread`.

A bounded diagnostic reproduced that controller-only failure with the same
512 MiB address-space limit. At failure it had 516,352 KiB virtual memory but
only 41,484 KiB resident memory; six approximately 64 MiB malloc arena mappings
consumed most of the reservation. The correction is confined to the host test
launcher: child-scoped `MALLOC_ARENA_MAX=2` reduces allocator arena reservation.
The memory limit, six workers, and 60-second watchdog are unchanged. No global
setting, installed pytest/execnet code, production collector, or runtime changed.
This allocator configuration is part of the receipt, not a performance claim.

The corrected policy test now uses the checkout root when
`PCC_RESEARCH_BASELINE_SOURCE` is absent, matching the existing waitset tests;
an explicit source override remains supported. An initial assertion also
mistook successful `waitset_remove`'s return value 1 for 0 and was corrected
against source. The earlier 256-descriptor removal trace exceeded the short
watchdog; the final control uses two descriptors rather than extending it.

Final host validation: **69 passed in 8.30 s, exit 0**: these 23 controls plus
46 source-identity, verified-comparison, and semantic-witness controls. It used
the actual root/tests conftests and default `-n 6 --dist=loadgroup` configuration
from a verified a48 source context, not `--noconftest`; the waitset source was
byte-identical to frozen85b. The source override for these 23 tests was unset,
so the checkout fallback and real two-thread pool cases executed. This selected
host suite is not a whole-repository, native, five-GC, or performance gate.
CPython was still 3.15.0rc1, not the repository's final 3.15.0 pin.

Reproduction from a checkout root with the selected research files and existing
pytest/xdist interpreter, without provisioning:

```bash
export PCC_RESEARCH_PYTHON="$PY" PCC_RESEARCH_LOCK="$LOCK"
# For the source-copy controls in the same invocation:
export PCC_RESEARCH_SOURCE="$PWD"
env -u PCC_RESEARCH_BASELINE_SOURCE PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  scripts/research/run_bounded_host_pytest.sh -p xdist.plugin -vv --tb=short \
  tests/research/test_confined_jdk_metadata.py \
  tests/research/test_confined_jdk_policy.py \
  experimental/record_scope_lifetime/test_escape_boundaries.py \
  tests/research/test_pcc_research_source_identity.py \
  tests/research/test_pcc_research_evidence_controls.py \
  tests/research/test_pcc_ttf_semantic_cross_type.py \
  tests/research/test_pcc_ttf_verified.py \
  tests/research/test_pcc_verified_compare.py
```

The launcher acquires the shared absolute lock nonblocking, uses the existing
interpreter through `uv run --offline --no-project`, never raises an inherited
address-space limit, and returns pytest/timeout failure rather than masking it.
Its allocator limit is Linux/glibc-specific host-validation configuration.

The next useful native comparison is burst-to-idle and repeated grow/shrink,
including retained-byte distribution, actual allocator calls, steady throughput,
and p50/p99 wait latency. Keep identical event/generation results, cancellation
acknowledgement, lock-reacquisition failure, and all five collector contracts.
The host lock and zeroed bytearray adapter do not qualify those boundaries.
