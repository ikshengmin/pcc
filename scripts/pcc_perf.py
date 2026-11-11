#!/usr/bin/env python3
"""One entry for building, measuring and explaining runtime performance.

    arm NAME            seal the committed tree, with the working tree's
                        version of each --with PATH (your own uncommitted
                        edits; other sessions' stay out), build its runtime
                        and the benchmark program, and run every primitive
                        on all five collectors
    ab A B              alternating paired runs of two arms: medians, pair
                        wins, stopped time and graph-lock waits per collector
    table NAME          every collector with one and four workers, against
                        GC0 and against the recorded asyncio figure
    asyncio             measure the CPython asyncio counterpart and record it
    prim NAME           what each primitive operation costs: CPython, every
                        collector with one worker, and four-worker scaling;
                        with --against OTHER, the change between two arms
    gap NAME            where a request's time goes, by category, per collector
    top NAME            the functions a request (or, with --primitive, one
                        operation) spends its time in; against another
                        collector or arm, the functions that differ
    locks NAME          which callers wait for the graph lock
    layout A [B]        cache-line audit (scripts/pcc_hot_layout.py)

Arms, the object cache and the ledger live outside the repository, under
PCC_PERF_HOME (default ~/.cache/pcc/perf). An arm is a sealed copy of the
source, so two arms differ by exactly their recorded diffs and an edit to
the working tree cannot change a measurement in progress. The workspace is
shared: an arm takes HEAD, and uncommitted files only where --with names
them, so another session's unfinished edit never enters a build.

The loop this replaces took ten minutes per question. What makes it short:

* the self backend's object cache is on for the benchmark build: a change
  to the runtime rebuilds the runtime archive and relinks, without
  re-emitting the application's unchanged modules;
* every run carries its own attribution (stopped time by sweep phase,
  graph-lock waits by class, allocations), so a throughput change is read
  with its cause;
* the primitives (benchmarks/handler/primitive_ops.py) run the same source
  under CPython and compiled: a minute tells which operation costs more
  compiled than interpreted, more under one collector than another, or
  does not scale to four workers, before any profile is taken;
* a layout audit and a lock-caller profile answer in seconds the two
  questions that otherwise each cost a build: "did a hot word change
  neighbours" and "who waits".

A result is a claim only with at least five pairs and an unchanged control
collector; ``ab`` prints both, and appends every comparison to the ledger.
"""

from __future__ import annotations

import argparse
import bisect
import collections
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).absolute().parents[1]
HOME = Path(os.environ.get("PCC_PERF_HOME", "~/.cache/pcc/perf")).expanduser()
SEALED = (
    "pcc", "tests", "scripts", "utils", "benchmarks", "conftest.py", "run.py",
    "pyproject.toml", "uv.lock", "hatch_build.py", ".python-version",
)
PROGRAMS = ("sync_payload", "native_stats")
COLLECTORS = (0, 1, 2, 3, 4)
WORKERS = (1, 4)
# PCC_GC_COUNTER indexes the handler program reports.
STW_SUM_US = 120
SWEEP_PHASE_US = (133, 137, 141)
LOCK_WAIT_COUNT = (146, 149, 152, 155)
LOCK_WAIT_US = (147, 150, 153, 156)
ALLOCATIONS = 0
# (primitive, operations per batch): a batch is about a millisecond of
# compiled code, so the clock read between batches is not what is measured.
PRIMITIVES = (
    ("loop", 200000), ("range", 200000), ("call", 100000), ("kwcall", 100000),
    ("method", 100000), ("attr", 100000), ("instance", 20000), ("tuple", 20000),
    ("list", 20000), ("dict", 10000), ("dict_get", 50000), ("dict_set", 50000),
    ("list_append", 32000), ("list_iter", 160000), ("str_concat", 20000),
    ("int_str", 20000), ("fstring", 10000), ("encode", 20000), ("json", 1000),
    ("closure", 20000), ("try", 100000), ("raise", 2000), ("generator", 50000),
    ("clock", 20000), ("sleep", 2000), ("scope", 2000), ("spawn", 500),
    ("request", 200),
)
# Primitives of the virtual-thread runtime; their CPython counterparts are
# asyncio tasks (benchmarks/handler/primitives_asyncio.py).
TASK_PRIMITIVES = frozenset(("sleep", "scope", "spawn", "request"))
SMOKE_BATCH = 64

# Functions that own a category of a request's time. The frame nearest the
# program entry wins, so a collection owns everything it calls.
CATEGORIES = (
    ("collect", ("pcc_gc_safepoint", "pcc_gc_collect_tracing", "pcc_gc_collect",
                 "py_gc_collect", "pcc_gc_alloc_stage_drain_stopped")),
    ("register", ("pcc_gc_note_object_allocated_sized", "pcc_gc_alloc_stage_note")),
    ("unregister", ("pcc_gc_note_object_freeing",)),
    ("barrier", ("pcc_gc_note_slot_write_barrier", "pcc_gc_note_store",
                 "pcc_gc_note_relocation_read", "pcc_gc_store_ptr",
                 "pcc_gc_store_root_plan_init")),
    ("roots", ("pcc_gc_store_root", "pcc_gc_native_frame_enter",
               "pcc_gc_frame_leave_local_mapped", "pcc_gc_native_root_bind_range",
               "pcc_gc_note_frame_enter", "pcc_gc_note_frame_leave",
               "pcc_gc_root_copy_lease", "pcc_gc_release", "pcc_gc_retain")),
    ("graph-lock", ("pcc_thread_graph_lock_acquire_for", "pcc_thread_graph_lock_release",
                    "user_freestanding_thread_kernel_pthread__graph_wait_acquire")),
    ("allocator", ("malloc", "free", "calloc", "realloc", "memset",
                   "pcc_allocator_alloc_object", "pcc_gc_free_object_memory")),
)
LOCK_WAIT = "user_freestanding_thread_kernel_pthread__graph_wait_acquire"
LOCK_WRAPPERS = frozenset((
    LOCK_WAIT, "pcc_thread_graph_lock_acquire_for", "pcc_py_gc_minor_graph_lock",
    "pcc_py_gc_minor_graph_lock_for", "pcc_thread_graph_lock_acquire",
))
IDLE = frozenset((
    "__psynch_cvwait", "__semwait_signal", "mach_msg2_trap", "__workq_kernreturn",
    "__select", "__ulock_wait2", "__ulock_wait", "kevent", "__psynch_mutexwait",
    "mach_msg_trap", "semaphore_wait_trap",
))


