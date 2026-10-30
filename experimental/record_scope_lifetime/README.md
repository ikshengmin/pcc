# Record scopes and lifetime costs — isolated experiment

**Result: fewer small environment containers, but no demonstrated frontend
speedup. Do not enable in production from this evidence.**

## Question and source

Baseline: `85b0e08cd28e143d87607a5cd76e8f7c350cabd7`, specifically
`pcc/frontends/python/type_infer.py::_Scope`, `_infer_expr`, and
`_narrow_scope_for_isinstance`. The reference already links parent environments;
it does **not** copy the full environment on scope entry.

[Record Types Everywhere, §§2.2–2.3 and 4](https://arxiv.org/html/2610.27519v1)
uses records for environments, shadowing, and stores; it does not establish a
PCC performance result. This experiment borrows that representation perspective,
not the paper's complete calculus or mechanized soundness proof.
[Mojo's lifetime/origin documentation](https://mojolang.org/docs/manual/values/lifetimes/)
motivates separating the record owner, borrow validity, and mutation rights.
The prior reconstruction `352e64ee8019142e37f133e0e08e4098ac3c944e` was a
proposal, not existing validation.

## Executable design

`RecordScope` inlines zero/one field and spills to a dict on a second distinct
name. Class-object marks allocate a set only when needed. The current direct
`bindings.get` / membership interface remains supported. Empty scopes must stay
truthy: real `_infer_expr` walks them with `while scope`.

The independent `Region` / `Borrow` contract checks owner generation, close,
move, and thread boundaries. A live `Capture` retains the parent graph and
late-bound aliases; a snapshot explicitly copies visible bindings. It is not
legal to replace a live closure capture with a snapshot. Mutable payloads are
not cloned, and owning a reference does not establish exclusive mutation.

These are **dynamic host-side contract checks, not a compiler lifetime pass**.
The inference experiment uses the unchecked record representation, temporarily
swapping `_Scope` in an isolated process and restoring it even on exceptions.
That global swap is not a thread-safe production integration. The checked
borrow microbenchmark separately measures the runtime guard cost.

## Observed results

Linux x86-64, CPython **3.15.0rc1** (not the repository's 3.15.0 pin), pytest
9.1.1 / xdist 3.8. Shared research lock; seven alternating-order samples, eight
inferences per sample. Parser setup, inventory, and tracemalloc are outside the
inference timer. Actual-source inputs are standalone inference without external
export preloads, not the complete compiler pipeline.

| Host inference input | Scope dicts, reference → record | Class sets | Time ratio, record/reference | Peak traced bytes |
|---|---:|---:|---:|---:|
| 16 functions × 8 read-only narrowings | 273 → 17 | 273 → 0 | 1.012 | 201,176 → 200,392 |
| Same, writing another local | 273 → 145 | 273 → 0 | 1.005 | 229,896 → 229,296 |
| 16 functions × 24 locals | 17 → 17 | 17 → 0 | 0.984 | 295,216 → 294,816 |
| Actual `export_meta.py` | 99 → 14 | 99 → 0 | 0.995 | 150,660 → 150,260 |
| Actual `raw_pointer_types.py` | 39 → 4 | 39 → 0 | 1.015 | 124,551 → 124,151 |

The timing spread is much larger than these frontend differences. **No
frontend speedup is established.** Peak inference memory improves by less than
0.4%, despite substantially fewer cumulative container allocations: most scopes
are short-lived. These are logical container-constructor counts and traced
Python bytes, not malloc counts, RSS, or native retain counts.

For 4,000 simultaneously retained records, zero/one/eight-field memory was
1,377,752→321,368 / 2,045,748→509,364 / 3,713,868→2,913,484 bytes. Their time
ratios were 0.856 / 0.756 / **1.138**: the wide-record case was slower. This
microbenchmark deliberately retains every record; it is not frontend peak RSS.
The separate dynamic-origin lookup loop was **2.59× slower** than unchecked
record lookup. Runtime checking is not free; a future optimization would need
a genuinely proven static lifetime boundary, not merely omission of guards.

Full-environment copies were zero in both variants. Retained local fields and
parent edges stayed equal. Each spilled record adds one first-field transfer
(17 in the read-only fixture). A CPython `sys.getrefcount` check also confirms
the same live payload reference count in one/eight-field records and complete
release on destruction. This measures live references, not retain/release
traffic. There is no native retain/root elimination.

## Validation and limits

Tests execute the actual native-parser implementation under CPython, then real
`infer_module`; six fixtures also run the owned `L1CodeGen` IR builder. AST and
IR text are identical for narrowing, class shadowing, late-bound closures,
comprehensions, exception/finally, and generators. Additional differential tests
infer the two actual compiler source files above.

Lifetime tests cover stale generations, moves, exceptional exits, invalid
cross-origin parents, thread crossing, escaped live captures, shared aliases,
snapshot semantics, and releasing the final owner. Fault injection demonstrates
that a false-empty-scope mutant loses a real inferred type and that removing
origin checks admits a stale borrow. Initial fixture-count assertions failed
and were corrected after inspecting actual scope fields: parameters also bind
their recursive function name; each condition creates an empty scope; branch
assignments widen narrowed scopes.

**Not run/proven:** native emitted execution, full Stage1, stage2/3 fixed point,
five-GC runtime qualification, moving-address safety, finalizer/resurrection
behavior under PCC, automatic escape/await/yield analysis, or a threaded
frontend integration. No production file, runtime option, collector, or thread
safepoint setting changes. Generator IR equality is not suspension-lifetime
verification.

## Reproduce

From a checkout containing this experiment, set `PY` to an already provisioned
interpreter with pytest/xdist, and `LOCK` to the shared workspace research lock.
No dependency installation or compiler/runtime build is required.

```sh
export PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PCC_DISABLE_ROADMAP_DEEPWIRE=1 PCC_NO_AUTO_PCC1=1
export PCC_TEST_NO_NATIVE_PROVISIONING=1 UV_PYTHON_DOWNLOADS=never
export PYTHONPATH="$PWD"
flock "$LOCK" timeout -k 3s 60s uv run --offline --no-project --python "$PY" \
  "$PY" -m pytest -x -vv --tb=short --noconftest -p xdist.plugin \
  -n 6 --dist=loadgroup experimental/record_scope_lifetime
flock "$LOCK" timeout -k 3s 60s uv run --offline --no-project --python "$PY" \
  "$PY" -m experimental.record_scope_lifetime.benchmark
flock "$LOCK" timeout -k 3s 60s uv run --offline --no-project --python "$PY" \
  "$PY" -m experimental.record_scope_lifetime.benchmark --skip-micro \
  --source pcc/frontends/python/export_meta.py \
  --source pcc/frontends/python/raw_pointer_types.py
```

Do not turn the experimental record into a production default on the strength
of its allocation microbenchmark. The measured next question is whether a
real hot owner retains many narrow environments; these frontend runs do not.

## Additional lifetime-boundary controls

`test_escape_boundaries.py` separates dynamic handle validity from a static
origin proof. An ordinary mutable value obtained through `Borrow.lookup` remains
usable after `Region.close`; a closed stale handle still retains its graph until
that handle is dropped; moving one handle does not revoke another live capture
or establish exclusive mutation; and a suspended generator keeps its origin
live until cleanup. These passing negative controls expose the prototype's
limits. They do **not** show that Mojo's compile-time origin analysis is useless.

The actual PCC integration boundary is native ownership lowering, not `_Scope`:
`L1CodeGenMixinStack` uses `OwnershipLoweringMixin` to emit retain/release and
frame-root operations. Assignment lowering explicitly keeps borrowed managed
locals in updateable roots across allocations. Return lowering promotes borrowed
values when needed, and generator spills carry actual ownership flags across
suspension. A static optimization must prove alias/escape and cleanup conditions
without removing roots required by moving/tracing collectors.

`native_lifetime_cases.py` prepares four emitted-execution cases: mutable aliases
with late-bound closure capture; exception/finally escape; generator suspension
and close; and weakref/finalizer/resurrection. Their CPython reference executions
pass. **No PCC emitted execution of these new cases, collector qualification,
static lifetime pass, native retain reduction, or end-to-end benefit is claimed.**
All five collectors must eventually run the same cases on identity-verified
native artifacts before treating an optimization as qualified.

The eight new controls first passed in a serial 23-test host diagnostic; that
was not a default-worker qualification. The later bounded host validation in
`scripts/research/confined_jdk/README.md` passed **69 selected tests in 8.30 s,
exit 0**, including these eight controls, with the real repository conftests and
six-worker default. Child-scoped `MALLOC_ARENA_MAX=2` reduced glibc arena virtual
reservation and avoided the reproduced execnet teardown exhaustion while
preserving the 512 MiB address-space cap and 60-second watchdog. No tests were
skipped, no native/five-GC qualification is claimed, no production file changed,
and the earlier frontend timing campaign was not repeated.
