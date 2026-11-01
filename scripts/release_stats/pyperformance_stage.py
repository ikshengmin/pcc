"""pyperformance benchmarks: CPython time divided by pcc time.

The pinned pyperformance release is downloaded from PyPI and checked against
its SHA-256. Every benchmark program runs unchanged: once under the reference
CPython and once compiled by pcc (``python -m pcc``, default options), both
importing ``pyperf_shim.py`` as ``pyperf``. A benchmark counts only when the
pcc program exits 0 and reports the same benchmark names and result hashes
as CPython. Benchmarks that need third-party packages are listed with those
packages and not run: the packages stage measures whether pcc can import
them at all.
"""

from __future__ import annotations

import json
import shutil
import sys
import tarfile
import tomllib
import urllib.request
from pathlib import Path

from common import (
    HERE,
    first_error_line,
    geometric_mean,
    read_json,
    run_bounded,
    sha256_file,
    write_json,
)


VERSION = "1.14.0"
SDIST_SHA256 = "91f74393997b604375ad5b79bf569a24076d70181c076a53abad5383a238a8aa"
MARK = "@@pyperf-shim@@"


def fetch(stage_dir: Path) -> Path:
    """The unpacked pinned release; download and verify it once."""
    root = stage_dir / "source" / f"pyperformance-{VERSION}"
    if root.is_dir():
        return root
    archive = stage_dir / "download" / f"pyperformance-{VERSION}.tar.gz"
    if not archive.is_file():
        archive.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(f"https://pypi.org/pypi/pyperformance/{VERSION}/json",
                                    timeout=60) as response:
            release = json.load(response)
        url = next(item["url"] for item in release["urls"] if item["packagetype"] == "sdist")
        with urllib.request.urlopen(url, timeout=300) as response:
            archive.write_bytes(response.read())
    digest = sha256_file(archive)
    if digest != SDIST_SHA256:
        raise SystemExit(f"error: {archive} has SHA-256 {digest}, expected {SDIST_SHA256}")
    with tarfile.open(archive) as bundle:
        bundle.extractall(stage_dir / "source", filter="data")
    return root


def benchmarks(root: Path) -> list[dict]:
    """Every benchmark the release's MANIFEST lists, with its options."""
    data = root / "pyperformance" / "data-files" / "benchmarks"
    rows = []
    in_table = False
    for line in (data / "MANIFEST").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("["):
            in_table = line == "[benchmarks]"
            continue
        if not in_table or not line or line.startswith("name") or line.startswith("#"):
            continue
        name, metafile = line.split()[:2]
        if metafile == "<local>":
            directory, config = data / f"bm_{name}", data / f"bm_{name}" / "pyproject.toml"
        elif metafile.startswith("<local:") and metafile.endswith(">"):
            base = metafile[len("<local:"):-1]
            directory, config = data / f"bm_{base}", data / f"bm_{base}" / f"bm_{name}.toml"
        else:
            raise SystemExit(f"error: unknown pyperformance metafile {metafile!r} for {name}")
        settings = tomllib.loads(config.read_text(encoding="utf-8"))
        base_settings = tomllib.loads((directory / "pyproject.toml").read_text(encoding="utf-8"))
        project = settings.get("project") or base_settings.get("project") or {}
        tool = settings.get("tool", {}).get("pyperformance", {})
        packages = [requirement for requirement in project.get("dependencies", [])
                    if not requirement.split()[0].lower().startswith("pyperf")]
        rows.append({
            "name": name,
            "directory": str(directory),
            "extra_opts": list(tool.get("extra_opts", [])),
            "tags": tool.get("tags", base_settings.get("tool", {}).get("pyperformance", {})
                             .get("tags", "")),
            "packages": packages,
        })
    return rows


def parse_reports(text: str) -> dict:
    reports = {}
    for line in text.splitlines():
        words = line.split()
        if len(words) == 6 and words[0] == MARK:
            reports[words[1]] = {"seconds": float(words[2]), "loops": int(words[3]),
                                 "inner_loops": int(words[4]), "result": words[5]}
    return reports