# ---- arms ----------------------------------------------------------------

def arm_dir(name: str) -> Path:
    return HOME / "arms" / name


def seal(name: str, with_paths: tuple[str, ...] = (), root: Path | None = None) -> Path:
    """Copy HEAD's files into the arm, then the working tree's version of
    every file under ``with_paths`` (tracked or new; a file deleted there
    is deleted in the arm). Returns the arm directory."""
    root = REPO_ROOT if root is None else root
    arm = arm_dir(name)
    source = arm / "source"
    if source.exists():
        raise SystemExit(f"arm {name} already exists: {arm}")
    source.mkdir(parents=True)

    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", *args], cwd=root)

    head = git("rev-parse", "HEAD").decode().strip()
    sealed = [path for path in SEALED if (root / path).exists()]
    archive = subprocess.Popen(
        ["git", "archive", "--format=tar", head, "--", *sealed],
        cwd=root, stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", str(source)], stdin=archive.stdout, check=True)
    if archive.wait() != 0:
        raise SystemExit("git archive failed")

    overlaid = []
    for path in with_paths:
        listed = git("ls-files", "-z", "--cached", "--others", "--exclude-standard",
                     "--", path).split(b"\0")
        for raw in listed:
            if not raw:
                continue
            relative = raw.decode()
            origin, target = root / relative, source / relative
            if origin.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origin, target)
            elif target.exists():
                target.unlink()
            overlaid.append(relative)
    diff = git("diff", head, "--", *with_paths) if with_paths else b""
    # What the working tree has changed that this arm does not contain.
    dirty = [line[3:] for line in git("status", "--porcelain", "--", *sealed)
             .decode().splitlines() if line[3:] not in overlaid]

    files = {}
    for target in sorted(source.rglob("*")):
        if target.is_file():
            files[str(target.relative_to(source))] = hashlib.sha256(
                target.read_bytes()).hexdigest()
    (arm / "source.json").write_text(json.dumps({
        "revision": head, "arm": name, "with": list(with_paths),
        "overlaid": sorted(set(overlaid)), "left_out": dirty,
        "dirty_diff_sha256": hashlib.sha256(diff).hexdigest(),
        "dirty_diff_bytes": len(diff), "files": files,
    }, indent=1) + "\n")
    (arm / "worktree.diff").write_bytes(diff)
    return arm


def _gateway_package() -> Path:
    """The pcc_gateway package the handler program imports: the sibling
    checkout, unless PCC_GATEWAY_PACKAGE names another."""
    configured = os.environ.get("PCC_GATEWAY_PACKAGE", "")
    package = Path(configured) if configured else REPO_ROOT.parent / "pcc-gateway" / "pcc_gateway"
    if not (package / "structured.py").is_file():
        raise SystemExit(
            f"pcc_gateway package not found at {package}; set PCC_GATEWAY_PACKAGE")
    return package


def build(name: str, timeout: int, max_tree_rss: int) -> int:
    """Build a sealed arm under the shared performance lock and RSS guard."""
    arm = arm_dir(name)
    programs = arm / "programs"
    programs.mkdir(exist_ok=True)
    for program in sorted((arm / "source/benchmarks/handler").glob("*.py")):
        shutil.copy2(program, programs)
    package = programs / "pcc_gateway"
    if not package.exists():
        shutil.copytree(_gateway_package(), package,
                        ignore=shutil.ignore_patterns("__pycache__"))
    sys.path.insert(0, str(REPO_ROOT))
    from scripts.file_lock import exclusive_file_lock
    from scripts import run_process_tree_sample

    environment = dict(
        UV_PROJECT_ENVIRONMENT=str(REPO_ROOT / ".venv"), UV_NO_SYNC="1",
        PCC_THREADS="1", PCC_REFCOUNT_KIND="atomic", PCC_RUNTIME_BUILD_JOBS="6",
        PCC_NO_AUTO_PCC1="1", PYTHONPATH=str(arm / "source"),
        PCC_PERF_HOME=str(HOME),
    )
    os.environ.update(environment)
    (REPO_ROOT / "build").mkdir(exist_ok=True)
    with exclusive_file_lock(str(REPO_ROOT / "build/.pcc-performance.lock"), blocking=False):
        return run_process_tree_sample.main([
            "--result", str(arm / "guard.json"), "--samples", str(arm / "rss.tsv"),
            "--stdout", str(arm / "build.stdout"), "--stderr", str(arm / "build.stderr"),
            "--cwd", str(arm / "source"), "--timeout", str(timeout),
            "--max-tree-rss-bytes", str(max_tree_rss),
            "--darwin-preflight-reserve-bytes", "8589934592",
            "--no-performance-lock", "--",
            "uv", "run", "--no-sync", "python", str(arm / "source/scripts/pcc_perf.py"),
            "_build", str(arm),
        ])


