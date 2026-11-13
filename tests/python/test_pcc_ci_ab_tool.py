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
    ({"status": "COMPLETE", "returncode": 0}, {"phase": "MEASURED"}, "FAILED"),
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
    assert result["claim_level"] == "unaccepted"
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


@pytest.fixture
def mechanism():
    return _load(ROOT / "scripts/pcc_ci_ab_mechanism.py", "ci_ab_mechanism_test")


def _mechanism_result_fixture(tool, mechanism, tmp_path):
    """Synthetic protocol evidence only; never execution acceptance."""
    root = tmp_path / "candidate-mechanism"
    inputs = root / "inputs"
    inputs.mkdir(parents=True)
    for name in mechanism.ARTIFACTS:
        shutil.copy2(ROOT / mechanism.DATA / name, inputs / name)
    source = tmp_path / "source"
    for relative in (mechanism.SOURCE, mechanism.FIXTURE):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, path)
    host_source = source / "pcc/frontends/python/pipeline.py"
    host_source.write_text("def compile_python(): pass\n")
    compiler, runtime, host = (tmp_path / name for name in ("pcc1", "runtime.a", "python"))
    for path in (compiler, runtime, host):
        path.write_bytes(b"synthetic evidence, not executable")
    source_files = tool._tree(source, readonly=False)
    payload = {"source_root": str(source), "source_files": source_files,
               "compiler": str(compiler), "runtime_archive": str(runtime), "receipt_sha256": "a" * 64}
    request = root / "request.json"
    tool._persist(request, payload)
    collected = [dict(row, nodeid="node-" + str(n) + "@" + mechanism.GROUP)
                 for n, row in enumerate(mechanism.expected_cases(inputs / "identity_cases.py"))]
    result = {"schema": mechanism.SCHEMA, "status": "PASSED", "pytest_exitcode": 0,
              "request_sha256": tool._sha(request), "assertions_enabled": True, "implementation": "cpython",
              "collected": collected, "reports": [{"nodeid": row["nodeid"], "when": when,
                  "outcome": "passed", "wasxfail": None} for row in collected for when in ("setup", "call", "teardown")],
              "executions": [], "compiles": [], "programs": [], "source_sha256": tool._identity(source_files),
              "compiler_receipt_sha256": payload["receipt_sha256"], "guard_contract_sha256": "b" * 64,
              "import_origins": {"pcc.frontends.python.pipeline_import_scan": {
                  "path": str(source / mechanism.SOURCE), "sha256": tool._sha(source / mechanism.SOURCE)}},
              "host_python_runtime": {"runtime_files": {"executable": {"path": str(host), "sha256": tool._sha(host)}}}}
    for owner in ("cpython", "pcc0", "pcc1"):
        program = root / (owner + ".py")
        program.write_bytes(mechanism.program_bytes(source / mechanism.SOURCE, inputs / "native_cases.py",
                                                     native=owner != "cpython"))
        result["programs"].append({"owner": owner, "path": program.name, "sha256": tool._sha(program)})
        if owner == "cpython":
            runs = [None]
            binary, command = host, [str(host), str(program)]
        else:
            runs = range(5)
            binary = root / (owner + "-binary")
            binary.write_bytes(b"synthetic native output, not executed")
            command = [str(binary)]
            native = {"command": [str(compiler), "--backend", "self", "--ir-scaffold", "on", str(program), "-o", str(binary)],
                      "returncode": 0, "environment": {"PCC_RUNTIME_ARCHIVE": str(runtime),
                      "PCC_HOST_PYTHON": "/usr/bin/false", "PCC_HOST_PCC": "/usr/bin/false"}}
            result["compiles"].append({"owner": owner, "binary": binary.name, "binary_sha256": tool._sha(binary),
                "linkage": {"checked": True, "links_libpython": False, "links_llvm": False},
                "fixture_sha256": tool._sha(source / mechanism.FIXTURE),
                "native_compile": native if owner == "pcc1" else None,
                "host_origin": {"path": str(host_source), "sha256": tool._sha(host_source), "name": "compile_python"}})
        for gc in runs:
            out = mechanism.EXPECTED + ("" if gc is None else "GC " + str(gc) + "\n")
            row = {"owner": owner, "gc": gc, "returncode": 0, "stdout": out, "stderr": "", "command": command,
                   "binary_sha256": tool._sha(binary), "environment": {} if gc is None else
                   {"PCC_GC_BACKEND": str(gc), "PATH": "/nonexistent"}}
            for key in ("stdout", "stderr"):
                path = root / (owner + "-" + str(gc) + "." + key)
                path.write_text(row[key])
                row[key + "_path"] = path.name
            result["executions"].append(row)
    result["lanes"] = {}
    for lane, names in mechanism.LANES.items():
        invocation = json.loads(json.dumps({key: value for key, value in result.items() if key != "lanes"}))
        invocation["lane"] = lane
        invocation["collected"] = [row for row in collected if row["name"] in names]
        nodes = {row["nodeid"] for row in invocation["collected"]}
        invocation["reports"] = [row for row in result["reports"] if row["nodeid"] in nodes]
        owners = set() if lane == "ordinary" else {"cpython"} if lane == "reference" else {"pcc0", "pcc1"}
        for key in ("executions", "compiles", "programs"):
            invocation[key] = [row for row in result[key] if row["owner"] in owners]
        workers = {}
        for index in range(6):
            worker = json.loads(json.dumps(invocation))
            name = "gw" + str(index)
            worker["worker_identity"] = {"worker": name, "pid": 100 + index, "workercount": 6, "lane": lane,
                                         "request_sha256": result["request_sha256"]}
            if index:
                worker.update(status="IDLE", reports=[], executions=[], compiles=[], programs=[])
            path = root / ("worker-" + lane + "-" + name + ".json")
            tool._persist(path, worker)
            workers[name] = {"path": path.name, "sha256": tool._sha(path), "worker": name}
        invocation["xdist"] = {"workers": 6, "dist": "loadgroup", "group": mechanism.GROUP,
                               "executing_worker": "gw0", "worker_results": workers}
        controller = {"reports": invocation["reports"], "pytest_exitcode": 0, "completed_workers": workers,
                      "lane": lane, "request_sha256": result["request_sha256"], "worker_identity": {"worker": None}}
        path = root / ("controller-" + lane + ".json")
        tool._persist(path, controller)
        invocation["controller"] = {"path": path.name, "sha256": tool._sha(path)}
        invocation["selection"] = [str(inputs / "identity_cases.py")] if lane == "ordinary" else [
            str(inputs / "native_cases.py") + "::" + (mechanism.REFERENCE if lane == "reference" else mechanism.NATIVE)]
        path = root / ("lane-" + lane + ".json")
        tool._persist(path, invocation)
        result["lanes"][lane] = {"path": path.name, "sha256": tool._sha(path)}
    return result, request, root


