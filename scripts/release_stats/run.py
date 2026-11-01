#!/usr/bin/env python3
"""Measure pcc's Python compatibility and speed for a release listing.

Each stage writes ``<out>/<stage>.json`` when it finishes and keeps its raw
material under ``<out>/<stage>/``; ``report`` turns the stage files into the
README section and SVG charts. Nothing is written inside the repository
except by ``report --update-readme`` (README.md and docs/images/release-stats).

Stages (see each ``*_stage.py`` for the exact method):

- ``cpython``: CPython's own Lib/test, each module compiled natively by pcc.
- ``pyperformance``: the pyperformance benchmarks, CPython time / pcc time.
- ``micro``: pcc's own benchmark programs against CPython.
- ``packages``: the top 100 PyPI projects: does ``import`` compile and run.
- ``languages``: official C suites by pcc alone; C++/Go/Java/Rust test suites.
- ``report``: README section and charts from the stage files.

One release, end to end (the timed stages take the shared performance lock;
keep the machine otherwise idle)::

    uv run python scripts/release_stats/run.py cpython pyperformance micro \\
        packages languages --out ~/pcc-release-stats/<release> \\
        --cpython-src ~/pcc-conformance/cpython-v3.15.0
    uv run python scripts/release_stats/run.py report \\
        --out ~/pcc-release-stats/<release> --update-readme
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import REPO, identity, write_json  # noqa: E402

STAGES = ("cpython", "pyperformance", "micro", "packages", "languages", "report")
TIMED = ("pyperformance", "micro")
LOCK = REPO / "build" / ".pcc-performance.lock"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    result.add_argument("stages", nargs="+", choices=STAGES)
    result.add_argument("--out", type=Path, required=True,
                        help="one directory per release, outside the repository")
    result.add_argument("--python", default=sys.executable,
                        help="the reference CPython (default: this interpreter)")
    result.add_argument("--pcc-revision", default=None,
                        help="the commit of an exported tree without Git metadata")
    result.add_argument("--resume", action="store_true",
                        help="keep per-item results already written")
    cpython = result.add_argument_group("cpython")
    cpython.add_argument("--cpython-src", default=None, help="a CPython v3.15.0 checkout")
    cpython.add_argument("--cpython-run", default=None,
                         help="summarize an existing scripts/cpython_conformance run instead")
    cpython.add_argument("--runtime-archive", default=None)
    cpython.add_argument("--jobs", type=int, default=3)
    cpython.add_argument("--cpython-timeout", type=float, default=6 * 3600)
    timing = result.add_argument_group("pyperformance, micro, packages")
    timing.add_argument("--benchmarks", nargs="*", default=None,
                        help="only these pyperformance benchmarks")
    timing.add_argument("--compile-timeout", type=float, default=900.0)
    timing.add_argument("--run-timeout", type=float, default=600.0)
    timing.add_argument("--rss-limit-gb", type=float, default=8.0,
                        help="stop a compile or run whose process tree exceeds this")
    timing.add_argument("--backends", default="0", help="collectors for the micro stage")
    timing.add_argument("--repeats", type=int, default=5)
    timing.add_argument("--packages", type=int, default=100)
    timing.add_argument("--projects", nargs="*", default=None,
                        help="check these PyPI projects instead of the top list")
    languages = result.add_argument_group("languages")
    languages.add_argument("--c-case-timeout", type=int, default=60)
    languages.add_argument("--suite-timeout", type=float, default=7200.0)
    languages.add_argument("--official-only", action="store_true",
                           help="languages: no C-suite or ordinary/integration pytest execution")
    languages.add_argument("--official-selection", choices=("fast", "full"), default="fast",
                           help="fast selects the pinned tiny shard; full is explicitly unavailable")
    official_go = languages.add_mutually_exclusive_group()
    official_go.add_argument("--official-go-run", type=Path,
                             help="import a retained go_conformance run directory, preserving its revision")
    official_go.add_argument("--official-go-execute", action="store_true",
                             help="execute the existing pinned Go adapter with 90 s/1 GiB guards")
    languages.add_argument("--official-go-inventory", type=Path,
                           help="verified go_conformance inventory JSON for the full denominator")
    languages.add_argument("--official-go-src", type=Path)
    languages.add_argument("--official-go-tool", type=Path)
    languages.add_argument("--official-go-reference-cache", type=Path)
    languages.add_argument("--official-pcc-source", type=Path,
                           help="clean compiler checkout at this measured revision (default: this tree)")
    result.add_argument("--update-readme", action="store_true",
                        help="report: replace the README section and its images")
    return result


def main(argv: list[str] | None = None) -> int:
    options = parser().parse_args(argv)
    out = options.out.expanduser().resolve()
    if out == REPO or REPO in out.parents:
        raise SystemExit("error: --out must be outside the repository")
    out.mkdir(parents=True, exist_ok=True)
    for stage in options.stages:
        if stage == "report":
            import report

            report.run(options, out)
            continue
        module = __import__(f"{stage}_stage")
        lock = contextlib.nullcontext()
        if stage in TIMED:
            from scripts.file_lock import exclusive_file_lock

            print(f"{stage}: waiting for {LOCK}", flush=True)
            lock = exclusive_file_lock(LOCK)
        with lock:
            result = module.run(options, out, identity(options))
        write_json(out / f"{stage}.json", result)
        print(f"{stage}: wrote {out / (stage + '.json')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(REPO))
    raise SystemExit(main())
