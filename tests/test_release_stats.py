"""scripts/release_stats: the pieces that need neither the network nor pcc."""

from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest


STATS = Path(__file__).resolve().parents[1] / "scripts" / "release_stats"
sys.path.insert(0, str(STATS))

import charts  # noqa: E402
import common  # noqa: E402
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
