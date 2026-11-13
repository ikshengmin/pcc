"""Exercise production compile-window call sites with no real child processes.

The fake AB boundary supplies preparation/receipt fixtures only. Production
run(), _run_build(), function smoke, _run_stage(), _run_guarded() and guard
command construction remain live. These are orchestration tests, not native
execution or performance evidence.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
OUTER = {
    "PCC_WORKER_TREE_STATE_PATH": "/outer/tree.tsv",
    "PCC_WORKER_TREE_BUDGET_BYTES": "1024",
    "PCC_WORKER_TREE_RECOVERY": "pcc.worker-tree-recovery.v1\t1.0,3.0\t4.0\t140.0",
}


def _load(name, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "compile_wiring_" + name, ROOT / "scripts" / (name + ".py"),
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.syspath_prepend(str(ROOT))
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def no_real_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("compile-window wiring tests must never launch a real process")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)


def _stage1_fixture(tmp_path, monkeypatch, *, measured, failure=0, target_logs=True):
    tool = _load("run_pcc_stage1_build", monkeypatch)
    source = tmp_path / "source"
    (source / "pcc").mkdir(parents=True)
    (source / "pcc/__main__.py").write_text("# synthetic source\n")
    archive = tmp_path / "runtime.a"
    archive.write_bytes(b"synthetic runtime")
    output = tmp_path / "output"
    calls, events, failures = [], [], []
    manifest = {"bootstrap_source_sha256": "source", "files": {"pcc/__main__.py": "main"}}
    monkeypatch.setattr(tool, "_require_immutable_source", lambda *a: None)
    monkeypatch.setattr(tool, "source_manifest", lambda *a: manifest)

    def snapshot(_source, _manifest, destination, _ab):
        (destination / "pcc").mkdir(parents=True)
        (destination / "pcc/__main__.py").write_text("# synthetic snapshot\n")
    monkeypatch.setattr(tool, "_snapshot_sources", snapshot)

    def producer(destination, _ab):
        (destination / "producer-tools").mkdir()
        return {}, {}
    monkeypatch.setattr(tool, "_snapshot_producer_tools", producer)
    original_persist = tool._persist

    def persist(path, value):
        events.append(path.name)
        original_persist(path, value)
    monkeypatch.setattr(tool, "_persist", persist)
    for key, value in OUTER.items():
        monkeypatch.setenv(key, value)

    @contextlib.contextmanager
    def lock():
        events.append("lock-enter")
        yield
        events.append("lock-exit")

    class CompileABError(RuntimeError):
        pass

    def run_process(command, *, timeout, env, cwd):
        calls.append((list(command), timeout, dict(env), cwd))
        index = len(calls)
        events.append("compile" if index == 1 else "smoke")
        if index == 1:
            raw = command[command.index("--") + 1:] if measured else command
            (output / "pcc1").write_bytes(b"synthetic executable")
            (output / "pcc1").chmod(0o700)
            Path(raw[raw.index("-o") + 1]).write_text("synthetic time")
            Path(raw[raw.index("--profile-json") + 1]).write_text("{}")
            if measured and target_logs:
                (output / "compile-process/target.stdout").write_text("compiler stdout")
                (output / "compile-process/target.stderr").write_text("compiler stderr")
            return subprocess.CompletedProcess(command, failure, "observer stdout", "observer stderr")
        if index == 3:
            Path(command[-1]).write_bytes(b"synthetic smoke program")
        return subprocess.CompletedProcess(command, 0, "42\n" if index == 4 else "", "")

    def linkage(*args, **kwargs):
        events.append("linkage")
        return {"synthetic": True}

    ab = SimpleNamespace(
        CompileABError=CompileABError, TIME_BINARY=Path("/synthetic/time"),
        PRIMARY_SOURCE="pcc/__main__.py", BUILD_RECEIPT_SCHEMA="synthetic.receipt",
        require_claim_platform=lambda: None,
        _absolute_existing=lambda path: Path(path),
        _validate_output_dir=lambda path: Path(path),
        _paths_overlap=lambda *a: False,
        _claim_output_directory=lambda path, **kw: path.mkdir(),
        _prepare_runtime_bundle=lambda *a, **kw: {"archive": str(archive)},
        runtime_bundle_evidence=lambda bundle: bundle,
        external_tool_evidence=lambda *a: [],
        host_python_runtime_evidence=lambda *a: {},
        _measurement_env=lambda *a, **kw: {},
        _verify_source_snapshot=lambda *a, **kw: None,
        _run_process=run_process,
        parse_time_output=lambda text: {"wall_s": 999},
        _linkage=linkage,
        _verify_runtime_bundle=lambda *a: None,
        sha256_path=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        _performance_lock=lock,
        _mark_owned_run_failure=lambda path, **kw: failures.append(kw),
    )
    args = tool._parser().parse_args([
        "--arm", "candidate", "--source-root", str(source),
        "--runtime-archive", str(archive), "--output-dir", str(output),
        "--memory-budget-bytes", "1024", "--timeout", "61", "--smoke-timeout", "7",
        *(["--measure-compile-window"] if measured else []),
    ])
    return tool, ab, args, output, calls, events, failures


@pytest.mark.parametrize("measured", [False, True])
def test_stage1_run_measures_only_compile_and_publishes_after_smoke(tmp_path, monkeypatch, measured):
    tool, ab, args, output, calls, events, failures = _stage1_fixture(
        tmp_path, monkeypatch, measured=measured,
    )
    result = tool.run(args, _ab=ab)
    assert result["status"] == "SUCCEEDED" and not failures
    assert len(calls) == 4  # build, --help, function compile, function run
    command, timeout, env, cwd = calls[0]
    assert timeout == 61 and cwd == output / "source-snapshot"
    assert {key: env[key] for key in OUTER} == OUTER
    raw = command[command.index("--") + 1:] if measured else command
    assert raw == result["command"]
    assert raw[:4] == ["/synthetic/time", "-lp", "-o", str(output / "stage1.time")]
    assert raw[-3:] == [str(output / "source-snapshot/pcc/__main__.py"), "-o", str(output / "pcc1")]
    if measured:
        assert command[:2] == [str(Path(sys.executable).resolve()), str(output / "producer-tools/run_process_tree_sample.py")]
        for flag, value in {
            "--result": output / "compile-process/result.json",
            "--samples": output / "compile-process/samples.tsv",
            "--stdout": output / "compile-process/target.stdout",
            "--stderr": output / "compile-process/target.stderr",
            "--cwd": cwd, "--timeout": "61", "--interval": "0.25",
            "--max-tree-rss-bytes": "1024",
        }.items():
            assert command[command.index(flag) + 1] == str(value)
        assert "--no-performance-lock" in command and "--preserve-worker-budget" in command
        assert "--darwin-preflight-reserve-bytes" not in command
    else:
        assert not (output / "compile-process").exists()
    for smoke_command, smoke_timeout, smoke_env, smoke_cwd in calls[1:]:
        assert "--preserve-worker-budget" not in smoke_command
        assert not any("run_process_tree_sample.py" in item for item in smoke_command)
        assert smoke_timeout == 7 and smoke_cwd == output / "work"
        assert {key: smoke_env[key] for key in OUTER} == OUTER
    assert calls[1][0] == [str(output / "pcc1"), "--help"]
    assert calls[2][0][-3:] == [str(output / "work/stage1_function_smoke.py"), "-o", str(output / "work/stage1_function_smoke")]
    assert calls[3][0] == [str(output / "work/stage1_function_smoke")]
    assert events[0] == "lock-enter" and events[-1] == "lock-exit"
    assert events.index("compile") < events.index("smoke") < events.index("linkage")
    assert events.index("linkage") < events.index("stage1-result.json") < events.index("build-receipt.json")
    receipt = json.loads((output / "build-receipt.json").read_text())
    assert receipt["command"] == raw
    prefix = "compiler" if measured else "observer"
    assert (output / "stage1.stdout").read_text() == prefix + " stdout"
    assert (output / "stage1.stderr").read_text() == prefix + " stderr"


@pytest.mark.parametrize("measured,target_logs", [(False, False), (True, True), (True, False)])
@pytest.mark.parametrize("returncode", [7, 124, 125])
def test_stage1_compile_failure_keeps_code_logs_and_skips_smoke_and_publication(
    tmp_path, monkeypatch, measured, target_logs, returncode,
):
    tool, ab, args, output, calls, events, failures = _stage1_fixture(
        tmp_path, monkeypatch, measured=measured, failure=returncode, target_logs=target_logs,
    )
    prefix = "compiler" if measured and target_logs else "observer"
    with pytest.raises(ab.CompileABError, match=f"rc={returncode}: {prefix} stderr"):
        tool.run(args, _ab=ab)
    assert len(calls) == 1 and "smoke" not in events and "linkage" not in events
    assert not (output / "build-receipt.json").exists()
    assert not (output / "stage1-result.json").exists()
    assert failures[0]["status"] == "ERROR" and f"rc={returncode}" in failures[0]["error"]
    assert (output / "stage1.stdout").read_text() == prefix + " stdout"
    assert (output / "stage1.stderr").read_text() == prefix + " stderr"


def _bootstrap_fixture(tmp_path, monkeypatch, *, measured, external):
    tool = _load("bootstrap", monkeypatch)
    monkeypatch.setattr(tool, "_configured_host_memory_reserve_bytes", lambda env: 32)
    monkeypatch.setattr(tool.sys, "platform", "darwin")
    options = tool.Options({
        **OUTER, "PCC_BOOTSTRAP_OUT_DIR": str(tmp_path),
        "PCC_BOOTSTRAP_MEASURE_COMPILE_WINDOW": "1" if measured else "0",
        "PCC_BOOTSTRAP_EXTERNAL_MEMORY_GUARD": "1" if external else "0",
        "PCC_BOOTSTRAP_MAX_TREE_RSS_BYTES": "1024", "PCC_BOOTSTRAP_STAGE_TIMEOUT": "61",
    })
    options.memory_budget_selection = {"selection_kind": "inherited", "max_tree_rss_bytes": 1024}
    return tool, options


@pytest.mark.skipif(os.name == "nt", reason="POSIX sampler wiring")
@pytest.mark.parametrize("measured,external", [(False, False), (False, True), (True, True)])
@pytest.mark.parametrize("returncode", [0, 7, 124, 125])
def test_bootstrap_guard_preserves_outer_contract_paths_and_status(
    tmp_path, monkeypatch, capsys, measured, external, returncode,
):
    tool, options = _bootstrap_fixture(tmp_path, monkeypatch, measured=measured, external=external)
    target = ["synthetic-pcc1", "source.py", "-o", "synthetic-pcc2"]
    calls = []

    def launch(command, **kwargs):
        calls.append((list(command), kwargs))
        if "--result" in command:
            directory = Path(command[command.index("--result") + 1]).parent
            (directory / "target.stdout").write_text("compile stdout\n")
            (directory / "target.stderr").write_text("compile stderr\n")
        return subprocess.CompletedProcess(command, returncode)
    monkeypatch.setattr(tool.subprocess, "run", launch)
    code, directory = tool._run_guarded(target, options, 2, dict(OUTER))
    assert code == returncode and len(calls) == 1
    command, kwargs = calls[0]
    assert kwargs == {"cwd": str(tool.ROOT), "env": OUTER, "check": False}
    if external and not measured:
        assert command == target and directory is None
        return
    assert directory is not None
    if measured:
        assert directory == tmp_path / "stage2.compile-process"
    assert command[command.index("--") + 1:] == target
    assert command.count(str(tool.SCRIPTS / "run_process_tree_sample.py")) == 1
    for flag, value in {
        "--result": directory / "result.json", "--samples": directory / "samples.tsv",
        "--stdout": directory / "target.stdout", "--stderr": directory / "target.stderr",
        "--cwd": tool.ROOT, "--timeout": "61", "--interval": "0.25",
        "--max-tree-rss-bytes": "1024",
    }.items():
        assert command[command.index(flag) + 1] == str(value)
    assert "--no-performance-lock" in command
    assert ("--preserve-worker-budget" in command) is measured
    assert ("--darwin-preflight-reserve-bytes" in command) is (not measured)
    assert "--auto-tree-rss-ceiling-bytes" not in command
    captured = capsys.readouterr()
    assert "compile stdout" in captured.out and "compile stderr" in captured.err


@pytest.mark.skipif(os.name == "nt", reason="POSIX sampler wiring")
@pytest.mark.parametrize("compile_rc,barrier_rc", [(0, 0), (7, 0), (124, 0), (125, 0), (0, 9)])
def test_bootstrap_stage_runs_barrier_and_publication_outside_compile_sampler(
    tmp_path, monkeypatch, compile_rc, barrier_rc,
):
    tool, options = _bootstrap_fixture(tmp_path, monkeypatch, measured=True, external=True)
    output = tmp_path / "pcc2"
    events, calls, receipts = [], [], []
    monkeypatch.setattr(tool, "_plan_paths", lambda *a: ("", "", ""))
    monkeypatch.setattr(tool, "stage_environment", lambda *a: dict(OUTER))
    monkeypatch.setattr(tool, "_children_cpu_split", lambda: None)
    monkeypatch.setattr(tool, "now_ms", lambda: 1000)

    def launch(command, **kwargs):
        events.append("compile")
        calls.append(command)
        output.write_bytes(b"synthetic executable")
        output.chmod(0o700)
        return subprocess.CompletedProcess(command, compile_rc)

    def barrier(*args):
        assert len(calls) == 1
        events.append("barrier")
        return barrier_rc

    def publish(*args, **kwargs):
        events.append("publish")
        receipts.append(kwargs)

    monkeypatch.setattr(tool.subprocess, "run", launch)
    monkeypatch.setattr(tool, "stage_exec_barrier", barrier)
    monkeypatch.setattr(tool, "write_stage_result_json", publish)
    expected = compile_rc or barrier_rc
    if expected:
        with pytest.raises(tool.BootstrapError) as raised:
            tool._run_stage(2, output, ["synthetic-pcc1"], options)
        assert raised.value.exit_code == expected
    else:
        tool._run_stage(2, output, ["synthetic-pcc1"], options)
    assert events == (["compile", "publish"] if compile_rc else ["compile", "barrier", "publish"])
    assert receipts[0]["returncode"] == expected
    assert receipts[0]["barrier_returncode"] == (0 if compile_rc else barrier_rc)
    assert len(calls) == 1
    assert calls[0][calls[0].index("--") + 1:] == [
        "synthetic-pcc1", "--backend", "self", str(tool.MAIN_PY), "-o", str(output),
    ]
    # Success must retain raw measurement artifacts even without KEEP_PROCESS_SAMPLES.
    assert (tmp_path / "stage2.compile-process").is_dir()
