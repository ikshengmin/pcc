"""The most downloaded PyPI projects: does ``import`` compile and run natively?

For each of the top ``--packages`` projects of the previous calendar month
(hugovk.dev/top-pypi-packages, the snapshot is kept with the results):

1. PyPI wheel tags describe artifact shapes, not extension/CLI roles or
   mandatory native dependencies. SHA-256-verified source archive filenames
   provide unverified language hints only: docs, tests, headers and vendored
   examples can match, while generated sources can be absent. Actual native
   build languages and artifact roles remain unknown to this scanner.
2. The release and its dependencies are installed from wheels only
   (``uv pip install --target --only-binary :all:``), so no compiler runs.
   CPython must import the project's module from that directory; otherwise
   the project has no baseline and is reported as such.
3. pcc compiles a two-line program next to the installed tree
   (``import <module>``; ``print("import ok")``) and the native program must
   print that line and exit 0.

Package test suites are not run yet; the report says so.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

from common import (
    failure_category,
    first_error_line,
    read_json,
    run_bounded,
    write_json,
)


TOP_URL = "https://hugovk.dev/top-pypi-packages/top-pypi-packages.min.json"
SDIST_SCAN_LIMIT = 80 << 20
LANGUAGE_SUFFIXES = {
    "C": (".c", ".h"),
    "C++": (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"),
    "Rust": (".rs",),
    "Cython": (".pyx", ".pxd"),
    "Fortran": (".f", ".f90", ".f95", ".for"),
    "Go": (".go",),
    "Java": (".java",),
}
PROBE = "release_stats_import_probe.py"


def _get_json(url: str):
    with urllib.request.urlopen(url, timeout=120) as response:
        return json.load(response)


def top_projects(stage_dir: Path, count: int) -> tuple[dict, list[str]]:
    snapshot = stage_dir / "top-pypi-packages.json"
    if not snapshot.is_file():
        write_json(snapshot, _get_json(TOP_URL))
    data = read_json(snapshot)
    last_update = data.get("last_update", "")
    month = None
    # Do not relabel historical snapshots from a different/unknown provider.
    if data.get("source") == "ClickHouse":
        try:
            updated = date.fromisoformat(last_update[:10])
            month = (updated.replace(day=1) - timedelta(days=1)).isoformat()[:7]
        except (TypeError, ValueError):
            pass
    return {
        "url": TOP_URL,
        "last_update": last_update,
        "source": data.get("source", "unknown"),
        "period": "previous-calendar-month" if month else "unknown",
        "month": month,
        "snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
    }, [row["project"] for row in data["rows"][:count]]


def _language_hints(names: list[str]) -> list[str]:
    """Filename indicators only; never evidence that a language is built."""
    found = set()
    for name in names:
        lower = name.lower()
        if lower.endswith("/cargo.toml") or lower == "cargo.toml":
            found.add("Rust")
        for language, suffixes in LANGUAGE_SUFFIXES.items():
            if lower.endswith(suffixes):
                found.add(language)
    return sorted(found)


def scan_sdist(entry: dict, cache: Path) -> dict:
    result = {"scanned": False, "integrity": "unverified",
              "language_evidence": "unverified-filename-hints",
              "filename": entry["filename"], "url": entry["url"]}
    if entry["size"] > SDIST_SCAN_LIMIT:
        return {**result, "detail": f"source distribution over {SDIST_SCAN_LIMIT >> 20} MiB"}
    digests = entry.get("digests")
    expected = digests.get("sha256") if isinstance(digests, dict) else None
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        return {**result, "integrity": "missing-or-invalid-sha256",
                "detail": "PyPI source SHA-256 is missing or invalid; archive not read"}
    if Path(entry["filename"]).name != entry["filename"]:
        return {**result, "detail": "source filename is not a basename"}
    path = cache / entry["filename"]
    if not path.is_file():
        cache.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(entry["url"], timeout=300) as response:
            path.write_bytes(response.read(SDIST_SCAN_LIMIT + 1))
    if path.stat().st_size > SDIST_SCAN_LIMIT:
        return {**result, "detail": "cached/downloaded source exceeds scan limit; "
                "retained without scanning (download may be incomplete)"}
    # Verify cached bytes too. Retain a mismatch for diagnosis; never silently
    # delete it, retry the download, or inspect an unverified archive.
    with path.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    result.update(expected_sha256=expected.lower(), actual_sha256=actual)
    if actual != expected.lower():
        return {**result, "integrity": "sha256-mismatch",
                "detail": "source SHA-256 mismatch; bytes retained without scanning"}
    result["integrity"] = "verified-sha256"
    try:
        if path.name.endswith(".zip"):
            with zipfile.ZipFile(path) as bundle:
                names = [item.filename for item in bundle.infolist() if not item.is_dir()]
        else:
            with tarfile.open(path) as bundle:
                names = [item.name for item in bundle.getmembers() if item.isfile()]
    except (tarfile.TarError, zipfile.BadZipFile, EOFError) as error:
        return {**result, "detail": f"source archive unreadable: {error}"}
    return {**result, "scanned": True, "language_hints": _language_hints(names)}


def package_evidence(result: dict) -> dict:
    """Normalize retained results without promoting legacy suffix guesses."""
    result = dict(result)
    legacy = result.pop("native", None)
    if "source_scan" not in result and legacy is not None:
        result["source_scan"] = {
            "scanned": legacy.get("scanned", False),
            "integrity": "unverified-legacy",
            "language_evidence": "unverified-filename-hints",
            "language_hints": legacy.get("languages", []),
            "detail": "legacy filename scan; source SHA-256 verification not recorded",
            "legacy_detail": legacy.get("detail"),
        }
    if "pure_python_wheel" in result:
        result["universal_wheel"] = result.pop("pure_python_wheel")
    result["native_build"] = {"status": "unknown", "languages": None}
    result["native_artifact_kind"] = "unknown"
    return result


_MODULE_PROBE = r"""
import importlib.metadata as metadata, json, sys
site, name = sys.argv[1], sys.argv[2]
sys.path.insert(0, site)
dist = metadata.distribution(name)
print(json.dumps({"files": [str(path) for path in (dist.files or [])],
                  "top_level": (dist.read_text("top_level.txt") or "").split()}))
