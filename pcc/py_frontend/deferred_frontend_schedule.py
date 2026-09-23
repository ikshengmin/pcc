"""Memory-bounded scheduling after the native frontend coordinator exits.

Frontend and PCO publication use their existing phase-specific admission
estimates. The combined frontend/emitter's old two-slot limit does not
describe these fresh-process phases.
"""

import os

from pcc.unsafe import abi_constant, load_i8, null, ptr_is_null

from .pipeline_pass_config import parallel_cpu_budget
from .worker_process_pool import (
    run_weighted_worker_processes,
    run_worker_processes,
)


_GIB = 1073741824
_DRIVER_RESERVE = _GIB
_FRONTEND_BASE = 3 * _GIB // 4
_FRONTEND_GC0_PER_AST_MB = _GIB // 10
_FRONTEND_LEGACY_PER_AST_MB = 19 * _GIB // 100
_PCO_BASE = 320 * 1048576
_PCO_PER_SIDECAR_MB = 21 * 1048576
_PCO_LEGACY_BASE = _GIB // 4
_PCO_LEGACY_PER_SIDECAR_MB = 13 * _GIB // 100
_PCO_CAP = 6 * _GIB
_MAX_WIDTH = 12


def _native_ast_reads_available() -> bool:
    try:
        return ptr_is_null(null()) != 0
    except NotImplementedError:
        return False


_NATIVE_AST_READS = _native_ast_reads_available()


def _call_node_score(payload: bytes) -> int:
    """Count the exact AST wire marker without a managed byte-slice per hit."""
    if not _NATIVE_AST_READS:
        return payload.count(b'"Call"')
    size = len(payload)
    base = abi_constant("object.bytes.data_offset")
    index = 0
    score = 0
    while index + 6 <= size:
        if load_i8(payload, base + index) == 34:
            if (
                load_i8(payload, base + index + 1) == 67
                and load_i8(payload, base + index + 2) == 97
                and load_i8(payload, base + index + 3) == 108
                and load_i8(payload, base + index + 4) == 108
                and load_i8(payload, base + index + 5) == 34
            ):
                score += 1
                index += 6
                continue
        index += 1
    return score


def indexed_frontend_floor_bytes(ast_bytes, gc_backend=0):
    # GC0: all 392 frozen compiler workers, measured with system-reported RSS
    # and footprint high watermarks. Reserve 25% plus 128 MiB over the larger
    # observed peak. Other collectors retain the uncalibrated legacy floor.
    per_mb = (
        _FRONTEND_GC0_PER_AST_MB if gc_backend == 0
        else _FRONTEND_LEGACY_PER_AST_MB
    )
    return _FRONTEND_BASE + max(0, int(ast_bytes)) * per_mb // 1000000


