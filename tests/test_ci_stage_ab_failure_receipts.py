"""Stage A/B checkpoints survive failed or interrupted downstream stages.

The actual harness and JSON writer run here; source admission, locks, compilers
and samplers are replaced before invocation. These tests launch no children.
"""

from __future__ import annotations

from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


STAGE_AB = Path(__file__).resolve().parents[1] / "scripts" / "run_pcc_stage_ab.py"


def _fixture(tmp_path, monkeypatch, *, pairs=1, stages="stage1-stage2"):
    spec = importlib.util.spec_from_file_location("stage_ab_failure_test", STAGE_AB)
    assert spec is not None and spec.loader is not None
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    for name in ("baseline", "candidate"):
        (tmp_path / name).mkdir()
    runtime = tmp_path / "runtime.a"
    runtime.write_bytes(b"fixture identity, never executed")
    output = tmp_path / "out"
    args = tool._parser().parse_args([
        "--baseline-source", str(tmp_path / "baseline"),
        "--candidate-source", str(tmp_path / "candidate"),
        "--runtime-archive", str(runtime),
        "--output-dir", str(output),
        "--pairs", str(pairs), "--stages", stages,
        "--stage1-timeout", "17", "--stage2-timeout", "23",
        "--smoke-timeout", "3", "--max-tree-rss-bytes", "104857600",
        "--frontend-jobs", "1", "--self-backend-jobs", "2", "--gc-backend", "4",
    ])
    ab = SimpleNamespace(_performance_lock=nullcontext, sha256_path=lambda path: "runtime")
    producer = SimpleNamespace(
        STAGE1_METRIC_SCOPES={"wall_s": "end_to_end_elapsed"},
        _validate_source_root=lambda *a: None,
        _require_immutable_source=lambda *a: None,
        source_manifest=lambda root, ab: {"source": root.name},
    )
    modules = {
        tool.COMPILE_AB_TOOL: ab,
        tool.STAGE1_TOOL: producer,
        tool.PROCESS_SAMPLER: SimpleNamespace(),
    }
    monkeypatch.setattr(tool, "_load_module", lambda path, name: modules[path])
    calls = []

    def record(stage, kwargs):
        calls.append((stage, kwargs["arm"], kwargs["arm_root"].parent.name))
        process = {"status": "COMPLETE", "returncode": 0, "peak_tree_rss_bytes": 4096}
        envelope = tool._resource_envelope(args=args, environment={}, process=process)
        if stage == "stage1":
            result = {"metrics": {"wall_s": 2.0, "cpu_s": 3.0}}
        else:
            result = {
                "wall_ms": 2500, "compile_wall_ms": 2000,
                "compile_user_ms": 2000, "compile_sys_ms": 1000,
                "metric_scopes": {
                    key: value for key, value in tool.STAGE2_METRIC_SCOPES.items()
                    if key != "peak_tree_rss_bytes"
                },
            }
        return {
            "result": result, "process": process, "resource_envelope": envelope,
            "normalized_environment_sha256": "same-environment",
            "receipt_path": str(kwargs["arm_root"] / stage / "build-receipt.json"),
        }

    monkeypatch.setattr(tool, "_run_stage1", lambda **kw: record("stage1", kw))
    monkeypatch.setattr(tool, "_run_stage2", lambda **kw: record("stage2", kw))
    return tool, args, output / "manifest.json", calls, producer


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("kind", ("stage", "os", "runtime", "key", "interrupt", "exit"))
def test_stage2_failure_retains_stage1_before_downstream_call(tmp_path, monkeypatch, kind):
    tool, args, manifest_path, calls, _ = _fixture(tmp_path, monkeypatch)
    error_type = {
        "stage": tool.StageABError, "os": OSError, "runtime": RuntimeError,
        "key": KeyError, "interrupt": KeyboardInterrupt, "exit": SystemExit,
    }[kind]
    error = error_type("downstream failed")

    def fail_stage2(**kwargs):
        checkpoint = _read(manifest_path)
        baseline = checkpoint["active_pair"]["baseline"]
        assert baseline["stage1"] == kwargs["stage1_record"]
        assert baseline["stage_status"] == {"stage1": "COMPLETE", "stage2": "RUNNING"}
        raise error

    monkeypatch.setattr(tool, "_run_stage2", fail_stage2)
    with pytest.raises(error_type) as caught:
        tool.run(args)
    assert caught.value is error
    receipt = _read(manifest_path)
    expected = "INTERRUPTED" if kind in ("interrupt", "exit") else "FAILED"
    assert receipt["status"] == expected
    assert receipt["failure"] == {"exception_type": error_type.__name__, "message": str(error)}
    assert receipt["finished_at_utc"]
    assert receipt["requested_stages"] == "stage1-stage2"
    assert receipt["resource_limits"] == {
        "stage1_timeout_s": 17, "stage2_timeout_s": 23, "smoke_timeout_s": 3,
        "max_tree_rss_bytes": 104857600, "frontend_jobs": 1,
        "self_backend_jobs": 2, "gc_backend": 4,
    }
    active = receipt["active_pair"]
    assert active["baseline"]["stage_status"] == {"stage1": "COMPLETE", "stage2": expected}
    assert active["baseline"]["stage1"]["process"]["peak_tree_rss_bytes"] == 4096
    assert "stage2" not in active["baseline"]
    assert active["candidate"] == {"stage_status": {"stage1": "NOT_RUN", "stage2": "NOT_RUN"}}
    assert receipt["pairs"] == []
    assert "summary" not in receipt and "completed_at_utc" not in receipt
    assert calls == [("stage1", "baseline", "pair-1")]


