"""The threading benchmark programs print CPython's results on all five
collectors.

Package 7 of docs/plans/p0-threaded-concurrent-gc-runtime.md times these
programs with scripts/run_threading_benchmarks.py, which records a time only
for a run that prints CPython's fields. This runs each program once per
collector at the check sizes, the threaded ones with one and four threads.
"""
from pathlib import Path

import scripts.run_threading_benchmarks as bench


def test_threading_benchmark_programs_match_cpython_on_five_collectors(
    tmp_path: Path, pcc_runtime_archive
):
    rows = bench.run_benchmarks(
        outdir=tmp_path / "bin", out=tmp_path / "rows.jsonl",
        programs=bench.SINGLE_THREAD + bench.MULTI_THREAD,
        backends=(0, 1, 2, 3, 4), repeats=1, small=True, threads=(1, 4),
        runtime_archive=str(pcc_runtime_archive), timeout=120,
    )
    # Four single-threaded programs and two threaded ones at two thread
    # counts, each measured on CPython and on five collectors.
    assert len(rows) == (4 + 2 * 2) * 6
    reference = {
        (row["program"], tuple(row["args"])): row["fields"]
        for row in rows if row["label"] == "cpython"
    }
    for row in rows:
        assert row["fields"] == reference[(row["program"], tuple(row["args"]))], row