def build_child(arm: Path) -> int:
    """Runs inside the sealed source: runtime archive, programs, smoke runs."""
    import pcc
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from pcc.frontends.python.owned_runtime_build import ensure_target_runtime

    source = arm / "source"
    assert Path(pcc.__file__).resolve().parents[1] == source, pcc.__file__
    identity = json.loads((arm / "source.json").read_text())

    def digest(path) -> str:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def verify() -> None:
        for relative, expected in identity["files"].items():
            assert digest(source / relative) == expected, relative

    verify()
    started = time.monotonic()
    runtime = Path(ensure_target_runtime(str(source / "pcc/runtime"), host_target_triple()))
    report = {
        "complete": False, "revision": identity["revision"], "runtime": str(runtime),
        "runtime_sha256": digest(runtime), "runtime_seconds": time.monotonic() - started,
        "programs": {}, "runs": [],
    }

    def save() -> None:
        (arm / "build.json").write_text(json.dumps(report, indent=1) + "\n")

    save()
    os.environ.update(
        PCC_RUNTIME_ARCHIVE=str(runtime), PCC_THREADED_RUNTIME_ARCHIVE=str(runtime),
        PCC_NO_AUTO_PCC1="1", PCC_TEST_NO_NATIVE_PROVISIONING="1",
        # The application's modules are identical between arms that differ
        # only in the runtime: reuse their objects (keyed by emitted IR and
        # the emitter's own source identity).
        PCC_SELF_BACKEND_OBJECT_CACHE_IDENTITY="pcc-perf",
        PCC_SELF_BACKEND_OBJECT_CACHE_DIR=str(HOME / "object-cache"),
    )
    for program in PROGRAMS:
        binary = arm / program
        started = time.monotonic()
        compile_python(
            str(arm / "programs" / (program + ".py")), str(binary), backend="self",
            ir_scaffold_mode="on", runtime_archive=str(runtime),
        )
        report["programs"][program] = {
            "binary": str(binary), "sha256": digest(binary),
            "seconds": time.monotonic() - started,
        }
        save()
        cases = smoke_cases(program, binary, arm / "programs")
        for collector in COLLECTORS:
            for argv, expected in cases:
                run = subprocess.run(
                    argv, capture_output=True, text=True, timeout=120,
                    env={"PATH": "/nonexistent", "PCC_GC_BACKEND": str(collector),
                         "BENCH_N": "31"},
                )
                report["runs"].append({
                    "program": program, "gc": collector, "argv": argv[1:],
                    "code": run.returncode, "stdout": run.stdout[-400:],
                    "stderr": run.stderr[-400:],
                })
                if not smoke_passed(run.returncode, run.stdout, run.stderr, expected):
                    save()
                    print("SMOKE FAILED", program, "gc", collector, argv[1:],
                          run.returncode, "expected", expected,
                          run.stdout[-300:], run.stderr[-300:], flush=True)
                    return 1
        save()
    verify()
    assert digest(runtime) == report["runtime_sha256"]
    report["complete"] = True
    save()
    print("ARM COMPLETE", arm.name, flush=True)
    return 0


