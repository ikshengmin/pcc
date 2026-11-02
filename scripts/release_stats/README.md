# Release statistics

`run.py` measures, for one release, how much Python pcc runs natively (no
libpython, no cc/c++/rustc/javac/go) and how fast, and renders the README
section "Compatibility and speed, measured" with its charts. Run it for every
release; nothing in the section is copied by hand.

```bash
OUT=~/pcc-release-stats/<release>
uv run python scripts/release_stats/run.py cpython pyperformance micro packages languages \
    --out $OUT --cpython-src ~/pcc-conformance/cpython-v3.15.0
uv run python scripts/release_stats/run.py report --out $OUT --update-readme
```

`--out` must be outside the repository; each stage writes `<out>/<stage>.json`
and keeps its raw material (logs, per-item results, downloaded sources) under
`<out>/<stage>/`. `--resume` keeps per-item results already written. A tree
exported without Git metadata names its commit with `--pcc-revision`.

| Stage | Measures | Pass rule | Time |
|---|---|---|---|
| `cpython` | CPython v3.15.0 `Lib/test`, each module compiled by pcc (`scripts/cpython_conformance`) | a case CPython passes also passes natively | ~2 h |
| `pyperformance` | pyperformance 1.14.0 (pinned SHA-256), programs unchanged, `pyperf_shim.py` as `pyperf` on both sides | exit 0, same benchmarks and result hashes as CPython; speedup = CPython s / pcc s | ~2 h |
| `micro` | `tests/benchmarks/threading/programs` single-thread programs | same printed fields as CPython | minutes |
| `packages` | top 100 PyPI projects (hugovk.dev previous-calendar-month list, snapshot/hash retained): wheels-only install, CPython import baseline, `import <module>` compiled and run by pcc; wheel shapes and unverified source-language filename hints reported separately | the native program prints `import ok` | ~1-3 h |
| `languages` | existing C suites; ordinary/integration pytest checks; separately selected official-source adapter results | C expectations; junit outcomes separately; official raw reference streams/exit and upstream assertions | selected lanes |
| `report` | README section, `docs/images/release-stats/*.svg` | | seconds |

Rules the numbers keep:

- Denominators include everything attempted. A benchmark or project that does
  not compile, crashes, times out or exceeds `--rss-limit-gb` is counted and
  listed with its first error, never dropped from the denominator.
- The pyperformance geometric mean covers only benchmarks that ran and matched
  CPython; every other benchmark is listed by status (needs third-party
  packages, compile failed, ...) next to it.
- The timed stages (`pyperformance`, `micro`) hold `build/.pcc-performance.lock`;
  keep the machine otherwise idle and compare releases on the same machine.
- Package test suites are not run yet. The official Go adapter covers a tiny
  pinned shard, not the entire Go suite. Official C++/Rust/Java adapters remain
  explicitly missing; authored tests never become official-suite counts.

## Package evidence boundaries

The ranking provider's [ClickHouse query](https://github.com/hugovk/top-pypi-packages/blob/main/clickhouse.py)
uses the previous calendar month, not a rolling 30-day window. Results retain
the snapshot date, source, month and SHA-256 of the saved snapshot. A historical
snapshot without that provider/date evidence keeps its ranking window unknown.

`universal_wheel_projects` and `platform_wheel_projects` describe published
wheel filename tags; their memberships can overlap. Platform wheels do not
establish whether an artifact is a Python extension or a standalone CLI. A
universal wheel does not rule out an optional native build or native dependencies.

`source_language_hints` counts filename indicators only, not actual native
build requirements. Documentation, tests, headers and vendored examples can
match; generated C/C++ and Cython's annotated `.py` inputs can be missed. No
package-name rules or manually researched counts are encoded. `native_build`
and `native_artifact_kind` stay unknown, including when no suffix matches.
Independent, pinned source/build-configuration research is separate evidence,
not a rule that promotes future filenames into verified build languages.

Before inspecting an sdist, the scanner verifies both downloaded and cached
bytes against PyPI's SHA-256. Missing/invalid hashes and mismatches produce an
explicit `source-integrity-failed` result; mismatched bytes are retained, not
deleted or silently downloaded again. Downloads and eligible archive file
sizes are bounded by the source-size cap (not expanded archive size). A
verified digest establishes artifact identity, **not** the
truth of a language inference. Legacy resumed scans remain unverified, and
legacy report fields are normalized to filename hints in Markdown, SVG and
`report/summary.json`; original stage inputs are not rewritten. Neither scanning
nor these statistics proves a package's native build or transitive dependency
closure. The historical chart filename `native-languages.svg` is retained for
compatibility; its title and labels now state the unverified-hint boundary.

## Bounded official-language lane

Use `--official-only` to avoid running the existing C suites or pytest lanes.
By default this performs no compiler execution: the five Go obligations stay
UNRUN, and missing adapters remain visible. Import retained observations with:

```sh
uv run python scripts/release_stats/run.py languages --out "$OUT" --official-only \
  --official-go-run "$RETAINED_GO_RUN" \
  --official-go-inventory "$VERIFIED_GO_INVENTORY_JSON"
uv run python scripts/release_stats/run.py report --out "$OUT"
```

The inventory is the existing `scripts/go_conformance.py inventory` output,
not a pytest count. Its pinned version/revision/archive/license and selection
hashes must agree. Without it the complete denominator and unrun remainder
are **unknown**, never five or zero. With it the report states the declared
driver-test inventory and unobserved remainder; standard-library tests are
outside that inventory. Missing/malformed raw artifacts do not pass.

Explicit execution uses the existing adapter, not another compiler path:

```sh
uv run python scripts/release_stats/run.py languages --out "$FRESH_OUT" --official-only \
  --official-selection fast --official-go-execute \
  --official-go-src "$GO_SOURCE_ROOT" --official-go-tool "$GO_REFERENCE_COMPILER" \
  --official-go-reference-cache "$EXISTING_BOUNDED_CACHE" \
  --official-pcc-source "$CLEAN_PCC_CHECKOUT_AT_REPORT_REVISION"
```

No download or installation is performed. The source/license hashes and
reference version are locked by
[`go1.27.3-representative.json`](../../specs/conformance/suites/go1.27.3-representative.json)
and the [existing adapter](../../specs/conformance/README.md). `fast` selects
exactly its three run-pass, one compile-only and one expected-rejection inputs.
The adapter runs sequentially with six seconds per process inside the existing
process-tree watchdog's **90-second / 1-GiB** cap. Existing pytest six-worker
defaults and other language guards are unchanged. Coordinate heavy runs using
the repository's existing admission/serialization procedure.

`full` is an explicit selection interface, but currently reports **unsupported**
without launching the tiny adapter. It is not an alias for `fast`, an automatic
download, or authorization to run a full suite. Workflow owners can consume
this interface without this change editing any workflow.

The report re-reads retained source bytes, stdout, stderr, process exits and
upstream assertions. It separates actual executed programs and raw-reference
matches from compile-only/rejection passes, failures, unsupported cases,
missing prerequisites/evidence and unrun cases. Selected-obligation percentages
always retain their five-input denominator, including failures/unrun; runtime
matches use their three-input denominator. These are **tiny-shard rates**, not
language completeness or whole-suite pass rates. The observed compiler commit
and pcc0/self/Linux/GC axes remain attached even when importing an older run.
No imported run is relabeled as the report checkout, and no passed baseline is
exported. Raw logs, receipts, original inputs and local paths stay outside Git.
