#!/usr/bin/env python3
"""Time the threading benchmark programs on pcc's collectors and on CPython.

The programs live in ``tests/benchmarks/threading/programs``. Each prints its
result fields and, last, the seconds its measured section took. A pcc run must
print exactly CPython's fields for the same arguments; a mismatch or a failing
run fails the measurement instead of recording a time. Every measurement is
appended to ``--out`` as one JSON line as soon as it is taken.

Example (full sizes, GC0 and GC4, three runs each)::

    uv run python scripts/run_threading_benchmarks.py \
        --out /tmp/threading.jsonl --backends 0,4 --repeats 3
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).absolute().parents[1]
PROGRAMS = REPO_ROOT / "tests" / "benchmarks" / "threading" / "programs"
SINGLE_THREAD = ("st_fib", "st_alloc", "st_objects", "st_strings")
MULTI_THREAD = ("mt_fib", "mt_alloc")
THREAD_COUNTS = (1, 2, 4, 8)
# Sizes that keep each program well under a second on CPython: for checking
# that a build prints CPython's results, not for timing.
SMALL_ARGS = {
    "st_fib": ("20",),
    "st_alloc": ("2000",),
    "st_objects": ("20000",),
    "st_strings": ("3000",),
    "mt_fib": ("8",),
    "mt_alloc": ("8",),
}


class BenchmarkMismatch(RuntimeError):
    """A pcc run failed or printed something other than CPython's fields."""


def program_argv_sets(name: str, *, small: bool,
                      threads: tuple[int, ...] = THREAD_COUNTS) -> list[tuple[str, ...]]:
    """Argument lists to run ``name`` with: thread counts for the threaded
    programs (with the job count when ``small``), the size when ``small``."""
    if name in MULTI_THREAD:
        jobs = SMALL_ARGS[name] if small else ()
        return [(str(count),) + jobs for count in threads]
    return [SMALL_ARGS[name] if small else ()]


def compile_program(name: str, outdir: Path, runtime_archive: str | None) -> Path:
    from pcc.frontends.python.pipeline import compile_python

    binary = outdir / name
    compile_python(
        str(PROGRAMS / (name + ".py")), str(binary), backend="self",
        libpython_mode="off", ir_scaffold_mode="on",
        runtime_archive=runtime_archive,
    )
    return binary


def run_once(argv: list[str], env: dict[str, str], timeout: float) -> tuple[list[str], float, float]:
    """Run one program; return its result fields, its own seconds and the
    wall seconds around the whole process."""
    started = time.perf_counter()
    proc = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=timeout)
    wall = time.perf_counter() - started
    if proc.returncode != 0:
        raise BenchmarkMismatch(
            f"{' '.join(argv)} exited {proc.returncode}: {proc.stderr[-1500:]}"
        )
    lines = proc.stdout.strip().splitlines()
    if not lines:
        raise BenchmarkMismatch(f"{' '.join(argv)} printed nothing")
    fields = lines[-1].split()
    return fields[:-1], float(fields[-1]), wall


def measure(argv: list[str], env: dict[str, str], repeats: int, timeout: float,
            expected: list[str] | None) -> dict:
    seconds: list[float] = []
    walls: list[float] = []
    fields: list[str] = []
    for _ in range(repeats):
        fields, own, wall = run_once(argv, env, timeout)
        if expected is not None and fields != expected:
            raise BenchmarkMismatch(
                f"{' '.join(argv)} printed {fields}, CPython printed {expected}"
            )
        seconds.append(own)
        walls.append(wall)
    return {
        "fields": fields,
        "median": statistics.median(seconds),
        "best": min(seconds),
        "times": seconds,
        "wall_median": statistics.median(walls),
    }


def run_benchmarks(
    *,
    outdir: Path,
    out: Path | None,
    programs: tuple[str, ...],
    backends: tuple[int, ...],
    repeats: int,
    small: bool,
    threads: tuple[int, ...] = THREAD_COUNTS,
    cpython: str = sys.executable,
    runtime_archive: str | None = None,
    timeout: float = 600.0,
) -> list[dict]:
    """Compile every program once, then measure CPython and each collector
    for every argument list. Returns the rows it appended to ``out``."""
    outdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    rows: list[dict] = []

    def emit(row: dict) -> None:
        rows.append(row)
        if out is not None:
            with out.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
        print(row["label"], row["program"], " ".join(row["args"]),
              round(row["median"], 4), flush=True)

    for name in programs:
        binary = compile_program(name, outdir, runtime_archive)
        for args in program_argv_sets(name, small=small, threads=threads):
            source = str(PROGRAMS / (name + ".py"))
            reference = measure([cpython, source, *args], env, repeats, timeout, None)
            emit({"label": "cpython", "program": name, "args": list(args), **reference})
            for backend in backends:
                backend_env = dict(env, PCC_GC_BACKEND=str(backend))
                result = measure([str(binary), *args], backend_env, repeats, timeout,
                                 reference["fields"])
                emit({"label": f"pcc-gc{backend}", "program": name, "args": list(args),
                      **result})
    return rows


def _int_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split(",") if part.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path,
                        help="JSON-lines file the measurements are appended to")
    parser.add_argument("--build-dir", type=Path, default=None,
                        help="where the binaries go (default: next to --out)")
    parser.add_argument("--programs", default=",".join(SINGLE_THREAD + MULTI_THREAD))
    parser.add_argument("--backends", default="0,1,2,3,4")
    parser.add_argument("--threads", default=",".join(str(n) for n in THREAD_COUNTS))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--small", action="store_true",
                        help="check sizes instead of the timing sizes")
    parser.add_argument("--cpython", default=sys.executable)
    parser.add_argument("--runtime-archive", default=None)
    parser.add_argument("--timeout", type=float, default=600.0)
    options = parser.parse_args(argv)
    names = tuple(part for part in options.programs.split(",") if part)
    unknown = [name for name in names if name not in SINGLE_THREAD + MULTI_THREAD]
    if unknown:
        parser.error(f"unknown programs: {', '.join(unknown)}")
    build_dir = options.build_dir or options.out.parent / (options.out.stem + "-bin")
    try:
        run_benchmarks(
            outdir=build_dir, out=options.out, programs=names,
            backends=_int_tuple(options.backends), repeats=options.repeats,
            small=options.small, threads=_int_tuple(options.threads),
            cpython=options.cpython, runtime_archive=options.runtime_archive,
            timeout=options.timeout,
        )
    except BenchmarkMismatch as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
