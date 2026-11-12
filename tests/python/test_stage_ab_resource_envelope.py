"""Stage A/B binds one external resource envelope to Stage1 and Stage2.

The earlier operational pair mixed caps (host Stage1 resolved against ~48 GiB,
capped Stage2 at 8 GiB), so a Stage2/Stage1 ratio was not same-resource.  The
harness now records a ``resource_envelope`` on every stage record and refuses
to accept an arm whose two stages differ on any envelope-parity key
(cap / CPU / jobs / gc / cache policy).  Peak RSS and admitted worker count are
observations INSIDE the envelope, recorded but not required equal.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

STAGE_AB = Path(__file__).absolute().parents[2] / "scripts" / "run_pcc_stage_ab.py"


def _load_stage_ab():
    spec = importlib.util.spec_from_file_location("pcc_stage_ab_test_module", STAGE_AB)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    scripts = str(STAGE_AB.parent)
    sys.path.insert(0, scripts)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(scripts)
    return module


def _args(**overrides):
    base = dict(
        max_tree_rss_bytes=8 * 1024 * 1024 * 1024,
        frontend_jobs=2,
        self_backend_jobs=2,
        gc_backend=0,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


_ENV = {
    "PCC_PY_FRONTEND_IR_CACHE": "0",
    "PCC_SELF_BACKEND_OBJECT_CACHE": "0",
    "PYTHONPYCACHEPREFIX": "/tmp/private-pycache",
}


def _proc(**overrides):
    base = dict(
        peak_tree_rss_bytes=4_000_000_000,
        peak_process_count=9,
        status="COMPLETE",
        returncode=0,
    )
    base.update(overrides)
    return base


def test_resource_envelope_records_the_cap_and_observations():
    stage_ab = _load_stage_ab()
    envelope = stage_ab._resource_envelope(
        args=_args(), environment=_ENV, process=_proc(peak_process_count=6)
    )
    assert envelope["max_tree_rss_bytes"] == 8 * 1024 * 1024 * 1024
    assert envelope["cpu_count"] >= 1
    assert envelope["cache_policy"]["PCC_PY_FRONTEND_IR_CACHE"] == "0"
    assert envelope["cache_policy"]["private_pycache"] == "True"
    # observations, not parity keys
    assert envelope["observed_peak_tree_rss_bytes"] == 4_000_000_000
    assert envelope["observed_peak_process_count"] == 6


def test_same_envelope_passes_even_with_different_observed_rss():
    stage_ab = _load_stage_ab()
    args = _args()
    stage1 = stage_ab._resource_envelope(
        args=args, environment=_ENV, process=_proc(peak_tree_rss_bytes=3_000_000_000, peak_process_count=2)
    )
    stage2 = stage_ab._resource_envelope(
        args=args, environment=_ENV, process=_proc(peak_tree_rss_bytes=7_900_000_000, peak_process_count=10)
    )
    # A live-RSS-driven admission difference inside the same cap must NOT fail.
    assert stage_ab.envelope_parity_key(stage1) == stage_ab.envelope_parity_key(stage2)
    stage_ab.assert_stage_envelope_parity(
        {"stage1": {"resource_envelope": stage1}, "stage2": {"resource_envelope": stage2}},
        arm="baseline",
    )


@pytest.mark.parametrize(
    ("override", "drift"),
    [
        ({"max_tree_rss_bytes": 48 * 1024 * 1024 * 1024}, "max_tree_rss_bytes"),
        ({"frontend_jobs": 1}, "frontend_jobs"),
        ({"gc_backend": 3}, "gc_backend"),
    ],
)
def test_mixed_cap_or_jobs_or_gc_is_refused(override, drift):
    stage_ab = _load_stage_ab()
    stage1 = stage_ab._resource_envelope(
        args=_args(), environment=_ENV, process=_proc()
    )
    stage2 = stage_ab._resource_envelope(
        args=_args(**override), environment=_ENV, process=_proc()
    )
    with pytest.raises(stage_ab.StageABError, match=drift):
        stage_ab.assert_stage_envelope_parity(
            {"stage1": {"resource_envelope": stage1}, "stage2": {"resource_envelope": stage2}},
            arm="candidate",
        )


def test_different_cache_policy_is_refused():
    stage_ab = _load_stage_ab()
    stage1 = stage_ab._resource_envelope(args=_args(), environment=_ENV, process=_proc())
    warm = dict(_ENV, PCC_SELF_BACKEND_OBJECT_CACHE="1")
    stage2 = stage_ab._resource_envelope(args=_args(), environment=warm, process=_proc())
    with pytest.raises(stage_ab.StageABError, match="cache_policy"):
        stage_ab.assert_stage_envelope_parity(
            {"stage1": {"resource_envelope": stage1}, "stage2": {"resource_envelope": stage2}},
            arm="baseline",
        )


def test_missing_envelope_fails_closed():
    stage_ab = _load_stage_ab()
    stage1 = stage_ab._resource_envelope(args=_args(), environment=_ENV, process=_proc())
    with pytest.raises(stage_ab.StageABError, match="missing its resource envelope"):
        stage_ab.assert_stage_envelope_parity(
            {"stage1": {"resource_envelope": stage1}, "stage2": {}},
            arm="candidate",
        )


def test_stage1_only_arm_needs_no_stage2_envelope():
    stage_ab = _load_stage_ab()
    stage1 = stage_ab._resource_envelope(args=_args(), environment=_ENV, process=_proc())
    # No stage2 key: a stage1-only arm is fine (no ratio to protect).
    stage_ab.assert_stage_envelope_parity(
        {"stage1": {"resource_envelope": stage1}}, arm="baseline"
    )


def test_stage1_command_propagates_the_external_admission_budget(tmp_path, monkeypatch):
    stage_ab = _load_stage_ab()
    cap = 3 * 1024 * 1024 * 1024
    args = _args(max_tree_rss_bytes=cap)
    args.stage1_timeout = 360
    args.smoke_timeout = 60
    captured = {}

    def capture_sampler_args(sampler, **kwargs):
        captured.update(kwargs)
        return kwargs

    def stop_before_execution(sampler_args):
        assert sampler_args == captured
        raise RuntimeError("mock stops before any process runs")

    monkeypatch.setattr(stage_ab, "_sampler_args", capture_sampler_args)
    with pytest.raises(RuntimeError, match="mock stops before any process runs"):
        stage_ab._run_stage1(
            arm="baseline", source_root=tmp_path / "source",
            runtime_archive=tmp_path / "runtime.a", arm_root=tmp_path / "arm",
            args=args, ab=None, stage1=None,
            sampler=SimpleNamespace(run=stop_before_execution),
        )
    command = captured["command"]
    assert command[command.index("--memory-budget-bytes") + 1] == str(cap)
    assert command.count("--memory-budget-bytes") == 1
    assert captured["max_tree_rss_bytes"] == cap
    assert command[command.index("--jobs") + 1] == str(args.frontend_jobs)
    assert command[command.index("--with-threads") + 1] == "1"
    # Exercise the real consumer parser, not a duplicate list of its options.
    # Loading this tool does not build or invoke the captured command.
    builder = stage_ab._load_module(stage_ab.STAGE1_TOOL, "stage_ab_builder_contract")
    parsed = builder._parser().parse_args(command[2:])
    assert parsed.with_threads == 1
    assert parsed.memory_budget_bytes == cap
    environment = {}
    builder._set_stage1_build_modes(environment, with_threads=parsed.with_threads)
    assert environment["PCC_WITH_THREADS"] == "1"
    stage2_environment = dict(environment)
    stage2_environment.update(stage_ab._stage2_environment_overrides(
        pair_index=1, arm="baseline", self_backend_jobs=args.self_backend_jobs,
        max_tree_rss_bytes=cap,
    ))
    assert stage2_environment["PCC_WITH_THREADS"] == "1"
    assert "PCC_REFCOUNT_KIND" not in stage2_environment
    removed_mode = list(command[2:])
    removed_mode[removed_mode.index("--with-threads") + 1] = "0"
    with pytest.raises(SystemExit):
        builder._parser().parse_args(removed_mode)


def test_stage2_receipt_environment_is_copied_without_thread_override():
    # Keep the inheritance assertion tied to the production source path.
    source = STAGE_AB.read_text(encoding="utf-8")
    start = source.index("def _run_stage2(")
    end = source.index("\ndef run(", start)
    stage2_source = source[start:end]
    assert 'receipt["environment"].items()' in stage2_source
    assert "_stage2_environment_overrides(" in stage2_source
    assert "PCC_WITH_THREADS" not in stage2_source
    assert "PCC_REFCOUNT_KIND" not in stage2_source


@pytest.mark.parametrize("selected,advertised", [
    (None, None), (8 * 1024**3, str(3 * 1024**3)),
    (3 * 1024**3, str(8 * 1024**3)), (True, str(3 * 1024**3)),
])
def test_stage1_admission_receipt_rejects_missing_or_mixed_budgets(selected, advertised):
    stage_ab = _load_stage_ab()
    with pytest.raises(stage_ab.StageABError, match="admission budget"):
        stage_ab._assert_stage1_admission_budget(
            {"memory_budget_selection": {"max_tree_rss_bytes": selected},
             "environment": {"PCC_WORKER_TREE_BUDGET_BYTES": advertised}},
            expected=3 * 1024**3, arm="candidate",
        )


@pytest.mark.parametrize("receipt", [{}, {"memory_budget_selection": {}}])
def test_stage1_admission_receipt_requires_both_producer_records(receipt):
    stage_ab = _load_stage_ab()
    with pytest.raises(stage_ab.StageABError, match="missing its admission budget"):
        stage_ab._assert_stage1_admission_budget(
            receipt, expected=3 * 1024**3, arm="candidate"
        )


def test_stage1_admission_receipt_accepts_the_actual_matching_budget():
    stage_ab = _load_stage_ab()
    cap = 3 * 1024**3
    stage_ab._assert_stage1_admission_budget(
        {"memory_budget_selection": {"max_tree_rss_bytes": cap},
         "environment": {"PCC_WORKER_TREE_BUDGET_BYTES": str(cap)}},
        expected=cap, arm="baseline",
    )


def test_frontend_policy_reports_auto_without_relabelling_requested_width():
    stage_ab = _load_stage_ab()
    args = _args()
    host_env = dict(_ENV, PCC_PY_FRONTEND_JOBS="2",
                    PCC_BOOTSTRAP_STAGE1_PY_FRONTEND_JOBS="2")
    native_env = dict(host_env)
    native_env.update(stage_ab._stage2_environment_overrides(
        pair_index=1, arm="baseline", self_backend_jobs=2,
        max_tree_rss_bytes=args.max_tree_rss_bytes,
    ))
    host = stage_ab._resource_envelope(args=args, environment=host_env, process=_proc())
    native = stage_ab._resource_envelope(args=args, environment=native_env, process=_proc())
    assert host["frontend_jobs"] == native["frontend_jobs"] == 2
    assert native["frontend_jobs_scope"] == "requested_cli_width"
    assert host["effective_frontend_policy"]["PCC_PY_FRONTEND_JOBS"] == "2"
    assert set(native["effective_frontend_policy"].values()) == {"auto"}
    assert native["frontend_policy_parity"] == "not_checked"
    # The old v1 keys keep their meaning; observations do not waive or replace
    # any existing cap, width, GC, CPU or cache comparison.
    assert stage_ab.envelope_parity_key(host) == stage_ab.envelope_parity_key(native)


def test_missing_frontend_policy_is_reported_as_missing_not_inferred():
    stage_ab = _load_stage_ab()
    envelope = stage_ab._resource_envelope(args=_args(), environment={}, process={})
    assert set(envelope["effective_frontend_policy"].values()) == {""}
    assert envelope["frontend_policy_parity"] == "not_checked"
