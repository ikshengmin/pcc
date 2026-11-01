"""pcc's own benchmark programs against CPython (scripts/run_threading_benchmarks.py).

The single-thread programs in ``tests/benchmarks/threading/programs`` run at
their timing sizes under CPython and natively on the chosen collectors; a pcc
run must print CPython's result fields or the stage fails. Each program
reports the seconds of its own measured section, so process startup is not
part of the time.
"""

from __future__ import annotations

import importlib.util
import statistics

from common import REPO, geometric_mean


def _driver():
    spec = importlib.util.spec_from_file_location(
        "run_threading_benchmarks", REPO / "scripts" / "run_threading_benchmarks.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(options, out, identity: dict) -> dict:
    driver = _driver()
    stage_dir = out / "micro"
    stage_dir.mkdir(parents=True, exist_ok=True)
    backends = tuple(int(part) for part in options.backends.split(","))
    rows = driver.run_benchmarks(
        outdir=stage_dir / "bin", out=stage_dir / "measurements.jsonl",
        programs=driver.SINGLE_THREAD, backends=backends, repeats=options.repeats,
        small=False, cpython=options.python,
    )
    programs = []
    for name in driver.SINGLE_THREAD:
        reference = next(row for row in rows if row["program"] == name and row["label"] == "cpython")
        entry = {"name": name, "cpython_seconds": reference["median"], "pcc": {}}
        for backend in backends:
            row = next(row for row in rows
                       if row["program"] == name and row["label"] == f"pcc-gc{backend}")
            entry["pcc"][f"gc{backend}"] = {
                "seconds": row["median"],
                "speedup": reference["median"] / row["median"] if row["median"] > 0 else None,
            }
        programs.append(entry)
    summary = {}
    for backend in backends:
        values = [entry["pcc"][f"gc{backend}"]["speedup"] for entry in programs
                  if entry["pcc"][f"gc{backend}"]["speedup"]]
        summary[f"gc{backend}"] = {
            "geometric_mean_speedup": geometric_mean(values),
            "median_speedup": statistics.median(values) if values else None,
        }
    return {
        "stage": "micro",
        "identity": identity,
        "method": "tests/benchmarks/threading/programs at timing sizes, median of "
                  f"{options.repeats} runs of each program's own measured section; "
                  "speedup = CPython seconds / pcc seconds",
        "programs": programs,
        "summary": summary,
    }