def _load_module(path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def smoke_cases(program: str, binary: Path, programs: Path) -> list[tuple[list[str], dict]]:
    """(argv, expected output) of the runs that accept a built program on
    every collector. A primitive's expected sum is what CPython computes
    from the same source."""
    if program == "sync_payload":
        return [([str(binary)], {"stdout": "2666\n"})]
    cases = [([str(binary), "20", "0", "3", "--summary", str(workers)], {"requests": 60})
             for workers in WORKERS]
    reference = _load_module(programs / "primitive_ops.py").OPS
    for name, _ in PRIMITIVES:
        value = SMOKE_BATCH if name in TASK_PRIMITIVES else reference[name](SMOKE_BATCH)
        for workers in WORKERS:
            cases.append((
                [str(binary), "prim", name, str(SMOKE_BATCH), "0", str(workers)],
                {"operations": SMOKE_BATCH * workers, "checksum": value * workers},
            ))
    return cases


def smoke_passed(code: int, stdout: str, stderr: str, expected: dict) -> bool:
    if code != 0 or stderr != "":
        return False
    if "stdout" in expected:
        return stdout == expected["stdout"]
    try:
        data = json.loads(stdout)
    except ValueError:
        return False
    return all(data.get(key) == value for key, value in expected.items())


def binary_of(name: str, program: str = "native_stats") -> Path:
    binary = arm_dir(name) / program
    if not binary.is_file():
        raise SystemExit(f"arm {name} has no {program}: build it with `arm {name}`")
    return binary


# ---- runs ----------------------------------------------------------------

def run_once(binary: Path, collector: int, workers: int, rounds: int,
             concurrency: int = 100) -> dict:
    """One handler run: requests per second and the counters it reports."""
    out = subprocess.run(
        [str(binary), str(concurrency), "0", str(rounds), "--summary", str(workers)],
        capture_output=True, text=True, timeout=600,
        env={"PATH": "/nonexistent", "PCC_GC_BACKEND": str(collector)},
    )
    if out.returncode != 0:
        raise SystemExit(
            f"{binary} failed on gc{collector} w{workers}: {out.stderr[-400:]}")
    data = json.loads(out.stdout)
    counters = {int(key): value for key, value in data.get("gc", {}).items()}
    return {
        "qps": data["requests"] / (data["finished_at"] - data["started_at"]),
        "requests": data["requests"],
        "stw_ms": counters.get(STW_SUM_US, 0) / 1000.0,
        "sweep_ms": sum(counters.get(index, 0) for index in SWEEP_PHASE_US) / 1000.0,
        "lock_waits": sum(counters.get(index, 0) for index in LOCK_WAIT_COUNT),
        "lock_wait_ms": sum(counters.get(index, 0) for index in LOCK_WAIT_US) / 1000.0,
        "allocations": counters.get(ALLOCATIONS, 0),
    }


def summarize_pairs(first: list[dict], second: list[dict]) -> dict:
    """Medians of two arms' runs and how many pairs the second won."""
    def median(runs, key):
        return statistics.median(run[key] for run in runs)

    qps_a, qps_b = median(first, "qps"), median(second, "qps")
    return {
        "pairs": len(first),
        "qps_a": qps_a, "qps_b": qps_b,
        "change_percent": 100.0 * (qps_b / qps_a - 1.0) if qps_a else 0.0,
        "wins": sum(1 for a, b in zip(first, second) if b["qps"] > a["qps"]),
        "stw_ms_a": median(first, "stw_ms"), "stw_ms_b": median(second, "stw_ms"),
        "lock_wait_ms_a": median(first, "lock_wait_ms"),
        "lock_wait_ms_b": median(second, "lock_wait_ms"),
        "lock_waits_a": median(first, "lock_waits"),
        "lock_waits_b": median(second, "lock_waits"),
    }


def compare(a: str, b: str, pairs: int, collectors, workers, rounds: int) -> list[dict]:
    binary_a, binary_b = binary_of(a), binary_of(b)
    runs: dict[tuple, list[dict]] = {}
    for index in range(pairs):
        for collector in collectors:
            for count in workers:
                order = ((a, binary_a), (b, binary_b))
                if index % 2:
                    order = order[::-1]
                for name, binary in order:
                    runs.setdefault((collector, count, name), []).append(
                        run_once(binary, collector, count, rounds))
    results = []
    for collector in collectors:
        for count in workers:
            row = summarize_pairs(
                runs[(collector, count, a)], runs[(collector, count, b)])
            row.update({"gc": collector, "workers": count, "a": a, "b": b,
                        "requests": rounds * 100, "time": time.time()})
            results.append(row)
    return results


def print_comparison(rows: list[dict]) -> None:
    for row in rows:
        print(
            f"gc{row['gc']} w{row['workers']}  {row['qps_a']:8.0f} -> {row['qps_b']:8.0f} "
            f"({row['change_percent']:+6.1f}%, {row['wins']}/{row['pairs']} pairs)  "
            f"stopped {row['stw_ms_a']:5.0f} -> {row['stw_ms_b']:5.0f} ms  "
            f"lock wait {row['lock_wait_ms_a']:6.0f} -> {row['lock_wait_ms_b']:6.0f} ms "
            f"({row['lock_waits_a']:.0f} -> {row['lock_waits_b']:.0f})",
            flush=True,
        )
    for row in rows:
        if row["pairs"] < 5:
            print("note: fewer than five pairs is a screen, not a result")
            break


def append_ledger(rows: list[dict]) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    with open(HOME / "ledger.jsonl", "a", encoding="utf-8") as ledger:
        for row in rows:
            ledger.write(json.dumps(row, sort_keys=True) + "\n")


def measure_asyncio(rounds: int, repeats: int) -> float:
    program = REPO_ROOT / "benchmarks/handler/asyncio_stats.py"
    rates = []
    for _ in range(repeats):
        out = subprocess.run(
            [sys.executable, str(program), "100", "0", str(rounds), "--summary"],
            capture_output=True, text=True, timeout=600, check=True,
        )
        data = json.loads(out.stdout)
        rates.append(data["requests"] / (data["finished_at"] - data["started_at"]))
    rate = statistics.median(rates)
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / "asyncio.json").write_text(json.dumps({
        "qps": rate, "runs": rates, "python": sys.version.split()[0],
        "requests": rounds * 100, "time": time.time(),
    }, indent=1) + "\n")
    return rate


def print_table(name: str, rounds: int, repeats: int) -> None:
    binary = binary_of(name)
    asyncio_rate = 0.0
    recorded = HOME / "asyncio.json"
    if recorded.is_file():
        asyncio_rate = json.loads(recorded.read_text())["qps"]
    base = {}
    print(f"{'':6s} {'req/s':>9s} {'of GC0':>7s} {'of asyncio':>10s} "
          f"{'stopped ms':>10s} {'lock wait ms':>12s}")
    for collector in COLLECTORS:
        rates = {}
        for count in WORKERS:
            runs = [run_once(binary, collector, count, rounds) for _ in range(repeats)]
            rate = statistics.median(run["qps"] for run in runs)
            rates[count] = rate
            if collector == 0:
                base[count] = rate
            against = f"{100 * rate / asyncio_rate:9.0f}%" if asyncio_rate else "         -"
            print(
                f"gc{collector} w{count} {rate:9.0f} {100 * rate / base[count]:6.0f}% {against} "
                f"{statistics.median(run['stw_ms'] for run in runs):10.0f} "
                f"{statistics.median(run['lock_wait_ms'] for run in runs):12.0f}",
                flush=True,
            )
        if rates[4] < rates[1]:
            print(f"  !! gc{collector}: four workers below one")


# ---- primitives ----------------------------------------------------------

