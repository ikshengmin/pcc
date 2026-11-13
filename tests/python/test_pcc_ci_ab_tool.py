"""Orchestration boundaries only: real compiler/process execution is forbidden."""
from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tool():
    return _load(ROOT / "scripts/run_pcc_ci_ab.py", "ci_ab_test")


@pytest.fixture(autouse=True)
def forbid_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("orchestration unit tests must mock process boundaries")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)


def _fixture(tool, tmp_path, monkeypatch):
    live = tmp_path / "repo"
    live.mkdir()
    for name in tool.TOOLS:
        path = live / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# same qualified tooling\n")
    monkeypatch.setattr(tool, "REPO_ROOT", live)
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    variable = "pcc/frontends/python/pipeline_import_scan.py"
    live_variable = live / variable
    live_variable.parent.mkdir(parents=True, exist_ok=True)
    live_variable.write_text("after\n")
    trees = {}
    for arm, value in (("baseline", "before"), ("candidate", "after")):
        root = tmp_path / arm
        shutil.copytree(live, root)
        path = root / variable
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n")
        tool._seal(root)
        trees[arm] = {"path": str(root), "sha256": tool._identity(tool._tree(root))}
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    def file(name, content):
        path = inputs / name
        path.write_text(content)
        path.chmod(0o444)
        return {"path": str(path), "sha256": tool._sha(path)}
    diff = "".join(difflib.unified_diff(["before\n"], ["after\n"],
                        fromfile="baseline/" + variable, tofile="candidate/" + variable))
    runtime = file("runtime.a", "runtime")
    sidecar = file("runtime.a.provenance.json", "{}")
    spec = {"schema": tool.SCHEMA, "target_sha": "a" * 40,
            "baseline_source": trees["baseline"], "candidate_source": trees["candidate"],
            "runtime_archive": runtime,
            "runtime_files": {"runtime.a": runtime["sha256"], "runtime.a.provenance.json": sidecar["sha256"]},
            "source_diff": file("diff", diff), "allowed_changed_source": variable,
            "warmup": file("warmup.py", "print(0)"),
            "inputs": [dict(file(str(n) + ".py", "print(" + str(n) + ")"), expected_output=str(n) + "\n")
                       for n in range(1, 7)],
            "qualification": file("qualification.json", "{}")}
    ab = types.SimpleNamespace(COMPILER_VARIABLE_SOURCES={variable},
                                RUNTIME_SIDECAR_SUFFIXES=(".provenance.json", ".target"))
    return spec, ab


def test_validates_exact_full_tree_and_six_inputs(tool, tmp_path, monkeypatch):
    spec, ab = _fixture(tool, tmp_path, monkeypatch)
    roots, trees = tool._validate_spec(spec, tmp_path / "output", ab)
    assert set(roots) == {"candidate", "baseline"}
    assert trees["candidate"] != trees["baseline"]


@pytest.mark.parametrize("mutation", ["extra_source", "tool", "duplicate_input", "wrong_sha", "diff", "runtime", "expected_output"])
def test_rejects_missing_or_changed_prerequisites(tool, tmp_path, monkeypatch, mutation):
    spec, ab = _fixture(tool, tmp_path, monkeypatch)
    if mutation in {"extra_source", "tool"}:
        root = Path(spec["candidate_source"]["path"])
        root.chmod(0o755)
        path = root / ("extra.py" if mutation == "extra_source" else tool.TOOLS[0])
        path.parent.chmod(0o755)
        if path.exists():
            path.chmod(0o644)
        path.write_text("changed\n")
        tool._seal(root)
        spec["candidate_source"]["sha256"] = tool._identity(tool._tree(root))
    elif mutation == "duplicate_input":
        spec["inputs"][5] = spec["inputs"][0]
    elif mutation == "wrong_sha":
        spec["target_sha"] = "b" * 40
    elif mutation == "expected_output":
        spec["inputs"][0]["expected_output"] = "1"
    elif mutation == "diff":
        spec["source_diff"]["sha256"] = "f" * 64
    else:
        spec["runtime_files"]["../../escape"] = "f" * 64
    with pytest.raises(tool.CampaignError):
        tool._validate_spec(spec, tmp_path / "output", ab)


def test_rejects_symlink_and_mutable_sources(tool, tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "x").write_text("x")
    with pytest.raises(tool.CampaignError, match="read-only"):
        tool._tree(root)
    (root / "linked").symlink_to(root / "x")
    with pytest.raises(tool.CampaignError, match="symlink"):
        tool._tree(root, readonly=False)