def test_mechanism_preserves_complete_identity_artifacts(mechanism):
    for name, digest in mechanism.ARTIFACTS.items():
        assert mechanism._sha(ROOT / mechanism.DATA / name) == digest
    cases = mechanism.expected_cases(ROOT / mechanism.DATA / "identity_cases.py")
    assert len(cases) == 19
    assert sum(row["name"] == mechanism.UNIT_NAMES[0] for row in cases) == 5
    assert sum(row["name"] == mechanism.UNIT_NAMES[1] for row in cases) == 11
    assert sum(row["name"] == mechanism.REFERENCE for row in cases) == 1
    assert {row["parameters"]["python_program_compiler"] for row in cases if row["name"] == mechanism.NATIVE} == {"pcc0", "pcc1"}


def test_mechanism_protocol_fixture_is_not_execution_acceptance(tool, mechanism, tmp_path):
    result, request, root = _mechanism_result_fixture(tool, mechanism, tmp_path)
    mechanism.validate_result(result, request=request, root=root)
    # A complete synthetic shape cannot produce a launch-bound campaign proof.
    with pytest.raises(tool.CampaignError, match="not produced"):
        tool._verify_mechanism({"status": "PASSED", "returncode": 0, "root": str(root)},
                               output=tmp_path, ab=None, launched=True)


@pytest.mark.parametrize("mutation", [
    "returncode_only", "missing_case", "missing_report", "skip", "xfail", "empty_xfail", "missing_gc",
    "wrong_gc_output", "missing_owner", "host_fallback", "missing_origins", "program_drift", "binary_drift",
    "source_drift", "foreign_origin", "missing_worker", "duplicate_worker_pid", "worker_collection",
    "worker_receipt", "worker_execution", "worker_assertions", "reference_command", "assertions_disabled",
    "missing_lane", "mixed_reference_selection", "controller_reports",
])
def test_mechanism_rejects_incomplete_fabricated_or_mutated_evidence(tool, mechanism, tmp_path, mutation):
    result, request, root = _mechanism_result_fixture(tool, mechanism, tmp_path)
    if mutation == "returncode_only":
        result = {"status": "PASSED", "returncode": 0}
    elif mutation == "missing_case":
        result["collected"].pop()
    elif mutation == "missing_report":
        result["reports"].pop()
    elif mutation == "skip":
        result["reports"][0]["outcome"] = "skipped"
    elif mutation in {"xfail", "empty_xfail"}:
        result["reports"][0]["wasxfail"] = "reason" if mutation == "xfail" else ""
    elif mutation == "missing_gc":
        result["executions"].pop()
    elif mutation == "wrong_gc_output":
        result["executions"][-1]["stdout"] = mechanism.EXPECTED + "GC 0\n"
    elif mutation == "missing_owner":
        result["compiles"].pop()
    elif mutation == "host_fallback":
        result["compiles"][1]["native_compile"]["environment"]["PCC_HOST_PYTHON"] = sys.executable
    elif mutation == "missing_origins":
        result["import_origins"] = {}
    elif mutation == "program_drift":
        (root / result["programs"][0]["path"]).write_text("print('fake')\n")
    elif mutation == "binary_drift":
        (root / result["compiles"][0]["binary"]).write_text("changed")
    elif mutation == "source_drift":
        payload = json.loads(request.read_text())
        (Path(payload["source_root"]) / mechanism.SOURCE).write_text("def wrong(): pass\n")
    elif mutation == "foreign_origin":
        result["import_origins"]["pcc.frontends.python.pipeline_import_scan"]["path"] = str(tmp_path / "foreign.py")
    elif mutation == "missing_lane":
        result["lanes"].pop("reference")
    elif (mutation.startswith("worker_") or mutation in {"duplicate_worker_pid", "missing_worker", "mixed_reference_selection", "controller_reports"}):
        lane_receipt = result["lanes"]["integration"]
        lane_path = root / lane_receipt["path"]
        invocation = json.loads(lane_path.read_text())
        receipt = invocation["xdist"]["worker_results"]["gw5"]
        path = root / receipt["path"]
        worker = json.loads(path.read_text())
        if mutation == "duplicate_worker_pid":
            worker["worker_identity"]["pid"] = 100
        elif mutation == "worker_collection":
            worker["collected"].pop()
        elif mutation == "worker_receipt":
            worker["compiler_receipt_sha256"] = "f" * 64
        elif mutation == "worker_execution":
            worker["executions"] = [{"owner": "pretend"}]
        elif mutation == "worker_assertions":
            worker["assertions_enabled"] = False
        elif mutation == "missing_worker":
            invocation["xdist"]["worker_results"].pop("gw5")
        elif mutation == "mixed_reference_selection":
            invocation["selection"] = [str(root / "inputs/native_cases.py")]
        else:
            invocation["reports"].pop()
        tool._persist(path, worker)
        receipt["sha256"] = tool._sha(path)
        if mutation != "controller_reports":
            controller_receipt = invocation["controller"]
            controller_path = root / controller_receipt["path"]
            controller = json.loads(controller_path.read_text())
            controller["completed_workers"] = invocation["xdist"]["worker_results"]
            tool._persist(controller_path, controller)
            controller_receipt["sha256"] = tool._sha(controller_path)
        tool._persist(lane_path, invocation)
        lane_receipt["sha256"] = tool._sha(lane_path)
    elif mutation == "reference_command":
        result["executions"][0]["command"] = ["/bin/true"]
    else:
        result["assertions_enabled"] = False
    failure = {
        "duplicate_worker_pid": "six distinct worker processes",
        "worker_collection": "worker identity, collection",
        "worker_receipt": "worker identity, collection",
        "worker_assertions": "worker identity, collection",
        "worker_execution": "unexpected work outside",
        "missing_worker": "six-worker lane execution",
        "mixed_reference_selection": "selection was not separate",
        "controller_reports": "actual controller and worker reports disagree",
    }.get(mutation)
    with pytest.raises(mechanism.MechanismError, match=failure):
        mechanism.validate_result(result, request=request, root=root)


