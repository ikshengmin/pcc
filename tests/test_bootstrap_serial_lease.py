"""Harmless POSIX lease concurrency; Windows verifies non-Darwin dispatch.

No compiler, native bootstrap, GC operation, or original native-test import.
The lease functions are taken unchanged from their actual source AST.
"""
import ast
import contextlib
from copy import deepcopy
import multiprocessing
import os
from pathlib import Path
import queue
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/python/test_pcc_bootstrap_full.py"
FUNCTIONS = {
    "_env_int", "_pid_alive", "_clamp_int",
    "_bootstrap_resource_run_id", "_bootstrap_resource_run_dir",
    "_bootstrap_resource_pool_lock", "_mark_bootstrap_gc_done",
    "_bootstrap_done_gc_count", "_bootstrap_gc_active_limit",
    "_bootstrap_effective_active_limit_locked", "_bootstrap_gc_priority",
    "_bootstrap_wait_file_backend", "_bootstrap_pid_file_alive",
    "_prune_bootstrap_resource_files_locked", "_higher_priority_waiting_locked",
    "_bootstrap_active_gc_lease",
}
CONSTANTS = {"_GC_BACKENDS", "_GC_BOOTSTRAP_WEIGHT", "_GC_BOOTSTRAP_MAX_ACTIVE_BACKENDS"}


def _load(root):
    tree = ast.parse(SOURCE.read_text())
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS]
    assert {node.name for node in functions} == FUNCTIONS
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in CONSTANTS:
                    constants[target.id] = ast.literal_eval(node.value)
    assert set(constants) == CONSTANTS
    namespace = {"os": os, "time": time, "contextlib": contextlib, "Path": Path,
                 "_BOOTSTRAP_RESOURCE_POOL_DIR": root / "pool",
                 "_BOOTSTRAP_RESOURCE_POOL_LOCK": root / "pool.lock", **constants}
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                             *deepcopy(functions)], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
    return namespace