def indexed_pco_floor_bytes(sidecar_bytes, gc_backend=0):
    # GC0: all 392 frozen compiler PIDX inputs, system-reported per-process
    # RSS AND footprint maxima, each covered by 25% plus 128 MiB headroom.
    # The regression fixture retains the complete envelope. Other collectors
    # keep the previous v57 estimate until their own corpus is calibrated.
    base = _PCO_BASE if gc_backend == 0 else _PCO_LEGACY_BASE
    per_mb = _PCO_PER_SIDECAR_MB if gc_backend == 0 else _PCO_LEGACY_PER_SIDECAR_MB
    return min(_PCO_CAP, base + max(0, int(sidecar_bytes)) * per_mb // 1000000)


def _admission_groups(floors, tree_budget, cpu_budget):
    """Group equal-admission-width jobs without wave straggler serialization.

    Every member's reservation fits ``available // width``. Consequently any
    sliding window of ``width`` members in that group fits the same budget;
    no live-RSS subprocess or untracked GC pointer is needed in the scheduler.
    The external tree-RSS watchdog remains the backstop for estimate errors.
    An individually oversized job runs alone, as in the existing scheduler.
    """
    available = max(0, int(tree_budget) - _DRIVER_RESERVE)
    cap = max(1, min(int(cpu_budget), _MAX_WIDTH))
    groups = [[] for _ in range(cap + 1)]
    for index, floor in enumerate(floors):
        width = max(1, min(cap, available // floor))
        groups[width].append(index)
    return [(items, width) for width, items in enumerate(groups) if items]


def frontend_groups(ast_sizes, tree_budget, cpu_budget, gc_backend=0):
    return _admission_groups(
        [indexed_frontend_floor_bytes(size, gc_backend) for size in ast_sizes],
        tree_budget, cpu_budget,
    )


def pco_groups(sidecar_sizes, tree_budget, cpu_budget, gc_backend=0):
    return _admission_groups(
        [indexed_pco_floor_bytes(size, gc_backend) for size in sidecar_sizes], tree_budget, cpu_budget,
    )


def run_pco_commands(commands, sidecars, oversized, safe_jobs):
    if len(commands) != len(sidecars):
        raise ValueError("PCO command/sidecar inventory mismatch")
    raw = str(os.environ.get("PCC_PY_FRONTEND_JOBS", "") or "").strip().lower()
    budget_raw = str(os.environ.get("PCC_WORKER_TREE_BUDGET_BYTES", "") or "")
    budget = int(budget_raw) if budget_raw.isdigit() else 0
    if raw not in ("auto", "on", "true", "yes") or budget <= 0:
        run_worker_processes(commands[:oversized], 1)
        run_worker_processes(commands[oversized:], safe_jobs)
        return
    sizes = [os.path.getsize(path) for path in sidecars]
    raw_gc = str(os.environ.get("PCC_GC_BACKEND", "0"))
    gc_backend = 0 if raw_gc == "0" else -1
    floors = [indexed_pco_floor_bytes(size, gc_backend) for size in sizes]
    order = sorted(range(len(commands)), key=lambda index: (-sizes[index], index))
    run_weighted_worker_processes(
        [commands[index] for index in order],
        [floors[index] for index in order],
        min(parallel_cpu_budget(), _MAX_WIDTH),
        max(1, budget - _DRIVER_RESERVE),
    )


def run_frontend_commands(commands, manifests, oversized, safe_jobs):
    raw = str(os.environ.get("PCC_PY_FRONTEND_JOBS", "") or "").strip().lower()
    budget_raw = str(os.environ.get("PCC_WORKER_TREE_BUDGET_BYTES", "") or "")
    budget = int(budget_raw) if budget_raw.isdigit() else 0
    if raw not in ("auto", "on", "true", "yes") or budget <= 0:
        # Explicit worker counts and callers without a resource budget retain
        # the plan's conservative policy. Never infer spare physical memory.
        run_worker_processes(commands[:oversized], 1)
        run_worker_processes(commands[oversized:], safe_jobs)
        return
    if len(commands) != len(manifests):
        raise ValueError("frontend command/manifest inventory mismatch")
    sizes = []
    scores = []
    for manifest in manifests:
        with open(manifest, "r", encoding="utf-8") as stream:
            lines = stream.read().splitlines()
        if len(lines) < 12 or lines[0] != "pcc.py_frontend.codegen_worker.v4" or lines[-2] != "1":
            raise ValueError("indexed frontend scheduling requires singleton v4 manifests")
        index = int(lines[-1])
        if index < 0:
            raise ValueError("negative frontend module index")
        ast_path = os.path.join(lines[5], "module_" + str(index) + ".json")
        with open(ast_path, "rb") as stream:
            payload = stream.read()
        sizes.append(len(payload))
        # Literal-heavy ASTs can be large but cheap to lower. Call nodes are
        # a better cheap first-work estimate for this independent module queue.
        scores.append(_call_node_score(payload))
    raw_gc = str(os.environ.get("PCC_GC_BACKEND", "0"))
    gc_backend = 0 if raw_gc == "0" else -1
    floors = [indexed_frontend_floor_bytes(size, gc_backend) for size in sizes]
    order = sorted(
        range(len(commands)),
        key=lambda index: (-scores[index], -floors[index], index),
    )
    run_weighted_worker_processes(
        [commands[index] for index in order],
        [floors[index] for index in order],
        min(parallel_cpu_budget(), _MAX_WIDTH),
        max(1, budget - _DRIVER_RESERVE),
    )