def _bounded(command, work, options, step):
    return run_bounded(command, cwd=work, timeout=options.run_timeout
                       if step != "compile" else options.compile_timeout,
                       rss_limit_bytes=int(options.rss_limit_gb * (1 << 30)),
                       output_dir=work / "logs" / step)


def _failure(result, status, outcome):
    if outcome["killed"]:
        status = {"timeout": status.replace("failed", "timeout"),
                  "memory": status.replace("failed", "memory")}[outcome["killed"]]
    result.update(status=status,
                  detail=first_error_line(outcome["stderr"] or outcome["stdout"],
                                          compile_output=status.startswith("compile")))
    return result


def run_one(benchmark: dict, work: Path, options) -> dict:
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(benchmark["directory"], work)
    shutil.copyfile(HERE / "pyperf_shim.py", work / "pyperf.py")
    program = work / "run_benchmark.py"
    argv = list(benchmark["extra_opts"])
    result = {"name": benchmark["name"], "tags": benchmark["tags"], "extra_opts": argv}
    reference = _bounded([options.python, str(program), *argv], work, options, "cpython")
    result["cpython"] = parse_reports(reference["stdout"])
    if reference["killed"] or reference["returncode"] != 0 or not result["cpython"]:
        _failure(result, "reference-failed", reference)
        if "not applicable" in result["detail"] or "not supported" in result["detail"]:
            result["status"] = "not-applicable"
        return result
    binary = work / "benchmark.native"
    compiled = _bounded([sys.executable, "-m", "pcc", str(program), "-o", str(binary)],
                        work, options, "compile")
    result["compile_seconds"] = compiled["seconds"]
    result["compile_peak_rss_bytes"] = compiled["peak_rss_bytes"]
    if compiled["killed"] or compiled["returncode"] != 0 or not binary.is_file():
        return _failure(result, "compile-failed", compiled)
    native = _bounded([str(binary), *argv], work, options, "pcc")
    result["pcc"] = parse_reports(native["stdout"])
    if native["killed"] or native["returncode"] != 0:
        return _failure(result, "run-failed", native)
    if sorted(result["pcc"]) != sorted(result["cpython"]):
        result.update(status="mismatch", detail="reported benchmarks differ")
        return result
    for name, expected in result["cpython"].items():
        if result["pcc"][name]["result"] != expected["result"]:
            result.update(status="mismatch", detail=f"{name} returned a different value")
            return result
    result["status"] = "ok"
    result["speedup"] = {name: expected["seconds"] / result["pcc"][name]["seconds"]
                         for name, expected in result["cpython"].items()
                         if result["pcc"][name]["seconds"] > 0}
    return result


def run(options, out: Path, identity: dict) -> dict:
    stage_dir = out / "pyperformance"
    root = fetch(stage_dir)
    rows = benchmarks(root)
    if options.benchmarks:
        wanted = set(options.benchmarks)
        rows = [row for row in rows if row["name"] in wanted]
    results = []
    for row in rows:
        record = stage_dir / "results" / f"{row['name']}.json"
        if options.resume and record.is_file():
            results.append(read_json(record))
            continue
        if row["packages"]:
            result = {"name": row["name"], "tags": row["tags"], "status": "needs-packages",
                      "detail": ", ".join(row["packages"])}
        else:
            result = run_one(row, stage_dir / "work" / row["name"], options)
        write_json(record, result)
        results.append(result)
        print(f"pyperformance {row['name']}: {result['status']}", flush=True)
    speedups = [value for result in results if result["status"] == "ok"
                for value in result["speedup"].values()]
    counts: dict[str, int] = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    return {
        "stage": "pyperformance",
        "identity": identity,
        "pyperformance": {"version": VERSION, "sdist_sha256": SDIST_SHA256},
        "method": "unchanged benchmark programs; in-process median of "
                  "PYPERF_SHIM_REPEATS timings after calibration (pyperf_shim.py); "
                  "speedup = CPython seconds / pcc seconds",
        "benchmarks": len(results),
        "status_counts": counts,
        "geometric_mean_speedup": geometric_mean(speedups),
        "results": results,
    }
