"""CI-only fixtures for the real stage owner and nested observation interface."""
import argparse
import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_pcc_compile_ab as ab
from scripts import run_pcc_stage_ab as stage
from scripts import run_process_tree_sample as sampler


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(ab.time, "monotonic", lambda: clock[0])
    cap = ab.CI_TREE_CEILING_BYTES
    observation = dict(platform="darwin", reclaimable_bytes=16 * 1024**3,
                       disk_free_bytes=32 * 1024**3, swap_total_bytes=0,
                       swap_used_bytes=0, swap_free_bytes=0)
    selection = sampler.select_tree_memory_budget(
        0, default_ceiling=cap, minimum_bytes=ab.CI_TREE_FLOOR_BYTES,
        reserve_bytes=ab.CI_TREE_RESERVE_BYTES, observation=observation)
    guard_path = tmp_path / "guard.json"
    retries = sampler._SAFETY_PROCESS_TABLE_TIMEOUTS_S
    environment = dict(zip(ab.WORKER_BUDGET_KEYS, (
        str(cap), str(guard_path) + ".worker-rss.tsv",
        "pcc.worker-tree-recovery.v1\t" + ",".join(map(str, retries))
        + "\t" + str(sum(retries)) + "\t200.0")))
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    guard = dict(schema="pcc.process_tree_sample.v1", status="RUNNING",
                 started_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                 command=["controlled-ci-child"], cwd=str(tmp_path),
                 timeout_s=100.0, interval_s=0.25, max_tree_rss_bytes=cap,
                 memory_budget_selection=selection,
                 resource_preflight=selection["resource_preflight"],
                 darwin_preflight_reserve_bytes=ab.CI_TREE_RESERVE_BYTES,
                 environment=environment)
    guard_path.write_text(json.dumps(guard))
    token = object()
    monkeypatch.setattr(ab, "_HELD_PERFORMANCE_LOCK", token)
    monkeypatch.setattr(ab, "_ACTIVE_WORKER_BUDGET", None)
    monkeypatch.setattr(sampler, "_host_memory_observation",
                        lambda: pytest.fail("must not re-admit memory"))
    monkeypatch.setattr(ab, "_performance_lock",
                        lambda: pytest.fail("must not acquire another lock"))
    args = stage._parser().parse_args([
        "--baseline-source", str(tmp_path / "baseline"),
        "--candidate-source", str(tmp_path / "candidate"),
        "--runtime-archive", str(tmp_path / "runtime.a"),
        "--output-dir", str(tmp_path / "stage"),
        "--max-tree-rss-bytes", str(cap), "--measure-compile-window"])
    kwargs = dict(ab=ab, performance_lock_token=token,
                  guard_result_path=guard_path,
                  expected_guard_command=guard["command"], expected_guard_cwd=tmp_path,
                  campaign_deadline_monotonic=200.0)
    return SimpleNamespace(args=args, kwargs=kwargs, clock=clock, guard=guard,
                           path=guard_path, environment=environment)


@pytest.mark.parametrize("field,value", [("max_tree_rss_bytes", 8 * 1024**3),
                                          ("measure_compile_window", False),
                                          ("frontend_jobs", 1)])
def test_rejects_changed_cap_or_policy(campaign, field, value):
    setattr(campaign.args, field, value)
    with pytest.raises(stage.StageABError):
        stage.run_guarded(campaign.args, **campaign.kwargs)


def test_rejects_other_module_lock(campaign):
    campaign.kwargs["performance_lock_token"] = object()
    with pytest.raises(stage.StageABError, match="live module and lock"):
        stage.run_guarded(campaign.args, **campaign.kwargs)


def test_preparation_failure_is_durable(campaign, monkeypatch):
    def exhausted(*unused):
        campaign.clock[0] = 201.0
        ab._ACTIVE_WORKER_BUDGET.verify()
    monkeypatch.setattr(stage, "_load_module", exhausted)
    with pytest.raises(ab.CompileABError, match="deadline"):
        stage.run_guarded(campaign.args, **campaign.kwargs)
    result = json.loads((Path(campaign.args.output_dir) / "manifest.json").read_text())
    assert result["status"] == "FAILED"
    assert "summary" not in result
    assert ab._ACTIVE_WORKER_BUDGET is None