def test_copy_preserves_full_input_and_refuses_peer_path(tool, tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    (source / "file").write_text("payload")
    tool._seal(source)
    files = tool._tree(source)
    tool._copy_tree(source, target, files)
    assert tool._tree(target) == files
    with pytest.raises(tool.CampaignError, match="existing"):
        tool._copy_tree(source, target, files)
    assert tool._tree(source) == files


def test_remaining_uses_original_deadline(tool, monkeypatch):
    monkeypatch.setattr(tool.time, "monotonic", lambda: 100)
    assert tool._remaining(120, 7200) == 20
    with pytest.raises(TimeoutError):
        tool._remaining(100, 7200)


@pytest.mark.parametrize("guard, child, expected", [
    ({"status": "COMPLETE", "returncode": 0}, {"phase": "MEASURED"}, "SUCCEEDED"),
    ({"status": "COMPLETE", "returncode": 1}, {"phase": "MEASURED"}, "FAILED"),
    ({"status": "COMPLETE", "returncode": 0}, {"phase": "BUILDING_BASELINE"}, "FAILED"),
    ({"status": "MEMORY_LIMIT", "returncode": 0}, {"phase": "MEASURED"}, "MEMORY_LIMIT"),
    ({"status": "TIMEOUT"}, {}, "TIMEOUT"),
    ({"status": "INTERRUPTED"}, {}, "INTERRUPTED"),
    ({"status": "PREFLIGHT_REJECTED"}, {}, "PREFLIGHT_REJECTED"),
    ({"status": "COMPLETE", "returncode": 1}, {"child_status": "TIMEOUT"}, "TIMEOUT"),
    (None, {}, "FAILED"),
])
def test_terminal_never_promotes_partial_or_failed_guard(tool, guard, child, expected):
    result = tool._terminal(child, guard)
    assert result["status"] == expected
    assert result["claim_level"] == "compile-slice-only"
    assert "Stage2 <= Stage1 wall time or process-tree memory" in result["does_not_prove"]


def test_preparation_failure_keeps_owned_manifest(tool, tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path / "repo")
    output = tmp_path / "out"
    args = argparse.Namespace(output_dir=str(output), campaign_spec="/unused", job_deadline_epoch=0)
    assert tool.run(args) == 1
    result = json.loads((output / "manifest.json").read_text())
    assert result["status"] == "FAILED"
    assert result["phase"] == "ADMISSION"
    assert result["run_token"]


def test_existing_output_is_never_modified(tool, tmp_path, monkeypatch):
    output = tmp_path / "peer"
    output.mkdir()
    marker = output / "manifest.json"
    marker.write_text("peer")
    args = argparse.Namespace(output_dir=str(output))
    with pytest.raises(tool.CampaignError, match="existing"):
        tool.run(args)
    assert marker.read_text() == "peer"


def test_archive_remains_loadable_and_snapshotable_without_receipt_rewrite(tool, tmp_path, monkeypatch):
    ab = _load(ROOT / "scripts/run_pcc_compile_ab.py", "ab_archive_test")
    fixtures = _load(ROOT / "tests/python/test_pcc_compile_ab_tool.py", "ab_fixture_test")
    compilers = [tmp_path / "candidate", tmp_path / "baseline"]
    for index, compiler in enumerate(compilers):
        compiler.write_bytes(b"compiler-" + bytes([index]))
        compiler.chmod(0o555)
    runtime = tmp_path / "runtime.a"
    runtime.write_bytes(b"runtime")
    receipts, _ = fixtures._write_build_evidence(ab, tmp_path, compilers, runtime)
    # Fixture records canonical-source/canonical-stage-build. Adapt only our
    # lifecycle aliases, not receipt bytes or the strict loader under test.
    output = tmp_path / "campaign"
    output.mkdir()
    original_receipt = receipts[0].read_bytes()
    bundle = receipts[0].parent
    shutil.copy2(compilers[0], bundle / "pcc1")
    recorded_origin = tmp_path / "canonical-source"
    recorded_build = tmp_path / "canonical-stage-build"
    bundle.rename(recorded_build)
    before = json.loads((recorded_build / "build-receipt.json").read_text())
    monkeypatch.setattr(tool, "_canonical_paths", lambda output: (recorded_origin, recorded_build))
    row = tool._archive_arm(output, "candidate", ab, ab.sha256_path(runtime))
    archive = Path(row["archive"])
    archived = archive / "build"
    assert (archived / "build-receipt.json").read_bytes() == original_receipt
    loaded = ab._load_build_receipt(archived / "build-receipt.json", arm="candidate",
                    compiler_sha256=ab.sha256_path(compilers[0]), runtime_sha256=ab.sha256_path(runtime),
                    compiler_size_bytes=compilers[0].stat().st_size, expected_gc_backend=0)
    assert loaded["origin_source_root"] == before["origin_source_root"]
    assert loaded["source_root"] == before["cwd"]
    target = output / "measurement"
    target.mkdir()
    evidence = ab._snapshot_build_evidence(archived / "build-receipt.json", target, "candidate")
    reloaded = ab._load_build_receipt(Path(evidence["receipt"]), arm="candidate",
                    compiler_sha256=ab.sha256_path(compilers[0]), runtime_sha256=ab.sha256_path(runtime),
                    compiler_size_bytes=compilers[0].stat().st_size, expected_gc_backend=0)
    assert reloaded["receipt"] == loaded["receipt"]
    assert (archive / "origin/pcc/__main__.py").is_file()


def test_compile_args_preserve_six_pairs_control_and_worker_limits(tool, tmp_path):
    fake = types.SimpleNamespace(_parser=lambda: types.SimpleNamespace(parse_args=lambda args: args))
    frozen = {"runtime_archive": {"path": "/runtime.a"}, "source_diff": {"path": "/diff"},
              "allowed_changed_source": "pcc/ir/ir.py", "warmup": {"path": "/warmup.py"},
              "inputs": [{"path": "/input" + str(n), "expected_output": str(n) + "\n"} for n in range(6)]}
    arms = {arm: {"compiler": "/" + arm, "receipt": "/" + arm + ".json"}
            for arm in ("candidate", "baseline")}
    frozen["inputs"][0]["expected_output"] = "0\n\n"
    argv = tool._compile_args(frozen, tmp_path, arms, fake)
    assert argv.count("--input") == 6
    assert len([arg for arg in argv if arg.startswith("--expected-output=")]) == 6
    assert "--expected-output=0\n" in argv
    assert "--unchanged-control" in argv
    assert argv[argv.index("--frontend-jobs") + 1] == "1"
    assert argv[argv.index("--self-backend-jobs") + 1] == "2"
    assert "--pairs" not in argv


def test_build_invokes_existing_producer_once_with_remaining_budget(tool, tmp_path, monkeypatch):
    output = tmp_path / "campaign"
    output.mkdir()
    calls = []
    class BuildError(RuntimeError):
        pass
    budget = types.SimpleNamespace(max_tree_rss_bytes=3 * 1024 ** 3,
                                    remaining_timeout=lambda requested: min(requested, 17))
    fake = types.SimpleNamespace(
        CompileABError=BuildError,
        _validated_worker_budget=lambda **kwargs: budget,
        _run_process=lambda command, **kwargs: (calls.append((command, kwargs))
            or subprocess.CompletedProcess(command, 0, "producer output", "")),
    )
    monkeypatch.setattr(tool, "_copy_tree", lambda *args: None)
    monkeypatch.setattr(tool, "_remaining", lambda deadline, ceiling: 21)
    monkeypatch.setattr(tool, "_archive_arm", lambda *args: {"archive": "complete"})
    args = argparse.Namespace(campaign_deadline_monotonic=123, job_deadline_epoch=456, run_token="owner", campaign_request=None)
    result = tool._build_arm(args, output, "baseline", tmp_path / "source", {},
                            {"runtime_archive": {"path": "/runtime.a", "sha256": "a" * 64}}, fake)
    assert result == {"archive": "complete"}
    command, kwargs = calls[0]
    assert len(calls) == 1
    assert command[1].endswith("scripts/run_pcc_stage1_build.py")
    assert command[command.index("--timeout") + 1] == "17"
    assert command[command.index("--memory-budget-bytes") + 1] == str(3 * 1024 ** 3)
    assert command[command.index("--jobs") + 1] == "2"
    assert command[command.index("--self-backend-jobs") + 1] == "2"
    assert "--no-performance-lock" in command
    assert kwargs["timeout"] == 17
    assert (output / "baseline-producer.stdout").read_text() == "producer output"


def test_missing_qualification_stops_before_any_build(tool, tmp_path, monkeypatch):
    from contextlib import nullcontext
    output = tmp_path / "campaign"
    output.mkdir()
    spec = output / "campaign-spec.json"
    spec.write_text("{}")
    spec.chmod(0o444)
    tool._persist(output / "manifest.json", {"run_token": "owner", "status": "IN_PROGRESS",
                                            "input_sha256": tool._sha(spec), "input_kind": "spec"})
    monkeypatch.setattr(tool, "_validate_spec", lambda *args: ({}, {"baseline": {}, "candidate": {}}))
    def reject(*args):
        raise tool.CampaignError("original candidate gates missing")
    monkeypatch.setattr(tool, "_qualification", reject)
    monkeypatch.setattr(tool, "_build_arm", lambda *args: pytest.fail("build before qualification"))
    fake = types.SimpleNamespace(require_claim_platform=lambda: None,
                                 _validated_worker_budget=lambda **kwargs: types.SimpleNamespace(deadline_monotonic=123),
                                 _performance_lock=lambda: nullcontext(object()))
    args = argparse.Namespace(campaign_deadline_monotonic=123, job_deadline_epoch=456, run_token="owner", campaign_request=None)
    with pytest.raises(tool.CampaignError, match="candidate gates"):
        tool._guarded(args, output, fake)


def test_live_helper_closure_rejects_dirty_or_untracked_imports(tool, tmp_path, monkeypatch):
    spec, ab = _fixture(tool, tmp_path, monkeypatch)
    trees = tool._tree(Path(spec["candidate_source"]["path"]))
    tool._verify_live_source(trees)
    extra = tool.REPO_ROOT / "pcc/untracked.py"
    extra.write_text("injected = True\n")
    with pytest.raises(tool.CampaignError, match="live helper"):
        tool._verify_live_source(trees)
    extra.unlink()
    helper = tool.REPO_ROOT / "pcc/tools/runtime_archive_provenance.py"
    helper.write_text("changed = True\n")
    with pytest.raises(tool.CampaignError, match="live helper"):
        tool._verify_live_source(trees)


@pytest.mark.parametrize("prefix, name", [("pcc", "foreign"), ("scripts", "run_pcc_ci_ab")])
def test_live_helper_closure_rejects_cached_bytecode(tool, tmp_path, monkeypatch, prefix, name):
    spec, ab = _fixture(tool, tmp_path, monkeypatch)
    trees = tool._tree(Path(spec["candidate_source"]["path"]))
    cache = tool.REPO_ROOT / prefix / "__pycache__"
    cache.mkdir()
    (cache / (name + ".cpython-315.pyc")).write_bytes(b"unowned")
    with pytest.raises(tool.CampaignError, match="cached live helper"):
        tool._verify_live_source(trees)


def test_foreign_preloaded_helper_module_is_rejected(tool, tmp_path, monkeypatch):
    foreign = tmp_path / "foreign.py"
    foreign.write_text("outside = True\n")
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path / "qualified")
    monkeypatch.setattr(tool, "sys", types.SimpleNamespace(modules={
        "pcc.backend": types.SimpleNamespace(__file__=str(foreign))}))
    with pytest.raises(tool.CampaignError, match="foreign helper"):
        tool._verify_live_import_origins({})


