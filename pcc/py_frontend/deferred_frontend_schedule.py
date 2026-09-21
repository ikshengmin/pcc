"""Memory-bounded scheduling after the native frontend coordinator exits.

Frontend and PCO publication use their existing phase-specific admission
estimates. The combined frontend/emitter's old two-slot limit does not
describe these fresh-process phases.
"""

import os

from .pipeline_pass_config import parallel_cpu_budget
from .worker_process_pool import run_worker_processes


_GIB = 1073741824
_DRIVER_RESERVE = _GIB
_FRONTEND_BASE = 3 * _GIB // 4
_FRONTEND_PER_AST_MB = 19 * _GIB // 100
_PCO_BASE = 320 * 1048576
_PCO_PER_SIDECAR_MB = 21 * 1048576
_PCO_LEGACY_BASE = _GIB // 4
_PCO_LEGACY_PER_SIDECAR_MB = 13 * _GIB // 100
_PCO_CAP = 6 * _GIB
_MAX_WIDTH = 12


def indexed_frontend_floor_bytes(ast_bytes):
    return _FRONTEND_BASE + max(0, int(ast_bytes)) * _FRONTEND_PER_AST_MB // 1000000


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


def frontend_groups(ast_sizes, tree_budget, cpu_budget):
    return _admission_groups(
        [indexed_frontend_floor_bytes(size) for size in ast_sizes], tree_budget, cpu_budget,
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
    for indices, width in pco_groups(sizes, budget, parallel_cpu_budget(), gc_backend):
        run_worker_processes([commands[index] for index in indices], width)


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
    for manifest in manifests:
        with open(manifest, "r", encoding="utf-8") as stream:
            lines = stream.read().splitlines()
        if len(lines) < 12 or lines[0] != "pcc.py_frontend.codegen_worker.v4" or lines[-2] != "1":
            raise ValueError("indexed frontend scheduling requires singleton v4 manifests")
        index = int(lines[-1])
        if index < 0:
            raise ValueError("negative frontend module index")
        ast_path = os.path.join(lines[5], "module_" + str(index) + ".json")
        sizes.append(os.path.getsize(ast_path))
    for indices, width in frontend_groups(sizes, budget, parallel_cpu_budget()):
        run_worker_processes([commands[index] for index in indices], width)