@pytest.mark.parametrize("mutation", ["deadline", "receipt", "mailbox", "lock"])
def test_real_child_boundary_rechecks_original_contract(campaign, monkeypatch, mutation):
    def run(args, *, ab):
        if mutation == "deadline":
            campaign.clock[0] = 201.0
        elif mutation == "receipt":
            campaign.guard["max_tree_rss_bytes"] -= 1
            campaign.path.write_text(json.dumps(campaign.guard))
        elif mutation == "mailbox":
            monkeypatch.setenv("PCC_WORKER_TREE_STATE_PATH", "/wrong")
        else:
            monkeypatch.setattr(ab, "_HELD_PERFORMANCE_LOCK", object())
        return stage._run_stage1(
            arm="baseline", source_root=Path(args.baseline_source),
            runtime_archive=Path(args.runtime_archive), arm_root=Path(args.output_dir),
            args=args, ab=ab, stage1=None, sampler=None)
    monkeypatch.setattr(stage, "_run", run)
    with pytest.raises((stage.StageABError, ab.CompileABError)):
        stage.run_guarded(campaign.args, **campaign.kwargs)
    assert ab._ACTIVE_WORKER_BUDGET is None


def test_child_failure_keeps_original_deadline_and_mailbox(campaign, monkeypatch):
    def child(parsed):
        assert parsed.preserve_worker_budget is True
        assert parsed.performance_lock is False
        assert parsed.timeout == 10.0
        assert parsed.max_tree_rss_bytes == ab.CI_TREE_CEILING_BYTES
        assert ab._ACTIVE_WORKER_BUDGET.environment == campaign.environment
        return {"status": "TIMEOUT", "returncode": 124}
    fake = SimpleNamespace(_parser=sampler._parser, run=child)
    def run(args, *, ab):
        campaign.clock[0] = 190.0
        return stage._run_stage1(
            arm="baseline", source_root=Path(args.baseline_source),
            runtime_archive=Path(args.runtime_archive), arm_root=Path(args.output_dir),
            args=args, ab=ab, stage1=None, sampler=fake)
    monkeypatch.setattr(stage, "_run", run)
    with pytest.raises(stage.StageABError, match="Stage1 did not complete"):
        stage.run_guarded(campaign.args, **campaign.kwargs)


def test_observer_never_resets_recovery_deadline(campaign):
    assert sampler._inherited_observer_deadline(
        campaign.environment, ab.CI_TREE_CEILING_BYTES, 999.0) == 200.0
    campaign.clock[0] = 201.0
    with pytest.raises(sampler.ProcessTreeSampleError, match="exhausted"):
        sampler._inherited_observer_deadline(
            campaign.environment, ab.CI_TREE_CEILING_BYTES, 999.0)


def test_finalizer_rejects_caller_json(campaign):
    with pytest.raises(stage.StageABError, match="source-owned"):
        stage.verify_guarded_result({}, ab=ab,
                                    performance_lock_token=campaign.kwargs["performance_lock_token"])


@pytest.mark.parametrize("mutation", [None, "artifact", "result", "copy", "deadline"])
def test_live_finalizer_preserves_original_completion_identity(campaign, monkeypatch, mutation):
    artifact = campaign.path.parent / "compiler"
    artifact.write_text("controlled child output")
    def controlled_completion(args, *, ab):
        context = args._inherited_stage_context
        context.result_files = [ab._path_receipt(artifact)]
        context.sources = []
        context.builds = []
        return {"status": "COMPLETE", "pairs": []}
    monkeypatch.setattr(stage, "_run", controlled_completion)
    result = stage.run_guarded(campaign.args, **campaign.kwargs)
    if mutation == "artifact":
        artifact.write_text("changed after production")
    elif mutation == "result":
        result["pairs"].append({})
    elif mutation == "copy":
        result = dict(result)
    elif mutation == "deadline":
        campaign.clock[0] = 201.0
    if mutation is None:
        stage.verify_guarded_result(result, ab=ab,
                                    performance_lock_token=campaign.kwargs["performance_lock_token"])
    else:
        with pytest.raises((stage.StageABError, ab.CompileABError)):
            stage.verify_guarded_result(result, ab=ab,
                                        performance_lock_token=campaign.kwargs["performance_lock_token"])
    assert ab._ACTIVE_WORKER_BUDGET is None


