#!/usr/bin/env python3
"""One entry for building, measuring and explaining runtime performance.

    arm NAME            seal the committed tree, with the working tree's
                        version of each --with PATH (your own uncommitted
                        edits; other sessions' stay out), build its runtime
                        and the benchmark program, and run every primitive;
                        --reference-runtime clang-O2|clang-codegen is a
                        labeled LLVM reference experiment, not a product
                        route: the same runtime IR compiled by clang (the
                        program stays self-compiled), the bound that code
                        generation can buy
    ab A B             alternating paired runs of two arms: medians, pair
                        wins, stopped time and graph-lock waits; with
                        --control-arm NAME, the noise floor and a verdict
                        for every row
    instr BASE ARM...   instructions and cycles one handler request executes
                        (Darwin /usr/bin/time -l, Linux perf stat), the
                        difference of two run lengths, against the first arm;
                        Linux --counter-backend callgrind measures simulated
                        user instruction reads (Ir), not cycles or IPC;
                        --functions N reports exclusive Ir by reported symbol;
                        a control arm shows how far each moves unchanged
    steps A [B]         executed instructions per request by function on the
                        request thread (Darwin, lldb single-stepping); with B,
                        the functions that differ most
    table NAME          one and four workers, against the recorded asyncio
                        figure for the same worker count
    asyncio             measure the CPython asyncio counterpart with one and
                        four workers (one event loop per process, the same
                        requests split over them) and record it
    prim NAME           what each primitive operation costs: CPython, every
                        collector with one worker, and four-worker scaling;
                        with --against OTHER, the change between two arms
    gap NAME            where a request's time goes, by category, per collector
    top NAME            the functions a request (or, with --primitive, one
                        operation) spends its time in; against another
                        collector or arm, the functions that differ; with
                        --callers F, F's own time split by who called it
    locks NAME          which callers wait for the graph lock
    layout A [B]        cache-line audit (scripts/pcc_hot_layout.py)
    compile NAME        where one compile of the benchmark program spends its
                        processor time: the arm's sealed compiler, its own
                        runtime, empty caches; wall, tree CPU and peak RSS,
                        CPU by process role, by owner package and by function
                        (self and inclusive), from CPU-time (SIGPROF) samples
                        of every Python process of the build; --program
                        stage1 profiles the arm's bootstrap --stage 1, and
                        --keep-ir keeps the backend's input IR for `ir`
    ir PATH...          the largest functions and the block shapes repeated
                        most across emitted IR (.ll files or directories):
                        where code generation copies one sequence into
                        every exit
    profile-preflight  test Linux kernel sample transport on this tool's thread

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

A result is a claim only with at least five pairs and an unchanged control:
``--control-arm`` names a second build of arm A, rotated with A and B. The
control's largest deviation from 1.0 is the noise floor; ``ab``
calls each row a gain or a loss only beyond it, and a change inside a floor
of 5% or more inconclusive. Every comparison goes to the ledger.
Rates are wall-clock and need idle cores: ``ab`` and ``table`` print the
machine's load, and beside each rate the processor time one request cost,
which other work on the machine barely moves (it does not see time spent
waiting for a lock, so it is the cost of the work, not the scaling).
On a loaded machine ``instr`` is the result: the instructions a request
executes do not depend on other work, and its cycles barely do.
"""

from __future__ import annotations

import argparse
import bisect
import collections
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


REPO_ROOT = Path(__file__).absolute().parents[1]
HOME = Path(os.environ.get("PCC_PERF_HOME", "~/.cache/pcc/perf")).expanduser()
SEALED = (
    "pcc", "tests", "scripts", "utils", "benchmarks", "conftest.py", "run.py",
    "pyproject.toml", "uv.lock", "hatch_build.py", ".python-version",
)
PROGRAMS = ("sync_payload", "native_stats")
# `compile --program stage1` profiles the arm's own `bootstrap.py --stage 1`.
STAGE1 = "stage1"
# pcc has one collector; the tuple keeps the per-collector report shape.
COLLECTORS = (0,)
WORKERS = (1, 4)
# PCC_GC_COUNTER indexes the handler program reports.
STW_SUM_US = 120
STW_MAX_US = 121
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
    # pcc_gc_cms_worker_main_py is the GC1-GC4 collector thread; every
    # sample on it is collection work, whatever it calls. A mutator also
    # marks through pcc_gc_tracing_step_cycle when it pays allocation debt.
    ("collect", ("pcc_gc_safepoint", "pcc_gc_collect_tracing", "pcc_gc_collect",
                 "py_gc_collect", "pcc_gc_alloc_stage_drain_stopped",
                 "pcc_gc_cms_worker_main_py", "pcc_gc_tracing_step_cycle")),
    # pcc_gc_register_object_cell indexes an allocator cell; the other two
    # are its fallbacks in pcc_gc_alloc.
    ("register", ("pcc_gc_note_object_allocated_sized", "pcc_gc_alloc_stage_note",
                  "pcc_gc_register_object_cell")),
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


def _gc2_pacer_percent(identity: dict) -> int | None:
    value = identity.get("gc2_pacer_percent")
    if value is not None and (type(value) is not int or not 0 <= value <= 10000):
        raise SystemExit("GC2 pacer percent must be an integer from 0 to 10000")
    return value


def _pacer_env(collector: int, percent: int | None) -> dict:
    env = {"PATH": "/nonexistent", "PCC_GC_BACKEND": str(collector)}
    if collector == 2 and percent is not None:
        env["PCC_GC2_PACER_PERCENT"] = str(percent)
    return env


def _read_arm_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read arm receipt {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"invalid arm receipt: {path}")
    return data


def _sealed_runtime_env(binary: Path, collector: int) -> dict:
    identity = _read_arm_json(binary.parent / "source.json")
    build_report = _read_arm_json(binary.parent / "build.json")
    percent = _gc2_pacer_percent(identity)
    built_percent = _gc2_pacer_percent(build_report)
    if percent is not None or built_percent is not None:
        if (not build_report.get("complete") or percent != built_percent
                or identity.get("revision") != build_report.get("revision")
                or build_report.get("gc2_pacer_attested") != {"on": percent, "off": -1, "gc0": -1}):
            raise SystemExit(f"arm {binary.parent.name} has no matching GC2 pacer attestation")
    return _pacer_env(collector, percent)


def _attest_gc2_pacer(arm: Path, runtime: Path, percent: int, compiler) -> dict:
    source = arm / "programs/gc2_pacer_attest.py"
    binary = arm / "gc2_pacer_attest"
    source.write_text(
        'from pcc.extern import extern, c_int64\n'
        'backend = extern("pcc_gc_backend", (), c_int64)\n'
        'mode = extern("pcc_gc2_pacer_mode", (), c_int64)\n'
        'backend()\nprint(mode())\n'
    )
    compiler(str(source), str(binary), backend="self", ir_scaffold_mode="on",
             runtime_archive=str(runtime))
    observed = {}
    for label, collector, mode, expected in (
        ("on", 2, percent, percent), ("off", 2, None, -1), ("gc0", 0, percent, -1),
    ):
        run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                             env=_pacer_env(collector, mode))
        if (run.returncode, run.stdout, run.stderr) != (0, f"{expected}\n", ""):
            raise SystemExit(f"arm {arm.name}: GC2 pacer attestation {label} failed: "
                             f"{run.returncode} {run.stdout[-200:]} {run.stderr[-200:]}")
        observed[label] = expected
    return observed


# ---- reference runtime (a labeled LLVM experiment) ------------------------
#
# AGENTS.md admits an external LLVM oracle only as a labeled reference
# experiment: it bounds what code generation can buy and never satisfies an
# ownership claim. Such an arm compiles the IR of its own owned runtime
# archive's members with clang instead of the self backend; the benchmark
# program is self-compiled exactly as in any other arm.

REFERENCE_RUNTIMES = {
    "clang-O2": ("-O2",),
    # LLVM code generation without LLVM's IR optimization.
    "clang-codegen": ("-O2", "-Xclang", "-disable-llvm-optzns"),
    # The same code generation with LLVM's local fast register allocator:
    # against clang-codegen, what allocation quality is worth on this core.
    "clang-codegen-fastra": ("-O2", "-Xclang", "-disable-llvm-optzns",
                             "-mllvm", "-regalloc=fast"),
}
REFERENCE_LABEL = "LLVM reference experiment; not a product route or an ownership claim"
# Deliberately not libpy_runtime_pcc_py.a: nothing may take it for owned output.
REFERENCE_ARCHIVE = "libpy_runtime_reference.a"
# The self backend expands llvm.pcc.capture.gc.registers inline
# (self_backend_aarch64_darwin_calls); clang leaves it an external call.
# The stub stores what the expansion stores: x19-x30, the stack pointer and
# zeros in 16 words, and returns the stack pointer (a call leaves sp as is).
_CAPTURE_STUBS = {
    "arm64-apple-darwin": "\n".join([
        "// pcc_perf reference runtime only: the self backend inlines this intrinsic.",
        ".text",
        ".p2align 2",
        '.globl "_llvm.pcc.capture.gc.registers"',
        '"_llvm.pcc.capture.gc.registers":',
        *[f"  stp x{first}, x{first + 1}, [x0, #{(first - 19) * 8}]" for first in range(19, 31, 2)],
        "  mov x10, sp",
        "  stp x10, xzr, [x0, #96]",
        "  stp xzr, xzr, [x0, #112]",
        "  mov x0, x10",
        "  ret",
        "",
    ]),
}


# Beside the named kinds, two forms locate one LLVM step's worth:
#   opt:PIPELINE   LLVM's IR passes PIPELINE (opt -passes=PIPELINE), then
#                  LLVM code generation alone, as clang-codegen;
#   llc:A,B        clang-codegen with each code generation option -mllvm -A,
#                  for example llc:regalloc=fast or llc:disable-block-placement.
_CODEGEN_ONLY = REFERENCE_RUNTIMES["clang-codegen"]


def _reference_plan(kind: str | None) -> tuple[str | None, tuple[str, ...]] | None:
    """(opt pipeline or None, clang flags) for a reference kind."""
    if kind is None:
        return None
    if kind in REFERENCE_RUNTIMES:
        return None, REFERENCE_RUNTIMES[kind]
    form, _, rest = kind.partition(":")
    if form == "opt" and rest and not rest.startswith("-"):
        return rest, _CODEGEN_ONLY
    if form == "llc" and rest:
        options = rest.split(",")
        if all(option and not option.startswith("-") for option in options):
            flags = list(_CODEGEN_ONLY)
            for option in options:
                flags.extend(["-mllvm", "-" + option])
            return None, tuple(flags)
    raise SystemExit(f"unknown reference runtime {kind!r}; expected one of "
                     + ", ".join(sorted(REFERENCE_RUNTIMES))
                     + ", opt:PIPELINE or llc:OPTION[,OPTION...]")


def _reference_flags(kind: str | None) -> tuple[str, ...] | None:
    plan = _reference_plan(kind)
    return None if plan is None else plan[1]


