"""The nested observer must not issue a fresh campaign admission or deadline."""
import pytest

from scripts import run_process_tree_sample as sampler


def environment(deadline=200.0):
    retries = sampler._SAFETY_PROCESS_TABLE_TIMEOUTS_S
    return {
        "PCC_WORKER_TREE_BUDGET_BYTES": str(4 * 1024**3),
        "PCC_WORKER_TREE_STATE_PATH": "/original/guard.worker-rss.tsv",
        "PCC_WORKER_TREE_RECOVERY": "pcc.worker-tree-recovery.v1\t"
        + ",".join(map(str, retries)) + "\t" + str(sum(retries)) + "\t" + str(deadline),
    }


def test_observer_deadline_is_original_or_shorter(monkeypatch):
    monkeypatch.setattr(sampler.time, "monotonic", lambda: 100.0)
    inherited = environment()
    assert sampler._inherited_observer_deadline(inherited, 4 * 1024**3, 900) == 200
    assert sampler._inherited_observer_deadline(inherited, 4 * 1024**3, 150) == 150
    assert inherited == environment()


@pytest.mark.parametrize("deadline", [100.0, 99.0, float("nan"), float("inf")])
def test_observer_rejects_exhausted_or_invalid_deadline(monkeypatch, deadline):
    monkeypatch.setattr(sampler.time, "monotonic", lambda: 100.0)
    with pytest.raises(sampler.ProcessTreeSampleError):
        sampler._inherited_observer_deadline(environment(deadline), 4 * 1024**3, 900)


@pytest.mark.parametrize("key,value", [
    ("PCC_WORKER_TREE_BUDGET_BYTES", str(8 * 1024**3)),
    ("PCC_WORKER_TREE_STATE_PATH", ""),
    ("PCC_WORKER_TREE_RECOVERY", "pcc.worker-tree-recovery.v1\t1,2\t3\t200"),
    ("PCC_WORKER_TREE_RECOVERY", "broken"),
])
def test_observer_rejects_changed_contract(monkeypatch, key, value):
    monkeypatch.setattr(sampler.time, "monotonic", lambda: 100.0)
    inherited = environment()
    inherited[key] = value
    with pytest.raises(sampler.ProcessTreeSampleError):
        sampler._inherited_observer_deadline(inherited, 4 * 1024**3, 900)


@pytest.mark.parametrize("option", ["--auto-tree-rss-ceiling-bytes", "--darwin-preflight-reserve-bytes"])
def test_preserve_mode_rejects_re_admission_before_probe(tmp_path, monkeypatch, option):
    monkeypatch.setattr(sampler, "_host_memory_observation",
                        lambda: pytest.fail("must not re-admit memory"))
    monkeypatch.setattr(sampler, "_darwin_resource_preflight",
                        lambda **kwargs: pytest.fail("must not run another preflight"))
    args = sampler._parser().parse_args([
        "--result", str(tmp_path / "result.json"), "--timeout", "60",
        "--samples", str(tmp_path / "samples.tsv"),
        "--stdout", str(tmp_path / "stdout"), "--stderr", str(tmp_path / "stderr"),
        "--preserve-worker-budget", option, "1", "--", "not-launched"])
    with pytest.raises(sampler.ProcessTreeSampleError, match="cannot re-admit"):
        sampler._run(args)
    assert not (tmp_path / "result.json").exists()


def test_expired_observer_persists_failure_before_launch(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(sampler.time, "monotonic", lambda: 201.0)
    for key, value in environment().items():
        monkeypatch.setenv(key, value)
    args = sampler._parser().parse_args([
        "--result", str(tmp_path / "result.json"), "--timeout", "60",
        "--samples", str(tmp_path / "samples.tsv"),
        "--stdout", str(tmp_path / "stdout"), "--stderr", str(tmp_path / "stderr"),
        "--preserve-worker-budget", "--max-tree-rss-bytes", str(4 * 1024**3),
        "--no-performance-lock", "--", "not-launched"])
    with pytest.raises(sampler.ProcessTreeSampleError, match="exhausted"):
        sampler._run(args)
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["status"] == "SAMPLER_ERROR"
    assert result["returncode"] is None
    assert result["sample_count"] == 0


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), 0.0])
def test_observer_rejects_invalid_requested_deadline(monkeypatch, deadline):
    monkeypatch.setattr(sampler.time, "monotonic", lambda: 100.0)
    with pytest.raises(sampler.ProcessTreeSampleError):
        sampler._inherited_observer_deadline(environment(), 4 * 1024**3, deadline)