def test_run_process_smoke_uses_remaining_campaign_deadline(campaign, monkeypatch):
    class Child:
        returncode = 0
        pid = 12345
        def communicate(self, timeout):
            assert timeout == 10.0
            return "ok", ""
    monkeypatch.setattr(ab.subprocess, "Popen", lambda *a, **kw: Child())
    monkeypatch.setattr(ab, "_process_group_exists", lambda child: False)
    def controlled_completion(args, *, ab):
        campaign.clock[0] = 190.0
        outcome = ab._run_process(["controlled-smoke"], timeout=60,
                                  env=campaign.environment, cwd=campaign.path.parent)
        assert outcome.returncode == 0
        context = args._inherited_stage_context
        context.result_files, context.sources, context.builds = [], [], []
        return {"status": "COMPLETE", "pairs": []}
    monkeypatch.setattr(stage, "_run", controlled_completion)
    stage.run_guarded(campaign.args, **campaign.kwargs)


@pytest.mark.parametrize("changed", ["result", "samples", "ordinary", "ordinary_samples"])
def test_seal_rejects_mutated_completed_observations(tmp_path, changed):
    result = tmp_path / "compile.json"
    samples = tmp_path / "samples.tsv"
    ordinary = tmp_path / "stage-process.result.json"
    ordinary_samples = tmp_path / "stage-process.samples.tsv"
    stage_result = tmp_path / "stage-result.json"
    compiler = tmp_path / "pcc2"
    for path in (result, samples, compiler, ordinary_samples):
        path.write_text("original")
    process = {"status": "COMPLETE", "artifacts": {"samples": str(ordinary_samples)},
               "target_window": {"samples_sha256": ab.sha256_path(ordinary_samples)}}
    ordinary.write_text(json.dumps(process))
    stage_result.write_text(json.dumps({"returncode": 0}))
    record = {
        "compile_measurement": {"result": str(result), "samples": str(samples),
            "result_sha256": ab.sha256_path(result), "samples_sha256": ab.sha256_path(samples)},
        "compiler": str(compiler), "compiler_sha256": ab.sha256_path(compiler),
        "result_path": str(stage_result), "result": {"returncode": 0},
        "process": process, "process_result_path": str(ordinary),
    }
    {"result": result, "samples": samples, "ordinary": ordinary,
     "ordinary_samples": ordinary_samples}[changed].write_text("changed")
    context = SimpleNamespace(ab=ab, verify=lambda: None, result_files=[])
    with pytest.raises(stage.StageABError):
        stage._seal_stage_record(context, record)
    assert context.result_files == []


@pytest.mark.parametrize("mutation", ["writable_file", "writable_directory", "extra_file"])
def test_retained_snapshot_revalidates_exact_read_only_closure(tmp_path, mutation):
    root = tmp_path / "snapshot"
    for name in ["pcc/__main__.py", "utils/fake_libc_include/fixture.h", *ab.BUILD_SOURCE_SUPPORT]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n")
    manifest = {"files": {p.relative_to(root).as_posix(): ab.sha256_path(p)
                          for p in ab.build_source_files(root)}}
    ab._seal_source_snapshot(root, "fixture")
    context = SimpleNamespace(ab=ab, verify=lambda: None,
                              sources=[(root, manifest, "snapshot")])
    stage._verify_stage_sources(context)
    if mutation == "writable_file":
        (root / "pcc/__main__.py").chmod(0o644)
    elif mutation == "writable_directory":
        (root / "pcc").chmod(0o755)
    else:
        root.chmod(0o755)
        (root / "unlisted.txt").write_text("extra")
        (root / "unlisted.txt").chmod(0o444)
        root.chmod(0o555)
    try:
        with pytest.raises(ab.CompileABError):
            stage._verify_stage_sources(context)
    finally:
        for path in [root, *root.rglob("*")]:
            path.chmod(0o755 if path.is_dir() else 0o644)


def test_full_original_root_uses_original_immutable_contract(tmp_path):
    calls = []
    expected = {"source": "frozen build closure"}
    context = SimpleNamespace(
        ab=ab, verify=lambda: None, sources=[(tmp_path, expected, "original")],
        require_immutable_source=lambda path, module: calls.append((path, module)),
        source_manifest=lambda path, module: expected,
    )
    (tmp_path / "unrelated-tracked-file.txt").write_text("legitimate full-root input")
    stage._verify_stage_sources(context)
    assert calls == [(tmp_path, ab)]


