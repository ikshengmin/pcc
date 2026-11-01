"""README section and SVG charts from the stage results of one release run."""

from __future__ import annotations

import shutil
from pathlib import Path

import charts
from common import REPO, read_json, write_json


BEGIN = "<!-- release-stats:begin -->"
END = "<!-- release-stats:end -->"
IMAGES = Path("docs") / "images" / "release-stats"
STAGES = ("cpython", "pyperformance", "micro", "packages", "languages")


def _load(out: Path) -> dict:
    return {stage: read_json(out / f"{stage}.json")
            for stage in STAGES if (out / f"{stage}.json").is_file()}


def _identity_line(results: dict) -> str:
    first = next(iter(results.values()))
    identity = first["identity"]
    revisions = sorted({result["identity"]["pcc"]["revision"][:12] for result in results.values()})
    machine = identity["machine"]
    python = identity["reference_python"]
    date = min(result["identity"]["started_utc"][:10] for result in results.values())
    return (f"pcc {', '.join(revisions)}; {python['implementation']} {python['version']}; "
            f"{machine.get('cpu') or machine['machine']}, {machine['platform']}; {date}")


def _pct(part: int, whole: int) -> str:
    if not whole:
        return "n/a"
    share = 100 * part / whole
    return f"{share:.2f}%" if share < 10 else f"{share:.1f}%"


def compatibility_rows(results: dict) -> list[tuple[str, int, int]]:
    rows = []
    if "cpython" in results:
        cpython = results["cpython"]
        rows.append(("CPython 3.15 Lib/test cases", cpython["native_success"],
                     cpython["baseline_success"]))
    if "packages" in results:
        packages = results["packages"]
        rows.append((f"Top {packages['projects']} PyPI projects: import",
                     packages["pcc_import_ok"], packages["projects"]))
    if "languages" in results:
        for suite, label in (("c-testsuite", "C: c-testsuite"),
                             ("gcc-torture-execute", "C: gcc torture execute")):
            data = results["languages"]["c_suites"].get(suite)
            if data:
                rows.append((label, data["passed"], data["cases"]))
    return rows


def speed_rows(results: dict) -> tuple[list, float | None, dict]:
    rows = []
    mean = None
    counts = {}
    if "pyperformance" in results:
        data = results["pyperformance"]
        mean = data["geometric_mean_speedup"]
        counts = data["status_counts"]
        measured, missing = [], []
        for result in data["results"]:
            if result["status"] == "ok":
                for name, value in result["speedup"].items():
                    measured.append((name, value, "ok"))
            else:
                missing.append((result["name"], None, result["status"].replace("-", " ")))
        measured.sort(key=lambda row: -row[1])
        missing.sort()
        rows = measured + missing
    return rows, mean, counts