def run_primitive(binary: Path, name: str, batch: int, milliseconds: int,
                  workers: int, collector: int) -> dict:
    """One compiled primitive run: operations per second of all workers and
    the objects it allocated."""
    out = subprocess.run(
        [str(binary), "prim", name, str(batch), str(milliseconds), str(workers)],
        capture_output=True, text=True, timeout=600,
        env={"PATH": "/nonexistent", "PCC_GC_BACKEND": str(collector)},
    )
    if out.returncode != 0:
        raise SystemExit(
            f"{binary} {name} failed on gc{collector} w{workers}: {out.stderr[-400:]}")
    data = json.loads(out.stdout)
    counters = {int(key): value for key, value in data.get("gc", {}).items()}
    return {
        "rate": data["operations"] / data["seconds"],
        "operations": data["operations"],
        "allocations": counters.get(ALLOCATIONS, 0),
    }


def run_primitive_cpython(name: str, batch: int, milliseconds: int) -> float:
    """Operations per second of the CPython counterpart."""
    program = REPO_ROOT / "benchmarks/handler/primitives_asyncio.py"
    out = subprocess.run(
        [sys.executable, str(program), name, str(batch), str(milliseconds)],
        capture_output=True, text=True, timeout=600, check=True,
    )
    data = json.loads(out.stdout)
    return data["operations"] / data["seconds"]


def allocations_per_operation(timed: dict, single: dict, batch: int) -> float:
    """Objects one operation allocates: a timed run against a one-batch run
    of the same program, so start-up allocations cancel."""
    operations = timed["operations"] - batch
    if operations <= 0:
        return 0.0
    return (timed["allocations"] - single["allocations"]) / operations


def measure_primitives(name: str, names, collectors, workers,
                       milliseconds: int, repeats: int) -> list[dict]:
    """Per primitive: CPython's rate, every collector's rate by worker
    count, and the objects one operation allocates."""
    binary = binary_of(name)
    batches = dict(PRIMITIVES)
    rows = []
    for primitive in names:
        batch = batches[primitive]
        row = {
            "primitive": primitive, "arm": name, "batch": batch,
            "cpython": statistics.median(
                run_primitive_cpython(primitive, batch, milliseconds)
                for _ in range(repeats)),
            "rates": {}, "time": time.time(),
        }
        for collector in collectors:
            for count in workers:
                runs = [run_primitive(binary, primitive, batch, milliseconds, count, collector)
                        for _ in range(repeats)]
                row["rates"][f"{collector}/{count}"] = statistics.median(
                    run["rate"] for run in runs)
                if collector == collectors[0] and count == workers[0]:
                    single = run_primitive(binary, primitive, batch, 0, count, collector)
                    row["allocations"] = allocations_per_operation(runs[0], single, batch)
        rows.append(row)
        print_primitive_row(row, collectors, workers, first=len(rows) == 1)
    return rows


def primitive_cells(row: dict, collectors, workers) -> list[str]:
    """The printed cells of one primitive: CPython and the first collector
    in nanoseconds per operation with their ratio, every other collector as
    a ratio to the first, and each collector's scaling to the last worker
    count."""
    base_workers, top_workers = workers[0], workers[-1]
    rates = row["rates"]
    base = rates[f"{collectors[0]}/{base_workers}"]
    cells = [
        f"{row['primitive']:12s}",
        f"{1e9 / row['cpython']:9.1f}",
        f"{1e9 * base_workers / base:9.1f}",
        f"{row['cpython'] * base_workers / base:6.2f}",
        f"{row.get('allocations', 0.0):6.1f}",
    ]
    for collector in collectors[1:]:
        cells.append(f"{base / rates[f'{collector}/{base_workers}']:6.2f}")
    if top_workers != base_workers:
        for collector in collectors:
            single = rates[f"{collector}/{base_workers}"]
            cells.append(f"{rates[f'{collector}/{top_workers}'] / single:5.2f}")
    return cells


def print_primitive_row(row: dict, collectors, workers, first: bool = False) -> None:
    if first:
        heads = [f"{'primitive':12s}", f"{'cpython':>9s}", f"{'gc%d ns' % collectors[0]:>9s}",
                 f"{'x cpy':>6s}", f"{'allocs':>6s}"]
        heads += [f"{'gc%d x' % collector:>6s}" for collector in collectors[1:]]
        if workers[-1] != workers[0]:
            heads += [f"{'g%dw%d' % (collector, workers[-1]):>5s}" for collector in collectors]
        print(" ".join(heads))
    print(" ".join(primitive_cells(row, collectors, workers)), flush=True)


def compare_primitives(a: str, b: str, names, collectors, workers,
                       milliseconds: int, pairs: int) -> list[dict]:
    """Alternating paired primitive runs of two arms: the change of the
    second arm's rate, and whether every pair agrees on its direction."""
    binary_a, binary_b = binary_of(a), binary_of(b)
    batches = dict(PRIMITIVES)
    rows = []
    for primitive in names:
        batch = batches[primitive]
        row = {"primitive": primitive, "a": a, "b": b, "pairs": pairs,
               "changes": {}, "agreed": {}, "time": time.time()}
        for collector in collectors:
            for count in workers:
                first, second = [], []
                for index in range(pairs):
                    order = ((first, binary_a), (second, binary_b))
                    if index % 2:
                        order = order[::-1]
                    for rates, binary in order:
                        rates.append(run_primitive(
                            binary, primitive, batch, milliseconds, count, collector)["rate"])
                key = f"{collector}/{count}"
                row["changes"][key] = 100.0 * (
                    statistics.median(second) / statistics.median(first) - 1.0)
                wins = sum(1 for x, y in zip(first, second) if y > x)
                row["agreed"][key] = wins in (0, pairs)
        rows.append(row)
        print_primitive_change(row, collectors, workers, first=len(rows) == 1)
    return rows


