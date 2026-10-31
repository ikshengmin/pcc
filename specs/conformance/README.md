# Official Go source adapter

`scripts/go_conformance.py` adds a deliberately bounded adapter to the existing
conformance ledger. It reuses `scripts/cpython_conformance/run.py::run_process`
and `tests/go/owned_execution.py`; it is not a second general-purpose test runner.

## Inputs and provenance

`suites/go1.27.3-representative.json` selects five **unchanged** upstream files:

| Original source | Recipe | Purpose |
|---|---|---|
| `test/fixedbugs/bug065.go` | run | parallel short declarations / blank assignments |
| `test/fixedbugs/bug115.go` | compile | unsigned complement and function argument |
| `test/fixedbugs/bug182.go` | errorcheck | reject a non-boolean `if` condition at its annotated line |
| `test/ken/for.go` | run | loop arithmetic with original panic assertion |
| `test/helloworld.go` | run | original print and `test/helloworld.out` expectation |

The manifest pins release `go1.27.3`, upstream commit
`2aae1000ea1d4b28923b96db28acf4e3b99d6aff`, byte lengths, SHA-256 hashes,
copyright attribution, BSD-3-Clause license identity, and any original `.out`
fixture. Source files are **not vendored or rewritten** by this adapter. Obtain
the matching original tree separately; never add a replacement `main` or remove
an assertion to make a case pass.

Primary sources:
[official release metadata](https://go.dev/dl/?mode=json),
[pinned test directory](https://go.googlesource.com/go/+/2aae1000ea1d4b28923b96db28acf4e3b99d6aff/test/),
[pinned driver](https://go.googlesource.com/go/+/2aae1000ea1d4b28923b96db28acf4e3b99d6aff/src/cmd/internal/testdir/testdir_test.go),
[license](https://go.dev/LICENSE).

An installed Go compiler is not proof that its distribution includes `test/`.
`inventory` compares every local `test/` file to the pinned release archive,
without extracting another tree. It uses the pinned official driver's directory
selection rather than miscounting `.dir` helper sources as independent tests.
Its complete-inventory claim applies only to that release-distributed `test/`
tree. A separate `src/**/*_test.go` count excludes `cmd` and remains explicitly
partial: files are not packages, functions, successful executions, or coverage.

## Commands

Run inventory without executing any compiler:

```sh
uv run python scripts/go_conformance.py inventory \
  --go-src "$GO_SOURCE_ROOT" --archive "$GO_RELEASE_ARCHIVE" \
  --out "$EXTERNAL_OUTPUT/inventory.json"
```

The first execution owner is **host pcc0 / self / Linux x86-64** only. Scalar
tests do not exercise shared-runtime GC; the output labels GC `not_exercised`.
This does not certify pcc1, another platform, or any of the five collectors.
Use the repository's process-tree watchdog/admission procedure around the run,
with the agreed locks, RSS cap, disk floor, and 90-second outer limit:

```sh
uv run python scripts/go_conformance.py run \
  --manifest specs/conformance/suites/go1.27.3-representative.json \
  --go-src "$GO_SOURCE_ROOT" --go "$GO_REFERENCE_COMPILER" \
  --reference-cache "$BOUNDED_EXISTING_GO_CACHE" \
  --pcc-source "$FROZEN_PCC_SOURCE" --compiler-revision "$ACTUAL_PCC_REVISION" \
  --out "$EXTERNAL_OUTPUT/new-shard" --timeout 6
```

At most six selected files run sequentially, with at most six seconds per
process. Missing tools, mismatched versions, signals, timeouts, and compiler
worker crashes are explicit non-passes. Output must be outside both source
trees. Raw stdout, stderr, exit statuses, original input bytes, and tool/compiler
identities remain there; no raw logs or local paths belong in this repository.
PCC compilation forbids subprocesses and external LLVM imports. Official Go
is an explicitly labeled development oracle, never a production fallback.

## Meaning of a result

`run` requires the official expectation and raw PCC/Go stdout, stderr, and exit
agreement. The original optional `.out` absence means empty expected output.
Combined nonempty stdout and stderr are currently unsupported because separate
captures cannot establish interleaving. `compile` requires actual owned object
emission and successful official compilation; it does not assert execution.

`errorcheck` is **not** “both compilers rejected.” It requires each original
line/regexp annotation, with no unrelated diagnostics. Required adapter checks
also gate the common ledger's PASS decision. A PCC wording mismatch is a strict
failure, not silently normalized equivalence. Capability diagnostics cannot
count as expected semantic rejections. No new baseline is generated or updated
by these commands.

The common `passed_allowlist` exporter also **rejects any suite containing
`required_checks`**. An adapter's passed boolean is not retained assertion proof,
and this adapter's process receipts are not a recognized baseline-export proof
format. Export remains closed until an independent assertion-proof replay path
exists; ordinary suites without adapter-required checks retain their existing
export behavior. This does not change the adapter's observed summary results.

Only plain `run`, `compile`, and `errorcheck` recipes are currently admitted.
Flags, arguments, build constraints, compiler pragmas, imports requiring an
import graph, directory tests, generated tests, assembly checks, special error
contexts, and nontrivial RE2 patterns remain explicit unsupported outcomes.
The parser never silently drops them. Raw message-only compiler diagnostics
are not invented stable codes. Summaries separate harness limitations,
frontend capability gaps, runtime/library dependencies, missing tools, and
other failures; these categories must not be merged into feature priorities.

Full `test/` execution and standard-library package execution require a separate
budgeted shard plan and additional recipe/import-graph support. Inventory or
parser admission alone is never a full-suite pass.