"""


def import_module_name(project: str, site: Path, python: str, work: Path) -> str | None:
    """The module to import: the project's own top-level package or module."""
    outcome = run_bounded([python, "-S", "-c", _MODULE_PROBE, str(site), project], cwd=work,
                          timeout=120, rss_limit_bytes=2 << 30, output_dir=work / "logs" / "module")
    if outcome["returncode"] != 0:
        return None
    found = json.loads(outcome["stdout"])
    wanted = re.sub(r"[-.]+", "_", project).lower()
    candidates = []
    for path in found["files"]:
        parts = path.split("/")
        if parts[0].endswith((".dist-info", ".data")) or parts[0] in ("bin", "..", "__pycache__"):
            continue
        if len(parts) == 1 and parts[0].endswith(".py"):
            candidates.append(parts[0][:-3])
        elif parts[-1] == "__init__.py":
            candidates.append(".".join(parts[:-1]))
    for name in found["top_level"]:
        if not name.startswith("_"):
            candidates.append(name)
    if not candidates:
        return None
    for name in candidates:
        if name.replace(".", "_").lower() == wanted:
            return name
    tops = sorted(name for name in candidates if "." not in name and not name.startswith("_"))
    if tops:
        return tops[0]
    return sorted(candidates, key=lambda name: (name.count("."), name))[0]