def test_stage1_failure_does_not_mark_stage2_as_attempted(tmp_path, monkeypatch):
    tool, args, path, calls, _ = _fixture(tmp_path, monkeypatch)
    error = tool.StageABError("Stage1 failed")

    def fail(**kwargs):
        raise error

    monkeypatch.setattr(tool, "_run_stage1", fail)
    with pytest.raises(tool.StageABError) as caught:
        tool.run(args)
    assert caught.value is error
    receipt = _read(path)
    assert receipt["status"] == "FAILED"
    assert receipt["active_pair"]["baseline"] == {
        "stage_status": {"stage1": "FAILED", "stage2": "NOT_RUN"},
    }
    assert receipt["active_pair"]["candidate"]["stage_status"]["stage1"] == "NOT_RUN"
    assert calls == []


def test_later_failure_preserves_completed_pairs_and_alternating_order(tmp_path, monkeypatch):
    tool, args, path, calls, _ = _fixture(tmp_path, monkeypatch, pairs=2)
    run_stage2 = tool._run_stage2
    first_pair = None

    def stage2(**kwargs):
        nonlocal first_pair
        if kwargs["pair_index"] == 2:
            first_pair = _read(path)["pairs"][0]
            assert kwargs["arm"] == "candidate"
            raise tool.StageABError("second pair failed")
        return run_stage2(**kwargs)

    monkeypatch.setattr(tool, "_run_stage2", stage2)
    with pytest.raises(tool.StageABError, match="second pair failed"):
        tool.run(args)
    receipt = _read(path)
    assert receipt["pairs"] == [first_pair]
    assert receipt["active_pair"]["index"] == 2
    assert receipt["active_pair"]["order"] == ["candidate", "baseline"]
    assert receipt["active_pair"]["candidate"]["stage_status"]["stage2"] == "FAILED"
    assert receipt["active_pair"]["baseline"]["stage_status"]["stage1"] == "NOT_RUN"
    assert calls[-1] == ("stage1", "candidate", "pair-2")
    assert "summary" not in receipt


def test_envelope_failure_retains_both_completed_stage_observations(tmp_path, monkeypatch):
    tool, args, path, _, _ = _fixture(tmp_path, monkeypatch)
    run_stage2 = tool._run_stage2

    def stage2(**kwargs):
        record = run_stage2(**kwargs)
        record["resource_envelope"]["max_tree_rss_bytes"] += 1
        return record

    monkeypatch.setattr(tool, "_run_stage2", stage2)
    with pytest.raises(tool.StageABError, match="resource envelopes differ"):
        tool.run(args)
    receipt = _read(path)
    assert receipt["status"] == "FAILED"
    baseline = receipt["active_pair"]["baseline"]
    assert baseline["stage_status"] == {"stage1": "COMPLETE", "stage2": "COMPLETE"}
    assert "stage1" in baseline and "stage2" in baseline
    assert receipt["pairs"] == [] and "summary" not in receipt