def _assert_nonmac_dispatch():
    for gc in range(5):
        path = ROOT / f"tests/python/gc/test_pcc_bootstrap_full_gc{gc}.py"
        tree = ast.parse(path.read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
        branch = next(node for node in function.body if isinstance(node, ast.If))
        assert ast.unparse(branch.test) == "not _is_macos_arm64()"
        calls = [node.func.id for node in ast.walk(ast.Module(body=branch.body, type_ignores=[]))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
        assert "run_from_shared" in calls and "run_full_three_stage_bootstrap_self_gc" not in calls
        assert isinstance(branch.body[-1], ast.Return)
        assert any(isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                   and isinstance(node.value.func, ast.Name)
                   and node.value.func.id == "run_full_three_stage_bootstrap_self_gc"
                   for node in function.body[function.body.index(branch) + 1:])


def _worker(root_text, gc, release, events, mode):
    root = Path(root_text)
    os.environ["PCC_BOOTSTRAP_FULL_RUN_ID"] = "finite-lease"
    os.environ["PCC_SELF_BACKEND_OBJECT_CACHE"] = "0"
    os.environ.pop("PCC_BOOTSTRAP_FULL_MAX_ACTIVE_GC", None)
    namespace = _load(root)
    (root / ("attempt-" + gc)).write_text(str(os.getpid()))
    outcome = "normal"
    start = finish = None
    try:
        with namespace["_bootstrap_active_gc_lease"](gc, 5) as slots:
            assert slots == 1
            # A real exclusive file is an independent overlap oracle, not a
            # replacement for the authentic fcntl/resource-pool lock.
            owner = root / "inside-critical-section"
            fd = os.open(owner, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            start = time.monotonic_ns()
            events.put(("enter", gc, start))
            try:
                if not release.wait(8):
                    raise AssertionError("finite controller did not release worker")
                time.sleep(0.03)
                if mode == "exception":
                    raise RuntimeError("finite-body-error")
                if mode == "interrupt":
                    raise KeyboardInterrupt("finite-body-interrupt")
            finally:
                finish = time.monotonic_ns()
                owner.unlink()
    except RuntimeError as exc:
        if str(exc) != "finite-body-error":
            raise
        outcome = "exception"
    except KeyboardInterrupt as exc:
        if str(exc) != "finite-body-interrupt":
            raise
        outcome = "interrupt"
    finally:
        namespace["_mark_bootstrap_gc_done"](gc)
    assert namespace["_pid_alive"](os.getpid())
    assert not (namespace["_bootstrap_resource_run_dir"]() / f"active-gc{gc}-{os.getpid()}").exists()
    assert not (namespace["_bootstrap_resource_run_dir"]() / f"waiting-gc{gc}-{os.getpid()}").exists()
    events.put(("exit", gc, finish, outcome))


def _one_contention_case(root, mode):
    root.mkdir()
    context = multiprocessing.get_context("spawn")
    release = context.Event()
    events = context.Queue()
    processes = []
    reports = []
    try:
        holder = context.Process(target=_worker, args=(str(root), "0", release, events, mode))
        holder.start()
        processes.append(holder)
        first = events.get(timeout=5)
        assert first[:2] == ("enter", "0")
        reports.append(first)
        for gc in ("1", "2", "3", "4"):
            worker = context.Process(target=_worker, args=(str(root), gc, release, events, "normal"))
            worker.start()
            processes.append(worker)
        deadline = time.monotonic() + 5
        while not all((root / ("attempt-" + gc)).is_file() for gc in ("1", "2", "3", "4")):
            assert time.monotonic() < deadline, "finite waiters did not start"
            time.sleep(0.01)
        # Four real workers have attempted the lease; none may enter while
        # holder0 remains in its body, including with the hard ceiling one.
        with pytest.raises(queue.Empty):
            events.get(timeout=0.3)
        assert all(process.is_alive() for process in processes)
        release.set()
        deadline = time.monotonic() + 10
        for process in processes:
            process.join(max(0, deadline - time.monotonic()))
            assert process.exitcode == 0
        while len(reports) < 10:
            reports.append(events.get(timeout=1))
        enters = {row[1]: row[2] for row in reports if row[0] == "enter"}
        exits = {row[1]: row for row in reports if row[0] == "exit"}
        assert set(enters) == set(exits) == set("01234")
        assert exits["0"][3] == mode
        intervals = sorted((enters[gc], exits[gc][2]) for gc in enters)
        assert all(start < end for start, end in intervals)
        assert all(left[1] <= right[0] for left, right in zip(intervals, intervals[1:]))
        assert not (root / "inside-critical-section").exists()
        pool = root / "pool/finite-lease"
        assert not list(pool.glob("active-gc*-*")) and not list(pool.glob("waiting-gc*-*"))
        assert {path.name for path in pool.glob("done-gc*")} == {"done-gc" + gc for gc in "01234"}
    finally:
        release.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(2)
            if process.is_alive():
                process.kill()
                process.join(2)
            assert not process.is_alive()
        events.close()
        events.join_thread()


def test_posix_five_waiters_serialize_and_release_or_windows_dispatch(tmp_path):
    if os.name == "nt":
        _assert_nonmac_dispatch()
        print("Windows dispatch-only contract: Darwin/POSIX lease workers were not executed")
        return
    for mode in ("normal", "exception", "interrupt"):
        _one_contention_case(tmp_path / mode, mode)


@pytest.mark.parametrize("override", [None, "3", "99"])
def test_original_max_one_policy_remains_clamped(tmp_path, monkeypatch, override):
    namespace = _load(tmp_path)
    if override is None:
        monkeypatch.delenv("PCC_BOOTSTRAP_FULL_MAX_ACTIVE_GC", raising=False)
    else:
        monkeypatch.setenv("PCC_BOOTSTRAP_FULL_MAX_ACTIVE_GC", override)
    assert namespace["_GC_BOOTSTRAP_MAX_ACTIVE_BACKENDS"] == 1
    assert [namespace["_bootstrap_gc_active_limit"](slots) for slots in (1, 2, 5)] == [1, 1, 1]


def test_original_cold_cache_reference_order_is_preserved(tmp_path, monkeypatch):
    namespace = _load(tmp_path)
    monkeypatch.setenv("PCC_SELF_BACKEND_OBJECT_CACHE", "1")
    limit = namespace["_bootstrap_effective_active_limit_locked"]
    assert limit(tmp_path, 5, 1, "0") == 1
    assert limit(tmp_path, 5, 1, "4") == 0
    (tmp_path / "done-gc0").write_text("done")
    assert limit(tmp_path, 5, 1, "4") == 1


def test_all_five_nonmac_nodes_avoid_the_darwin_lease():
    _assert_nonmac_dispatch()


def test_no_native_body_or_resource_limit_changes():
    tree = ast.parse(SOURCE.read_text())
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                constants[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    assert constants["_BOOTSTRAP_STAGE_TIMEOUT_S"] == 600
    assert constants["_GC_BOOTSTRAP_MAX_ACTIVE_BACKENDS"] == 1
    assert constants["_GC_BOOTSTRAP_MAX_FRONTEND_JOBS"] == 2
    assert constants["_GC_BOOTSTRAP_MAX_SELF_BACKEND_JOBS"] == 2
    assert "--no-performance-lock" not in SOURCE.read_text()
