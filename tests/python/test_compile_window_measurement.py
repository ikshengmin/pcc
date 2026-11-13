"""Compile-window evidence is separate from outer guard diagnostics.

All process launches below are replaced with synthetic owned-process fixtures.
These tests exercise the production sampler and validators without a compiler.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    path = ROOT / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location("compile_window_test_" + name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


def _synthetic_guard(tmp_path, monkeypatch, *, flags=(), outcome="complete", inherited=None):
    tool = _load("run_process_tree_sample")
    if tool.compile_ab is None:
        # Windows imports only the shared budget selector in production. This
        # fixture replaces every OS/process operation and exercises POSIX guard
        # bookkeeping without asking Windows to launch a POSIX process tree.
        monkeypatch.setattr(tool, "compile_ab", _load("run_pcc_compile_ab"))
    clock = SimpleNamespace(now=100.0)
    clock.monotonic = lambda: clock.now
    clock.sleep = lambda seconds: setattr(clock, "now", clock.now + seconds)
    monkeypatch.setattr(tool, "time", clock)
    trace = []
    launched = {}
    for key in ("PCC_WORKER_TREE_STATE_PATH", "PCC_WORKER_TREE_BUDGET_BYTES", "PCC_WORKER_TREE_RECOVERY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in (inherited or {}).items():
        monkeypatch.setenv(key, value)

    @contextlib.contextmanager
    def lock():
        clock.now += 10.0  # Preparation must not enter the compile window.
        yield
        clock.now += 7.0   # Nor may finalization enter the compile window.

    monkeypatch.setattr(tool.compile_ab, "_performance_lock", lock)
    process = SimpleNamespace(pid=123, returncode=None)
    identities = {
        123: tool._ProcessIdentity(123, 1, (10, 0)),
        124: tool._ProcessIdentity(124, 123, (20, 0)),
    }
    samples = iter([
        {123: (1, 100, "compile-root"), 124: (123, 200, "compile-worker")},
        {},
    ])
    polls = iter([None, 7 if outcome == "target_failure" else 0])

    def launch(command, **kwargs):
        launched.update(command=command, **kwargs)
        trace.append(("launch", clock.now))
        return process

    def poll():
        process.returncode = next(polls)
        trace.append(("poll", clock.now, process.returncode))
        return process.returncode

    process.poll = poll

    def table(**kwargs):
        stamp = clock.now
        clock.now += 0.1
        trace.append(("sample", stamp))
        if outcome == "sampler_error":
            raise tool.ProcessTreeSampleError("synthetic sampling failure")
        return next(samples), 0, stamp

    def post_exit(**kwargs):
        trace.append(("post_exit", clock.now))
        if outcome == "orphan":
            return {124: (1, 200, "compile-worker")}, 0
        return {}, 0

    def terminate(process, observed):
        trace.append(("terminate", clock.now))
        process.returncode = -9

    def forbidden(*args, **kwargs):
        pytest.fail("synthetic compile test must not launch or inspect real processes")

    monkeypatch.setattr(tool.subprocess, "Popen", launch)
    monkeypatch.setattr(tool.subprocess, "run", forbidden)
    monkeypatch.setattr(tool, "_timed_process_table", table)
    monkeypatch.setattr(tool, "_process_table", post_exit)
    monkeypatch.setattr(tool, "_process_identity", identities.get)
    monkeypatch.setattr(tool, "_process_command", forbidden)
    monkeypatch.setattr(tool, "_host_memory_observation", forbidden)
    monkeypatch.setattr(tool, "_terminate_owned_processes", terminate)
    monkeypatch.setattr(tool, "_capture_terminal_argv", lambda *args: ({"status": "synthetic"}, {}))
    monkeypatch.setattr(tool, "_capture_worker_manifests", lambda *args, **kwargs: {"status": "synthetic"})
    args = tool._parser().parse_args([
        "--result", str(tmp_path / "result.json"), "--samples", str(tmp_path / "samples.tsv"),
        "--stdout", str(tmp_path / "stdout"), "--stderr", str(tmp_path / "stderr"),
        "--cwd", str(tmp_path), "--timeout", "10.05" if outcome == "timeout" else "60",
        "--max-tree-rss-bytes", "250" if outcome == "memory_limit" else "1024",
        *flags, "--", "synthetic-compile",
    ])
    error = None
    try:
        tool.run(args)
    except tool.ProcessTreeSampleError as exc:
        error = str(exc)
    report = json.loads((tmp_path / "result.json").read_text())
    return report, launched, trace, error


def _outer_budget(cap="1024"):
    return {
        "PCC_WORKER_TREE_STATE_PATH": "/outer/observations.tsv",
        "PCC_WORKER_TREE_BUDGET_BYTES": cap,
        "PCC_WORKER_TREE_RECOVERY": "pcc.worker-tree-recovery.v1\t1.0,3.0\t4.0\t140.0",
    }


def test_compile_window_excludes_lock_and_finalization_but_keeps_legacy_elapsed(tmp_path, monkeypatch):
    report, launched, trace, error = _synthetic_guard(
        tmp_path, monkeypatch, flags=["--preserve-worker-budget"], inherited=_outer_budget(),
    )
    assert error is None and report["status"] == "COMPLETE"
    window = report["target_window"]
    assert window["schema"] == "pcc.compile-process-window.v1"
    assert window["scope"] == "launch_to_observed_owned_tree_exit_including_compile_wrappers"
    assert window["start_monotonic_s"] == 110.0
    assert window["samples_elapsed_origin_monotonic_s"] == 100.0
    assert window["end_monotonic_s"] == pytest.approx(110.45)
    assert window["wall_s"] == pytest.approx(0.45)
    assert report["elapsed_s"] == pytest.approx(17.45)
    assert window["root_pid"] == 123 and window["root_start_identity"] == [10, 0]
    assert window["clean_exit"] is True
    assert report["sample_count"] == 2 and report["peak_tree_rss_bytes"] == 300
    assert report["interval_s"] == 0.25 and report["max_tree_rss_bytes"] == 1024
    assert window["samples_sha256"] == hashlib.sha256((tmp_path / "samples.tsv").read_bytes()).hexdigest()
    assert window["sampler_sha256"] == hashlib.sha256(
        (ROOT / "scripts" / "run_process_tree_sample.py").read_bytes()
    ).hexdigest()
    assert window["support_tool_sha256"] == hashlib.sha256(
        (ROOT / "scripts" / "run_pcc_compile_ab.py").read_bytes()
    ).hexdigest()
    assert window["lock_tool_sha256"] == hashlib.sha256(
        (ROOT / "scripts" / "file_lock.py").read_bytes()
    ).hexdigest()
    assert trace[-1][0] == "post_exit"
    assert launched["start_new_session"] is True


def test_nested_measurement_preserves_outer_admission_and_original_deadline(tmp_path, monkeypatch):
    inherited = _outer_budget()
    report, launched, _trace, error = _synthetic_guard(
        tmp_path, monkeypatch, flags=["--preserve-worker-budget"], inherited=inherited,
    )
    assert error is None
    assert {key: launched["env"][key] for key in inherited} == inherited
    assert report["target_window"]["worker_budget_scope"] == "inherited_outer_tree"
    assert not (tmp_path / "result.json.worker-rss.tsv").exists()
    assert report["process_table_timeouts_s"] == [1.0, 3.0]


def test_ordinary_guard_keeps_its_original_schema_and_budget_mailbox(tmp_path, monkeypatch):
    report, launched, _trace, error = _synthetic_guard(tmp_path, monkeypatch)
    assert error is None
    assert "target_window" not in report
    assert (tmp_path / "result.json.worker-rss.tsv").is_file()
    assert launched["env"]["PCC_WORKER_TREE_STATE_PATH"] == str(tmp_path / "result.json.worker-rss.tsv")
    assert launched["env"]["PCC_WORKER_TREE_RECOVERY"].endswith("\t160.0")


@pytest.mark.parametrize("missing", ["PCC_WORKER_TREE_STATE_PATH", "PCC_WORKER_TREE_BUDGET_BYTES", "PCC_WORKER_TREE_RECOVERY"])
def test_nested_measurement_rejects_incomplete_outer_contract_before_launch(tmp_path, monkeypatch, missing):
    inherited = _outer_budget()
    inherited.pop(missing)
    _report, launched, trace, error = _synthetic_guard(
        tmp_path, monkeypatch, flags=["--preserve-worker-budget"], inherited=inherited,
    )
    assert "matching outer worker budget" in error
    assert launched == {} and trace == []


def test_nested_measurement_rejects_a_different_cap(tmp_path, monkeypatch):
    _report, launched, trace, error = _synthetic_guard(
        tmp_path, monkeypatch, flags=["--preserve-worker-budget"], inherited=_outer_budget("2048"),
    )
    assert "matching outer worker budget" in error
    assert launched == {} and trace == []


@pytest.mark.parametrize("outcome,status", [
    ("memory_limit", "MEMORY_LIMIT"), ("timeout", "TIMEOUT"),
    ("sampler_error", "SAMPLER_ERROR"), ("orphan", "COMPLETE"),
])
def test_nested_measurement_keeps_safety_failures_and_invalidates_window(tmp_path, monkeypatch, outcome, status):
    report, launched, trace, _error = _synthetic_guard(
        tmp_path, monkeypatch, flags=["--preserve-worker-budget"], outcome=outcome,
        inherited=_outer_budget("250" if outcome == "memory_limit" else "1024"),
    )
    assert report["status"] == status
    assert report["target_window"]["clean_exit"] is False
    assert any(entry[0] == "terminate" for entry in trace)
    assert launched["env"]["PCC_WORKER_TREE_RECOVERY"] == _outer_budget()["PCC_WORKER_TREE_RECOVERY"]
    if outcome == "orphan":
        assert report["post_exit_cleanup_pids"] == [124]


def _measurement_fixture(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    samples = (
        "elapsed_s\ttree_rss_bytes\tprocess_count\tlargest_pid\tlargest_rss_bytes\tlargest_command\n"
        "0.1\t300\t2\t124\t200\tcompiler-worker\n"
        "0.45\t0\t0\t0\t0\t\n"
    ).encode()
    (tmp_path / "samples.tsv").write_bytes(samples)
    payload = {
        "schema": "pcc.process_tree_sample.v1", "status": "COMPLETE", "returncode": 0,
        "command": ["compiler", "input.py", "-o", "output"], "cwd": str(tmp_path),
        "max_tree_rss_bytes": 1024, "interval_s": 0.25, "sample_count": 2,
        "peak_tree_rss_bytes": 300, "elapsed_s": 999.0,
        "artifacts": {"samples": str(tmp_path / "samples.tsv")},
        "target_window": {
            "schema": "pcc.compile-process-window.v1",
            "scope": "launch_to_observed_owned_tree_exit_including_compile_wrappers",
            "start_monotonic_s": 100.0, "end_monotonic_s": 100.45, "wall_s": 0.45,
            "samples_elapsed_origin_monotonic_s": 100.0,
            "root_pid": 123, "root_start_identity": [10, 0], "clean_exit": True,
            "worker_budget_scope": "inherited_outer_tree",
            "samples_sha256": hashlib.sha256(samples).hexdigest(), "sampler_sha256": "a" * 64,
            "support_tool_sha256": "e" * 64,
            "lock_tool_sha256": "f" * 64,
        },
    }
    (tmp_path / "result.json").write_text(json.dumps(payload))
    return payload


def _read_measurement(tool, directory, **overrides):
    kwargs = {
        "expected_command": ["compiler", "input.py", "-o", "output"],
        "expected_cwd": directory, "expected_sampler_sha256": "a" * 64,
        "expected_support_sha256": "e" * 64,
        "expected_lock_sha256": "f" * 64,
        "expected_cap": 1024,
    }
    kwargs.update(overrides)
    return tool._compile_measurement(directory, **kwargs)


def test_compile_evidence_uses_target_window_and_hashes_exact_raw_result(tmp_path):
    tool = _load("run_pcc_stage_ab")
    payload = _measurement_fixture(tmp_path)
    evidence = _read_measurement(tool, tmp_path)
    assert evidence["wall_s"] == 0.45 and evidence["wall_s"] != payload["elapsed_s"]
    assert evidence["peak_tree_rss_bytes"] == 300
    assert evidence["sample_count"] == 2
    assert evidence["observed_sample_elapsed_s"] == [0.1, 0.45]
    assert evidence["samples_elapsed_origin_monotonic_s"] == 100.0
    assert evidence["result_sha256"] == hashlib.sha256((tmp_path / "result.json").read_bytes()).hexdigest()
    assert evidence["samples_sha256"] == payload["target_window"]["samples_sha256"]


@pytest.mark.parametrize("key,value", [
    ("schema", "unrelated-result.v1"),
    ("status", "TIMEOUT"), ("returncode", 7), ("returncode", False), ("max_tree_rss_bytes", 2048),
    ("interval_s", 0.5), ("sample_count", 0), ("sample_count", True),
    ("sample_count", 3), ("peak_tree_rss_bytes", 299),
    ("post_exit_cleanup_pids", [124]), ("command", ["different-compiler"]),
    ("cwd", "/different-root"), ("artifacts", {}), ("artifacts", None),
    ("artifacts", []), ("target_window", None), ("target_window", []),
])
def test_compile_evidence_rejects_failed_mixed_or_missing_raw_evidence(tmp_path, key, value):
    tool = _load("run_pcc_stage_ab")
    payload = _measurement_fixture(tmp_path)
    payload[key] = value
    (tmp_path / "result.json").write_text(json.dumps(payload))
    with pytest.raises(tool.StageABError):
        _read_measurement(tool, tmp_path)


@pytest.mark.parametrize("key,value", [
    ("schema", "historical-v0"), ("scope", "whole_stage_including_smoke"),
    ("clean_exit", False), ("worker_budget_scope", "target_tree"),
    ("sampler_sha256", "b" * 64), ("support_tool_sha256", "b" * 64),
    ("lock_tool_sha256", "b" * 64),
    ("samples_sha256", "b" * 64),
    ("wall_s", None), ("wall_s", True), ("wall_s", float("nan")),
    ("wall_s", 0), ("wall_s", 999), ("root_pid", 0),
    ("root_start_identity", None), ("root_start_identity", [10]),
    ("root_start_identity", [10, 0, 0]), ("root_start_identity", [True, 0]),
    ("root_start_identity", [10, -1]), ("root_start_identity", "10,0"),
    ("end_monotonic_s", 100.0), ("samples_elapsed_origin_monotonic_s", None),
    ("samples_elapsed_origin_monotonic_s", 99.0),
    ("samples_elapsed_origin_monotonic_s", 101.0),
])
def test_compile_evidence_rejects_unproven_window(tmp_path, key, value):
    tool = _load("run_pcc_stage_ab")
    payload = _measurement_fixture(tmp_path)
    payload["target_window"][key] = value
    (tmp_path / "result.json").write_text(json.dumps(payload))
    with pytest.raises(tool.StageABError):
        _read_measurement(tool, tmp_path)


def test_compile_evidence_rejects_tampered_raw_samples(tmp_path):
    tool = _load("run_pcc_stage_ab")
    _measurement_fixture(tmp_path)
    with (tmp_path / "samples.tsv").open("ab") as stream:
        stream.write(b"forged sample\n")
    with pytest.raises(tool.StageABError, match="hash"):
        _read_measurement(tool, tmp_path)


@pytest.mark.parametrize("replacement", [
    b"0.1\tinvalid\t2\t124\t200\tcompiler-worker",
    b"0.1\t300\tinvalid\t124\t200\tcompiler-worker",
    b"0.1\t300\t2\tinvalid\t200\tcompiler-worker",
    b"0.1\t300\t2\t124\tinvalid\tcompiler-worker",
    b"0.1\t300\t2\t124\t301\tcompiler-worker",
    b"0.1\t300\t0\t124\t200\tcompiler-worker",
    b"0.1\t300\t2\t-1\t200\tcompiler-worker",
    b"0.1\t300\t2\t124\t200\t\xff",
])
def test_compile_evidence_rejects_malformed_hashed_samples(tmp_path, replacement):
    tool = _load("run_pcc_stage_ab")
    payload = _measurement_fixture(tmp_path)
    samples_path = tmp_path / "samples.tsv"
    samples = samples_path.read_bytes().splitlines()
    samples[1] = replacement
    data = b"\n".join(samples) + b"\n"
    samples_path.write_bytes(data)
    payload["target_window"]["samples_sha256"] = hashlib.sha256(data).hexdigest()
    (tmp_path / "result.json").write_text(json.dumps(payload))
    with pytest.raises(tool.StageABError, match="compile-window"):
        _read_measurement(tool, tmp_path)


def _pairs(tool, count=1):
    envelope = {
        "max_tree_rss_bytes": 1024, "cpu_count": 8, "frontend_jobs": 2,
        "self_backend_jobs": 2, "macho_link_jobs": 8, "gc_backend": 0,
        "cache_policy": {"frontend": "cold", "backend": "cold"},
    }
    measurement = {
        "schema": "pcc.compile-process-window.v1",
        "scope": "launch_to_observed_owned_tree_exit_including_compile_wrappers",
        "interval_s": 0.25, "sample_count": 2,
        "max_tree_rss_bytes": 1024, "sampler_sha256": "a" * 64,
        "support_tool_sha256": "e" * 64,
        "lock_tool_sha256": "f" * 64,
        "clean_exit": True, "wall_s": 2.0, "peak_tree_rss_bytes": 300,
    }
    stage1 = {
        "source_sha256": "b" * 64, "runtime_sha256": "c" * 64,
        "resource_envelope": envelope, "compile_measurement": measurement,
    }
    stage2 = copy.deepcopy(stage1)
    stage2["compile_measurement"].update(wall_s=1.0, peak_tree_rss_bytes=240)
    return [
        {"index": index, "order": list(tool.pair_order(index)),
         "baseline": {"stage1": copy.deepcopy(stage1), "stage2": copy.deepcopy(stage2)},
         "candidate": {"stage1": copy.deepcopy(stage1), "stage2": copy.deepcopy(stage2)}}
        for index in range(1, count + 1)
    ]


@pytest.mark.parametrize("count", [1, 4, 5])
def test_same_source_ratios_require_five_alternating_pairs_and_never_accept_source(count):
    tool = _load("run_pcc_stage_ab")
    summary = tool.summarize_compile_windows(_pairs(tool, count))
    assert summary["availability"] == "AVAILABLE"
    assert summary["qualification"] == (
        "SCREENING_UNQUALIFIED" if count < 5 else "MEASURED_NOT_CORRECTNESS_QUALIFIED"
    )
    assert summary["median_stage2_over_stage1"]["baseline"] == {
        "wall_s": 0.5, "peak_tree_rss_bytes": 0.8,
    }
    assert summary["automatic_acceptance"] is False
    assert "stage1_stage2_stage3_fixed_point" in summary["remaining_gates"]
    assert "five_gc" in summary["remaining_gates"]
    assert "PASS" not in json.dumps(summary)


@pytest.mark.parametrize("mutation", [
    "missing_stage2", "missing_measurement", "source", "runtime", "envelope",
    "sampler", "support", "lock", "scope", "interval", "cap", "cleanup", "missing_wall", "nan_wall",
])
def test_missing_failed_or_mismatched_pair_never_becomes_a_pass(mutation):
    tool = _load("run_pcc_stage_ab")
    pairs = _pairs(tool, 5)
    record = pairs[2]["candidate"]
    second = record["stage2"]
    measurement = second["compile_measurement"]
    if mutation == "missing_stage2":
        record.pop("stage2")
    elif mutation == "missing_measurement":
        second.pop("compile_measurement")
    elif mutation in ("source", "runtime"):
        second[mutation + "_sha256"] = "d" * 64
    elif mutation == "envelope":
        second["resource_envelope"]["gc_backend"] = 1
    else:
        key, value = {
            "sampler": ("sampler_sha256", "d" * 64),
            "support": ("support_tool_sha256", "d" * 64),
            "lock": ("lock_tool_sha256", "d" * 64),
            "scope": ("scope", "outer"), "interval": ("interval_s", 0.5),
            "cap": ("max_tree_rss_bytes", 2048), "cleanup": ("clean_exit", False),
            "missing_wall": ("wall_s", None), "nan_wall": ("wall_s", float("nan")),
        }[mutation]
        measurement[key] = value
    summary = tool.summarize_compile_windows(pairs)
    assert summary["availability"] == summary["qualification"] == "UNAVAILABLE"
    assert summary["ratios_by_arm"]["candidate"]["wall_s"][2] is None
    assert summary["median_stage2_over_stage1"]["candidate"]["wall_s"] is None
    assert summary["automatic_acceptance"] is False


@pytest.mark.parametrize("mutation", ["repeat_order", "duplicate_index"])
def test_five_non_alternating_pairs_stay_screening(mutation):
    tool = _load("run_pcc_stage_ab")
    pairs = _pairs(tool, 5)
    if mutation == "repeat_order":
        pairs[1]["order"] = pairs[0]["order"]
    else:
        pairs[1]["index"] = pairs[0]["index"]
    summary = tool.summarize_compile_windows(pairs)
    assert summary["alternating_pairs"] is False
    assert summary["qualification"] == "SCREENING_UNQUALIFIED"


def test_no_pairs_is_unavailable_not_zero_memory_or_time():
    tool = _load("run_pcc_stage_ab")
    summary = tool.summarize_compile_windows([])
    assert summary["availability"] == summary["qualification"] == "UNAVAILABLE"
    assert summary["median_stage2_over_stage1"]["baseline"]["wall_s"] is None


def _source_fixture(tool, root, *, supports_measurement):
    (root / "pcc").mkdir(parents=True)
    (root / "pcc" / "__main__.py").write_text("pass\n")
    (root / "utils" / "fake_libc_include").mkdir(parents=True)
    (root / "utils" / "fake_libc_include" / "stdio.h").write_text("/* fixture */\n")
    for name in tool.BUILD_SOURCE_SUPPORT:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# historical support fixture\n")
    if supports_measurement:
        (root / "scripts" / "bootstrap.py").write_text(
            'COMPILE_WINDOW_SCHEMA = "pcc.compile-process-window.v1"\n'
        )
        for name in ("run_process_tree_sample.py", "run_pcc_compile_ab.py", "file_lock.py"):
            (root / "scripts" / name).write_text("# frozen measurement fixture\n")


def test_measurement_capable_source_freezes_sampler_and_import_dependency(tmp_path):
    tool = _load("run_pcc_compile_ab")
    _source_fixture(tool, tmp_path, supports_measurement=True)
    names = {path.relative_to(tmp_path).as_posix() for path in tool.build_source_files(tmp_path)}
    assert {"scripts/run_process_tree_sample.py", "scripts/run_pcc_compile_ab.py", "scripts/file_lock.py"} <= names


def test_historical_snapshot_keeps_original_source_closure(tmp_path):
    tool = _load("run_pcc_compile_ab")
    _source_fixture(tool, tmp_path, supports_measurement=False)
    names = {path.relative_to(tmp_path).as_posix() for path in tool.build_source_files(tmp_path)}
    assert "scripts/run_process_tree_sample.py" not in names
    assert "scripts/run_pcc_compile_ab.py" not in names
    assert "scripts/file_lock.py" not in names


@pytest.mark.parametrize("missing", ["run_process_tree_sample.py", "run_pcc_compile_ab.py", "file_lock.py"])
def test_measurement_snapshot_with_missing_support_fails_closed(tmp_path, missing):
    tool = _load("run_pcc_compile_ab")
    _source_fixture(tool, tmp_path, supports_measurement=True)
    (tmp_path / "scripts" / missing).unlink()
    with pytest.raises(tool.CompileABError, match="missing required support file"):
        tool.build_source_files(tmp_path)


def test_producer_snapshot_and_live_tool_map_include_identical_sampler_identity(tmp_path):
    ab = _load("run_pcc_compile_ab")
    stage1 = _load("run_pcc_stage1_build")
    snapshot, live = stage1._snapshot_producer_tools(tmp_path, ab)
    current, current_live = ab.current_producer_tool_evidence()
    assert set(snapshot) == set(current) == {
        "run_pcc_stage1_build.py", "run_pcc_compile_ab.py", "run_process_tree_sample.py", "file_lock.py",
    }
    assert snapshot == current
    assert set(live) == set(snapshot) and len(current_live) == len(snapshot)
    assert snapshot["run_process_tree_sample.py"]["sha256"] == hashlib.sha256(
        (ROOT / "scripts" / "run_process_tree_sample.py").read_bytes()
    ).hexdigest()


@pytest.mark.skipif(os.name == "nt", reason="POSIX compile-window sampler contract")
def test_frozen_producer_sampler_imports_without_repository_or_site_paths(tmp_path):
    ab = _load("run_pcc_compile_ab")
    stage1 = _load("run_pcc_stage1_build")
    stage1._snapshot_producer_tools(tmp_path, ab)
    isolated_cwd = tmp_path / "outside-source"
    isolated_cwd.mkdir()
    # -S prevents an editable install/sitecustomize from hiding missing files.
    # No PYTHONPATH, working-tree cwd, patched imports or sys.path injection
    # reaches this subprocess. --help does not compile or launch a target.
    result = subprocess.run(
        [sys.executable, "-S", str(tmp_path / "producer-tools" / "run_process_tree_sample.py"), "--help"],
        cwd=isolated_cwd, env={"PATH": os.defpath}, text=True,
        capture_output=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--preserve-worker-budget" in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX compile-window sampler contract")
@pytest.mark.parametrize("entry", ["bootstrap.py", "run_process_tree_sample.py"])
def test_frozen_source_support_imports_from_its_own_closure(tmp_path, entry):
    ab = _load("run_pcc_compile_ab")
    source = tmp_path / "source"
    _source_fixture(ab, source, supports_measurement=True)
    for name in ("bootstrap.py", "run_process_tree_sample.py", "run_pcc_compile_ab.py", "file_lock.py"):
        shutil.copyfile(ROOT / "scripts" / name, source / "scripts" / name)
    snapshot = tmp_path / "snapshot"
    for original in ab.build_source_files(source):
        destination = snapshot / original.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
    isolated_cwd = tmp_path / "outside-source"
    isolated_cwd.mkdir()
    result = subprocess.run(
        [sys.executable, "-S", str(snapshot / "scripts" / entry), "--help"],
        cwd=isolated_cwd, env={"PATH": os.defpath}, text=True,
        capture_output=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "usage:" in result.stdout.lower()