def print_primitive_change(row: dict, collectors, workers, first: bool = False) -> None:
    keys = [f"{collector}/{count}" for collector in collectors for count in workers]
    if first:
        print(f"{'primitive':12s} " + " ".join(
            f"{'gc%sw%s' % tuple(key.split('/')):>8s}" for key in keys))
    cells = []
    for key in keys:
        # A star: every pair moved the same way, by three percent or more.
        mark = "*" if row["agreed"][key] and abs(row["changes"][key]) >= 3.0 else " "
        cells.append(f"{row['changes'][key]:+6.1f}%{mark}")
    print(f"{row['primitive']:12s} " + " ".join(cells), flush=True)


# ---- profiles ------------------------------------------------------------

def text_symbols(binary: Path) -> list[tuple[int, str]]:
    sys.path.insert(0, str(REPO_ROOT))
    from pcc.backend.macho_spec import parse_object

    found = []
    for row in parse_object(binary.read_bytes()).symbols():
        if (row["n_type"] & 0xE0) != 0 or (row["n_type"] & 0x0E) != 0x0E:
            continue
        name = row["name"]
        found.append((row["n_value"], name[1:] if name.startswith("_") else name))
    found.sort()
    return found


def parse_sample(text: str, symbols: list[tuple[int, str]], image: str) -> list[list]:
    """[indent, count, function] rows of a macOS ``sample`` call graph, with
    the image's frames resolved by address (its own names can be wrong)."""
    addresses = [address for address, _ in symbols]
    match = re.search(
        r"^\s*(0x[0-9a-f]+)\s+-\s+(0x[0-9a-f]+)\s+\+?%s " % re.escape(image), text, re.M)
    base = int(match.group(1), 16) if match else None
    tag = "(in %s)" % image

    def name(label: str) -> str:
        if tag in label and base is not None:
            address = re.search(r"\[(0x[0-9a-f]+)", label)
            if address:
                index = bisect.bisect_right(
                    addresses, 0x100000000 + int(address.group(1), 16) - base) - 1
                if index >= 0:
                    return symbols[index][1]
        # Another image's frame: ``sample`` prints its C name as it is.
        return label.split("  (in")[0].strip()[:70]

    graph = text.split("Call graph:", 1)[1].split("Total number in stack", 1)[0]
    rows = []
    for line in graph.splitlines():
        match = re.match(r"^(\s*[+|:!\s]*?)(\d+)\s+(.*)$", line)
        if match:
            rows.append([len(match.group(1)), int(match.group(2)), name(match.group(3))])
    return rows


def walk_sample(rows: list[list]):
    """(stack names, function, self count, thread) for every row."""
    own = [row[1] for row in rows]
    stack: list[int] = []
    for index, (depth, count, _) in enumerate(rows):
        while stack and rows[stack[-1]][0] >= depth:
            stack.pop()
        if stack:
            own[stack[-1]] -= count
        stack.append(index)
    stack = []
    for index, (depth, _, function) in enumerate(rows):
        while stack and rows[stack[-1]][0] >= depth:
            stack.pop()
        names = [rows[parent][2] for parent in stack]
        yield names, function, max(own[index], 0), (names[0] if names else function)
        stack.append(index)


def category_shares(rows: list[list]) -> dict[str, int]:
    """Busy self samples of all threads by the category owning each stack."""
    shares: collections.Counter = collections.Counter()
    for names, function, own, _ in walk_sample(rows):
        if not own or function in IDLE:
            continue
        owner = "program"
        for frame in names + [function]:
            for category, members in CATEGORIES:
                if frame in members:
                    owner = category
                    break
            if owner != "program":
                break
        shares[owner] += own
    return dict(shares)


def lock_callers(rows: list[list]) -> dict[str, int]:
    """Graph-lock wait samples by the function that asked for the lock."""
    callers: collections.Counter = collections.Counter()
    for names, function, own, _ in walk_sample(rows):
        if function != LOCK_WAIT or not own:
            continue
        caller = next((n for n in reversed(names) if n not in LOCK_WRAPPERS), "?")
        callers[caller] += own
    return dict(callers)


def function_shares(rows: list[list]) -> dict[str, int]:
    """Busy self samples of all threads by function."""
    shares: collections.Counter = collections.Counter()
    for _, function, own, _ in walk_sample(rows):
        if own and function not in IDLE:
            shares[function] += own
    return dict(shares)


def inclusive_shares(rows: list[list]) -> dict[str, int]:
    """Busy samples under each function, counted once per stack."""
    shares: collections.Counter = collections.Counter()
    for names, function, own, _ in walk_sample(rows):
        if not own or function in IDLE:
            continue
        for frame in set(names + [function]):
            shares[frame] += own
    return dict(shares)


def scaled(shares: dict[str, int], busy: int, unit_time: float) -> dict[str, float]:
    """Each function's part of ``unit_time``, the processor time of one
    request or operation, given the profile's ``busy`` samples."""
    return {name: unit_time * count / (busy or 1) for name, count in shares.items()}


def top_rows(first: dict[str, float], second: dict[str, float] | None,
             limit: int) -> list[tuple]:
    """(function, cost, other cost, change): the costliest functions, or
    with a second profile the functions whose cost differs most."""
    if second is None:
        ordered = sorted(first.items(), key=lambda item: -item[1])[:limit]
        return [(name, cost, None, None) for name, cost in ordered]
    rows = [(name, first.get(name, 0.0), second.get(name, 0.0),
             second.get(name, 0.0) - first.get(name, 0.0))
            for name in set(first) | set(second)]
    rows.sort(key=lambda row: (-abs(row[3]), row[0]))
    return rows[:limit]


