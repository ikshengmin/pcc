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
| `packages` | top 100 PyPI projects (hugovk.dev 30-day list, kept with the results): wheels-only install, CPython import baseline, `import <module>` compiled and run by pcc; native languages from the source distribution | the native program prints `import ok` | ~1-3 h |
| `languages` | c-testsuite and gcc torture execute by pcc alone (`tests/*_cases.py` owned path); `tests/{cpp,go,java,rust}` ordinary and integration selections | exit 0 (and `.expected` output for c-testsuite); junit outcomes | ~1 h |
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
- Package test suites are not run yet, and C++/Go/Java/Rust have no official
  conformance suite in the repository yet; the README says so.