def check_project(project: str, stage_dir: Path, options) -> dict:
    work = stage_dir / "work" / re.sub(r"[^A-Za-z0-9_.-]", "_", project)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    release = _get_json(f"https://pypi.org/pypi/{project}/json")
    version = release["info"]["version"]
    files = release["urls"]
    universal = any(item["packagetype"] == "bdist_wheel"
                    and item["filename"].endswith("-none-any.whl") for item in files)
    platform_wheels = any(item["packagetype"] == "bdist_wheel"
                          and not item["filename"].endswith("-none-any.whl") for item in files)
    result = package_evidence({"project": project, "version": version,
                               "universal_wheel": universal, "platform_wheels": platform_wheels})
    sdist = next((item for item in files if item["packagetype"] == "sdist"), None)
    if sdist is None:
        result["source_scan"] = {"scanned": False, "integrity": "unverified",
                                 "detail": "no source distribution"}
    else:
        result["source_scan"] = scan_sdist(sdist, stage_dir / "sdists")
        if result["source_scan"]["integrity"] in {
            "sha256-mismatch", "missing-or-invalid-sha256",
        }:
            result.update(status="source-integrity-failed", detail=result["source_scan"]["detail"])
            return result
    rss = int(options.rss_limit_gb * (1 << 30))
    site = work / "site"
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("error: the packages stage installs wheels with uv; uv is not on PATH")
    installed = run_bounded(
        [uv, "pip", "install", "--quiet", "--target", str(site), "--python",
         options.python, "--only-binary", ":all:", f"{project}=={version}"],
        cwd=work, timeout=900, rss_limit_bytes=rss, output_dir=work / "logs" / "install",
    )
    if installed["killed"] or installed["returncode"] != 0:
        result.update(status="reference-install-failed",
                      detail=first_error_line(installed["stderr"] or installed["stdout"]))
        return result
    module = import_module_name(project, site, options.python, work)
    if module is None:
        result.update(status="no-importable-module", detail="no top-level module found")
        return result
    result["module"] = module
    baseline = run_bounded(
        [options.python, "-S", "-c",
         f"import sys; sys.path.insert(0, {str(site)!r}); import {module}; print('import ok')"],
        cwd=work, timeout=300, rss_limit_bytes=rss, output_dir=work / "logs" / "cpython",
    )
    if baseline["killed"] or baseline["returncode"] != 0 or "import ok" not in baseline["stdout"]:
        result.update(status="reference-import-failed",
                      detail=first_error_line(baseline["stderr"] or baseline["stdout"]))
        return result
    probe = site / PROBE
    probe.write_text(f"import {module}\nprint(\"import ok\")\n", encoding="utf-8")
    binary = work / "import_probe.native"
    compiled = run_bounded([sys.executable, "-m", "pcc", str(probe), "-o", str(binary)],
                           cwd=work, timeout=options.compile_timeout, rss_limit_bytes=rss,
                           output_dir=work / "logs" / "compile")
    result["compile_seconds"] = compiled["seconds"]
    result["compile_peak_rss_bytes"] = compiled["peak_rss_bytes"]
    if compiled["killed"] or compiled["returncode"] != 0 or not binary.is_file():
        status = {"timeout": "pcc-compile-timeout", "memory": "pcc-compile-memory"}.get(
            compiled["killed"], "pcc-compile-failed")
        result.update(status=status,
                      detail=first_error_line(compiled["stderr"] or compiled["stdout"],
                                              compile_output=True))
        return result
    native = run_bounded([str(binary)], cwd=work, timeout=options.run_timeout,
                         rss_limit_bytes=rss, output_dir=work / "logs" / "pcc")
    if native["killed"] or native["returncode"] != 0 or native["stdout"].strip() != "import ok":
        result.update(status="pcc-run-failed",
                      detail=first_error_line(native["stderr"] or native["stdout"]))
        return result
    result["status"] = "pcc-import-ok"
    return result


def run(options, out: Path, identity: dict) -> dict:
    stage_dir = out / "packages"
    top_list, projects = top_projects(stage_dir, options.packages)
    if options.projects:
        projects = list(options.projects)
    results = []
    for project in projects:
        record = stage_dir / "results" / f"{project}.json"
        if options.resume and record.is_file():
            results.append(package_evidence(read_json(record)))
            continue
        try:
            result = check_project(project, stage_dir, options)
        except OSError as error:
            result = {"project": project, "status": "reference-unavailable", "detail": str(error)}
        write_json(record, result)
        results.append(package_evidence(result))
        print(f"package {project}: {result['status']}", flush=True)
    counts: dict[str, int] = {}
    languages: dict[str, int] = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1
        for language in result.get("source_scan", {}).get("language_hints", []):
            languages[language] = languages.get(language, 0) + 1
    with_baseline = [result for result in results if result["status"].startswith("pcc-")]
    categories: dict[str, int] = {}
    for result in with_baseline:
        if result["status"] != "pcc-import-ok":
            key = failure_category(result.get("detail", ""))
            categories[key] = categories.get(key, 0) + 1
    return {
        "stage": "packages",
        "identity": identity,
        "top_list": {**top_list, "count": len(projects)},
        "method": "latest release; wheels only into a private target; CPython import "
                  "baseline; pcc compiles and runs 'import <module>'",
        "projects": len(results),
        "universal_wheel_projects": sum(1 for result in results if result.get("universal_wheel")),
        "platform_wheel_projects": sum(1 for result in results if result.get("platform_wheels")),
        "wheel_shape_unknown_projects": sum(1 for result in results
                                            if "platform_wheels" not in result),
        "source_language_hints": languages,
        "source_hint_projects": sum(1 for result in results
                                    if result.get("source_scan", {}).get("language_hints")),
        "sha256_verified_sdists": sum(1 for result in results
                                      if result.get("source_scan", {}).get("integrity")
                                      == "verified-sha256"),
        "native_build_unknown_projects": len(results),
        "with_baseline": len(with_baseline),
        "pcc_import_ok": counts.get("pcc-import-ok", 0),
        "status_counts": counts,
        "failure_categories": sorted(categories.items(), key=lambda item: -item[1]),
        "package_tests": "not run",
        "results": results,
    }
