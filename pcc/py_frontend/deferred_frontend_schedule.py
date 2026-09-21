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
_PCO_BASE = _GIB // 4
_PCO_PER_SIDECAR_MB = 13 * _GIB // 100
_PCO_CAP = 6 * _GIB
_MAX_WIDTH = 12


def indexed_frontend_floor_bytes(ast_bytes):
    return _FRONTEND_BASE + max(0, int(ast_bytes)) * _FRONTEND_PER_AST_MB // 1000000


def indexed_pco_floor_bytes(sidecar_bytes):
    # Same upper envelope as the host deferred controller's complete v57
    # 195-worker sample. Do not lower this from a subset or one wall result.
    return min(_PCO_CAP, _PCO_BASE + max(0, int(sidecar_bytes)) * _PCO_PER_SIDECAR_MB // 1000000)


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


def pco_groups(sidecar_sizes, tree_budget, cpu_budget):
    return _admission_groups(
        [indexed_pco_floor_bytes(size) for size in sidecar_sizes], tree_budget, cpu_budget,
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
    for indices, width in pco_groups(sizes, budget, parallel_cpu_budget()):
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