def test_producer_result_matches_persisted_json_shape(tmp_path, monkeypatch):
    """Exercise real orchestration/persistence/summary with controlled builds."""
    from contextlib import nullcontext
    from scripts import run_pcc_stage1_build as stage1

    roots = [tmp_path / name for name in ("baseline", "candidate")]
    for root in roots:
        root.mkdir()
    archive = tmp_path / "runtime.a"
    archive.write_bytes(b"controlled runtime")
    args = stage._parser().parse_args([
        "--baseline-source", str(roots[0]), "--candidate-source", str(roots[1]),
        "--runtime-archive", str(archive), "--output-dir", str(tmp_path / "run"),
        "--pairs", "2",
    ])
    source = {"bootstrap_source_sha256": "a" * 64, "files": {"pcc/__main__.py": "b" * 64}}
    fake_stage1 = SimpleNamespace(
        STAGE1_METRIC_SCOPES=stage1.STAGE1_METRIC_SCOPES,
        STAGE1_COMPARISON_CONTRACT=stage1.STAGE1_COMPARISON_CONTRACT,
        _validate_source_root=lambda *a: None, _require_immutable_source=lambda *a: None,
        source_manifest=lambda *a: source,
    )
    monkeypatch.setattr(stage, "_load_module",
                        lambda path, name: fake_stage1 if path == stage.STAGE1_TOOL else object())
    monkeypatch.setattr(ab, "_performance_lock", nullcontext)
    def first(**kwargs):
        return {"result": {"metrics": {"wall_s": 1.0, "cpu_s": 1.0}},
                "process": {"peak_tree_rss_bytes": 1024}, "resource_envelope": {},
                "normalized_environment_sha256": "c" * 64, "compile_measurement": None}
    def second(**kwargs):
        return {"result": {"wall_ms": 1000, "compile_wall_ms": 900,
                           "compile_user_ms": 400, "compile_sys_ms": 100,
                           "metric_scopes": {key: value for key, value in stage.STAGE2_METRIC_SCOPES.items()
                                             if key != "peak_tree_rss_bytes"}},
                "process": {"peak_tree_rss_bytes": 1024}, "resource_envelope": {},
                "compile_measurement": None}
    monkeypatch.setattr(stage, "_run_stage1", first)
    monkeypatch.setattr(stage, "_run_stage2", second)
    result = stage._run(args, ab=ab)
    parsed = json.loads((Path(args.output_dir) / "manifest.json").read_text())
    assert result == parsed
    assert result["pairs"][0]["order"] == ["baseline", "candidate"]
    assert result["pairs"][1]["order"] == ["candidate", "baseline"]
    assert stage.pair_order(1) == ("baseline", "candidate")


@pytest.mark.parametrize("storage_failure", ["read", "write"])
@pytest.mark.parametrize("broken_stderr", [False, True])
def test_preparation_receipt_storage_failure_preserves_original_error(
    campaign, monkeypatch, storage_failure, broken_stderr,
):
    original = stage.StageABError("original preparation failure")
    persist = stage._persist
    def fail_preparation(*args, **kwargs):
        raise original
    def fail_read(*args):
        raise OSError("receipt read unavailable")
    def fail_write(path, value):
        if value.get("status") == "FAILED":
            raise OSError("receipt write unavailable")
        persist(path, value)
    class BrokenStream:
        def write(self, text):
            raise OSError("stderr unavailable")
        def flush(self):
            pass
    monkeypatch.setattr(stage, "_run_prepared", fail_preparation)
    if storage_failure == "read":
        monkeypatch.setattr(stage, "_read_json", fail_read)
    else:
        monkeypatch.setattr(stage, "_persist", fail_write)
    if broken_stderr:
        monkeypatch.setattr(stage.sys, "stderr", BrokenStream())
    with pytest.raises(stage.StageABError) as caught:
        stage._run(campaign.args, ab=ab)
    assert caught.value is original
    checkpoint = json.loads((Path(campaign.args.output_dir) / "manifest.json").read_text())
    assert checkpoint["status"] == "PREPARING"
    assert "summary" not in checkpoint