def test_end_identity_failure_is_terminal_without_losing_pairs(tmp_path, monkeypatch):
    tool, args, path, _, producer = _fixture(tmp_path, monkeypatch)
    identities = []

    def source_manifest(root, ab):
        identities.append(root.name)
        return {"source": root.name, "drift": len(identities) > 2}

    producer.source_manifest = source_manifest
    with pytest.raises(tool.StageABError, match="source snapshot changed"):
        tool.run(args)
    receipt = _read(path)
    assert receipt["status"] == "FAILED"
    assert len(receipt["pairs"]) == 1
    assert "active_pair" not in receipt and "summary" not in receipt


@pytest.mark.parametrize("broken_stderr", (False, True))
def test_failure_receipt_write_error_does_not_replace_stage_error(
    tmp_path, monkeypatch, capsys, broken_stderr,
):
    tool, args, path, _, _ = _fixture(tmp_path, monkeypatch)
    persist = tool._persist
    original = tool.StageABError("original Stage2 failure")

    def fail_stage2(**kwargs):
        raise original

    def fail_receipt(target, value):
        if value.get("status") == "FAILED":
            raise OSError("receipt storage unavailable")
        persist(target, value)

    monkeypatch.setattr(tool, "_run_stage2", fail_stage2)
    monkeypatch.setattr(tool, "_persist", fail_receipt)

    class BrokenStream:
        def write(self, text):
            raise OSError("stderr storage unavailable")

        def flush(self):
            pass

    with monkeypatch.context() as stderr_patch:
        if broken_stderr:
            stderr_patch.setattr(tool.sys, "stderr", BrokenStream())
        with pytest.raises(tool.StageABError) as caught:
            tool.run(args)
    assert caught.value is original
    if not broken_stderr:
        assert "could not persist failure receipt: receipt storage unavailable" in capsys.readouterr().err
    # Failed storage cannot promise a terminal receipt; its earlier checkpoint
    # still retains Stage1 and does not fabricate an on-disk FAILED state.
    receipt = _read(path)
    assert receipt["status"] == "RUNNING"
    assert "stage1" in receipt["active_pair"]["baseline"]


@pytest.mark.parametrize("phase", ("summary", "final_write"))
def test_finalization_failure_does_not_publish_a_success_summary(tmp_path, monkeypatch, phase):
    tool, args, path, _, _ = _fixture(tmp_path, monkeypatch)
    error = OSError("finalization failed")
    persist = tool._persist

    def fail_summary(pairs):
        raise error

    def fail_final_write(target, value):
        if value.get("status") == "COMPLETE":
            raise error
        persist(target, value)

    if phase == "summary":
        monkeypatch.setattr(tool, "summarize_pairs", fail_summary)
    else:
        monkeypatch.setattr(tool, "_persist", fail_final_write)
    with pytest.raises(OSError) as caught:
        tool.run(args)
    assert caught.value is error
    receipt = _read(path)
    assert receipt["status"] == "FAILED" and len(receipt["pairs"]) == 1
    assert "summary" not in receipt and "completed_at_utc" not in receipt


def test_existing_output_is_never_rewritten_as_a_failure_receipt(tmp_path, monkeypatch):
    tool, args, path, calls, _ = _fixture(tmp_path, monkeypatch)
    path.parent.mkdir()
    path.write_text("existing run evidence\n", encoding="utf-8")
    with pytest.raises(tool.StageABError, match="refusing existing output directory"):
        tool.run(args)
    assert path.read_text(encoding="utf-8") == "existing run evidence\n"
    assert calls == []


@pytest.mark.parametrize("stages", ("stage1", "stage1-stage2"))
def test_success_keeps_normal_summary_and_marks_unrequested_stage(tmp_path, monkeypatch, stages):
    tool, args, path, calls, _ = _fixture(tmp_path, monkeypatch, stages=stages)
    result = tool.run(args)
    receipt = _read(path)
    assert receipt["status"] == "COMPLETE"
    assert receipt["summary"] == result["summary"]
    assert receipt["summary"]["verdict"] == "MEASURED_NO_AUTOMATIC_ACCEPTANCE"
    assert receipt["completed_at_utc"]
    assert "failure" not in receipt and "active_pair" not in receipt
    for arm in ("baseline", "candidate"):
        assert receipt["pairs"][0][arm]["stage_status"] == {
            "stage1": "COMPLETE",
            "stage2": "COMPLETE" if stages == "stage1-stage2" else "NOT_REQUESTED",
        }
    assert len(calls) == (4 if stages == "stage1-stage2" else 2)