def profile(name: str, collector: int, workers: int, seconds: int,
            primitive: str = "") -> tuple[list[list], float, str]:
    """Sample one run of the handler, or of one primitive. Returns the
    call-graph rows, the processor time of one request or operation in
    nanoseconds (from an unsampled run: the sample only splits it), and
    what that unit is called."""
    binary = binary_of(name)
    if primitive:
        batch = dict(PRIMITIVES)[primitive]
        rate = run_primitive(binary, primitive, batch, 300, workers, collector)["rate"]
        argv = ["prim", primitive, str(batch), str(seconds * 1000 + 700), str(workers)]
        unit = "operation"
    else:
        rate = run_once(binary, collector, workers, 100)["qps"]
        rounds = max(int(rate * (seconds + 0.7) / 100), 100)
        argv = ["100", "0", str(rounds), "--summary", str(workers)]
        unit = "request"
    target = arm_dir(name) / f"gc{collector}w{workers}{primitive}.sample"
    process = subprocess.Popen(
        [str(binary), *argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={"PATH": "/nonexistent", "PCC_GC_BACKEND": str(collector)},
    )
    time.sleep(0.3)
    subprocess.run(["/usr/bin/sample", str(process.pid), str(seconds), "-file", str(target)],
                   capture_output=True)
    process.communicate(timeout=900)
    if process.returncode != 0:
        raise SystemExit(f"{binary} failed while sampled on gc{collector} w{workers}")
    rows = parse_sample(target.read_text(), text_symbols(binary), binary.name)
    return rows, 1e9 * workers / rate, unit


def _cost(nanoseconds: float, unit: str) -> str:
    """Requests are read in microseconds, operations in nanoseconds."""
    if unit == "request":
        return f"{nanoseconds / 1000.0:8.2f}"
    return f"{nanoseconds:8.1f}"


def print_gap(name: str, collectors, workers: int, seconds: int, primitive: str) -> None:
    for collector in collectors:
        rows, unit_time, unit = profile(name, collector, workers, seconds, primitive)
        shares = category_shares(rows)
        total = sum(shares.values())
        label = "us" if unit == "request" else "ns"
        print(f"gc{collector} w{workers}: {_cost(unit_time, unit).strip()} {label} of "
              f"processor time per {primitive or unit}")
        for category, count in sorted(shares.items(), key=lambda item: -item[1]):
            print(f"    {category:11s} {100 * count / (total or 1):5.1f}%  "
                  f"{_cost(unit_time * count / (total or 1), unit)} {label}")


def print_top(name: str, collector: int, workers: int, seconds: int, primitive: str,
              limit: int, inclusive: bool, other_arm: str, other_collector: int) -> None:
    def costs(arm: str, gc: int) -> tuple[dict[str, float], float, str]:
        rows, unit_time, unit = profile(arm, gc, workers, seconds, primitive)
        busy = sum(function_shares(rows).values())
        shares = inclusive_shares(rows) if inclusive else function_shares(rows)
        return scaled(shares, busy, unit_time), unit_time, unit

    first, first_time, unit = costs(name, collector)
    label = "us" if unit == "request" else "ns"
    kind = "inclusive" if inclusive else "self"
    what = primitive or unit
    if not other_arm and other_collector < 0:
        print(f"{name} gc{collector} w{workers}: {_cost(first_time, unit).strip()} {label} "
              f"per {what}; {kind} {label} by function")
        for function, cost, _, _ in top_rows(first, None, limit):
            print(f"{_cost(cost, unit)}  {function}")
        return
    second_arm = other_arm or name
    second_collector = collector if other_collector < 0 else other_collector
    second, second_time, _ = costs(second_arm, second_collector)
    print(f"{name} gc{collector} -> {second_arm} gc{second_collector}, w{workers}: "
          f"{_cost(first_time, unit).strip()} -> {_cost(second_time, unit).strip()} {label} "
          f"per {what}; {kind} {label} by function, largest change first")
    for function, before, after, change in top_rows(first, second, limit):
        sign = "+" if change >= 0 else "-"
        print(f"{_cost(before, unit)} {_cost(after, unit)}  {sign}{_cost(abs(change), unit).strip():>8s}"
              f"  {function}")


def print_locks(name: str, collector: int, workers: int, seconds: int, primitive: str) -> None:
    rows, unit_time, unit = profile(name, collector, workers, seconds, primitive)
    callers = lock_callers(rows)
    waits = sum(callers.values())
    busy = sum(function_shares(rows).values())
    label = "us" if unit == "request" else "ns"
    print(f"gc{collector} w{workers}: {_cost(unit_time, unit).strip()} {label} per "
          f"{primitive or unit}; graph-lock waits are {100 * waits / (busy or 1):.1f}% "
          f"of busy samples")
    for caller, count in sorted(callers.items(), key=lambda item: -item[1])[:16]:
        print(f"    {100 * count / (waits or 1):5.1f}%  {caller}")


# ---- entry ---------------------------------------------------------------

def _numbers(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split(",") if part)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    arm = commands.add_parser("arm")
    arm.add_argument("name")
    arm.add_argument("--with", dest="with_paths", action="append", default=[],
                     metavar="PATH", help="take PATH from the working tree, not HEAD")
    arm.add_argument("--timeout", type=int, default=900)
    # The handler program's largest module emits in one worker of about
    # 2.4 GiB beside a 1.1 GiB coordinator; the approved build maximum is
    # 8 GiB.
    arm.add_argument("--max-tree-rss", type=int, default=6442450944)

    child = commands.add_parser("_build")
    child.add_argument("arm", type=Path)

    ab = commands.add_parser("ab")
    ab.add_argument("a")
    ab.add_argument("b")
    ab.add_argument("--pairs", type=int, default=5)
    ab.add_argument("--gc", type=_numbers, default=COLLECTORS)
    ab.add_argument("--workers", type=_numbers, default=WORKERS)
    ab.add_argument("--rounds", type=int, default=100)

    table = commands.add_parser("table")
    table.add_argument("name")
    table.add_argument("--rounds", type=int, default=100)
    table.add_argument("--repeats", type=int, default=3)

    oracle = commands.add_parser("asyncio")
    oracle.add_argument("--rounds", type=int, default=100)
    oracle.add_argument("--repeats", type=int, default=5)

    prim = commands.add_parser("prim")
    prim.add_argument("name")
    prim.add_argument("--against", help="a second arm: print the change, not the costs")
    prim.add_argument("--only", default="", help="comma-separated primitives")
    prim.add_argument("--gc", type=_numbers, default=COLLECTORS)
    prim.add_argument("--workers", type=_numbers, default=WORKERS)
    prim.add_argument("--ms", type=int, default=150)
    prim.add_argument("--repeats", type=int, default=1)
    prim.add_argument("--pairs", type=int, default=3)

    gap = commands.add_parser("gap")
    gap.add_argument("name")
    gap.add_argument("--gc", type=_numbers, default=COLLECTORS)
    gap.add_argument("--workers", type=int, default=1)
    gap.add_argument("--seconds", type=int, default=4)
    gap.add_argument("--primitive", default="")

    top = commands.add_parser("top")
    top.add_argument("name")
    top.add_argument("--gc", type=int, default=1)
    top.add_argument("--workers", type=int, default=1)
    top.add_argument("--seconds", type=int, default=4)
    top.add_argument("--primitive", default="")
    top.add_argument("--limit", type=int, default=30)
    top.add_argument("--inclusive", action="store_true")
    top.add_argument("--against", default="", help="another arm")
    top.add_argument("--against-gc", type=int, default=-1, help="another collector")

    locks = commands.add_parser("locks")
    locks.add_argument("name")
    locks.add_argument("--gc", type=int, default=1)
    locks.add_argument("--workers", type=int, default=4)
    locks.add_argument("--seconds", type=int, default=4)
    locks.add_argument("--primitive", default="")

    layout = commands.add_parser("layout")
    layout.add_argument("a")
    layout.add_argument("b", nargs="?")

    args = parser.parse_args(argv)
    if args.command == "arm":
        sealed = seal(args.name, tuple(args.with_paths))
        identity = json.loads((sealed / "source.json").read_text())
        print(f"arm {args.name}: {identity['revision'][:9]} with "
              f"{len(identity['overlaid'])} working-tree files", flush=True)
        for path in identity["left_out"]:
            print(f"  left out (uncommitted, not named by --with): {path}", flush=True)
        code = build(args.name, args.timeout, args.max_tree_rss)
        report = arm_dir(args.name) / "build.json"
        complete = report.is_file() and json.loads(report.read_text())["complete"]
        if code != 0 or not complete:
            print((arm_dir(args.name) / "build.stderr").read_text()[-1500:])
            return 1
        timing = json.loads(report.read_text())
        print(f"arm {args.name}: runtime {timing['runtime_seconds']:.0f} s, " + ", ".join(
            f"{program} {entry['seconds']:.0f} s"
            for program, entry in timing["programs"].items()))
        return 0
    if args.command == "_build":
        return build_child(args.arm)
    if args.command == "ab":
        rows = compare(args.a, args.b, args.pairs, args.gc, args.workers, args.rounds)
        print_comparison(rows)
        append_ledger(rows)
        return 0
    if args.command == "table":
        print_table(args.name, args.rounds, args.repeats)
        return 0
    if args.command == "asyncio":
        print(f"asyncio {measure_asyncio(args.rounds, args.repeats):.0f} req/s")
        return 0
    if args.command == "prim":
        names = [name for name, _ in PRIMITIVES]
        if args.only:
            wanted = args.only.split(",")
            unknown = [name for name in wanted if name not in names]
            if unknown:
                raise SystemExit("unknown primitives: " + ", ".join(unknown))
            names = wanted
        if args.against:
            rows = compare_primitives(args.name, args.against, names, args.gc,
                                      args.workers, args.ms, args.pairs)
            if args.pairs < 5:
                print("note: fewer than five pairs is a screen, not a result")
        else:
            rows = measure_primitives(args.name, names, args.gc, args.workers,
                                      args.ms, args.repeats)
        for row in rows:
            row["kind"] = "prim"
        append_ledger(rows)
        return 0
    if args.command in ("gap", "top", "locks") and args.primitive:
        if args.primitive not in dict(PRIMITIVES):
            raise SystemExit("unknown primitive: " + args.primitive)
    if args.command == "gap":
        print_gap(args.name, args.gc, args.workers, args.seconds, args.primitive)
        return 0
    if args.command == "top":
        print_top(args.name, args.gc, args.workers, args.seconds, args.primitive,
                  args.limit, args.inclusive, args.against, args.against_gc)
        return 0
    if args.command == "locks":
        print_locks(args.name, args.gc, args.workers, args.seconds, args.primitive)
        return 0
    if args.command == "layout":
        import pcc_hot_layout

        argv_layout = [str(binary_of(args.a))]
        if args.b:
            argv_layout += ["--diff", str(binary_of(args.b))]
        return pcc_hot_layout.main(argv_layout)
    return 2


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).absolute().parent))
    raise SystemExit(main())