def build_reference_runtime(arm: Path, runtime: Path, kind: str, target: str,
                            *, run=subprocess.run) -> tuple[Path, dict]:
    """The reference archive for an arm: each member of the owned ``runtime``
    archive compiled from its own IR by clang, plus the capture stub.
    Returns the archive and its receipt; every gap fails explicitly."""
    pipeline, flags = _reference_plan(kind)
    stub = _CAPTURE_STUBS.get(target)
    if stub is None:
        raise SystemExit(f"reference runtime: no capture stub for {target}")
    clang, ar = shutil.which("clang"), shutil.which("ar")
    if clang is None or ar is None:
        raise SystemExit("reference runtime needs clang and ar on PATH")
    opt = shutil.which("opt") if pipeline is not None else None
    if pipeline is not None and opt is None:
        raise SystemExit("reference runtime opt:PIPELINE needs LLVM opt on PATH")
    manifest = json.loads(Path(str(runtime) + ".provenance.json").read_text())
    members = [entry["member"] for entry in manifest["members"]]
    ir_dir = Path(str(runtime) + ".objects")
    sources = [ir_dir / (Path(member).stem + ".ll") for member in members]
    missing = [str(path.name) for path in sources if not path.is_file()]
    if missing:
        raise SystemExit("reference runtime: owned members without IR: " + ", ".join(missing))
    out = arm / "reference-runtime"
    out.mkdir(exist_ok=True)
    objects = [out / member for member in members]

    def compile_one(pair) -> None:
        source, target_object = pair
        if pipeline is not None:
            optimized = target_object.with_suffix(".opt.ll")
            done = run([opt, "-S", "-passes=" + pipeline, str(source), "-o", str(optimized)],
                       capture_output=True, text=True)
            if done.returncode != 0:
                raise SystemExit(f"reference runtime: opt failed on {source.name}: "
                                 + done.stderr[-400:])
            source = optimized
        done = run([clang, *flags, "-c", "-x", "ir", "-Wno-override-module",
                    str(source), "-o", str(target_object)], capture_output=True, text=True)
        if done.returncode != 0:
            raise SystemExit(f"reference runtime: clang failed on {source.name}: "
                             + done.stderr[-400:])

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(compile_one, zip(sources, objects)))
    stub_source = out / "capture_gc_registers.s"
    stub_source.write_text(stub)
    stub_object = out / "capture_gc_registers.o"
    done = run([clang, "-c", str(stub_source), "-o", str(stub_object)],
               capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit("reference runtime: capture stub failed: " + done.stderr[-400:])
    archive = out / REFERENCE_ARCHIVE
    if archive.exists():
        archive.unlink()
    done = run([ar, "rcs", str(archive), *map(str, objects), str(stub_object)],
               capture_output=True, text=True)
    if done.returncode != 0 or not archive.is_file():
        raise SystemExit("reference runtime: ar failed: " + done.stderr[-400:])
    version = run([clang, "--version"], capture_output=True, text=True).stdout
    return archive, {
        "label": REFERENCE_LABEL, "kind": kind, "flags": list(flags),
        "opt_pipeline": pipeline,
        "compiler": version.splitlines()[0] if version else "",
        "members": len(members), "owned_runtime": str(runtime),
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }


def seal(name: str, with_paths: tuple[str, ...] = (), root: Path | None = None,
         *, gc2_pacer_percent: int | None = None,
         reference_runtime: str | None = None) -> Path:
    """Copy HEAD's files into the arm, then the working tree's version of
    every file under ``with_paths`` (tracked or new; a file deleted there
    is deleted in the arm). Returns the arm directory."""
    _gc2_pacer_percent({"gc2_pacer_percent": gc2_pacer_percent})
    _reference_flags(reference_runtime)
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
        "gc2_pacer_percent": gc2_pacer_percent,
        "reference_runtime": reference_runtime,
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
    package = Path(configured) if configured else main_checkout().parent / "pcc-gateway" / "pcc_gateway"
    if not (package / "structured.py").is_file():
        raise SystemExit(
            f"pcc_gateway package not found at {package}; set PCC_GATEWAY_PACKAGE")
    return package


def main_checkout() -> Path:
    """The repository's main checkout: REPO_ROOT, or for a linked worktree
    the checkout that owns its common git directory. The project
    environment (.venv) and the sibling pcc-gateway live beside it."""
    try:
        common = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--path-format=absolute",
             "--git-common-dir"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return REPO_ROOT
    if not common:
        return REPO_ROOT
    return Path(common).parent


def project_environment() -> Path:
    """The environment uv runs arm builds in: the main checkout's .venv,
    so a linked worktree neither creates nor syncs an empty one."""
    return main_checkout() / ".venv"


def performance_lock():
    """The lock heavy builds and measurements share: wait for the holder
    (a bootstrap, another arm or measurement) instead of contending."""
    sys.path.insert(0, str(REPO_ROOT))
    from scripts.file_lock import exclusive_file_lock

    (REPO_ROOT / "build").mkdir(exist_ok=True)
    lock = REPO_ROOT / "build/.pcc-performance.lock"
    try:
        with exclusive_file_lock(str(lock), blocking=False):
            pass
    except OSError:
        print(f"waiting for {lock} (another build or measurement holds it)", flush=True)
    return exclusive_file_lock(str(lock), blocking=True)


def build(name: str, timeout: int, max_tree_rss: int,
          preflight_reserve: int = 8589934592) -> int:
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
    from scripts import run_process_tree_sample

    environment = dict(
        UV_PROJECT_ENVIRONMENT=str(project_environment()), UV_NO_SYNC="1",
        PCC_THREADS="1", PCC_REFCOUNT_KIND="atomic", PCC_RUNTIME_BUILD_JOBS="6",
        PCC_NO_AUTO_PCC1="1", PYTHONPATH=str(arm / "source"),
        PCC_PERF_HOME=str(HOME),
    )
    os.environ.update(environment)
    reserve_flag = ("--linux-physical-preflight-reserve-bytes" if sys.platform.startswith("linux")
                    else "--darwin-preflight-reserve-bytes")
    with performance_lock():
        return run_process_tree_sample.main([
            "--result", str(arm / "guard.json"), "--samples", str(arm / "rss.tsv"),
            "--stdout", str(arm / "build.stdout"), "--stderr", str(arm / "build.stderr"),
            "--cwd", str(arm / "source"), "--timeout", str(timeout),
            "--max-tree-rss-bytes", str(max_tree_rss),
            reserve_flag, str(preflight_reserve),
            "--no-performance-lock", "--",
            "uv", "run", "--no-sync", "python", str(arm / "source/scripts/pcc_perf.py"),
            "_build", str(arm),
        ])


def frontend_cache_identity(files: dict) -> str:
    """The frontend IR cache's namespace for an arm: its sealed compiler files.

    That cache keys a program's IR by the program's sources, the code
    generation options and the compiler executable, which for a host compile
    is the Python interpreter, not pcc. Without this namespace an arm that
    changes code generation reused the IR of an arm built before it and
    measured the old code. The runtime's own sources (pcc/runtime/py) are
    left out: code generation does not read them, so arms that differ only in
    the runtime still share the program's IR.
    """
    digest = hashlib.sha256(b"pcc-perf-frontend-v1\0")
    for relative in sorted(files):
        if relative.startswith("pcc/") and not relative.startswith("pcc/runtime/py/"):
            digest.update(relative.encode() + b"\0" + files[relative].encode() + b"\0")
    return "pcc-perf-frontend-" + digest.hexdigest()[:32]


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
    percent = _gc2_pacer_percent(identity)
    report = {
        "complete": False, "revision": identity["revision"], "runtime": str(runtime),
        "gc2_pacer_percent": percent,
        "runtime_sha256": digest(runtime), "runtime_seconds": time.monotonic() - started,
        "programs": {}, "runs": [],
    }

    def save() -> None:
        (arm / "build.json").write_text(json.dumps(report, indent=1) + "\n")

    save()
    program_runtime = runtime
    reference = identity.get("reference_runtime")
    if reference is not None:
        program_runtime, report["reference_runtime"] = build_reference_runtime(
            arm, runtime, reference, host_target_triple())
        save()
        print(f"arm {arm.name}: {REFERENCE_LABEL}: {reference}", flush=True)
        # The explicitly selected host reference route: it takes an explicit
        # archive without the owned archive's provenance, which clang's
        # objects cannot have.
        os.environ["PCC_RUNTIME_BUILD"] = "make"
    os.environ.update(
        PCC_RUNTIME_ARCHIVE=str(program_runtime),
        PCC_THREADED_RUNTIME_ARCHIVE=str(program_runtime),
        PCC_NO_AUTO_PCC1="1", PCC_TEST_NO_NATIVE_PROVISIONING="1",
        # The application's modules are identical between arms that differ
        # only in the runtime: reuse their objects (keyed by emitted IR and
        # the emitter's own source identity).
        PCC_SELF_BACKEND_OBJECT_CACHE_IDENTITY="pcc-perf",
        PCC_SELF_BACKEND_OBJECT_CACHE_DIR=str(HOME / "object-cache"),
        # The frontend IR cache otherwise falls back to the object cache's
        # fixed identity; see frontend_cache_identity.
        PCC_PY_FRONTEND_IR_CACHE_IDENTITY=frontend_cache_identity(identity["files"]),
    )
    if percent is not None:
        report["gc2_pacer_attested"] = _attest_gc2_pacer(arm, runtime, percent, compile_python)
        save()
    for program in PROGRAMS:
        binary = arm / program
        started = time.monotonic()
        compile_python(
            str(arm / "programs" / (program + ".py")), str(binary), backend="self",
            ir_scaffold_mode="on", runtime_archive=str(program_runtime),
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
                    env=dict(_pacer_env(collector, percent), BENCH_N="31"),
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


# ---- compile profile -----------------------------------------------------

# Injected as sitecustomize.py into every Python process of a profiled build
# (the build child and each worker it starts inherit PYTHONPATH). SIGPROF
# fires per period of the process's own processor time, so a process that
# waits in a C call (poll, read, sleep, waitpid) takes no sample: a wall-clock
# sampler charges that wait to the Python caller, which once ranked a worker
# pool's polling loop first with 45% of a compile. The handler sees the main
# thread's stack; the process's CPU total comes from getrusage and is exact.
_COMPILE_SAMPLER = r'''
import os
import signal
import sys

_DIR = os.environ.get("PCC_COMPILE_PROFILE_DIR", "")
if _DIR and hasattr(signal, "setitimer"):
    import atexit
    import resource

    _counts = {}
    _period = 1.0 / float(os.environ.get("PCC_COMPILE_PROFILE_HZ", "1000"))

    def _pcc_on_sigprof(signum, frame):
        codes = []
        while frame is not None and len(codes) < 256:
            codes.append(frame.f_code)
            frame = frame.f_back
        key = tuple(codes)
        _counts[key] = _counts.get(key, 0) + 1

    def _pcc_start():
        signal.signal(signal.SIGPROF, _pcc_on_sigprof)
        signal.setitimer(signal.ITIMER_PROF, _period, _period)

    _root = os.environ.get("PCC_COMPILE_PROFILE_ROOT", "")

    def _pcc_where(filename):
        # Relative to the compiler's source root; anything else is CPython's
        # (stdlib, site-packages) or generated code ("<string>").
        if _root and filename.startswith(_root + "/"):
            return filename[len(_root) + 1:]
        if filename.startswith("<"):
            return filename
        return "python/" + os.path.basename(filename)

    def _pcc_flush():
        signal.setitimer(signal.ITIMER_PROF, 0, 0)
        usage = resource.getrusage(resource.RUSAGE_SELF)
        path = os.path.join(_DIR, "cpu-%d.folded" % os.getpid())
        with open(path, "w", encoding="utf-8") as stream:
            stream.write("#cpu %.6f\n" % (usage.ru_utime + usage.ru_stime))
            stream.write("#argv " + " ".join(sys.argv).replace("\n", " ")[:400] + "\n")
            for codes, count in _counts.items():
                labels = [code.co_qualname + " [" + _pcc_where(code.co_filename) + "]"
                          for code in reversed(codes)]
                stream.write(";".join(labels) + " " + str(count) + "\n")
        _counts.clear()

    atexit.register(_pcc_flush)
    _pcc_exit = os._exit

    def _pcc_exit_flushing(code):
        try:
            _pcc_flush()
        finally:
            _pcc_exit(code)

    os._exit = _pcc_exit_flushing
    # Interval timers do not survive fork; restart them in a forked child.
    os.register_at_fork(after_in_child=lambda: (_counts.clear(), _pcc_start()))
    _pcc_start()
'''


def read_compile_samples(directory: Path) -> list[dict]:
    """The per-process sample files a profiled build wrote."""
    processes = []
    for path in sorted(directory.glob("cpu-*.folded")):
        entry = {"cpu": 0.0, "argv": "", "folded": {}}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#cpu "):
                entry["cpu"] = float(line[5:])
            elif line.startswith("#argv "):
                entry["argv"] = line[6:]
            elif line:
                stack, _, count = line.rpartition(" ")
                entry["folded"][stack] = entry["folded"].get(stack, 0) + int(count)
        processes.append(entry)
    return processes


def compile_process_role(argv: str) -> str:
    """A process's part in the build, from its command line: the first
    pcc worker flag, or the build itself."""
    for word in argv.split():
        if word.startswith("--pcc-") and word.endswith("worker"):
            return word[2:]
    if " _compile " in f" {argv} ":
        return "build coordinator"
    return argv.split(" ", 1)[0].rsplit("/", 1)[-1] or "unknown"


def frame_owner(frame: str) -> str:
    """The package a sampled frame's code belongs to: ``pcc/<dir>/<dir>``,
    CPython's import system, or the standard library."""
    where = frame[frame.rfind("[") + 1:-1] if frame.endswith("]") else ""
    if where.startswith("pcc/"):
        parts = where.split("/")
        return "/".join(parts[:3]) if len(parts) > 3 else "/".join(parts[:-1])
    if "importlib" in where:
        return "CPython import"
    if where == "<string>":
        # exec-generated functions: dataclass __init__/__eq__/__hash__.
        return "generated code (<string>)"
    if where.startswith("python/") or where.startswith("<"):
        return "CPython stdlib/other"
    return "repository " + where.split("/", 1)[0]


def compile_callers(processes: list[dict], needle: str, depth: int) -> list:
    """The self samples of the frames containing NEEDLE, by their callers:
    the DEPTH frames above each, nearest first."""
    chains: collections.Counter = collections.Counter()
    for process in processes:
        for stack, count in process["folded"].items():
            frames = stack.split(";")
            if needle in frames[-1]:
                above = frames[-1 - depth:-1]
                chains[(frames[-1], " <- ".join(reversed(above)))] += count
    return chains.most_common()


def compile_profile_rows(processes: list[dict]) -> dict:
    """CPU by process role (exact, from getrusage), and samples by owner
    package and by function, self and inclusive (each function once per
    stack)."""
    roles: dict[str, list] = {}
    owners: collections.Counter = collections.Counter()
    self_time: collections.Counter = collections.Counter()
    inclusive: collections.Counter = collections.Counter()
    total = 0
    for process in processes:
        role = compile_process_role(process["argv"])
        row = roles.setdefault(role, [0, 0.0])
        row[0] += 1
        row[1] += process["cpu"]
        for stack, count in process["folded"].items():
            frames = stack.split(";")
            total += count
            self_time[frames[-1]] += count
            owners[frame_owner(frames[-1])] += count
            for frame in set(frames):
                inclusive[frame] += count
    return {
        "samples": total,
        "roles": sorted(([role, n, cpu] for role, (n, cpu) in roles.items()),
                        key=lambda item: -item[2]),
        "owners": owners.most_common(),
        "self": self_time.most_common(),
        "inclusive": inclusive.most_common(),
    }


def print_compile_profile(report: dict, rows: dict, limit: int) -> None:
    settings = " ".join(f"{key}={value}" for key, value in report.get("settings", {}).items())
    print(f"compile {report['arm']} {report['program']}"
          f"{' with ' + settings if settings else ''}: "
          f"smoke {report.get('smoke', 'not run')}, "
          f"wall {report['wall']:.1f} s, tree CPU {report['cpu']:.1f} s "
          f"(user {report['user']:.1f} + sys {report['sys']:.1f}), "
          f"largest process RSS {report['max_rss_mb']:.0f} MB, "
          f"{sum(row[1] for row in rows['roles'])} sampled processes, "
          f"{rows['samples']} samples")
    total = max(rows["samples"], 1)
    print("\nCPU by process role (getrusage):")
    for role, count, cpu in rows["roles"]:
        print(f"  {cpu:8.1f} s  {count:3d} x  {role}")
    print("\nsamples by owner (self):")
    for owner, count in rows["owners"][:limit]:
        print(f"  {100.0 * count / total:5.1f}%  {owner}")
    for title, key in (("functions by self time", "self"),
                       ("functions by inclusive time", "inclusive")):
        print(f"\n{title}:")
        for frame, count in rows[key][:limit]:
            print(f"  {100.0 * count / total:5.1f}%  {frame}")


_IR_LABEL = re.compile(r'^"?([^"\s:][^":]*)"?:')
_IR_CALLEE = re.compile(r"call [^@]*@\"?([A-Za-z0-9_.$]+)")
_IR_OPCODE = re.compile(r"(?:%\S+ = )?([a-z_]+)")


def _ir_label_family(label: str) -> str:
    """A block label without its numbering: the code generator's name for it."""
    return re.sub(r"[0-9]+", "#", label).replace("#.#", "#")


def ir_shape_rows(lines) -> dict:
    """Functions by IR lines and block shapes repeated across a module set.

    A block's shape is its instruction sequence with names removed (opcodes,
    and callees for calls). A code generator that copies one cleanup into
    every exit shows up as one shape repeated thousands of times, and as a
    function whose lines are dominated by one label family.
    """
    functions: list = []
    shapes: dict = {}
    current = None
    block_family = None
    block_shape: list = []
    block_lines = 0

    def close_block():
        if current is None or block_family is None:
            return
        key = tuple(block_shape)
        entry = shapes.get(key)
        if entry is None:
            entry = shapes[key] = [0, 0, collections.Counter(), collections.Counter()]
        entry[0] += 1
        entry[1] += block_lines
        entry[2][block_family] += 1
        entry[3][current[0]] += block_lines
        current[3][block_family] += block_lines

    for raw in lines:
        line = raw.rstrip("\n")
        if line.startswith("define "):
            at = line.find("@")
            name = line[at + 1:line.find("(", at)].strip('"')
            current = [name, 0, 0, collections.Counter()]
            block_family = "entry-less"
            block_shape = []
            block_lines = 0
            continue
        if current is None:
            continue
        if line == "}":
            close_block()
            functions.append(current)
            current = None
            block_family = None
            continue
        current[1] += 1
        if not line.startswith((" ", "\t")) and line.endswith(":"):
            close_block()
            match = _IR_LABEL.match(line)
            block_family = _ir_label_family(match.group(1) if match else line)
            block_shape = []
            block_lines = 1
            current[2] += 1
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        block_lines += 1
        callee = _IR_CALLEE.search(stripped)
        if callee is not None:
            block_shape.append("call " + callee.group(1))
        else:
            opcode = _IR_OPCODE.match(stripped)
            block_shape.append(opcode.group(1) if opcode else "?")
    total = sum(function[1] for function in functions)
    return {
        "lines": total,
        "functions": sorted(functions, key=lambda function: -function[1]),
        "shapes": sorted(shapes.items(), key=lambda item: -item[1][1]),
    }


def print_ir_shapes(rows: dict, limit: int) -> None:
    total = max(rows["lines"], 1)
    print(f"{len(rows['functions'])} functions, {rows['lines']} IR lines")
    print("\nlargest functions (share of all lines; their largest label families):")
    for name, count, blocks, families in rows["functions"][:limit]:
        top = ", ".join(f"{family} {100.0 * lines / count:.0f}%"
                        for family, lines in families.most_common(3))
        print(f"  {100.0 * count / total:5.1f}%  {count:8d} lines {blocks:6d} blocks  {name}")
        print(f"          {top}")
    print("\nblock shapes by total lines (repeated code a generator copies):")
    for shape, (count, lines, families, owners) in rows["shapes"][:limit]:
        text = " ".join(shape)
        if len(text) > 110:
            text = text[:107] + "..."
        family = families.most_common(1)[0][0]
        owner = owners.most_common(1)[0]
        print(f"  {100.0 * lines / total:5.1f}%  {count:7d} x  {text}")
        print(f"          e.g. {family}; most in {owner[0]} ({100.0 * owner[1] / lines:.0f}%)")


def ir_shapes_of(paths: list[Path]) -> dict:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.ll")))
        else:
            files.append(path)
    if not files:
        raise SystemExit("no .ll files under " + ", ".join(str(path) for path in paths))

    def lines():
        for file in files:
            with open(file, encoding="utf-8", errors="replace") as stream:
                yield from stream

    return ir_shape_rows(lines())


def report_compile_run(run: Path, limit: int, callers: str, depth: int,
                       process_rank: int = 0) -> int:
    """Print a profiled compile's report from its run directory; with
    CALLERS, the self time of those frames by caller instead; with
    PROCESS_RANK, only the process with that CPU rank (1 = the largest)."""
    report = _read_arm_json(run / "compile.json")
    if not report.get("complete"):
        raise SystemExit(f"{run} holds no completed compile")
    processes = read_compile_samples(run / "samples")
    ranked = sorted(processes, key=lambda process: -process["cpu"])
    if process_rank:
        if not 1 <= process_rank <= len(ranked):
            raise SystemExit(f"--process must be 1..{len(ranked)}")
        chosen = ranked[process_rank - 1]
        print(f"process {process_rank} of {len(ranked)} by CPU: {chosen['cpu']:.1f} s, "
              f"{compile_process_role(chosen['argv'])}")
        processes = [chosen]
    rows = compile_profile_rows(processes)
    total = max(rows["samples"], 1)
    if callers:
        for needle in callers.split(","):
            print(f"self time of frames containing {needle!r}, by caller:")
            for (frame, chain), count in compile_callers(processes, needle, depth)[:limit]:
                print(f"  {100.0 * count / total:5.1f}%  {frame}  <-  {chain}")
        return 0
    print_compile_profile(report, rows, limit)
    if process_rank:
        return 0
    # The wall time follows the longest serial process, not the CPU sum.
    print("\nlargest processes by CPU (--process N reports one):")
    for rank, process in enumerate(ranked[:6], 1):
        print(f"  {rank}. {process['cpu']:8.1f} s  {compile_process_role(process['argv'])}")
    profile = run / "profile.json"
    if not profile.exists():
        profile.write_text(json.dumps(
            {"report": report, "rows": {key: value[:200] if isinstance(value, list) else value
                                        for key, value in rows.items()}}, indent=1) + "\n")
    print(f"\nreceipt {profile}")
    return 0


def compile_profile(name: str, program: str, hz: int, limit: int, timeout: int,
                    max_tree_rss: int, preflight_reserve: int,
                    settings: list[str] | None = None, keep_ir: bool = False) -> int:
    """Profile one cold compile of PROGRAM with arm NAME's sealed compiler;
    SETTINGS (``KEY=VALUE``) are compiler environment settings under test."""
    extra: dict[str, str] = {}
    for setting in settings or []:
        key, separator, value = setting.partition("=")
        if not separator or not key.startswith("PCC_"):
            raise SystemExit(f"--env takes PCC_NAME=VALUE, not {setting!r}")
        extra[key] = value
    arm = arm_dir(name)
    build_report = _read_arm_json(arm / "build.json")
    if not build_report.get("complete"):
        raise SystemExit(f"arm {name} is not completely built; run `arm {name}` first")
    if program not in PROGRAMS + (STAGE1,):
        raise SystemExit(f"program must be one of {', '.join(PROGRAMS + (STAGE1,))}")
    run = HOME / "compile" / f"{name}-{program}-{time.strftime('%Y%m%d-%H%M%S')}"
    samples = run / "samples"
    inject = run / "inject"
    for directory in (samples, inject, run / "object-cache", run / "ir-cache"):
        directory.mkdir(parents=True)
    if keep_ir:
        # The link step writes every module's backend input here.
        (run / "ir").mkdir()
        extra["PCC_DEBUG_SELF_IR_DUMP_DIR"] = str(run / "ir")
    (inject / "sitecustomize.py").write_text(_COMPILE_SAMPLER, encoding="utf-8")
    runtime = build_report["runtime"]
    sys.path.insert(0, str(REPO_ROOT))
    from scripts import run_process_tree_sample

    environment = dict(
        UV_PROJECT_ENVIRONMENT=str(project_environment()), UV_NO_SYNC="1",
        PCC_THREADS="1", PCC_REFCOUNT_KIND="atomic", PCC_NO_AUTO_PCC1="1",
        PCC_TEST_NO_NATIVE_PROVISIONING="1",
        PYTHONPATH=str(inject) + os.pathsep + str(arm / "source"),
        PCC_PERF_HOME=str(HOME), PCC_RUNTIME_ARCHIVE=runtime,
        PCC_THREADED_RUNTIME_ARCHIVE=runtime,
        PCC_SELF_BACKEND_OBJECT_CACHE_DIR=str(run / "object-cache"),
        PCC_PY_FRONTEND_IR_CACHE_DIR=str(run / "ir-cache"),
        PCC_COMPILE_PROFILE_DIR=str(samples), PCC_COMPILE_PROFILE_HZ=str(hz),
        PCC_COMPILE_PROFILE_ROOT=str(arm / "source"),
        PCC_COMPILE_PROFILE_SETTINGS=json.dumps(extra, sort_keys=True),
    )
    environment.update(extra)
    os.environ.update(environment)
    reserve_flag = ("--linux-physical-preflight-reserve-bytes" if sys.platform.startswith("linux")
                    else "--darwin-preflight-reserve-bytes")
    print(f"profiling a cold compile of {program} with arm {name} into {run}", flush=True)
    with performance_lock():
        code = run_process_tree_sample.main([
            "--result", str(run / "guard.json"), "--samples", str(run / "rss.tsv"),
            "--stdout", str(run / "build.stdout"), "--stderr", str(run / "build.stderr"),
            "--cwd", str(arm / "source"), "--timeout", str(timeout),
            "--max-tree-rss-bytes", str(max_tree_rss),
            reserve_flag, str(preflight_reserve),
            "--no-performance-lock", "--",
            "uv", "run", "--no-sync", "python", str(Path(__file__).absolute()),
            "_compile", str(arm), program, str(run),
        ])
    report = _read_arm_json(run / "compile.json")
    if code != 0 or not report.get("complete"):
        stderr = run / "build.stderr"
        print(stderr.read_text()[-2000:] if stderr.is_file() else f"guard exit {code}")
        return 1
    # The caches only existed to make the compile cold.
    shutil.rmtree(run / "object-cache", ignore_errors=True)
    shutil.rmtree(run / "ir-cache", ignore_errors=True)
    return report_compile_run(run, limit, "", 0)


def compile_child(arm: Path, program: str, run: Path) -> int:
    """Runs with the arm's sealed source first on sys.path: one compile."""
    import resource

    import pcc
    from pcc.frontends.python.pipeline import compile_python

    source = arm / "source"
    assert Path(pcc.__file__).resolve().parents[1] == source.resolve(), pcc.__file__
    build_report = json.loads((arm / "build.json").read_text())
    runtime = Path(build_report["runtime"])
    assert hashlib.sha256(runtime.read_bytes()).hexdigest() == build_report["runtime_sha256"]
    started = time.monotonic()
    stage1_code = None
    if program == STAGE1:
        # The production Stage1 itself: the arm's own bootstrap builds pcc1
        # from its sealed source and runs its stage smoke (compile and run a
        # program with the new pcc1).
        bootstrap = _load_module(source / "scripts" / "bootstrap.py")
        stage1_code = bootstrap.main(["--stage", "1", "--out-dir", str(run / "stage1")])
    else:
        compile_python(str(arm / "programs" / (program + ".py")), str(run / program),
                       backend="self", ir_scaffold_mode="on", runtime_archive=str(runtime))
    wall = time.monotonic() - started
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    # ru_maxrss is bytes on Darwin and KiB on Linux.
    scale = 1.0 if sys.platform == "darwin" else 1024.0
    report = {
        "complete": True, "arm": arm.name, "revision": build_report["revision"],
        "program": program, "wall": wall,
        "user": own.ru_utime + children.ru_utime, "sys": own.ru_stime + children.ru_stime,
        "max_rss_mb": max(own.ru_maxrss, children.ru_maxrss) * scale / 1048576.0,
    }
    report["cpu"] = report["user"] + report["sys"]
    report["settings"] = json.loads(os.environ.get("PCC_COMPILE_PROFILE_SETTINGS", "{}"))
    # A setting that changes how the program is compiled is only a result
    # when the program still computes what CPython computes.
    failures = []
    if stage1_code is not None:
        if stage1_code != 0:
            failures.append({"argv": ["bootstrap.py", "--stage", "1"], "code": stage1_code,
                             "stdout": "", "stderr": "see build.stderr"})
        report["smoke"] = "passed" if not failures else "FAILED"
        report["smoke_failures"] = failures
        (run / "compile.json").write_text(json.dumps(report, indent=1) + "\n")
        return 0 if not failures else 1
    binary = run / program
    for argv, expected in smoke_cases(program, binary, arm / "programs"):
        done = subprocess.run(argv, capture_output=True, text=True, timeout=120,
                              env=dict(_pacer_env(0, None), BENCH_N="31"))
        if not smoke_passed(done.returncode, done.stdout, done.stderr, expected):
            failures.append({"argv": argv[1:], "code": done.returncode,
                             "stdout": done.stdout[-300:], "stderr": done.stderr[-300:]})
    report["smoke"] = "passed" if not failures else "FAILED"
    report["smoke_failures"] = failures
    (run / "compile.json").write_text(json.dumps(report, indent=1) + "\n")
    return 0 if not failures else 1


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
    env = _sealed_runtime_env(binary, 2)
    if "PCC_GC2_PACER_PERCENT" in env:
        report = _read_arm_json(binary.parent / "build.json")
        recorded = report.get("programs", {}).get(program, {}).get("sha256")
        if hashlib.sha256(binary.read_bytes()).hexdigest() != recorded:
            raise SystemExit(f"arm {name} binary does not match its sealed build: {program}")
    return binary


# ---- runs ----------------------------------------------------------------

def _rss_bytes(value: int, platform: str) -> int | None:
    if platform.startswith("linux"):
        return value * 1024
    if platform == "darwin":
        return value
    return None


def _handler_p99_ms(data: dict) -> float:
    samples = data.get("latencies_ms")
    count = data["requests"]
    if (not isinstance(samples, list) or count <= 0 or len(samples) != count
            or any(type(value) not in (int, float) or value < 0 or not math.isfinite(value)
                   for value in samples)):
        raise SystemExit("handler latency receipt missing or does not match timed requests")
    return sorted(samples)[(99 * count + 99) // 100 - 1]


def _wait_with_usage(self, *args, **kwargs):
    """Popen._try_wait replacement that keeps the child's resource usage."""
    try:
        pid, status, usage = os.wait4(self.pid, args[0] if args else 0)
    except ChildProcessError:
        return self.pid, 0
    if pid == self.pid:
        self._pcc_usage = usage.ru_utime + usage.ru_stime
        self._pcc_peak_rss_bytes = _rss_bytes(usage.ru_maxrss, sys.platform)
    return pid, status


class MeasuredPopen(subprocess.Popen):
    _try_wait = _wait_with_usage


def run_once(binary: Path, collector: int, workers: int, rounds: int,
             concurrency: int = 100, *, latency_receipts: bool = False) -> dict:
    """One handler run: requests per second, the processor time of one
    request and the counters it reports."""
    data_mode = "--samples" if latency_receipts else "--summary"
    process = MeasuredPopen(
        [str(binary), str(concurrency), "0", str(rounds), data_mode, str(workers)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=_sealed_runtime_env(binary, collector),
    )
    try:
        stdout, stderr = process.communicate(timeout=600)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise SystemExit(f"{binary} timed out on gc{collector} w{workers}")
    if process.returncode != 0:
        raise SystemExit(
            f"{binary} failed on gc{collector} w{workers}: {stderr[-400:]}")
    data = json.loads(stdout)
    counters = {int(key): value for key, value in data.get("gc", {}).items()}
    executed = data["requests"] + data.get("warmup_requests", 0)
    max_stw_us = counters.get(STW_MAX_US)
    peak_rss_bytes = getattr(process, "_pcc_peak_rss_bytes", None)
    if latency_receipts and (data["requests"] != concurrency * rounds
                             or type(max_stw_us) is not int or max_stw_us < 0
                             or type(peak_rss_bytes) is not int or peak_rss_bytes <= 0):
        raise SystemExit("handler receipt missing required request count, maximum STW, or peak RSS")
    return {
        "qps": data["requests"] / (data["finished_at"] - data["started_at"]),
        "requests": data["requests"],
        "cpu_us": 1e6 * getattr(process, "_pcc_usage", 0.0) / executed,
        "stw_ms": counters.get(STW_SUM_US, 0) / 1000.0,
        "max_stw_us": max_stw_us,
        "peak_rss_bytes": peak_rss_bytes,
        "latency_p99_ms": _handler_p99_ms(data) if latency_receipts else None,
        "sweep_ms": sum(counters.get(index, 0) for index in SWEEP_PHASE_US) / 1000.0,
        "lock_waits": sum(counters.get(index, 0) for index in LOCK_WAIT_COUNT),
        "lock_wait_ms": sum(counters.get(index, 0) for index in LOCK_WAIT_US) / 1000.0,
        "allocations": counters.get(ALLOCATIONS, 0),
    }


def summarize_pairs(first: list[dict], second: list[dict],
                    control: list[dict] | None = None) -> dict:
    """Medians of two arms' runs and how many pairs the second won; with the
    runs of a duplicate base arm, how far that identical build strayed."""
    def median(runs, key):
        return statistics.median(run[key] for run in runs)

    def ratios(runs):
        return [b["qps"] / a["qps"] if a["qps"] else 0.0 for a, b in zip(first, runs)]

    qps_a, qps_b = median(first, "qps"), median(second, "qps")
    row = {
        "pairs": len(first),
        "qps_a": qps_a, "qps_b": qps_b,
        "change_percent": 100.0 * (qps_b / qps_a - 1.0) if qps_a else 0.0,
        "ratios": ratios(second),
        "wins": sum(1 for a, b in zip(first, second) if b["qps"] > a["qps"]),
        "cpu_us_a": median(first, "cpu_us"), "cpu_us_b": median(second, "cpu_us"),
        "stw_ms_a": median(first, "stw_ms"), "stw_ms_b": median(second, "stw_ms"),
        "lock_wait_ms_a": median(first, "lock_wait_ms"),
        "lock_wait_ms_b": median(second, "lock_wait_ms"),
        "lock_waits_a": median(first, "lock_waits"),
        "lock_waits_b": median(second, "lock_waits"),
    }
    metrics = ("qps", "max_stw_us", "peak_rss_bytes", "latency_p99_ms")
    if any("max_stw_us" in run for run in first + second):
        sources = (("a", first), ("b", second))
        if control is not None:
            sources += (("control", control),)
        row["run_receipts"] = {
            label: [{key: run.get(key) for key in metrics} for run in runs]
            for label, runs in sources
        }
    if control is not None:
        row["control_ratios"] = ratios(control)
        row["control_change_percent"] = (
            100.0 * (median(control, "qps") / qps_a - 1.0) if qps_a else 0.0)
    return row


# A candidate whose measured gain stays below this is one of the three that
# freeze a collector (docs/status.md, GC0-4 optimization).
FREEZE_PERCENT = 5.0


def verdict(change: float, noise: float | None, pairs: int,
            threshold: float = FREEZE_PERCENT) -> str:
    """What a change means against the noise floor its control measured."""
    if pairs < 5:
        return "screen"
    if noise is None:
        return "no control"
    if change > noise:
        return "gain"
    if change < -noise:
        return "loss"
    # A floor this wide cannot tell a real sub-threshold change from drift.
    return "inconclusive" if noise >= threshold else "within noise"


def apply_control(rows: list[dict], threshold: float = FREEZE_PERCENT) -> list[dict]:
    """Give every row its noise floor and verdict.

    The floor is the largest deviation of the unchanged control from 1.0,
    over its single pairs and its median. The control is the row's own
    duplicate base arm (``ab --control-arm``); without one a row has no
    floor and cannot count.
    """
    def floor(ratios, change):
        return max([abs(ratio - 1.0) * 100.0 for ratio in ratios] + [abs(change)])

    for row in rows:
        noise = None
        if "control_ratios" in row:
            noise = floor(row["control_ratios"], row["control_change_percent"])
        row["noise_percent"] = noise
        row["verdict"] = verdict(row["change_percent"], noise, row["pairs"], threshold)
    return rows


def compare(a: str, b: str, pairs: int, collectors, workers, rounds: int,
            control_arm: str | None = None, *, latency_receipts: bool = False) -> list[dict]:
    arms = [(a, binary_of(a)), (b, binary_of(b))]
    if control_arm is not None:
        arms.append((control_arm, binary_of(control_arm)))
    pacer_modes = {
        name: _gc2_pacer_percent(_read_arm_json(binary.parent / "source.json"))
        for name, binary in arms
    } if 2 in collectors else {}
    reference_kinds = {
        name: _read_arm_json(Path(binary).parent / "source.json").get("reference_runtime")
        for name, binary in arms
    }
    runs: dict[tuple, list[dict]] = {}
    receipt_flags = {"latency_receipts": True} if latency_receipts else {}
    for index in range(pairs):
        for collector in collectors:
            for count in workers:
                # Rotate who runs first, so no arm always runs on a cold or
                # a warm machine; with two arms this alternates.
                shift = index % len(arms)
                for name, binary in arms[shift:] + arms[:shift]:
                    runs.setdefault((collector, count, name), []).append(
                        run_once(binary, collector, count, rounds, **receipt_flags))
    results = []
    for collector in collectors:
        for count in workers:
            row = summarize_pairs(
                runs[(collector, count, a)], runs[(collector, count, b)],
                runs.get((collector, count, control_arm)))
            row.update({"gc": collector, "workers": count, "a": a, "b": b,
                        "requests": rounds * 100, "time": time.time(),
                        "load": os.getloadavg()[0]})
            if latency_receipts:
                row["latency_receipts"] = True
                row["rss_scope"] = "process"
            if collector == 2 and any(mode is not None for mode in pacer_modes.values()):
                row["gc2_pacer_percent_by_arm"] = pacer_modes
            if any(kind is not None for kind in reference_kinds.values()):
                row["reference_runtime_by_arm"] = reference_kinds
            results.append(row)
    return results


INSTR_CONCURRENCY = 20


def parse_counted_run(stderr: str, darwin: bool) -> tuple[int, int]:
    """The instructions and cycles one counted run reports: ``/usr/bin/time
    -l`` on Darwin, ``perf stat -x,`` on Linux."""
    if darwin:
        instructions = re.search(r"(\d+)\s+instructions retired", stderr)
        cycles = re.search(r"(\d+)\s+cycles elapsed", stderr)
    else:
        instructions = re.search(r"^(\d+),[^,\n]*,instructions", stderr, re.M)
        cycles = re.search(r"^(\d+),[^,\n]*,cycles", stderr, re.M)
    if instructions is None or cycles is None:
        raise SystemExit("the counted run reported no instructions or cycles")
    return int(instructions.group(1)), int(cycles.group(1))


CALLGRIND_UNKNOWN_OBJECT = "<unknown object>"
CALLGRIND_UNKNOWN_SYMBOL = "<unknown symbol>"
CALLGRIND_UNREPRESENTED = ("", "<unrepresented Ir>")


def _callgrind_counts(fields):
    events = fields.get("events", [])
    if len(set(events)) != len(events) or events.count("Ir") != 1:
        raise SystemExit("Callgrind profile has no unique Ir event")
    if "summary" not in fields:
        raise SystemExit("Callgrind profile has no whole-run summary")
    def costs(name):
        values = fields[name]
        if (len(values) != len(events)
                or any(re.fullmatch(r"[0-9]+", value) is None for value in values)):
            raise SystemExit("Callgrind profile has invalid " + name + " costs")
        return [int(value) for value in values]
    summary = costs("summary")
    totals = costs("totals") if "totals" in fields else None
    if totals is not None and any(a > b for a, b in zip(totals, summary)):
        raise SystemExit("Callgrind profile totals exceed its whole-run summary")
    if summary[events.index("Ir")] <= 0:
        raise SystemExit("Callgrind profile has no positive Ir count")
    return events, summary, totals


def _callgrind_field(fields, key, value):
    if key in fields:
        raise SystemExit("Callgrind profile has ambiguous " + key)
    fields[key] = value.split()


def parse_callgrind_counted_run(profile) -> tuple[int, None]:
    """Full-run Callgrind Ir, not hardware-retired instructions.

    The format specifies summary as the whole profile cost. Optional totals
    represent recorded exclusive/self costs and may be smaller. Never sum
    ordinary and call-edge costs: edges include inclusive callee costs.
    We request a single process, combined threads and one final dump.
    """
    lines = profile.splitlines() if isinstance(profile, str) else profile
    fields = {}
    for line in lines:
        key, sep, value = line.strip().partition(":")
        if sep and key in ("events", "summary", "totals", "part"):
            _callgrind_field(fields, key, value)
    events, summary, _totals = _callgrind_counts(fields)
    return summary[events.index("Ir")], None


def _callgrind_position(value):
    return re.fullmatch(r"(?:[+-]?(?:0x[0-9a-fA-F]+|[0-9]+)|\*)", value) is not None


def _callgrind_number(value):
    if re.fullmatch(r"(?:0x[0-9a-fA-F]+|[0-9]+)", value) is None:
        raise SystemExit("Callgrind profile has invalid cost/count")
    return int(value, 16 if value.startswith("0x") else 10)


def _callgrind_name(value, aliases):
    raw = value.strip()
    match = re.fullmatch(r"\(([0-9]+)\)(?:[ \t]+(.*))?", raw)
    if match is None:
        if re.match(r"\([0-9]", raw):
            raise SystemExit("Callgrind profile has malformed compressed name")
        return raw
    index, name = int(match.group(1)), match.group(2)
    if name is not None:
        if index in aliases and aliases[index] != name:
            raise SystemExit("Callgrind profile redefines a compressed name")
        aliases[index] = name
    if index not in aliases:
        raise SystemExit("Callgrind profile references an undefined compressed name")
    return aliases[index]


def parse_callgrind_function_profile(profile) -> tuple[int, dict]:
    """Exclusive Ir grouped by reported object and symbol, never call edges.

    Stream the actual format with shared caller/callee compressed name views.
    Source-file position changes inside an inlined function do not change the
    containing symbol. Same name in one object is a symbol group, not proof
    of a unique physical function. Unknown symbols and the summary gap are
    explicit, distinct buckets.
    """
    lines = profile.splitlines() if isinstance(profile, str) else profile
    fields = {}
    aliases = {"object": {}, "function": {}}
    owner_object = CALLGRIND_UNKNOWN_OBJECT
    owner_symbol = CALLGRIND_UNKNOWN_SYMBOL
    costs = collections.defaultdict(int)
    exclusive = None
    pending_call = False
    seen_body = False
    position_fields = ["line"]
    events = None
    ir_index = None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        header = re.match(r"^([a-zA-Z][a-zA-Z0-9_.-]*):[ \t]*(.*)$", line)
        if header is not None:
            key, value = header.groups()
            if pending_call:
                raise SystemExit("Callgrind call association lacks its cost line")
            if key in ("events", "summary", "totals", "part", "positions"):
                if seen_body and key != "totals":
                    raise SystemExit("Callgrind profile has a second header part")
                _callgrind_field(fields, key, value)
                if key == "events":
                    events = fields[key]
                    if len(set(events)) != len(events) or events.count("Ir") != 1:
                        raise SystemExit("Callgrind profile has no unique Ir event")
                    ir_index = events.index("Ir")
                    exclusive = [0] * len(events)
                if key == "positions":
                    position_fields = fields[key]
                    canonical = ["instr", "bb", "line"]
                    if (not position_fields or
                            position_fields != [x for x in canonical if x in position_fields]):
                        raise SystemExit("Callgrind profile has invalid positions")
            elif seen_body:
                raise SystemExit("Callgrind profile has unexpected body header")
            continue
        if "totals" in fields or events is None:
            raise SystemExit("Callgrind profile has body outside its event header")
        seen_body = True
        spec, sep, value = line.partition("=")
        if sep:
            if pending_call:
                raise SystemExit("Callgrind call association lacks its cost line")
            if spec in ("ob", "cob", "fn", "cfn"):
                namespace = "object" if spec in ("ob", "cob") else "function"
                name = _callgrind_name(value, aliases[namespace])
                if spec == "ob":
                    owner_object = (name if name and name.strip("?")
                                    else CALLGRIND_UNKNOWN_OBJECT)
                elif spec == "fn":
                    owner_symbol = (name if name and name.strip("?")
                                    and re.fullmatch(r"0x[0-9a-fA-F]+", name) is None
                                    else CALLGRIND_UNKNOWN_SYMBOL)
            elif spec in ("fl", "fi", "fe", "cfi", "cfl"):
                # File positions can change inside the same containing fn.
                pass
            elif spec in ("calls", "jump", "jcnd"):
                values = value.split()
                counts = 2 if spec == "jcnd" else 1
                if (len(values) != counts + len(position_fields)
                        or not all(_callgrind_position(x) for x in values[counts:])):
                    raise SystemExit("Callgrind profile has malformed association")
                for count in values[:counts]:
                    _callgrind_number(count)
                # Only calls= has an immediately associated inclusive cost row.
                pending_call = spec == "calls"
            else:
                raise SystemExit("Callgrind profile has unknown body specification")
            continue
        values = line.split()
        npos = len(position_fields)
        if (len(values) < npos or len(values) > npos + len(events)
                or not all(_callgrind_position(x) for x in values[:npos])):
            raise SystemExit("Callgrind profile has malformed cost row")
        row = [_callgrind_number(x) for x in values[npos:]]
        row += [0] * (len(events) - len(row))
        if pending_call:
            # This is the callee's inclusive cost, not the caller's own Ir.
            pending_call = False
            continue
        for index, cost in enumerate(row):
            exclusive[index] += cost
        costs[(owner_object, owner_symbol)] += row[ir_index]
    if pending_call:
        raise SystemExit("Callgrind call association lacks its cost line")
    events, summary, totals = _callgrind_counts(fields)
    if exclusive is None:
        exclusive = [0] * len(events)
    if totals is not None and exclusive != totals:
        raise SystemExit("Callgrind exclusive cost rows do not reconcile with totals")
    if any(a > b for a, b in zip(exclusive, summary)):
        raise SystemExit("Callgrind exclusive cost rows exceed whole-run summary")
    total = summary[events.index("Ir")]
    gap = total - sum(costs.values())
    out = {group: cost for group, cost in costs.items() if cost}
    if gap:
        out[CALLGRIND_UNREPRESENTED] = gap
    if sum(out.values()) != total:
        raise SystemExit("Callgrind function costs do not reconcile with total Ir")
    return total, out


def callgrind_function_difference(short: dict, long: dict, short_total: int,
                                  long_total: int, short_rounds: int,
                                  long_rounds: int) -> dict:
    """Exact raw reconciliation before division; keep signed differences."""
    if sum(short.values()) != short_total or sum(long.values()) != long_total:
        raise SystemExit("Callgrind function costs do not reconcile with total Ir")
    per_request((short_total, None), (long_total, None), short_rounds, long_rounds)
    requests = (long_rounds - short_rounds) * INSTR_CONCURRENCY
    difference = {group: long.get(group, 0) - short.get(group, 0)
                  for group in set(short) | set(long)}
    if sum(difference.values()) != long_total - short_total:
        raise SystemExit("Callgrind function differential does not reconcile")
    return {group: cost / requests for group, cost in difference.items()}


def _counter_backend(requested: str) -> tuple[str, str]:
    """Auto is the original platform default; no silent backend fallback."""
    linux = sys.platform.startswith("linux")
    darwin = sys.platform == "darwin"
    backend = requested
    if backend == "auto":
        backend = "darwin" if darwin else "perf" if linux else ""
    if backend == "darwin" and darwin:
        return backend, "/usr/bin/time"
    if backend in ("perf", "callgrind"):
        if not linux:
            raise SystemExit(backend + " instruction counting requires Linux")
        tool = "perf" if backend == "perf" else "valgrind"
        executable = shutil.which(tool)
        if executable is None:
            raise SystemExit("instr " + backend + " backend needs " + tool + " on PATH")
        return backend, executable
    raise SystemExit("instr needs /usr/bin/time -l (Darwin) or perf (Linux); "
                     "Callgrind is an explicit Linux alternative")


def _validate_callgrind_handler(stdout: str, rounds: int) -> None:
    """The differential must represent the same completed benchmark work."""
    try:
        data = json.loads(stdout)
    except ValueError as error:
        raise SystemExit("Callgrind handler did not report completed requests") from error
    expected = {
        "concurrency": INSTR_CONCURRENCY, "rounds": rounds,
        "requests": rounds * INSTR_CONCURRENCY,
        # native_stats runs exactly two warm-up batches before timed work.
        "warmup_requests": 2 * INSTR_CONCURRENCY,
    }
    if (not isinstance(data, dict)
            or any(type(data.get(name)) is not int or data[name] != value
                   for name, value in expected.items())):
        raise SystemExit("Callgrind handler did not report exact timed and warm-up requests")


def counted_run(binary: Path, rounds: int, workers: int, *,
                counter_backend: str = "auto", function_costs: dict | None = None) -> tuple[int, int | None]:
    """One handler run; optional exclusive costs from the same profile."""
    backend, executable = _counter_backend(counter_backend)
    if function_costs is not None and (backend != "callgrind" or function_costs):
        raise SystemExit("Callgrind function attribution needs an empty output map")
    argv = [str(binary), str(INSTR_CONCURRENCY), "0", str(rounds), "--summary", str(workers)]
    if backend == "callgrind":
        with tempfile.TemporaryDirectory(prefix="pcc-callgrind-") as directory:
            # %p prevents accidental child-process/profile collisions. The
            # handler creates threads, not subprocesses; multiple dumps or
            # files cannot silently become a partial or duplicated count.
            profile_pattern = str(Path(directory) / "callgrind.out.%p")
            prefix = [
                executable, "--tool=callgrind", "--instr-atstart=yes",
                "--collect-atstart=yes", "--cache-sim=no", "--branch-sim=no",
                "--separate-threads=no", "--trace-children=no",
                "--callgrind-out-file=" + profile_pattern,
            ]
            process = subprocess.run(
                prefix + argv, capture_output=True, text=True, timeout=600,
                env=_sealed_runtime_env(binary, 0),
            )
            if process.returncode != 0:
                raise SystemExit(f"{binary} failed under Callgrind: {process.stderr[-400:]}")
            _validate_callgrind_handler(process.stdout, rounds)
            profiles = list(Path(directory).glob("callgrind.out.*"))
            if (len(profiles) != 1 or not profiles[0].is_file()
                    or re.fullmatch(r"callgrind\.out\.[0-9]+", profiles[0].name) is None):
                raise SystemExit("Callgrind did not produce exactly one complete process profile")
            with profiles[0].open(encoding="utf-8", errors="replace") as profile:
                if function_costs is None:
                    return parse_callgrind_counted_run(profile)
                total, costs = parse_callgrind_function_profile(profile)
                function_costs.update(costs)
                return total, None
    prefix = ([executable, "-l"] if backend == "darwin"
              else [executable, "stat", "-x,", "-e", "instructions,cycles"])
    process = subprocess.run(
        prefix + argv, capture_output=True, text=True, timeout=600,
        env=_sealed_runtime_env(binary, 0),
    )
    if process.returncode != 0:
        raise SystemExit(f"{binary} failed under the counters: {process.stderr[-400:]}")
    return parse_counted_run(process.stderr, backend == "darwin")


def per_request(short: tuple[int, int | None], long: tuple[int, int | None],
                short_rounds: int, long_rounds: int) -> tuple[float, float | None]:
    """Difference of completed run lengths; fixed start-up/warm-up cancel."""
    requests = (long_rounds - short_rounds) * INSTR_CONCURRENCY
    if requests <= 0 or long[0] <= short[0] or (short[1] is None) != (long[1] is None):
        raise SystemExit("invalid instruction counter differential")
    cycles = None
    if short[1] is not None:
        if long[1] <= short[1]:
            raise SystemExit("invalid cycle counter differential")
        cycles = (long[1] - short[1]) / requests
    return (long[0] - short[0]) / requests, cycles


def measure_instructions(names: list[str], repeats: int, short_rounds: int,
                         long_rounds: int, workers: int, *,
                         counter_backend: str = "auto",
                         function_results: dict | None = None) -> dict[str, list]:
    """Per-request instructions (and cycles when available), alternating arms."""
    backend, _executable = _counter_backend(counter_backend)
    if function_results is not None and (backend != "callgrind" or function_results):
        raise SystemExit("Callgrind function attribution needs an empty output map")
    results: dict[str, list] = {name: [] for name in names}
    for _ in range(repeats):
        for name in names:
            binary = binary_of(name)
            if function_results is None:
                short = counted_run(binary, short_rounds, workers, counter_backend=backend)
                long = counted_run(binary, long_rounds, workers, counter_backend=backend)
            else:
                short_costs, long_costs = {}, {}
                short = counted_run(binary, short_rounds, workers, counter_backend=backend,
                                    function_costs=short_costs)
                long = counted_run(binary, long_rounds, workers, counter_backend=backend,
                                   function_costs=long_costs)
                function_results.setdefault(name, []).append(callgrind_function_difference(
                    short_costs, long_costs, short[0], long[0], short_rounds, long_rounds))
            results[name].append(per_request(short, long, short_rounds, long_rounds))
    return results


def instruction_rows(results: dict[str, list], *,
                     counter_backend: str = "hardware") -> list[dict]:
    """Medians, spreads, change from base; unavailable metrics stay absent."""
    rows = []
    base = None
    for name, runs in results.items():
        if not runs or any(run[0] <= 0 for run in runs):
            raise SystemExit("invalid instruction counter runs")
        has_cycles = runs[0][1] is not None
        if any((run[1] is not None) != has_cycles for run in runs):
            raise SystemExit("mixed instruction counter availability")
        if base is not None and (base["cycles"] is not None) != has_cycles:
            raise SystemExit("mixed instruction counter availability across arms")
        instructions = statistics.median(run[0] for run in runs)
        spread = 100.0 * (max(run[0] for run in runs) - min(run[0] for run in runs)) / instructions
        cycles = cycle_spread = cycle_runs = ipc = None
        if has_cycles:
            if any(run[1] <= 0 for run in runs):
                raise SystemExit("invalid cycle counter runs")
            cycles = statistics.median(run[1] for run in runs)
            cycle_spread = 100.0 * (max(run[1] for run in runs) - min(run[1] for run in runs)) / cycles
            cycle_runs = [round(run[1]) for run in runs]
            ipc = instructions / cycles
        row = {"arm": name, "counter_backend": counter_backend,
               "instructions": instructions, "cycles": cycles, "ipc": ipc,
               "instruction_spread": spread, "cycle_spread": cycle_spread,
               "runs": [round(run[0]) for run in runs], "cycle_runs": cycle_runs}
        if base is None:
            base = row
        else:
            row["instruction_change"] = 100.0 * (instructions / base["instructions"] - 1.0)
            if cycles is not None:
                row["cycle_change"] = 100.0 * (cycles / base["cycles"] - 1.0)
        rows.append(row)
    return rows


def print_instructions(rows: list[dict]) -> None:
    for row in rows:
        change = ""
        label = ("Callgrind Ir (simulated instruction reads)"
                 if row.get("counter_backend") == "callgrind" else "instructions")
        if "instruction_change" in row:
            change = f"  {row['instruction_change']:+.2f}% {label}"
            if "cycle_change" in row:
                change += f"  {row['cycle_change']:+.2f}% cycles"
        cycle_text = " cycles unavailable; IPC unavailable"
        if row["cycles"] is not None:
            cycle_text = (f" {row['cycles']:10.0f} cycles per request"
                          f" (runs within {row['cycle_spread']:.2f}%)")
        print(f"{row['arm']:14s} {row['instructions']:10.0f} {label}"
              f" (runs within {row['instruction_spread']:.2f}%)"
              f"{cycle_text}{change}")


def print_instruction_functions(function_results: dict, limit: int) -> None:
    for name, runs in function_results.items():
        print(f"{name}: Callgrind symbol groups, self Ir/request (signed differential medians)")
        groups = set().union(*(run.keys() for run in runs))
        medians = {group: statistics.median(run.get(group, 0.0) for run in runs)
                   for group in groups}
        named = [(group, cost) for group, cost in medians.items()
                 if group != CALLGRIND_UNREPRESENTED and group[1] != CALLGRIND_UNKNOWN_SYMBOL]
        named.sort(key=lambda item: (-item[1], item[0]))
        if not named:
            print("  no named function attribution available")
        for (owner, symbol), cost in named[:limit]:
            print(f"  {cost:12.2f}  {symbol} [{owner}]")
        unknown = [(group, cost) for group, cost in medians.items()
                   if group == CALLGRIND_UNREPRESENTED or group[1] == CALLGRIND_UNKNOWN_SYMBOL]
        for (owner, symbol), cost in sorted(unknown):
            print(f"  {cost:12.2f}  {symbol}" + (f" [{owner}]" if owner else ""))
        print("  each run reconciles to total Ir; separate symbol medians need not sum to the total median")


# ---- steps: executed instructions per function (Darwin, lldb) --------------
#
# Hardware counters give a request's instruction total, not where the
# instructions run. `steps` single-steps the handler's request thread under
# lldb after a warm-up, records every PC, and charges each to the function
# that encloses it; marker hits (a function the handler calls once per
# request) turn counts into instructions per request. Exact and independent
# of machine load, but slow: about one request per 230k steps.

_STEP_SCRIPT = r'''
import os
import time

import lldb


def __lldb_init_module(debugger, internal):
    debugger.SetAsync(False)
    target = debugger.GetSelectedTarget()
    bp = target.BreakpointCreateByName(os.environ["PCC_STEP_MARKER"])
    bp.SetIgnoreCount(int(os.environ["PCC_STEP_WARM"]))
    error = lldb.SBError()
    process = target.Launch(target.GetLaunchInfo(), error)
    if not error.Success() or process.GetState() != lldb.eStateStopped:
        print("PCC_STEP_FAILED launch", error, process.GetState())
        return
    thread = process.GetSelectedThread()
    marker = bp.GetLocationAtIndex(0).GetLoadAddress()
    target.BreakpointDelete(bp.GetID())
    header = target.FindModule(target.GetExecutable()).GetObjectFileHeaderAddress()
    slide = header.GetLoadAddress(target) - header.GetFileAddress()
    pcs = []
    markers = 0
    started = time.time()
    for _ in range(int(os.environ["PCC_STEP_COUNT"])):
        pc = thread.GetFrameAtIndex(0).GetPC()
        pcs.append(pc)
        if pc == marker:
            markers += 1
        thread.StepInstruction(False)
        if process.GetState() != lldb.eStateStopped:
            break
    with open(os.environ["PCC_STEP_OUT"], "w") as stream:
        stream.write("markers %d steps %d slide %x seconds %.1f\n"
                     % (markers, len(pcs), slide, time.time() - started))
        for pc in pcs:
            stream.write("%x\n" % pc)
    process.Kill()
'''


def parse_step_symbols(nm_text: str) -> list[tuple[int, str]]:
    """Text symbols of ``nm -n`` output in address order, without the
    Mach-O leading underscore; the header symbol is not a function."""
    symbols = []
    for line in nm_text.splitlines():
        fields = line.split()
        if len(fields) != 3 or fields[1] not in ("T", "t"):
            continue
        name = fields[2][1:] if fields[2].startswith("_") else fields[2]
        if name == "_mh_execute_header":
            continue
        symbols.append((int(fields[0], 16), name))
    symbols.sort()
    return symbols


def parse_step_sections(otool_text: str) -> list[tuple[int, int, str]]:
    """(start, end, name) of each __TEXT section in ``otool -l`` output."""
    sections = []
    current: dict = {}
    for line in otool_text.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] in ("sectname", "segname", "addr", "size"):
            current[fields[0]] = fields[1]
            if fields[0] == "size" and current.get("segname") == "__TEXT":
                start = int(current["addr"], 16)
                sections.append((start, start + int(fields[1], 16), current["sectname"]))
            if fields[0] == "size":
                current = {}
    sections.sort()
    return sections


def step_function_counts(symbols: list[tuple[int, str]], pcs: list[int], slide: int,
                         sections: list[tuple[int, int, str]] = ()) -> dict:
    """Each PC under the function enclosing it; a PC in another __TEXT
    section (linker stubs, veneers) is that section's, not the function's
    laid out before it."""
    addresses = [address for address, _name in symbols]
    other = [(start, end, name) for start, end, name in sections if name != "__text"]
    low = min((start for start, _end, _name in sections), default=None)
    high = max((end for _start, end, _name in sections), default=None)
    counts = collections.Counter()
    for pc in pcs:
        address = pc - slide
        if low is not None and not low <= address < high:
            counts["<outside>"] += 1
            continue
        section = next((name for start, end, name in other if start <= address < end), None)
        if section is not None:
            counts["<" + section + ">"] += 1
            continue
        index = bisect.bisect_right(addresses, address) - 1
        counts[symbols[index][1] if index >= 0 else "<outside>"] += 1
    return dict(counts)


def step_program_scale(first: dict, second: dict, runtime: set) -> float | None:
    """How much more program work the first window ran than the second.

    The benchmark program is self-compiled in every arm, so its functions
    execute the same instructions per unit of work; only the runtime (and
    stubs) differs. Scaling the second arm by this ratio compares the two
    runtimes at equal work even when the stepped windows cover different
    parts of the requests."""
    def program(counts: dict) -> int:
        return sum(count for name, count in counts.items()
                   if name not in runtime and not name.startswith(("<", "?")))
    first_program, second_program = program(first), program(second)
    if not first_program or not second_program:
        return None
    return first_program / second_program


def parse_step_output(text: str) -> tuple[int, int, list[int]]:
    lines = text.splitlines()
    head = lines[0].split() if lines else []
    if len(head) < 6 or head[0] != "markers":
        raise SystemExit("steps: lldb wrote no step record")
    markers, slide = int(head[1]), int(head[5], 16)
    if markers <= 0:
        raise SystemExit("steps: the marker was not reached while stepping; step more")
    return markers, slide, [int(line, 16) for line in lines[1:] if line]


def step_rows(first: dict, first_requests: int, second: dict | None, *,
              limit: int) -> list[tuple]:
    """Instructions per request by function; against a second arm (counts
    already scaled to the first arm's work), the largest change first."""
    per_first = {name: count / first_requests for name, count in first.items()}
    if second is None:
        ordered = sorted(per_first.items(), key=lambda item: (-item[1], item[0]))
        return [(name, value, None, None) for name, value in ordered[:limit]]
    per_second = {name: count / first_requests for name, count in second.items()}
    rows = []
    for name in set(per_first) | set(per_second):
        before, after = per_first.get(name, 0.0), per_second.get(name, 0.0)
        rows.append((name, before, after, after - before))
    rows.sort(key=lambda row: (-abs(row[3]), row[0]))
    return rows[:limit]


def run_steps(name: str, count: int, warm: int, marker: str, rounds: int) -> tuple[dict, int, int]:
    """(instructions by function, marker hits, steps) for one arm."""
    binary = binary_of(name)
    with tempfile.TemporaryDirectory(prefix="pcc-steps-") as scratch:
        script = Path(scratch) / "pcc_step.py"
        script.write_text(_STEP_SCRIPT)
        out = Path(scratch) / "pcs.txt"
        env = dict(os.environ, PCC_STEP_MARKER=marker, PCC_STEP_WARM=str(warm),
                   PCC_STEP_COUNT=str(count), PCC_STEP_OUT=str(out))
        env.update(_sealed_runtime_env(binary, COLLECTORS[0]))
        env["PATH"] = os.environ.get("PATH", "")
        ran = subprocess.run(
            ["xcrun", "lldb", "--batch", "-o", f"command script import {script}",
             "--", str(binary), "20", "0", str(rounds), "--summary", "1"],
            capture_output=True, text=True, env=env, timeout=3600)
        if not out.is_file():
            raise SystemExit(f"steps: lldb failed on {name}: {ran.stdout[-400:]} {ran.stderr[-400:]}")
        record = out.read_text()
        markers, slide, pcs = parse_step_output(record)
    # Keep the raw record: a new attribution needs no new half-hour of stepping.
    (arm_dir(name) / f"steps-{count}-{marker}.txt").write_text(record)
    nm = subprocess.run(["xcrun", "nm", "-n", str(binary)], capture_output=True, text=True,
                        check=True).stdout
    otool = subprocess.run(["xcrun", "otool", "-l", str(binary)], capture_output=True, text=True,
                           check=True).stdout
    counts = step_function_counts(parse_step_symbols(nm), pcs, slide, parse_step_sections(otool))
    return counts, markers, len(pcs)


def runtime_function_names(name: str) -> set:
    """Functions the arm's owned runtime archive defines (a reference arm's
    archive is built from the same members' IR, so the names agree)."""
    report = _read_arm_json(arm_dir(name) / "build.json")
    archive = report.get("runtime")
    if not archive or not Path(archive).is_file():
        raise SystemExit(f"arm {name} records no runtime archive")
    nm = subprocess.run(["xcrun", "nm", "-g", "-U", "-j", archive], capture_output=True,
                        text=True, check=True).stdout
    return {line[1:] if line.startswith("_") else line
            for line in nm.splitlines() if line and not line.endswith(":")}


def print_steps(names: list[str], count: int, warm: int, marker: str, rounds: int,
                limit: int) -> None:
    if sys.platform != "darwin":
        raise SystemExit("steps single-steps under lldb and needs Darwin")
    if not 1 <= len(names) <= 2:
        raise SystemExit("steps takes one arm or two")
    measured = [run_steps(name, count, warm, marker, rounds) for name in names]
    for name, (counts, markers, steps) in zip(names, measured):
        print(f"{name}: {steps} steps, {markers} requests (hits of {marker}), "
              f"{steps / markers:.0f} instructions per request on the stepped thread")
    if len(measured) == 1:
        for function, value, _after, _change in step_rows(measured[0][0], measured[0][1], None,
                                                          limit=limit):
            print(f"{value:10.1f}  {function}")
        return
    runtime = runtime_function_names(names[0])
    scale = step_program_scale(measured[0][0], measured[1][0], runtime)
    if scale is None:
        raise SystemExit("steps: a window ran no program code; step more")
    second = {function: count * scale for function, count in measured[1][0].items()}
    def total(counts: dict, runtime_only: bool) -> float:
        return sum(count for function, count in counts.items()
                   if not runtime_only or function in runtime or function.startswith("<"))
    print(f"program work scale {names[1]} x{scale:.3f}; per request of {names[0]}: "
          f"all {total(measured[0][0], False) / measured[0][1]:.0f} -> "
          f"{total(second, False) / measured[0][1]:.0f}, runtime and stubs "
          f"{total(measured[0][0], True) / measured[0][1]:.0f} -> "
          f"{total(second, True) / measured[0][1]:.0f}")
    rows = step_rows(measured[0][0], measured[0][1], second, limit=limit)
    print(f"instructions per request: {names[0]} -> {names[1]}, largest change first")
    for function, before, after, change in rows:
        print(f"{before:10.1f} {after:10.1f} {change:+10.1f}  {function}")


def load_note(load: float) -> str:
    """What the machine's one-minute load says about a wall-clock rate."""
    cores = os.cpu_count() or 1
    if load + max(WORKERS) <= cores:
        return f"load {load:.1f} on {cores} cores"
    return (f"load {load:.1f} on {cores} cores: other work is using them, "
            f"read the processor time, not the rate")


def print_comparison(rows: list[dict]) -> None:
    if rows and "load" in rows[0]:
        print(load_note(max(row["load"] for row in rows)))
    for row in rows:
        print(
            f"gc{row['gc']} w{row['workers']}  {row['qps_a']:8.0f} -> {row['qps_b']:8.0f} "
            f"({row['change_percent']:+6.1f}%, {row['wins']}/{row['pairs']} pairs)  "
            f"cpu {row['cpu_us_a']:6.1f} -> {row['cpu_us_b']:6.1f} us  "
            f"stopped {row['stw_ms_a']:5.0f} -> {row['stw_ms_b']:5.0f} ms  "
            f"lock wait {row['lock_wait_ms_a']:6.0f} -> {row['lock_wait_ms_b']:6.0f} ms "
            f"({row['lock_waits_a']:.0f} -> {row['lock_waits_b']:.0f})"
            + _verdict_note(row),
            flush=True,
        )
    for row in rows:
        if row["pairs"] < 5:
            print("note: fewer than five pairs is a screen, not a result")
            break
    if any(row.get("verdict") == "no control" for row in rows):
        print("note: no --control or --control-arm: the noise floor is unknown, "
              "so this comparison cannot count toward the freeze rule")
    if any(row.get("verdict") == "inconclusive" for row in rows):
        print(f"note: inconclusive rows changed less than a control floor of "
              f"{FREEZE_PERCENT:.0f}% or more; they count neither as a gain nor "
              f"toward the freeze rule: add pairs or use a quieter machine")


def _verdict_note(row: dict) -> str:
    if row.get("verdict") == "control":
        return f"  control, floor {row['noise_percent']:.1f}%"
    if row.get("noise_percent") is not None:
        return f"  {row['verdict']} (floor {row['noise_percent']:.1f}%)"
    return f"  {row['verdict']}" if "verdict" in row else ""


def append_ledger(rows: list[dict]) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    with open(HOME / "ledger.jsonl", "a", encoding="utf-8") as ledger:
        for row in rows:
            ledger.write(json.dumps(row, sort_keys=True) + "\n")


ASYNCIO_PROGRAM = REPO_ROOT / "benchmarks/handler/asyncio_stats.py"


def asyncio_run(workers: int, rounds: int, concurrency: int = 100) -> dict:
    """One CPython asyncio handler run with ``workers`` workers.

    A compiled run's workers are carrier threads of one process sharing the
    same ``concurrency`` requests per round. CPython runs one event loop per
    process, so its counterpart is ``workers`` processes that split those
    requests evenly. They warm up, then start together behind a file
    barrier; the rate is all their requests over the time from the first
    start to the last finish (perf_counter is monotonic system-wide).
    """
    if workers < 1 or concurrency % workers:
        raise SystemExit(f"{concurrency} requests do not split over {workers} workers")
    with tempfile.TemporaryDirectory(prefix="pcc-asyncio-") as scratch:
        barrier = Path(scratch) / "start"
        readies = [Path(scratch) / f"ready{index}" for index in range(workers)]
        children = [
            subprocess.Popen(
                [sys.executable, str(ASYNCIO_PROGRAM), str(concurrency // workers), "0",
                 str(rounds), "--summary", str(barrier), str(ready)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for ready in readies
        ]
        try:
            deadline = time.monotonic() + 600
            while not all(ready.exists() for ready in readies):
                if any(child.poll() is not None for child in children):
                    break
                if time.monotonic() > deadline:
                    raise SystemExit(f"asyncio w{workers} did not reach its start barrier")
                time.sleep(0.001)
            barrier.write_text("start")
            outputs = [child.communicate(timeout=600) for child in children]
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.communicate()
    for child, (_, stderr) in zip(children, outputs):
        if child.returncode != 0:
            raise SystemExit(f"asyncio w{workers} failed: {stderr[-400:]}")
    results = [json.loads(stdout) for stdout, _ in outputs]
    requests = sum(result["requests"] for result in results)
    if requests != concurrency * rounds:
        raise SystemExit(f"asyncio w{workers} ran {requests} of {concurrency * rounds} requests")
    started = min(result["started_at"] for result in results)
    finished = max(result["finished_at"] for result in results)
    return {"qps": requests / (finished - started), "requests": requests}


def measure_asyncio(rounds: int, repeats: int) -> dict[int, float]:
    """Record CPython asyncio's rate at every worker count ``table`` uses."""
    runs: dict[int, list[float]] = {count: [] for count in WORKERS}
    for _ in range(repeats):
        for count in WORKERS:
            runs[count].append(asyncio_run(count, rounds)["qps"])
    rates = {count: statistics.median(values) for count, values in runs.items()}
    HOME.mkdir(parents=True, exist_ok=True)
    (HOME / "asyncio.json").write_text(json.dumps({
        "qps": rates[1],
        "workers": {str(count): rate for count, rate in rates.items()},
        "runs": {str(count): values for count, values in runs.items()},
        "python": sys.version.split()[0],
        "requests": rounds * 100, "time": time.time(),
    }, indent=1) + "\n")
    return rates


def recorded_asyncio_rates() -> dict[int, float]:
    """The recorded asyncio rate per worker count.

    A record from before worker counts were measured holds one event loop's
    rate only; it is the counterpart of one worker and of nothing else.
    """
    recorded = HOME / "asyncio.json"
    if not recorded.is_file():
        return {}
    data = json.loads(recorded.read_text())
    if "workers" in data:
        return {int(count): rate for count, rate in data["workers"].items()}
    return {1: data["qps"]}


def print_table(name: str, rounds: int, repeats: int) -> None:
    binary = binary_of(name)
    asyncio_rates = recorded_asyncio_rates()
    base = {}
    print(load_note(os.getloadavg()[0]))
    print(f"{'':6s} {'req/s':>9s} {'of GC0':>7s} {'of asyncio':>10s} "
          f"{'cpu us':>7s} {'stopped ms':>10s} {'lock wait ms':>12s}")
    for collector in COLLECTORS:
        rates = {}
        for count in WORKERS:
            runs = [run_once(binary, collector, count, rounds) for _ in range(repeats)]
            rate = statistics.median(run["qps"] for run in runs)
            rates[count] = rate
            if collector == 0:
                base[count] = rate
            asyncio_rate = asyncio_rates.get(count)
            against = f"{100 * rate / asyncio_rate:9.0f}%" if asyncio_rate else "         -"
            print(
                f"gc{collector} w{count} {rate:9.0f} {100 * rate / base[count]:6.0f}% {against} "
                f"{statistics.median(run['cpu_us'] for run in runs):7.1f} "
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
        env=_sealed_runtime_env(binary, collector),
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


def allocations_per_operation(timed: dict, single: dict) -> float:
    """Cancel start-up allocations against a zero-ms run of the same program.

    The reference reports one completed batch per worker, not one total.
    """
    operations = timed["operations"] - single["operations"]
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
                    row["allocations"] = allocations_per_operation(runs[0], single)
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


def function_callers(rows: list[list], target: str, depth: int = 1) -> dict[str, int]:
    """``target``'s self samples by the chain of its ``depth`` nearest
    callers, nearest first ("a < b": called by a, which b called)."""
    callers: collections.Counter = collections.Counter()
    for names, function, own, _ in walk_sample(rows):
        if function != target or not own:
            continue
        chain = list(reversed(names))[:depth]
        callers[" < ".join(chain) or "?"] += own
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
    label = f"{name} gc{collector} w{workers} {primitive or unit}"
    # The previous artifact may belong to a different run. Parse a private
    # output first and only publish it after the sampler and benchmark pass.
    with tempfile.TemporaryDirectory(prefix=".profile-", dir=target.parent) as directory:
        output = Path(directory) / target.name
        process = subprocess.Popen(
            [str(binary), *argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=_sealed_runtime_env(binary, collector),
        )
        try:
            time.sleep(0.3)
            sampler = subprocess.run(
                ["/usr/bin/sample", str(process.pid), str(seconds), "-file", str(output)],
                capture_output=True,
            )
        finally:
            process.communicate(timeout=900)
        if process.returncode != 0:
            raise SystemExit(f"{binary} failed while sampled on gc{collector} w{workers}")
        if sampler.returncode != 0:
            raise SystemExit(f"{label}: sample failed (exit {sampler.returncode})")
        try:
            report = output.read_text()
        except (OSError, UnicodeError) as exc:
            raise SystemExit(f"{label}: sample report is unreadable: {exc}") from exc
        if "Call graph:" not in report:
            raise SystemExit(f"{label}: sample report has no call graph")
        rows = parse_sample(report, text_symbols(binary), binary.name)
        if not rows:
            raise SystemExit(f"{label}: sample report has an empty call graph")
        output.replace(target)
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
              limit: int, inclusive: bool, other_arm: str, other_collector: int,
              callers: tuple[str, ...] = (), depth: int = 1) -> None:
    def costs(arm: str, gc: int) -> tuple[dict[str, float], float, str]:
        rows, unit_time, unit = profile(arm, gc, workers, seconds, primitive)
        busy = sum(function_shares(rows).values())
        if callers:
            for target in callers:
                shares = scaled(function_callers(rows, target, depth), busy, unit_time)
                print(f"{arm} gc{gc} w{workers}: {target}, "
                      f"{_cost(sum(shares.values()), unit).strip()} self per "
                      f"{primitive or unit}, by caller")
                for caller, cost, _, _ in top_rows(shares, None, limit):
                    print(f"{_cost(cost, unit)}  {caller}")
            return {}, unit_time, unit
        shares = inclusive_shares(rows) if inclusive else function_shares(rows)
        return scaled(shares, busy, unit_time), unit_time, unit

    first, first_time, unit = costs(name, collector)
    if callers:
        return
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
    arm.add_argument("--gc2-pacer-percent", type=int,
                     help="seal and verify the review runtime GC2 pacer opt-in (0–10000)")
    arm.add_argument("--reference-runtime", default=None, metavar="KIND",
                     help="labeled LLVM reference experiment: the runtime IR compiled by clang; "
                          + ", ".join(sorted(REFERENCE_RUNTIMES))
                          + ", opt:PIPELINE or llc:OPTION[,OPTION...]")
    arm.add_argument("--timeout", type=int, default=900)
    # The handler program's largest module emits in one worker of about
    # 2.4 GiB beside a 1.1 GiB coordinator; the approved build maximum is
    # 8 GiB.
    arm.add_argument("--max-tree-rss", type=int, default=6442450944)
    # Memory the host must keep reclaimable beside the capped tree before
    # the build starts. 8 GiB suits a large Mac; a 16 GiB machine with a
    # desktop session never has 14 GiB reclaimable, so it names a smaller
    # reserve explicitly. The tree cap itself is still enforced.
    arm.add_argument("--preflight-reserve", type=int, default=8589934592,
                     help="bytes to leave available beyond --max-tree-rss before building "
                          "(Linux: positive reserve against host MemAvailable, not cgroup headroom; "
                          "Darwin: reclaimable memory, disk and swap checks)")

    probe = commands.add_parser("profile-preflight")
    probe.add_argument("--cpu-ms", type=int, default=80)
    probe.add_argument("--precise-cycles", action="store_true",
                       help="require hardware cycles and an exact IP on every sample")

    child = commands.add_parser("_build")
    child.add_argument("arm", type=Path)

    compile_parser = commands.add_parser(
        "compile", help="where one cold compile of the benchmark program spends its CPU")
    compile_parser.add_argument("name", nargs="?", default="")
    compile_parser.add_argument("--from", dest="from_run", type=Path, default=None,
                                help="report an earlier run directory instead of compiling")
    compile_parser.add_argument("--callers", default="",
                                help="comma-separated frame substrings: their self time by caller")
    compile_parser.add_argument("--depth", type=int, default=2)
    compile_parser.add_argument("--process", type=int, default=0, metavar="RANK",
                                help="report only the process with this CPU rank (1 = largest)")
    compile_parser.add_argument("--keep-ir", action="store_true",
                                help="keep the backend's input IR in the run directory (ir/)")
    compile_parser.add_argument("--env", dest="settings", action="append", default=[],
                                metavar="PCC_NAME=VALUE",
                                help="a compiler setting under test (repeatable)")
    compile_parser.add_argument("--program", default="native_stats", choices=PROGRAMS + (STAGE1,),
                                help="a benchmark program, or stage1: the arm's own "
                                     "bootstrap --stage 1 (pcc compiling pcc)")
    compile_parser.add_argument("--hz", type=int, default=1000,
                                help="samples per second of each process's own CPU time")
    compile_parser.add_argument("--limit", type=int, default=25)
    compile_parser.add_argument("--timeout", type=int, default=900)
    compile_parser.add_argument("--max-tree-rss", type=int, default=6442450944)
    compile_parser.add_argument("--preflight-reserve", type=int, default=8589934592)

    compile_child_parser = commands.add_parser("_compile")
    compile_child_parser.add_argument("arm", type=Path)
    compile_child_parser.add_argument("program")
    compile_child_parser.add_argument("run", type=Path)

    ab = commands.add_parser("ab")
    ab.add_argument("a")
    ab.add_argument("b")
    ab.add_argument("--pairs", type=int, default=5)
    ab.add_argument("--gc", type=_numbers, default=COLLECTORS)
    ab.add_argument("--workers", type=_numbers, default=WORKERS)
    ab.add_argument("--rounds", type=int, default=100)
    ab.add_argument("--latency-receipts", action="store_true",
                    help="record timed request p99 in each pair (CPU/RSS include output formatting)")
    ab.add_argument("--control-arm", default=None, metavar="NAME",
                    help="a second build of arm A, rotated with A and B; "
                         "its pairs set the noise floor")

    instr = commands.add_parser(
        "instr", help="instructions and cycles per handler request; the first arm is the base")
    instr.add_argument("names", nargs="+")
    instr.add_argument("--repeats", type=int, default=3)
    instr.add_argument("--short-rounds", type=int, default=10)
    instr.add_argument("--long-rounds", type=int, default=50)
    instr.add_argument("--workers", type=int, default=1)
    instr.add_argument("--counter-backend", choices=("auto", "darwin", "perf", "callgrind"),
                       default="auto", help="auto preserves platform hardware counters; "
                       "callgrind is Linux simulated user Ir only (cycles/IPC unavailable)")
    instr.add_argument("--functions", type=int, default=0, metavar="N",
                       help="top N reported symbol groups by exclusive Ir/request (Callgrind only)")

    steps = commands.add_parser("steps")
    steps.add_argument("names", nargs="+")
    steps.add_argument("--count", type=int, default=2000000, help="instructions to step")
    steps.add_argument("--warm", type=int, default=60, help="marker hits before stepping")
    steps.add_argument("--marker", default="user_json_dumps",
                       help="a function the handler calls once per request")
    steps.add_argument("--rounds", type=int, default=400)
    steps.add_argument("--limit", type=int, default=40)

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
    top.add_argument("--callers", default="",
                     help="comma-separated functions: their self time by caller")
    top.add_argument("--depth", type=int, default=1, help="callers in each --callers chain")

    locks = commands.add_parser("locks")
    locks.add_argument("name")
    locks.add_argument("--gc", type=int, default=1)
    locks.add_argument("--workers", type=int, default=4)
    locks.add_argument("--seconds", type=int, default=4)
    locks.add_argument("--primitive", default="")

    ir_parser = commands.add_parser(
        "ir", help="functions and repeated block shapes of emitted IR (files or directories)")
    ir_parser.add_argument("paths", nargs="+", type=Path)
    ir_parser.add_argument("--limit", type=int, default=25)

    layout = commands.add_parser("layout")
    layout.add_argument("a")
    layout.add_argument("b", nargs="?")

    args = parser.parse_args(argv)
    if args.command == "profile-preflight":
        if sys.platform != "linux":
            raise SystemExit("profile-preflight requires Linux")
        sys.path.insert(0, str(REPO_ROOT))
        from scripts.pcc_perf_linux import task_clock_preflight

        try:
            result = task_clock_preflight(args.cpu_ms, precise_cycles=args.precise_cycles)
            print(json.dumps(result, sort_keys=True))
        except (OSError, ValueError) as exc:
            event = "precise cycles" if args.precise_cycles else "task-clock"
            raise SystemExit(f"Linux {event} preflight: {exc}") from exc
        return 0
    if args.command == "arm":
        options = {}
        if args.gc2_pacer_percent is not None:
            options["gc2_pacer_percent"] = args.gc2_pacer_percent
        if args.reference_runtime is not None:
            options["reference_runtime"] = args.reference_runtime
        sealed = seal(args.name, tuple(args.with_paths), **options)
        identity = json.loads((sealed / "source.json").read_text())
        print(f"arm {args.name}: {identity['revision'][:9]} with "
              f"{len(identity['overlaid'])} working-tree files", flush=True)
        for path in identity["left_out"]:
            print(f"  left out (uncommitted, not named by --with): {path}", flush=True)
        code = build(args.name, args.timeout, args.max_tree_rss, args.preflight_reserve)
        report = arm_dir(args.name) / "build.json"
        complete = report.is_file() and json.loads(report.read_text())["complete"]
        if code != 0 or not complete:
            stderr = arm_dir(args.name) / "build.stderr"
            if stderr.is_file():
                print(stderr.read_text()[-1500:])
            else:
                # The guard refused before starting the build (for example
                # its memory preflight); it already printed why.
                print(f"arm {args.name}: build did not start (guard exit {code})")
            return 1
        timing = json.loads(report.read_text())
        print(f"arm {args.name}: runtime {timing['runtime_seconds']:.0f} s, " + ", ".join(
            f"{program} {entry['seconds']:.0f} s"
            for program, entry in timing["programs"].items()))
        return 0
    if args.command == "_build":
        return build_child(args.arm)
    if args.command == "compile":
        if args.from_run is not None:
            return report_compile_run(args.from_run, args.limit, args.callers, args.depth,
                                      args.process)
        if not args.name:
            raise SystemExit("compile needs an arm NAME or --from RUN")
        return compile_profile(args.name, args.program, args.hz, args.limit, args.timeout,
                               args.max_tree_rss, args.preflight_reserve, args.settings,
                               args.keep_ir)
    if args.command == "_compile":
        return compile_child(args.arm, args.program, args.run)
    if args.command == "ab":
        with performance_lock():
            flags = {"latency_receipts": True} if args.latency_receipts else {}
            rows = compare(args.a, args.b, args.pairs, args.gc, args.workers, args.rounds,
                           control_arm=args.control_arm, **flags)
        apply_control(rows)
        print_comparison(rows)
        append_ledger(rows)
        return 0
    if args.command == "steps":
        print_steps(args.names, args.count, args.warm, args.marker, args.rounds, args.limit)
        return
    if args.command == "instr":
        if args.short_rounds < 0 or args.long_rounds <= args.short_rounds:
            raise SystemExit("--long-rounds must exceed nonnegative --short-rounds")
        if args.repeats <= 0 or args.workers <= 0:
            raise SystemExit("--repeats and --workers must be positive")
        if args.functions < 0 or (args.functions and args.counter_backend != "callgrind"):
            raise SystemExit("--functions requires an explicit Callgrind backend and nonnegative N")
        backend, _executable = _counter_backend(args.counter_backend)
        print(load_note(os.getloadavg()[0]))
        function_results = {} if args.functions else None
        options = {} if function_results is None else {"function_results": function_results}
        rows = instruction_rows(measure_instructions(
            args.names, args.repeats, args.short_rounds, args.long_rounds, args.workers,
            counter_backend=backend, **options), counter_backend=backend)
        print_instructions(rows)
        if function_results is not None:
            print_instruction_functions(function_results, args.functions)
        return 0
    if args.command == "table":
        with performance_lock():
            print_table(args.name, args.rounds, args.repeats)
        return 0
    if args.command == "asyncio":
        for count, rate in measure_asyncio(args.rounds, args.repeats).items():
            print(f"asyncio w{count} {rate:.0f} req/s")
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
                  args.limit, args.inclusive, args.against, args.against_gc,
                  tuple(part for part in args.callers.split(",") if part), args.depth)
        return 0
    if args.command == "locks":
        print_locks(args.name, args.gc, args.workers, args.seconds, args.primitive)
        return 0
    if args.command == "ir":
        print_ir_shapes(ir_shapes_of(args.paths), args.limit)
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
