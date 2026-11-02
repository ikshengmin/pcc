"""scripts/release_stats: the pieces that need neither the network nor pcc."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ElementTree
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest


STATS = Path(__file__).resolve().parents[1] / "scripts" / "release_stats"
sys.path.insert(0, str(STATS))

import charts  # noqa: E402
import common  # noqa: E402
import cpython_stage  # noqa: E402
import packages_stage  # noqa: E402
import pyperformance_stage  # noqa: E402
import report  # noqa: E402


def _benchmark_dir(tmp_path: Path, body: str) -> Path:
    work = tmp_path / "bench"
    work.mkdir()
    (work / "pyperf.py").write_text((STATS / "pyperf_shim.py").read_text(encoding="utf-8"),
                                    encoding="utf-8")
    (work / "run_benchmark.py").write_text(body, encoding="utf-8")
    return work


def test_shim_times_both_entry_points_and_parses_benchmark_options(tmp_path):
    work = _benchmark_dir(tmp_path, '''
import pyperf


def bench_sum(loops, size):
    started = pyperf.perf_counter()
    for _ in range(loops):
        sum(range(size))
    return pyperf.perf_counter() - started


def work_list(size):
    return [index * 2 for index in range(size)]


if __name__ == "__main__":
    runner = pyperf.Runner()
    runner.metadata["description"] = "shim check"
    runner.argparser.add_argument("--size", type=int, default=10)
    runner.argparser.add_argument("mode", choices=["fast", "slow"])
    args = runner.parse_args()
    assert (args.size, args.mode) == (50, "slow"), (args.size, args.mode)
    runner.bench_time_func("sum_" + args.mode, bench_sum, args.size)
    runner.bench_func("list", work_list, args.size, inner_loops=2)
''')
    completed = subprocess.run(
        [sys.executable, "run_benchmark.py", "slow", "--size", "50"], cwd=work,
        capture_output=True, text=True, timeout=120,
        env={"PYPERF_SHIM_MIN_TIME": "0.001", "PYPERF_SHIM_REPEATS": "3", "PATH": "/usr/bin"},
    )
    assert completed.returncode == 0, completed.stderr
    reports = pyperformance_stage.parse_reports(completed.stdout)
    assert sorted(reports) == ["list", "sum_slow"]
    assert reports["sum_slow"]["result"] == "-"
    assert reports["list"]["inner_loops"] == 2
    assert reports["list"]["seconds"] > 0
    # The result hash is deterministic, so CPython and pcc can be compared.
    again = subprocess.run(
        [sys.executable, "run_benchmark.py", "slow", "--size", "50"], cwd=work,
        capture_output=True, text=True, timeout=120,
        env={"PYPERF_SHIM_MIN_TIME": "0.001", "PYPERF_SHIM_REPEATS": "3", "PATH": "/usr/bin"},
    )
    assert pyperformance_stage.parse_reports(again.stdout)["list"]["result"] == \
        reports["list"]["result"]


def test_shim_refuses_startup_and_statement_string_benchmarks(tmp_path):
    work = _benchmark_dir(tmp_path, '''
import pyperf
pyperf.Runner().timeit("statement", "x = 1")
''')
    completed = subprocess.run([sys.executable, "run_benchmark.py"], cwd=work,
                               capture_output=True, text=True, timeout=60)
    assert completed.returncode != 0
    assert "not supported" in completed.stderr


def test_manifest_lists_local_benchmarks_variants_and_third_party_packages(tmp_path):
    data = tmp_path / "pyperformance" / "data-files" / "benchmarks"
    for name, toml in (
        ("bm_alpha", '[project]\nname = "a"\ndependencies = ["pyperf"]\n'
                     '[tool.pyperformance]\nname = "alpha"\ntags = "math"\n'),
        ("bm_beta", '[project]\nname = "b"\ndependencies = ["pyperf", "Genshi"]\n'
                    '[tool.pyperformance]\nname = "beta"\n'),
    ):
        (data / name).mkdir(parents=True)
        (data / name / "pyproject.toml").write_text(toml, encoding="utf-8")
    (data / "bm_alpha" / "bm_alpha_fast.toml").write_text(
        '[tool.pyperformance]\nname = "alpha_fast"\nextra_opts = ["fast"]\n', encoding="utf-8")
    (data / "MANIFEST").write_text(
        "[benchmarks]\n\nname\tmetafile\nalpha\t<local>\nalpha_fast\t<local:alpha>\n"
        "beta\t<local>\n\n[group default]\n", encoding="utf-8")
    rows = {row["name"]: row for row in pyperformance_stage.benchmarks(tmp_path)}
    assert sorted(rows) == ["alpha", "alpha_fast", "beta"]
    assert rows["alpha_fast"]["extra_opts"] == ["fast"]
    assert rows["alpha_fast"]["directory"].endswith("bm_alpha")
    assert rows["alpha"]["packages"] == [] and rows["beta"]["packages"] == ["Genshi"]


@pytest.mark.parametrize(("text", "compile_output", "expected"), [
    ("pcc frontend worker failed: Exception: codegen[six]: L1CodegenError: no binding\n"
     "error: PCC-PY-COMPILE-001: [python-frontend] parallel frontend codegen worker failed\n",
     True, "Exception: codegen[six]: L1CodegenError: no binding"),
    ("Traceback (most recent call last):\n  File \"x\", line 1\n"
     "ModuleNotFoundError: No module named 'atexit'\n",
     False, "ModuleNotFoundError: No module named 'atexit'"),
])
def test_first_error_line_names_the_cause(text, compile_output, expected):
    assert common.first_error_line(text, compile_output=compile_output) == expected


def test_failure_categories_merge_names_paths_and_numbers():
    first = common.failure_category(
        "Exception: codegen[six]: L1CodegenError: binding for 'importer' at /a/b.py:12")
    second = common.failure_category(
        "Exception: codegen[idna]: L1CodegenError: binding for 'codec' at /c/d.py:7")
    assert first == second


def _stage(stage: str, **fields) -> dict:
    identity = {
        "pcc": {"revision": "0123456789abcdef"},
        "reference_python": {"implementation": "CPython", "version": "3.15.0"},
        "machine": {"cpu": "test cpu", "machine": "arm64", "platform": "test-platform"},
        "started_utc": "2026-11-01T00:00:00+00:00",
    }
    return {"stage": stage, "identity": identity, **fields}


def test_report_counts_every_attempt_and_draws_valid_charts(tmp_path):
    results = {
        "cpython": _stage("cpython", baseline_success=1000, native_success=25, modules=10,
                          modules_compile_failed=4, modules_without_baseline=2),
        "pyperformance": _stage(
            "pyperformance", pyperformance={"version": "1.14.0"}, benchmarks=3,
            geometric_mean_speedup=0.5, status_counts={"ok": 1, "compile-failed": 1,
                                                       "needs-packages": 1},
            results=[{"name": "nbody", "status": "ok", "speedup": {"nbody": 0.5}},
                     {"name": "go", "status": "compile-failed", "detail": "x"},
                     {"name": "genshi", "status": "needs-packages", "detail": "Genshi"}]),
    }
    text = report.section(results, "images")
    assert "25 (2.50%) pass" in text
    assert "1 of 3 run natively" in text
    assert "1 compile failed, 1 needs packages" in text
    assert text.startswith(report.BEGIN) and text.rstrip().endswith(report.END)
    report.write_charts(results, tmp_path)
    for chart in ("compatibility.svg", "performance.svg"):
        root = ElementTree.parse(tmp_path / chart).getroot()
        assert root.tag.endswith("svg")
    performance = (tmp_path / "performance.svg").read_text(encoding="utf-8")
    assert "compile failed" in performance and "needs packages" in performance
    assert json.dumps(report.compatibility_rows(results)) == \
        json.dumps([["CPython 3.15 Lib/test cases", 25, 1000]])


def test_speedup_chart_places_cpython_at_one():
    svg = charts.speedup_chart("t", [("a", 2.0, "ok"), ("b", 0.25, "ok"), ("c", None, "x")],
                               "note", 0.7)
    ElementTree.fromstring(svg)
    assert "2.00x" in svg and "0.25x" in svg and "CPython = 1.0x" in svg


def test_language_report_retains_failures_unfinished_and_missing_lanes():
    results = {"languages": _stage(
        "languages", c_suites={}, language_suites={
            "cpp": {"ordinary": {"passed": 10, "failed": 2, "errors": 1, "skipped": 0},
                    "integration": {"passed": 3, "failed": 1, "errors": 4, "skipped": 0}},
            "rust": {"ordinary": {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}},
            "java": {"ordinary": {"passed": 0, "failed": 0, "errors": 1, "skipped": 0}},
        }, execution_audit={"lanes": {
            "cpp-ordinary": {"status": "COMPLETE", "unfinished_nodes": []},
            "cpp-integration": {"status": "TIMEOUT", "unfinished_nodes": ["a", "b"]},
            "rust-ordinary": {"status": "COMPLETE", "selected": 0, "returncode": 5,
                              "unfinished_nodes": []},
            "java-ordinary": {"status": "COMPLETE", "selected": 0, "returncode": 2,
                              "unfinished_nodes": []},
        }},
    )}
    text = report.section(results, "images")
    assert "10 passed, 2 failed, 1 errors" in text
    assert "3 passed, 1 failed, 4 errors" in text
    assert "2 unfinished; watchdog TIMEOUT" in text
    assert "ordinary: no selected tests" in text
    assert "integration: not run" in text
    assert "unrun scope unknown (collection did not complete)" in text
    assert "reference comparisons are not separately enumerated" in text
    assert "compiles every program" not in text


def test_imported_conformance_without_source_identity_is_not_relabelled(tmp_path):
    (tmp_path / "summary.json").write_text(json.dumps({"totals": {}}))
    (tmp_path / "identity.json").write_text(json.dumps({"pcc_head": ""}))
    with pytest.raises(ValueError, match="no measured pcc revision"):
        cpython_stage._summary(tmp_path, _stage("cpython")["identity"])


def _sdist_bytes(kind, names):
    """Synthetic data only: never install, import or execute an archived file."""
    content = b"raise RuntimeError('archive contents must not execute')\n"
    buffer = io.BytesIO()
    if kind == "zip":
        with zipfile.ZipFile(buffer, "w") as bundle:
            for name in names:
                bundle.writestr(name, content)
    else:
        with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
            for name in names:
                member = tarfile.TarInfo(name)
                member.size = len(content)
                bundle.addfile(member, io.BytesIO(content))
    return buffer.getvalue()


def _sdist_entry(data, kind="tar.gz"):
    return {"filename": "synthetic-1.0." + kind, "size": len(data),
            "url": "https://files.example.invalid/synthetic-1.0." + kind,
            "packagetype": "sdist", "digests": {"sha256": hashlib.sha256(data).hexdigest()}}


def _forbid_action(*args, **kwargs):
    raise AssertionError("unexpected network, archive inspection or package execution")


@pytest.mark.parametrize("kind", ["tar.gz", "zip"])
def test_package_cached_digest_verified_but_filename_languages_are_only_hints(tmp_path, monkeypatch, kind):
    # PyPI JSON API digests establish bytes, not build dependencies or roles.
    # https://docs.pypi.org/api/json/
    data = _sdist_bytes(kind, ["synthetic/docs/verify.go", "synthetic/tests/Test.java",
                              "synthetic/vendor/example/Cargo.toml", "synthetic/include/api.h",
                              "synthetic/setup.py"])
    entry = _sdist_entry(data, kind)
    (tmp_path / entry["filename"]).write_bytes(data)
    monkeypatch.setattr(packages_stage.urllib.request, "urlopen", _forbid_action)
    scan = packages_stage.scan_sdist(entry, tmp_path)
    assert scan["integrity"] == "verified-sha256" and scan["scanned"]
    assert scan["actual_sha256"] == entry["digests"]["sha256"]
    assert scan["language_hints"] == ["C", "Go", "Java", "Rust"]
    result = packages_stage.package_evidence({"source_scan": scan, "platform_wheels": True})
    assert result["native_build"] == {"status": "unknown", "languages": None}
    assert result["native_artifact_kind"] == "unknown"
    assert "languages" not in scan and "native" not in result


def test_package_cached_digest_mismatch_retains_bytes_without_scan_or_redownload(tmp_path, monkeypatch):
    good = _sdist_bytes("tar.gz", ["synthetic/runtime.c"])
    entry = _sdist_entry(good)
    path = tmp_path / entry["filename"]
    path.write_bytes(b"corrupt retained evidence")
    monkeypatch.setattr(packages_stage.urllib.request, "urlopen", _forbid_action)
    monkeypatch.setattr(packages_stage.tarfile, "open", _forbid_action)
    scan = packages_stage.scan_sdist(entry, tmp_path)
    assert scan["integrity"] == "sha256-mismatch" and not scan["scanned"]
    assert path.read_bytes() == b"corrupt retained evidence"
    assert "language_hints" not in scan


@pytest.mark.parametrize("matches", [True, False])
def test_package_download_verifies_digest_and_retains_mismatch(tmp_path, monkeypatch, matches):
    data = _sdist_bytes("tar.gz", ["synthetic/runtime.c"])
    entry = _sdist_entry(data)
    if not matches:
        entry["digests"]["sha256"] = "0" * 64
    calls = []

    def download(url, timeout):
        calls.append((url, timeout))
        return io.BytesIO(data)

    monkeypatch.setattr(packages_stage.urllib.request, "urlopen", download)
    scan = packages_stage.scan_sdist(entry, tmp_path)
    assert calls == [(entry["url"], 300)]
    assert scan["scanned"] is matches
    assert scan["integrity"] == ("verified-sha256" if matches else "sha256-mismatch")
    assert (tmp_path / entry["filename"]).read_bytes() == data


@pytest.mark.parametrize("digest", [None, "", "not-a-digest", "g" * 64])
def test_package_missing_or_invalid_digest_is_not_downloaded(tmp_path, monkeypatch, digest):
    entry = _sdist_entry(b"unused")
    entry["digests"] = {} if digest is None else {"sha256": digest}
    monkeypatch.setattr(packages_stage.urllib.request, "urlopen", _forbid_action)
    scan = packages_stage.scan_sdist(entry, tmp_path)
    assert scan["integrity"] == "missing-or-invalid-sha256"
    assert not scan["scanned"] and not list(tmp_path.iterdir())


def test_package_download_bound_and_unreadable_archive_preserve_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(packages_stage, "SDIST_SCAN_LIMIT", 8)
    entry = _sdist_entry(b"small")
    monkeypatch.setattr(packages_stage.urllib.request, "urlopen",
                        lambda *args, **kwargs: io.BytesIO(b"x" * 100))
    scan = packages_stage.scan_sdist(entry, tmp_path)
    assert not scan["scanned"] and scan["integrity"] == "unverified"
    assert (tmp_path / entry["filename"]).stat().st_size == 9
    other = tmp_path / "other"
    other.mkdir()
    entry = _sdist_entry(b"bad-tar")
    (other / entry["filename"]).write_bytes(b"bad-tar")
    scan = packages_stage.scan_sdist(entry, other)
    assert not scan["scanned"] and scan["integrity"] == "verified-sha256"
    assert "unreadable" in scan["detail"] and "language_hints" not in scan


def test_package_no_matching_suffix_does_not_prove_no_native_build(tmp_path, monkeypatch):
    data = _sdist_bytes("tar.gz", ["synthetic/build.py", "synthetic/annotated.py"])
    entry = _sdist_entry(data)
    (tmp_path / entry["filename"]).write_bytes(data)
    monkeypatch.setattr(packages_stage.urllib.request, "urlopen", _forbid_action)
    scan = packages_stage.scan_sdist(entry, tmp_path)
    assert scan["language_hints"] == []
    result = packages_stage.package_evidence({"source_scan": scan, "universal_wheel": True})
    assert result["native_build"]["languages"] is None
    assert result["native_artifact_kind"] == "unknown"


def test_package_integrity_failure_precedes_any_install(tmp_path, monkeypatch):
    data = _sdist_bytes("tar.gz", ["synthetic/runtime.c"])
    entry = _sdist_entry(data)
    cache = tmp_path / "sdists"
    cache.mkdir()
    (cache / entry["filename"]).write_bytes(b"mismatched")
    monkeypatch.setattr(packages_stage, "_get_json",
                        lambda url: {"info": {"version": "1.0"}, "urls": [entry]})
    monkeypatch.setattr(packages_stage.shutil, "which", _forbid_action)
    monkeypatch.setattr(packages_stage, "run_bounded", _forbid_action)
    result = packages_stage.check_project("synthetic", tmp_path, SimpleNamespace())
    assert result["status"] == "source-integrity-failed"
    assert result["source_scan"]["integrity"] == "sha256-mismatch"


@pytest.mark.parametrize("wheel_names", [
    ["synthetic-1.0-py3-none-any.whl"],
    ["synthetic-1.0-py3-none-linux_x86_64.whl"],
    ["synthetic-1.0-py3-none-any.whl", "synthetic-1.0-cp315-cp315-linux_x86_64.whl"],
])
def test_package_wheel_shapes_do_not_classify_extension_cli_or_optional_build(tmp_path, monkeypatch, wheel_names):
    # Wheel platform/ABI tags are artifact compatibility metadata, not roles.
    # https://packaging.python.org/en/latest/specifications/binary-distribution-format/
    monkeypatch.setattr(packages_stage, "_get_json", lambda url: {
        "info": {"version": "1.0"}, "urls": [
            {"packagetype": "bdist_wheel", "filename": name} for name in wheel_names]})
    monkeypatch.setattr(packages_stage.shutil, "which", lambda name: "not-executed-uv")
    monkeypatch.setattr(packages_stage, "run_bounded", lambda *args, **kwargs: {
        "killed": None, "returncode": 1, "stderr": "synthetic install not run", "stdout": ""})
    options = SimpleNamespace(rss_limit_gb=1, python=sys.executable)
    result = packages_stage.check_project("synthetic", tmp_path, options)
    assert result["status"] == "reference-install-failed"
    assert result["universal_wheel"] == any(name.endswith("-none-any.whl") for name in wheel_names)
    assert result["platform_wheels"] == any(not name.endswith("-none-any.whl") for name in wheel_names)
    assert result["native_build"] == {"status": "unknown", "languages": None}
    assert result["native_artifact_kind"] == "unknown"
    assert result["source_scan"]["detail"] == "no source distribution"


@pytest.mark.parametrize(("provider", "updated", "month"), [
    ("ClickHouse", "2026-11-01 06:41:05", "2026-10"),
    ("ClickHouse", "2026-01-01 06:41:05", "2025-12"),
    ("historical-provider", "2026-01-01", None),
    ("ClickHouse", "invalid-date", None),
])
def test_package_ranking_previous_calendar_month_or_unknown(tmp_path, monkeypatch, provider, updated, month):
    # Provider query: pypi_downloads_per_month WHERE month = last_month.
    # https://github.com/hugovk/top-pypi-packages/blob/main/clickhouse.py
    snapshot = tmp_path / "top-pypi-packages.json"
    snapshot.write_text(json.dumps({"source": provider, "last_update": updated,
                                   "rows": [{"project": "alpha"}, {"project": "beta"}]}))
    monkeypatch.setattr(packages_stage, "_get_json", _forbid_action)
    ranking, names = packages_stage.top_projects(tmp_path, 1)
    assert names == ["alpha"] and ranking["month"] == month
    assert ranking["period"] == ("previous-calendar-month" if month else "unknown")
    assert ranking["snapshot_sha256"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()


@pytest.mark.parametrize("scan", [
    {"scanned": True, "languages": ["Go"]},
    {"scanned": False, "detail": "no source distribution"},
    {"scanned": False, "detail": "source distribution over 80 MiB"},
])
def test_package_resume_keeps_legacy_hints_unverified_and_never_infers_build_counts(tmp_path, monkeypatch, scan):
    record = tmp_path / "packages" / "results" / "synthetic.json"
    record.parent.mkdir(parents=True)
    original = {"project": "synthetic", "status": "pcc-import-ok", "pure_python_wheel": True,
                "platform_wheels": True, "native": scan}
    record.write_text(json.dumps(original))
    monkeypatch.setattr(packages_stage, "top_projects",
                        lambda *args: ({"last_update": "old", "period": "unknown"}, ["synthetic"]))
    monkeypatch.setattr(packages_stage, "check_project", _forbid_action)
    result = packages_stage.run(SimpleNamespace(packages=1, projects=None, resume=True), tmp_path, {})
    assert result["source_language_hints"] == ({"Go": 1} if scan.get("languages") else {})
    assert result["sha256_verified_sdists"] == 0
    assert result["native_build_unknown_projects"] == 1
    assert result["universal_wheel_projects"] == result["platform_wheel_projects"] == 1
    assert result["results"][0]["source_scan"]["integrity"] == "unverified-legacy"
    assert result["results"][0]["source_scan"]["legacy_detail"] == scan.get("detail")
    assert "needs_native_build" not in result and "native_languages" not in result
    assert json.loads(record.read_text()) == original  # retain original evidence


@pytest.mark.parametrize("legacy", [False, True])
def test_package_report_and_chart_label_hints_not_native_requirements(tmp_path, legacy):
    fields = ({"native_languages": {"Go": 1}, "needs_native_build": 1, "pure_python_wheels": 1}
              if legacy else {"source_language_hints": {"Go": 1}, "source_hint_projects": 1,
                              "universal_wheel_projects": 1, "platform_wheel_projects": 1,
                              "wheel_shape_unknown_projects": 0, "sha256_verified_sdists": 1})
    ranking = ({"last_update": "2026-01-01"} if legacy else {
        "last_update": "2026-01-01", "period": "previous-calendar-month", "month": "2025-12"})
    results = {"packages": _stage("packages", projects=1, pcc_import_ok=0, with_baseline=0,
                                  top_list=ranking, **fields)}
    record = tmp_path / "packages.json"
    original = json.dumps(results["packages"])
    record.write_text(original)
    summary = report.run(SimpleNamespace(update_readme=False), tmp_path)
    assert record.read_text() == original
    assert json.loads((tmp_path / "report" / "summary.json").read_text()) == summary
    packages = summary["packages"]
    assert packages["source_language_hints"] == {"Go": 1}
    assert packages["source_hint_projects"] == packages["universal_wheel_projects"] == 1
    assert packages["source_language_evidence"] == "unverified-filename-hints"
    assert packages["native_build_unknown_projects"] == 1
    assert packages["native_artifact_kind"] == "unknown"
    assert not {"native_languages", "needs_native_build", "pure_python_wheels"} & packages.keys()
    if legacy:
        assert "platform_wheel_projects" not in packages  # missing is not zero
        assert "sha256_verified_sdists" not in packages
    else:
        assert packages["platform_wheel_projects"] == packages["sha256_verified_sdists"] == 1
    text = (tmp_path / "report" / "README-section.md").read_text()
    assert "Unverified source-language filename hints: Go 1" in text
    assert "actual native-build languages are unknown" in text
    assert "does not rule out optional native builds" in text
    assert ("wheel shape unknown: not recorded" if legacy else "wheel shape unknown: 0") in text
    assert ("download window unknown" if legacy else "calendar-month downloads, 2025-12") in text
    assert "would have to build" not in text and "have native parts" not in text
    svg = (tmp_path / "report" / "native-languages.svg").read_text()
    ElementTree.fromstring(svg)
    assert "Unverified filename hints" in svg and "actual native-build languages unknown" in svg
    assert "Native parts" not in svg