def _completion_fixture(tool, tmp_path):
    """Synthetic bounded metadata only; real launch is mocked in these tests."""
    output = tmp_path / "campaign"
    output.mkdir()
    args = argparse.Namespace(run_token="original-parent-token", campaign_request="request",
                              job_deadline_epoch=500, campaign_deadline_monotonic=200)
    launch = tool._launch_contract(args, output, input_kind="request", input_sha256="a" * 64, target_sha="b" * 40)
    expected = {"launch": launch, "started_monotonic": 90}
    tool._PARENT_LAUNCHES[id(expected)] = (expected, tool._identity(expected))
    guard = {"schema": "pcc.process_tree_sample.v1", "status": "COMPLETE", "returncode": 0,
             "command": launch["command"], "cwd": launch["cwd"], "started_at_utc": "2026-11-13T00:00:00+00:00",
             "environment": {"PCC_WORKER_TREE_BUDGET_BYTES": str(3 * 1024 ** 3)},
             "timeout_s": 110, "interval_s": 0.1, "max_tree_rss_bytes": 3 * 1024 ** 3,
             "darwin_preflight_reserve_bytes": 512 * 1024 ** 2,
             "memory_budget_selection": {"selection_kind": "automatic"}, "resource_preflight": {"status": "ok"}}
    mechanism = {"status": "PASSED", "run_token": args.run_token, "candidate_sha": "b" * 40,
                 "root": str(output / "candidate-mechanism"), "request_sha256": "c" * 64,
                 "result_sha256": "d" * 64, "sha256": "e" * 64,
                 "source_sha256": "f" * 64, "compiler_receipt_sha256": "1" * 64}
    context = {"final_identity_status": "EXACT_PAIR_REAUTHENTICATED", "promotion": "NOT_AUTHORIZED"}
    manifest = {"phase": "MEASURED", "measurement_status": "ACCEPTED", "run_token": args.run_token,
                "input_kind": "request", "input_sha256": "a" * 64, "absolute_deadline_monotonic": 200,
                "campaign_context": context, "candidate_mechanism": mechanism,
                "candidate_mechanism_sha256": tool._identity(mechanism),
                "compile_manifest": str(output / "compile-ab/manifest.json"), "compile_manifest_sha256": "3" * 64}
    manifest["full_stage"] = _full_stage_metadata(tool, output)
    completion = {"schema": tool.COMPLETION_SCHEMA, "status": "VERIFIED_ACCEPTED", "launch": launch,
                  "guard_contract_sha256": tool._guard_contract_digest(guard),
                  "campaign_context_sha256": tool._identity(context), "verified_at_monotonic": 100,
                  "result_body_sha256": tool._identity({key: manifest.get(key) for key in tool._COMPLETION_RESULT_FIELDS}),
                  "compile_manifest_sha256": "3" * 64,
                  "full_stage_sha256": tool._identity(manifest["full_stage"]),
                  "mechanism": {"proof_sha256": tool._identity(mechanism), "request_sha256": "c" * 64, "result_sha256": "d" * 64,
                                "tree_sha256": "e" * 64, "source_sha256": "f" * 64, "compiler_receipt_sha256": "1" * 64}}
    path = output / "completion.json"
    tool._persist(path, completion)
    path.chmod(0o444)
    manifest["completion"] = {"path": str(path), "sha256": tool._sha(path), "size_bytes": path.stat().st_size}
    return output, manifest, guard, expected, completion