def section(results: dict, image_prefix: str) -> str:
    lines = [BEGIN, "## Compatibility and speed, measured", ""]
    lines.append(f"Measured with `scripts/release_stats/run.py` ({_identity_line(results)}). "
                 "pcc compiles every program below natively, without libpython and without "
                 "cc, c++, rustc, javac or go. Numbers are reproduced by rerunning the script "
                 "for each release; nothing is extrapolated.")
    lines.append("")
    lines.append("### Compatible with Python")
    lines.append("")
    if "cpython" in results:
        c = results["cpython"]
        lines.append(
            f"- **CPython's own tests**: of the {c['baseline_success']:,} Lib/test cases "
            f"CPython {results['cpython']['identity']['reference_python']['version']} passes, "
            f"{c['native_success']:,} ({_pct(c['native_success'], c['baseline_success'])}) pass "
            f"when pcc compiles the test module. {c['modules_compile_failed']} of "
            f"{c['modules']} modules do not compile yet; {c['modules_without_baseline']} modules "
            "have no CPython baseline under the harness's minimal `test.support` and are "
            "not counted.")
    if "packages" in results:
        p = results["packages"]
        languages = ", ".join(f"{name} {count}" for name, count in
                              sorted(p["native_languages"].items(), key=lambda item: -item[1]))
        lines.append(
            f"- **Top {p['projects']} PyPI projects** (downloads, {p['top_list']['last_update'][:10]}): "
            f"`import` compiles and runs natively for {p['pcc_import_ok']} "
            f"({_pct(p['pcc_import_ok'], p['projects'])}); {p['with_baseline']} install from "
            f"wheels and import under CPython. {p['pure_python_wheels']} ship a pure-Python "
            f"wheel; {p['needs_native_build']} have native parts pcc would have to build "
            f"itself ({languages or 'none'}). Package test suites are not run yet.")
    if "languages" in results:
        suites = results["languages"]["c_suites"]
        parts = [f"{data['passed']:,} of {data['cases']:,} {suite} cases"
                 for suite, data in suites.items()]
        lines.append(f"- **C**: pcc alone compiles and runs {', '.join(parts)}.")
        ordinary = results["languages"]["language_suites"]
        named = {"cpp": "C++", "go": "Go", "java": "Java", "rust": "Rust"}
        counts = ", ".join(
            f"{named[name]} {entry['ordinary']['passed']:,}" for name, entry in ordinary.items())
        lines.append(f"- **C++, Go, Java, Rust**: bounded language subsets, no official "
                     f"conformance suite yet. Passing frontend tests: {counts}.")
    lines += ["", f"![Compatibility]({image_prefix}/compatibility.svg)", ""]
    if "packages" in results:
        lines += [f"![Native languages of the top PyPI projects]({image_prefix}/native-languages.svg)", ""]
    lines.append("### Speed against CPython")
    lines.append("")
    rows, mean, counts = speed_rows(results)
    if "pyperformance" in results:
        data = results["pyperformance"]
        ok = counts.get("ok", 0)
        others = ", ".join(f"{count} {status.replace('-', ' ')}"
                           for status, count in sorted(counts.items()) if status != "ok")
        mean_text = f"{mean:.2f}x" if mean else "n/a"
        lines.append(
            f"- **pyperformance {data['pyperformance']['version']}** (unchanged benchmark "
            f"programs, timed in process): {ok} of {data['benchmarks']} run natively and match "
            f"CPython; geometric mean speed {mean_text} of CPython's (above 1 is faster). "
            f"Not counted in the mean: {others or 'none'}.")
    if "micro" in results:
        programs = results["micro"]["programs"]
        parts = ", ".join(f"{entry['name']} {entry['pcc']['gc0']['speedup']:.2f}x"
                          for entry in programs if "gc0" in entry["pcc"])
        lines.append(f"- **pcc's benchmark programs** (default collector): {parts}.")
    if "pyperformance" not in results and "micro" not in results:
        lines.append("Not measured in this run.")
    if rows:
        lines += ["", f"![Speed against CPython]({image_prefix}/performance.svg)"]
    lines += [END, ""]
    return "\n".join(lines)


def write_charts(results: dict, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    note = _identity_line(results)
    rows = compatibility_rows(results)
    if rows:
        (directory / "compatibility.svg").write_text(
            charts.share_chart("Passing natively under pcc (no libpython, no C toolchain)",
                               rows, note), encoding="utf-8")
    if "packages" in results:
        packages = results["packages"]
        (directory / "native-languages.svg").write_text(
            charts.count_chart(f"Native parts of the top {packages['projects']} PyPI projects",
                               sorted(packages["native_languages"].items(),
                                      key=lambda item: -item[1]),
                               packages["projects"], note), encoding="utf-8")
    speed, mean, _counts = speed_rows(results)
    if speed:
        (directory / "performance.svg").write_text(
            charts.speedup_chart("pyperformance: speed relative to CPython (log scale)",
                                 speed, note, mean), encoding="utf-8")


def run(options, out: Path) -> dict:
    results = _load(out)
    if not results:
        raise SystemExit(f"error: {out} has no stage results")
    report_dir = out / "report"
    write_charts(results, report_dir)
    text = section(results, IMAGES.as_posix())
    (report_dir / "README-section.md").write_text(text, encoding="utf-8")
    summary = {stage: {key: value for key, value in result.items()
                       if key not in ("results", "programs")}
               for stage, result in results.items()}
    write_json(report_dir / "summary.json", summary)
    if options.update_readme:
        images = REPO / IMAGES
        images.mkdir(parents=True, exist_ok=True)
        for chart in report_dir.glob("*.svg"):
            shutil.copyfile(chart, images / chart.name)
        readme = REPO / "README.md"
        current = readme.read_text(encoding="utf-8")
        if BEGIN in current and END in current:
            head, rest = current.split(BEGIN, 1)
            tail = rest.split(END, 1)[1].lstrip("\n")
            updated = head + text + tail
        else:
            anchor = "\n## Install\n"
            if anchor not in current:
                raise SystemExit("error: README.md has no '## Install' heading to insert before")
            updated = current.replace(anchor, "\n" + text + anchor[1:], 1)
        readme.write_text(updated, encoding="utf-8")
        print(f"updated {readme} and {images}", flush=True)
    print((report_dir / "README-section.md").read_text(encoding="utf-8"), flush=True)
    return summary