def test_helper_namespace_path_escape_is_rejected(tool, tmp_path, monkeypatch):
    root = tmp_path / "qualified"
    root.mkdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    monkeypatch.setattr(tool, "REPO_ROOT", root)
    monkeypatch.setattr(tool, "sys", types.SimpleNamespace(modules={
        "scripts": types.SimpleNamespace(__path__=[str(foreign)])}))
    with pytest.raises(tool.CampaignError, match="search path escapes"):
        tool._verify_live_import_origins({})


@pytest.mark.parametrize("case", ["prefix", "runtime_prefix", "writes", "missing_flag"])
def test_bytecode_preconditions_fail_before_loading_project_tools(tool, tmp_path, monkeypatch, case):
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path / "repo")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_OS", "macOS")
    monkeypatch.delenv("PYTHONPYCACHEPREFIX", raising=False)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    if case == "prefix":
        monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "cache"))
    elif case == "missing_flag":
        monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    monkeypatch.setattr(tool, "sys", types.SimpleNamespace(
        pycache_prefix=str(tmp_path / "cache") if case == "runtime_prefix" else None,
        dont_write_bytecode=case != "writes"))
    loaded = []
    monkeypatch.setattr(tool, "_load", lambda name: loaded.append(name))
    output = tmp_path / "output"
    assert tool.run(argparse.Namespace(output_dir=str(output))) == 1
    assert loaded == []
    receipt = json.loads((output / "manifest.json").read_text())
    assert receipt["status"] == "FAILED"
    assert "bytecode" in receipt["error"] or "PYTHONDONTWRITEBYTECODE" in receipt["error"]