def test_terminal_requires_actual_mechanism_and_final_pair_reauthentication(tool, tmp_path, monkeypatch):
    output, child, guard, expected, _ = _completion_fixture(tool, tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("outer terminal must not import helpers or traverse source/runtime evidence")
    for name in ("_load", "_tree", "_verify_mechanism", "_verify_live_source"):
        monkeypatch.setattr(tool, name, forbidden)
    # A different outer venv prefix must not invalidate the base child's receipt.
    monkeypatch.setattr(tool, "sys", types.SimpleNamespace(prefix="outer-uv-venv"))
    result = tool._terminal(child, guard, output=output, expected=expected)
    assert result["status"] == "SUCCEEDED"
    # Parent status/telemetry updates must not invalidate any referenced digest.
    tool._verify_completion(result, guard, output=output, expected=expected)


@pytest.mark.parametrize("mutation", [
    "returncode_only", "missing_expected", "copied_expected", "mutated_expected", "missing_receipt", "other_run", "other_input", "other_kind",
    "other_target", "other_output", "command", "guard_contract", "guard_returncode", "guard_schema",
    "late_verification", "early_verification", "context", "mechanism", "proof_nested", "proof_hash", "compile_hash",
    "receipt_hash", "mutable", "symlink", "hardlink", "oversized", "partial_json", "copy_replay",
    "summary", "arms", "qualification", "source_identities",
])
def test_terminal_rejects_forged_replayed_or_mutated_compact_completion(tool, tmp_path, mutation):
    output, manifest, guard, expected, completion = _completion_fixture(tool, tmp_path)
    path = output / "completion.json"
    rewrite = False
    if mutation == "returncode_only":
        manifest = {"phase": "MEASURED", "measurement_status": "ACCEPTED"}
    elif mutation == "missing_expected":
        expected = None
    elif mutation == "copied_expected":
        expected = json.loads(json.dumps(expected))
    elif mutation == "mutated_expected":
        expected["started_monotonic"] = 1
    elif mutation == "missing_receipt":
        manifest.pop("completion")
    elif mutation in {"other_run", "other_input", "other_kind", "other_target", "other_output"}:
        key = {"other_run": "run_token", "other_input": "input_sha256", "other_kind": "input_kind",
               "other_target": "target_sha", "other_output": "output"}[mutation]
        # Keep the real registered parent expectation. Forge only a resealed
        # child receipt, without mutating the fixture's shared launch object.
        completion = json.loads(json.dumps(completion))
        completion["launch"][key] = "another-launch"
        rewrite = True
    elif mutation == "command":
        guard["command"] = ["/bin/true"]
    elif mutation == "guard_contract":
        guard["max_tree_rss_bytes"] += 1
    elif mutation == "guard_returncode":
        guard["returncode"] = 1
    elif mutation == "guard_schema":
        guard["schema"] = "pretend"
    elif mutation in {"late_verification", "early_verification"}:
        completion["verified_at_monotonic"] = 201 if mutation == "late_verification" else 89
        rewrite = True
    elif mutation == "context":
        manifest["campaign_context"]["promotion"] = "unauthorized-change"
    elif mutation == "mechanism":
        manifest["candidate_mechanism"]["source_sha256"] = "9" * 64
    elif mutation == "proof_nested":
        manifest["candidate_mechanism"]["budget"] = {"pretend": "different nested metadata"}
    elif mutation == "proof_hash":
        manifest["candidate_mechanism_sha256"] = "9" * 64
    elif mutation == "compile_hash":
        completion["compile_manifest_sha256"] = "9" * 64
        rewrite = True
    elif mutation in {"summary", "arms", "qualification", "source_identities"}:
        manifest["compile_summary" if mutation == "summary" else mutation] = {"changed": True}
    elif mutation == "receipt_hash":
        manifest["completion"]["sha256"] = "9" * 64
    elif mutation == "mutable":
        path.chmod(0o644)
    elif mutation == "symlink":
        moved = output / "other.json"
        path.rename(moved)
        path.symlink_to(moved)
    elif mutation == "hardlink":
        import os
        os.link(path, output / "alias.json")
    elif mutation == "oversized":
        path.chmod(0o644)
        path.write_bytes(b" " * (tool.COMPLETION_MAX_BYTES + 1))
        path.chmod(0o444)
        manifest["completion"].update(sha256=tool._sha(path), size_bytes=path.stat().st_size)
    elif mutation == "partial_json":
        path.chmod(0o644)
        path.write_text("{")
        path.chmod(0o444)
        manifest["completion"].update(sha256=tool._sha(path), size_bytes=path.stat().st_size)
    else:
        completion = json.loads(json.dumps(completion))
        completion["launch"]["run_token"] = "earlier-run-token"
        rewrite = True
    if rewrite:
        path.chmod(0o644)
        tool._persist(path, completion)
        path.chmod(0o444)
        manifest["completion"].update(sha256=tool._sha(path), size_bytes=path.stat().st_size)
    assert tool._terminal(manifest, guard, output=output, expected=expected)["status"] == "FAILED"


def test_terminal_guard_digest_preserves_original_admission_contract(tool, tmp_path):
    output, manifest, guard, expected, _ = _completion_fixture(tool, tmp_path)
    ab = _load(ROOT / "scripts/run_pcc_compile_ab.py", "terminal_guard_contract_test")
    original = ab._guard_contract_fields(guard)
    original["status"] = "RUNNING"
    assert tool._guard_contract_digest(guard) == tool._identity(original)
    guard.update(peak_tree_rss_bytes=123, sample_count=999)
    assert tool._terminal(manifest, guard, output=output, expected=expected)["status"] == "SUCCEEDED"


def test_guarded_completion_runs_full_checks_before_sealing_metadata(tool, tmp_path, monkeypatch):
    output = tmp_path / "out"
    (output / "compile-ab").mkdir(parents=True)
    result = {"status": "ACCEPTED", "summary": {"verdict": "ACCEPT"}}
    tool._persist(output / "compile-ab/manifest.json", result)
    calls = []
    for name, label in (("_verify_frozen_qualification", "qualification"), ("_verify_live_source", "source"),
                        ("_verify_live_import_origins", "origins"), ("_verify_mechanism", "mechanism"),
                        ("_validate_spec", "spec")):
        monkeypatch.setattr(tool, name, lambda *args, _label=label, **kwargs: calls.append(_label))
    context = {"final_identity_status": "EXACT_PAIR_REAUTHENTICATED"}
    monkeypatch.setattr(tool, "_load", lambda name: types.SimpleNamespace(
        finish_campaign=lambda proof: (calls.append("provider-final") or context)))
    guard = types.SimpleNamespace(receipt={"guard_result": {"contract_sha256": "a" * 64}},
                                  verify=lambda: calls.append("guard"))
    ab = types.SimpleNamespace(_validated_worker_budget=lambda **kwargs: guard)
    monkeypatch.setattr(tool.time, "monotonic", lambda: 100)
    args = argparse.Namespace(run_token="token", campaign_request="request", job_deadline_epoch=500,
                              campaign_deadline_monotonic=200)
    mechanism = {key: "c" * 64 for key in ("request_sha256", "result_sha256", "sha256", "source_sha256", "compiler_receipt_sha256")}
    monkeypatch.setattr(tool, "_verify_full_stage", lambda *args, **kwargs: calls.append("full-stage"))
    context_out, receipt, digest = tool._finish_guarded(args, output,
        {"input_kind": "request", "input_sha256": "b" * 64, "full_stage": _full_stage_metadata(tool, output)}, {"target_sha": "d" * 40},
        {"candidate": {}}, object(), mechanism, result, ab, prepared_root=None, performance_lock_token=object())
    assert calls == ["qualification", "source", "origins", "mechanism", "spec",
                     "qualification", "origins", "full-stage", "provider-final", "guard", "guard"]
    assert context_out == context
    assert digest == tool._sha(output / "compile-ab/manifest.json")
    assert receipt["size_bytes"] <= tool.COMPLETION_MAX_BYTES
    assert (output / "completion.json").stat().st_mode & 0o222 == 0
    assert (output / "compile-ab/manifest.json").stat().st_mode & 0o222 == 0


def test_terminal_json_reads_are_bounded_and_preserve_oversized_child_file(tool, tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(" " * 17)
    with pytest.raises(tool.CampaignError, match="bounded"):
        tool._json(path, max_bytes=16)
    assert path.read_text() == " " * 17


@pytest.mark.parametrize("worker, options, valid", [
    (None, {"numprocesses": 6, "dist": "loadgroup"}, True),
    ({"workercount": 6, "workerid": "gw2"}, {"numprocesses": None, "dist": "no", "loadgroup": True}, True),
    (None, {"numprocesses": 0, "dist": "no"}, False),
    ({"workercount": 5, "workerid": "gw2"}, {"loadgroup": True}, False),
    ({"workercount": 6, "workerid": "gw2"}, {"loadgroup": False}, False),
    ({"workercount": 6, "workerid": "gw3"}, {"loadgroup": True}, False),
])
def test_mechanism_checks_real_controller_and_worker_options(mechanism, tmp_path, monkeypatch, worker, options, valid):
    path = tmp_path / "request.json"
    path.write_text("{}")
    state = types.SimpleNamespace(worker="gw2" if worker else None, lane="ordinary", result={}, request=path)
    monkeypatch.setattr(mechanism, "_STATE", state)
    config = types.SimpleNamespace(workerinput=worker, getoption=lambda name: options.get(name),
                                  pluginmanager=types.SimpleNamespace(register=lambda *args: None))
    if valid:
        mechanism.configure(config)
        assert state.result["worker_identity"]["workercount"] == 6
    else:
        with pytest.raises(mechanism.MechanismError):
            mechanism.configure(config)


def test_mechanism_never_persists_inherited_secret_environment(mechanism, tmp_path, monkeypatch):
    sentinel = "SENTINEL-SECRET-MUST-NOT-BE-PERSISTED"
    monkeypatch.setenv("GH_TOKEN", sentinel)
    monkeypatch.setenv("PCC_UNEXPECTED_SECRET", sentinel)
    monkeypatch.setenv("GITHUB_FAKE_PRIVATE", sentinel)
    state = object.__new__(mechanism.Recorder)
    state.root, state.record_path = tmp_path, tmp_path / "result.json"
    state.active_owner, state.lane = None, "reference"
    state.result = {"executions": []}
    state.verify = lambda: None
    state.remaining = lambda seconds: seconds
    state.program = lambda *args: None
    state.ab = types.SimpleNamespace(_run_process=lambda *args, **kwargs:
        types.SimpleNamespace(returncode=0, stdout=mechanism.EXPECTED, stderr=""))
    state.execute([sys.executable, str(tmp_path / "reference.py")], cwd=tmp_path,
                  capture_output=True, text=True, timeout=20)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert sentinel not in path.read_text()
    row = json.loads((tmp_path / "result.json").read_text())["executions"][0]
    assert "GH_TOKEN" not in row["environment"]
    assert not set(row["environment"]) - set(mechanism.PUBLIC_ENV_KEYS)


def test_native_mechanism_uses_existing_process_owner_and_phase_deadline(mechanism, tmp_path):
    calls = []
    state = object.__new__(mechanism.Recorder)
    state.active_owner, state.compiler, state.source = "pcc1", tmp_path / "pcc1", tmp_path
    state.remaining = lambda seconds: min(seconds, 17)
    state.ab = types.SimpleNamespace(_run_process=lambda command, **kwargs:
        (calls.append((command, kwargs)) or types.SimpleNamespace(returncode=0, stdout="ok", stderr="")))
    command = [str(state.compiler), "--backend", "self", "--ir-scaffold", "on", "source.py", "-o", "out"]
    env = {"PCC_HOST_PYTHON": "/usr/bin/false", "PCC_HOST_PCC": "/usr/bin/false"}
    state.compile_native(command, env=env, timeout=400, stderr_path=str(tmp_path / "compile.stderr"))
    assert calls[0][1]["timeout"] == 17
    assert calls[0][1]["env"] == env
    with pytest.raises(mechanism.MechanismError):
        state.compile_native(["/bin/true"], env=env, timeout=400, stderr_path="unused")


@pytest.mark.parametrize("mutation", ["copied", "changed", "id_alias"])
def test_mechanism_launch_registry_rejects_copies_mutation_and_id_alias(tool, tmp_path, mutation):
    original = {"status": "PASSED", "returncode": 0, "root": str(tmp_path / "candidate-mechanism")}
    tool._MECHANISM_PROOFS[id(original)] = (original, tool._identity(original))
    if mutation == "copied":
        supplied = dict(original)
    elif mutation == "changed":
        supplied = original
        supplied["extra"] = True
    else:
        supplied = dict(original)
        tool._MECHANISM_PROOFS[id(supplied)] = (original, tool._identity(supplied))
    with pytest.raises(tool.CampaignError, match="not produced"):
        tool._verify_mechanism(supplied, output=tmp_path, ab=None, launched=True)


@pytest.mark.parametrize("outcome", ["failed", "missing", "returncode_only"])
def test_failed_or_unproven_mechanism_stops_before_timing(tool, tmp_path, monkeypatch, outcome):
    from contextlib import nullcontext
    output = tmp_path / "campaign"
    output.mkdir()
    captured = output / "campaign-spec.json"
    captured.write_text("{}")
    captured.chmod(0o444)
    tool._persist(output / "manifest.json", {"run_token": "owned", "status": "IN_PROGRESS",
        "input_kind": "spec", "input_sha256": tool._sha(captured)})
    trees = {"baseline": {}, "candidate": {}}
    monkeypatch.setattr(tool, "_validate_spec", lambda *args, **kwargs: ({"baseline": tmp_path, "candidate": tmp_path}, trees))
    monkeypatch.setattr(tool, "_qualification", lambda *args: {})
    monkeypatch.setattr(tool, "_verify_frozen_qualification", lambda *args: None)
    monkeypatch.setattr(tool, "_verify_live_source", lambda *args: None)
    monkeypatch.setattr(tool, "_verify_live_import_origins", lambda *args: None)
    monkeypatch.setattr(tool, "_freeze_inputs", lambda *args: {})
    monkeypatch.setattr(tool, "_build_arm", lambda *args: {})
    monkeypatch.setattr(tool, "_tree", lambda *args: {})
    def mechanism(*args):
        if outcome == "failed":
            raise tool.CampaignError("actual mechanism assertions failed")
        return None if outcome == "missing" else {"returncode": 0}
    monkeypatch.setattr(tool, "_run_mechanism", mechanism)
    ab = types.SimpleNamespace(require_claim_platform=lambda: None,
        _validated_worker_budget=lambda **kwargs: types.SimpleNamespace(deadline_monotonic=100),
        _performance_lock=lambda: nullcontext(object()),
        run_guarded=lambda *args, **kwargs: pytest.fail("timing must not run without mechanism proof"))
    args = argparse.Namespace(campaign_deadline_monotonic=100, job_deadline_epoch=200,
                              run_token="owned", campaign_request=None)
    with pytest.raises(tool.CampaignError):
        tool._guarded(args, output, ab)



def _full_stage_metadata(tool, output):
    return {"schema": "pcc.ci-full-stage-evidence.v1", "scope": tool.FULL_STAGE_SCOPE,
            "pair_count": 5, "control_pair_count": 5, "candidate_stage2_le_stage1": True,
            "control_verdict": "OBSERVATION_ONLY", "control_review_status": "REQUIRED",
            "source_promotion_authorized": False, "automatic_source_acceptance": False,
            **{key: {"path": str(output / name), "sha256": "7" * 64} for key, name in (
                ("manifest", "full-stage-ab/manifest.json"),
                ("control", "full-stage-control/manifest.json"),
                ("summary", "full-stage-summary.json"))}}


def test_full_stage_args_require_real_full_pairs_and_same_cap(tool, tmp_path):
    fake = types.SimpleNamespace(_parser=lambda: types.SimpleNamespace(parse_args=lambda args: args))
    roots = {"baseline": Path("/baseline"), "candidate": Path("/candidate")}
    frozen = {"runtime_archive": {"path": "/runtime.a"}}
    guard = types.SimpleNamespace(max_tree_rss_bytes=3 * 1024 ** 3)
    for control in (False, True):
        argv = tool._stage_args(roots, frozen, tmp_path, fake, guard, control=control)
        assert argv[argv.index("--pairs") + 1] == "5"
        assert argv[argv.index("--candidate-source") + 1] == ("/baseline" if control else "/candidate")
        assert argv[argv.index("--max-tree-rss-bytes") + 1] == str(3 * 1024 ** 3)
        assert argv[argv.index("--stages") + 1] == "stage1-stage2"
        assert argv[argv.index("--frontend-jobs") + 1] == "2"
        assert argv[argv.index("--self-backend-jobs") + 1] == "2"
        assert "--measure-compile-window" in argv
        assert "--no-performance-lock" not in argv


@pytest.mark.parametrize("mutation", ["missing", "control", "summary", "pairs", "controls", "relation", "scope", "stable", "promotion", "review"])
def test_terminal_requires_full_stage_relation_and_unreviewed_control(tool, tmp_path, mutation):
    output, manifest, guard, expected, completion = _completion_fixture(tool, tmp_path)
    evidence = manifest["full_stage"]
    if mutation == "missing":
        manifest.pop("full_stage")
    elif mutation in {"control", "summary"}:
        evidence.pop(mutation)
    else:
        key, value = {"pairs": ("pair_count", 4), "controls": ("control_pair_count", 0),
                      "relation": ("candidate_stage2_le_stage1", False), "scope": ("scope", "compile-slice"),
                      "stable": ("control_verdict", "PASS"), "promotion": ("source_promotion_authorized", True),
                      "review": ("control_review_status", "WAIVED")}[mutation]
        evidence[key] = value
    # Even coherently rehashed passive metadata cannot relax the contract.
    completion["full_stage_sha256"] = tool._identity(manifest.get("full_stage"))
    completion["result_body_sha256"] = tool._identity({key: manifest.get(key) for key in tool._COMPLETION_RESULT_FIELDS})
    path = output / "completion.json"
    path.chmod(0o644)
    tool._persist(path, completion)
    path.chmod(0o444)
    manifest["completion"].update(sha256=tool._sha(path), size_bytes=path.stat().st_size)
    assert tool._terminal(manifest, guard, output=output, expected=expected)["status"] == "FAILED"


@pytest.mark.parametrize("copied", [False, True])
def test_full_stage_finalizer_rejects_copied_or_mutated_proof(tool, tmp_path, copied):
    evidence = _full_stage_metadata(tool, tmp_path)
    tool._FULL_STAGE_PROOFS[id(evidence)] = (evidence, tool._identity(evidence), {}, {}, {}, {})
    if copied:
        evidence = json.loads(json.dumps(evidence))
    else:
        evidence["manifest"]["sha256"] = "8" * 64
    with pytest.raises(tool.CampaignError, match="same-process"):
        tool._verify_full_stage(evidence, args=None, output=tmp_path, ab=None, performance_lock_token=object())


def test_full_stage_success_keeps_control_review_and_source_acceptance_separate(tool, tmp_path):
    output, manifest, guard, expected, completion = _completion_fixture(tool, tmp_path)
    result = tool._terminal(manifest, guard, output=output, expected=expected)
    assert result["status"] == "SUCCEEDED"
    assert result["claim_level"] == "matched-full-compiler-windows"
    assert result["full_stage"]["control_review_status"] == "REQUIRED"
    assert result["full_stage"]["control_verdict"] == "OBSERVATION_ONLY"
    assert result["full_stage"]["automatic_source_acceptance"] is False
    assert result["full_stage"]["source_promotion_authorized"] is False
    assert "new Stage1 -> Stage2 -> Stage3 fixed point" in result["does_not_prove"]


def test_full_stage_invokes_producer_twice_with_same_guard_lock_and_deadline(tool, tmp_path, monkeypatch):
    output = tmp_path / "campaign"
    output.mkdir()
    token = object()
    calls = []
    guard = types.SimpleNamespace(max_tree_rss_bytes=3 * 1024 ** 3,
        receipt={"guard_result": {"contract_sha256": "1" * 64}}, verify=lambda: calls.append("guard"))
    ab = types.SimpleNamespace(_validated_worker_budget=lambda **kwargs: guard,
        _canonical_sha256=tool._identity, host_python_runtime_evidence=lambda path: {})
    args = argparse.Namespace(run_token="owner", campaign_request="request", job_deadline_epoch=500,
                              campaign_deadline_monotonic=200)
    roots = {"baseline": Path("/baseline"), "candidate": Path("/candidate")}
    frozen = {"baseline_source": {"sha256": "2" * 64},
              "runtime_archive": {"path": "/runtime.a", "sha256": "3" * 64}}
    monkeypatch.setattr(tool, "_sha", lambda path: "4" * 64)
    def run(stage_args, **kwargs):
        assert kwargs["ab"] is ab and kwargs["performance_lock_token"] is token
        assert kwargs["guard_result_path"] == output / "guard.json"
        assert kwargs["campaign_deadline_monotonic"] == 200
        assert kwargs["expected_guard_command"] == tool._child_command(args, output)
        assert stage_args.pairs == 5 and stage_args.max_tree_rss_bytes == guard.max_tree_rss_bytes
        calls.append(stage_args.candidate_source)
        path = Path(stage_args.output_dir)
        path.mkdir()
        result = {"status": "COMPLETE", "arm": stage_args.candidate_source}
        tool._persist(path / "manifest.json", result)
        return result
    def parse(argv):
        def value(flag): return argv[argv.index(flag) + 1]
        return argparse.Namespace(pairs=int(value("--pairs")), max_tree_rss_bytes=int(value("--max-tree-rss-bytes")),
            output_dir=value("--output-dir"), candidate_source=value("--candidate-source"))
    stage = types.SimpleNamespace(_parser=lambda: types.SimpleNamespace(parse_args=parse), run_guarded=run)
    summary = {"candidate_stage2_le_stage1": True, "unchanged_control": {"verdict": "OBSERVATION_ONLY"}}
    def evaluate(stage_arg, acceptance_arg, result, control, ab_arg, token_arg, identity):
        assert stage_arg is stage and result["arm"] == "/candidate" and control["arm"] == "/baseline"
        assert ab_arg is ab and token_arg is token
        calls.append("evaluate")
        return summary
    acceptance = types.SimpleNamespace()
    monkeypatch.setattr(tool, "_evaluate_full_stage", evaluate)
    monkeypatch.setattr(tool, "_load", lambda name: stage if name == "run_pcc_stage_ab" else acceptance)
    evidence = tool._run_full_stage(args, output, roots, frozen, ab, token)
    assert calls == ["guard", "/candidate", "guard", "/baseline", "evaluate"]
    assert tool._FULL_STAGE_PROOFS[id(evidence)][0] is evidence
    tool._verify_full_stage(evidence, args=args, output=output, ab=ab, performance_lock_token=token)
    assert calls[-2:] == ["evaluate", "guard"]


@pytest.mark.parametrize("reason", ["deadline exhausted", "source changed", "runtime changed", "producer changed", "sample changed"])
def test_final_stage_revalidation_failure_prevents_completion(tool, tmp_path, monkeypatch, reason):
    evidence = _full_stage_metadata(tool, tmp_path)
    tool._FULL_STAGE_PROOFS[id(evidence)] = (evidence, tool._identity(evidence), {}, {}, {}, {})
    def reject(*args, **kwargs):
        raise RuntimeError(reason)
    monkeypatch.setattr(tool, "_load", lambda name: types.SimpleNamespace())
    monkeypatch.setattr(tool, "_evaluate_full_stage", reject)
    with pytest.raises(RuntimeError, match=reason):
        tool._verify_full_stage(evidence, args=None, output=tmp_path, ab=None, performance_lock_token=object())


def test_full_stage_artifact_seal_survives_real_producer_finalizer(tool, tmp_path, monkeypatch):
    """Real capability finalizer and filesystem checks, synthetic launch only."""
    stage = _load(ROOT / "scripts/run_pcc_stage_ab.py", "ci_real_stage_finalizer")
    ab = _load(ROOT / "scripts/run_pcc_compile_ab.py", "ci_real_stage_receipts")
    token = object()
    output = tmp_path / "out"
    output.mkdir()
    path = output / "manifest.json"
    sample = output / "samples.tsv"
    sample.write_text("synthetic sample\n")
    result = {"status": "COMPLETE", "pairs": []}
    tool._persist(path, result)
    path.chmod(0o444)  # producer seals before retaining its receipt
    context = types.SimpleNamespace(ab=ab, token=token, budget=object(),
        verify=lambda: None, sources=[], builds=[], result_files=[ab._path_receipt(path), ab._path_receipt(sample)])
    stage._GUARDED_RESULTS[id(result)] = (result, context, stage._canonical_sha256(result))
    artifact = tool._stage_artifact(path, result)
    stage.verify_guarded_result(result, ab=ab, performance_lock_token=token)
    assert artifact["sha256"] == tool._sha(path)
    sample.write_text("changed synthetic sample\n")
    with pytest.raises(RuntimeError):
        stage.verify_guarded_result(result, ab=ab, performance_lock_token=token)


def test_full_stage_calculation_never_bypasses_live_stage_authority(tool, tmp_path, monkeypatch):
    calls = []
    result, control = {"pairs": []}, {"pairs": []}
    ab = types.SimpleNamespace(_canonical_sha256=tool._identity, host_python_runtime_evidence=lambda path: {})
    monkeypatch.setattr(tool, "_sha", lambda path: "a" * 64)
    identity = {"host_runtime_sha256": tool._identity({}),
                "tools_sha256": tool._identity({name: "a" * 64 for name in tool.TOOLS})}
    token = object()
    def verify(value, **kwargs):
        assert kwargs == {"ab": ab, "performance_lock_token": token}
        calls.append("ab" if value is result else "aa")
    stage = types.SimpleNamespace(verify_guarded_result=verify)
    acceptance = types.SimpleNamespace(
        calculation_bindings=lambda pairs, controls, **kwargs: (calls.append("bindings") or {}),
        evaluate_stage_campaign=lambda pairs, controls, **kwargs: (calls.append("calculate") or {"value": True}))
    assert tool._evaluate_full_stage(stage, acceptance, result, control, ab, token, identity) == {"value": True}
    assert calls == ["ab", "aa", "bindings", "calculate", "ab", "aa"]


def test_real_stage_pair_order_survives_ci_artifact_and_finalization(tool, tmp_path, monkeypatch):
    """Exercise real artifact/registry seam, with launch and arithmetic synthetic."""
    stage = _load(ROOT / "scripts/run_pcc_stage_ab.py", "ci_pair_order_stage")
    ab = _load(ROOT / "scripts/run_pcc_compile_ab.py", "ci_pair_order_ab")
    token = object()
    output = tmp_path / "campaign"
    output.mkdir()
    records = []
    samples = []
    for label in ("full-stage-ab", "full-stage-control"):
        directory = output / label
        directory.mkdir()
        path = directory / "manifest.json"
        sample = directory / "samples.tsv"
        sample.write_text("synthetic launch sample\n")
        # Match the producer's JSON-native pair representation; retain the
        # actual alternating order function rather than a hand-written order.
        pairs = [{"index": index, "order": list(stage.pair_order(index)),
                  **{arm: {"stage_status": {"stage1": "COMPLETE", "stage2": "COMPLETE"}}
                     for arm in stage.pair_order(index)}} for index in range(1, 6)]
        result = {"schema": "pcc.bootstrap-stage-ab.v1", "status": "COMPLETE", "pairs": pairs}
        stage._persist(path, result)
        path.chmod(0o444)
        assert json.loads(path.read_text()) == result
        context = types.SimpleNamespace(ab=ab, token=token, budget=object(),
            verify=lambda: None, sources=[], builds=[],
            result_files=[ab._path_receipt(path), ab._path_receipt(sample)])
        stage._GUARDED_RESULTS[id(result)] = (result, context, stage._canonical_sha256(result))
        stage.verify_guarded_result(result, ab=ab, performance_lock_token=token)
        records.append(result)
        samples.append(sample)
    summary = {"candidate_stage2_le_stage1": True, "unchanged_control": {"verdict": "OBSERVATION_ONLY"}}
    tool._persist(output / "full-stage-summary.json", summary)
    evidence = _full_stage_metadata(tool, output)
    for key, relative, value in (("manifest", "full-stage-ab/manifest.json", records[0]),
                                 ("control", "full-stage-control/manifest.json", records[1]),
                                 ("summary", "full-stage-summary.json", summary)):
        evidence[key] = tool._stage_artifact(output / relative, value)
    tool._FULL_STAGE_PROOFS[id(evidence)] = (evidence, tool._identity(evidence), *records, summary, {})
    # Only numerical calculation is stubbed. Production artifact reads, strong
    # registry checks, immutable receipt checks and final CI comparisons run.
    def calculate(stage_arg, acceptance, result, control, ab_arg, token_arg, identity):
        assert stage_arg is stage and ab_arg is ab and token_arg is token
        for manifest in (result, control):
            stage.verify_guarded_result(manifest, ab=ab, performance_lock_token=token)
        return summary
    monkeypatch.setattr(tool, "_evaluate_full_stage", calculate)
    monkeypatch.setattr(tool, "_load", lambda name: stage if name == "run_pcc_stage_ab" else object())
    monkeypatch.setattr(ab, "_validated_worker_budget", lambda **kwargs: types.SimpleNamespace(verify=lambda: None))
    args = argparse.Namespace(run_token="owner", campaign_request="request", job_deadline_epoch=500,
                              campaign_deadline_monotonic=200)
    tool._verify_full_stage(evidence, args=args, output=output, ab=ab, performance_lock_token=token)
    samples[1].write_text("changed unchanged-control sample\n")
    with pytest.raises(RuntimeError):
        tool._verify_full_stage(evidence, args=args, output=output, ab=ab, performance_lock_token=token)
