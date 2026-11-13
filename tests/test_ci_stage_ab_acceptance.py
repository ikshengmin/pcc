"""CI-only pure contract fixtures; not native measurements or launch authority.

Contract: AGENTS.md compiler performance and docs/compiler-contract.md. These
fixtures model the existing production pair receipt shape, not a new pipeline.
Guarded producer/caller integration tests must independently exercise file and
sample revalidation and same-process launch ownership.
"""

from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts/pcc_stage_ab_acceptance.py"
    spec = importlib.util.spec_from_file_location("stage_acceptance_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


acceptance = _load()
H = "a" * 64
B = "b" * 64
C = "c" * 64


def _record(group, index, arm, stage):
    root = f"/campaign/{group}/pair-{index}/{arm}"
    source = B if group == "control" or arm == "baseline" else C
    wall = 10 if stage == "stage1" else 9
    measure = {
        "schema": "pcc.compile-process-window.v1", "scope": acceptance.SCOPE,
        "clean_exit": True, "sample_count": 3, "interval_s": 0.25,
        "max_tree_rss_bytes": 2 * 1024**3, "wall_s": wall,
        "peak_tree_rss_bytes": 100 if stage == "stage1" else 90,
        "command": ["/frozen/python", "-m", "pcc"] if stage == "stage1" else [root + "/pcc1", "--backend", "self"],
        "cwd": root + "/source", "root_pid": 20 + index,
        "root_start_identity": [100, index],
        "samples": root + "/" + stage + "/samples.tsv",
        "result": root + "/" + stage + "/result.json",
        "samples_sha256": H, "result_sha256": H, "sampler_sha256": H,
        "support_tool_sha256": H, "lock_tool_sha256": H,
    }
    result = {"schema": "pcc.stage1-build-result.v1", "returncode": 0,
              "metrics": {"cpu_s": 20},
              "metric_scopes": {"cpu_s": "timed_command_plus_waited_children_cpu_sum"}}
    if stage == "stage2":
        result = {
            "schema": "pcc.bootstrap_stage_result.v1", "stage": 2, "backend": "self",
            "returncode": 0, "publish_barrier_returncode": 0,
            "compile_user_ms": 10000, "compile_sys_ms": 1000,
            "compile_wall_ms": 9000, "publish_barrier_ms": 100, "wall_ms": 9100,
            "metric_scopes": {
                "compile_wall_ms": "end_to_end_elapsed",
                "compile_time_real_ms": "end_to_end_elapsed",
                "compile_user_ms": "timed_command_plus_waited_children_cpu",
                "compile_sys_ms": "timed_command_plus_waited_children_cpu",
                "publish_barrier_ms": "end_to_end_elapsed",
                "wall_ms": "end_to_end_elapsed_including_publish_barrier",
            },
        }
    return {
        "source_sha256": source, "runtime_sha256": H,
        "source_snapshot": root + "/source", "normalized_environment_sha256": H,
        "result": result, "process": {"status": "COMPLETE", "returncode": 0},
        "compile_measurement": measure,
        "resource_envelope": {
            "max_tree_rss_bytes": 2 * 1024**3, "cpu_count": 8,
            "frontend_jobs": 2, "self_backend_jobs": 2, "macho_link_jobs": 0,
            "gc_backend": 0, "cache_policy": {"PCC_PY_FRONTEND_IR_CACHE": "0"},
        },
    }


def _pairs(group):
    pairs = []
    for index in range(1, 6):
        pair = {"index": index, "order": ["baseline", "candidate"] if index % 2 else ["candidate", "baseline"]}
        for arm in acceptance.ARMS:
            pair[arm] = {stage: _record(group, index, arm, stage) for stage in acceptance.STAGES}
            pair[arm]["stage_status"] = {stage: "COMPLETE" for stage in acceptance.STAGES}
        pairs.append(pair)
    return pairs


def _bindings(pairs):
    result = []
    for pair in pairs:
        bindings = {}
        for arm in acceptance.ARMS:
            bindings[arm] = {}
            for stage in acceptance.STAGES:
                record = pair[arm][stage]
                measure = record["compile_measurement"]
                bindings[arm][stage] = {
                    **{key: deepcopy(measure[key]) for key in acceptance.MEASUREMENT_IDENTITY_KEYS},
                    "source_sha256": record["source_sha256"], "runtime_sha256": H,
                    "campaign_runtime_sha256": H,
                    "record_sha256": acceptance.canonical_sha256(record),
                    "measurement_sha256": acceptance.canonical_sha256(measure),
                }
        result.append(bindings)
    return result


def _fixture():
    pairs, control = _pairs("ab"), _pairs("control")
    expected = {
        "campaign": {key: H for key in acceptance.CAMPAIGN_KEYS},
        "envelope": deepcopy(pairs[0]["baseline"]["stage1"]["resource_envelope"]),
        "sources": {"baseline": B, "candidate": C, "control": B},
        "pairs": _bindings(pairs), "control_pairs": _bindings(control),
    }
    return pairs, control, expected, deepcopy(expected)


def _evaluate(fixture):
    pairs, control, expected, observed = fixture
    return acceptance.evaluate_stage_campaign(pairs, control, expected=expected, observed=observed)


def _reseal(fixture):
    # Test-only model of valid revalidation, never execution authority.
    pairs, control, expected, observed = fixture
    expected["pairs"] = _bindings(pairs)
    expected["control_pairs"] = _bindings(control)
    observed.clear()
    observed.update(deepcopy(expected))


def test_complete_fixture_is_calculation_not_execution_authority():
    result = _evaluate(_fixture())
    assert result["candidate_stage2_le_stage1"] is True
    assert result["automatic_acceptance"] is False
    assert result["source_promotion_authorized"] is False
    assert result["calculation_only"] is True
    assert result["unchanged_control"]["verdict"] == "OBSERVATION_ONLY"
    assert result["stage2_over_stage1"]["candidate"]["wall_s"]["ratios"] == [0.9] * 5
    assert "original_compile_slice_acceptance" in result["remaining_gates"]


def test_one_regressing_pair_fails_despite_good_median():
    fixture = _fixture()
    fixture[0][0]["candidate"]["stage2"]["compile_measurement"]["wall_s"] = 11
    _reseal(fixture)
    result = _evaluate(fixture)
    assert result["candidate_stage2_le_stage1"] is False
    metric = result["stage2_over_stage1"]["candidate"]["wall_s"]
    assert metric["median_paired_ratio"] == 0.9
    assert metric["pair_wins"] == {"left": 1, "right": 4, "ties": 0}


def test_memory_independently_fails_without_wall_regression():
    fixture = _fixture()
    fixture[0][2]["candidate"]["stage2"]["compile_measurement"]["peak_tree_rss_bytes"] = 101
    _reseal(fixture)
    assert _evaluate(fixture)["candidate_stage2_le_stage1"] is False


def test_native_ab_is_separate_from_same_source_stage_relation():
    fixture = _fixture()
    for pair in fixture[0]:
        pair["baseline"]["stage2"]["compile_measurement"]["wall_s"] = 8
    _reseal(fixture)
    result = _evaluate(fixture)
    assert result["candidate_stage2_le_stage1"] is True
    assert result["native_candidate_over_baseline"]["wall_s"]["every_pair_non_regression"] is False


@pytest.mark.parametrize("value", [None, True, "1", -1, float("nan"), float("inf"), 10**1000])
def test_malformed_window_metrics_are_rejected_even_when_resealed(value):
    fixture = _fixture()
    fixture[0][0]["candidate"]["stage2"]["compile_measurement"]["wall_s"] = value
    with pytest.raises(acceptance.StageAcceptanceError):
        _reseal(fixture)
        _evaluate(fixture)


@pytest.mark.parametrize("field,value", [
    ("scope", "outer_pipeline_with_barrier"), ("sample_count", 0),
    ("sample_count", True), ("clean_exit", False), ("interval_s", 1.0),
    ("max_tree_rss_bytes", 4 * 1024**3), ("command", ["/wrong/image"]),
    ("cwd", "/moved"), ("samples_sha256", B), ("result_sha256", B),
    ("sampler_sha256", B), ("support_tool_sha256", B), ("lock_tool_sha256", B),
    ("root_pid", -1), ("root_start_identity", [1]),
])
def test_every_window_identity_or_sample_mutation_is_rejected(field, value):
    fixture = _fixture()
    fixture[0][0]["candidate"]["stage2"]["compile_measurement"][field] = value
    with pytest.raises(acceptance.StageAcceptanceError):
        _evaluate(fixture)


@pytest.mark.parametrize("mutation", ["missing_stage", "failed_child", "censored", "nonalternating", "gap", "source", "runtime", "cap", "missing_cpu", "partial_cpu", "cpu_scope", "barrier", "reuse", "control_source", "control_root", "control_env"])
def test_complete_contract_mutations_fail_closed(mutation):
    fixture = _fixture()
    pair = fixture[0][0]
    stage = pair["candidate"]["stage2"]
    if mutation == "missing_stage":
        del pair["candidate"]["stage2"]
    elif mutation == "failed_child":
        stage["process"]["returncode"] = 1
    elif mutation == "censored":
        pair["candidate"]["stage_status"]["stage2"] = "TIMED_OUT"
    elif mutation == "nonalternating":
        fixture[0][1]["order"] = ["baseline", "candidate"]
    elif mutation == "gap":
        fixture[0][1]["index"] = 3
    elif mutation == "source":
        stage["source_sha256"] = B
    elif mutation == "runtime":
        stage["runtime_sha256"] = B
    elif mutation == "cap":
        stage["resource_envelope"]["max_tree_rss_bytes"] *= 2
    elif mutation in ("missing_cpu", "partial_cpu"):
        del stage["result"]["compile_user_ms"]
        if mutation == "missing_cpu":
            del stage["result"]["compile_sys_ms"]
    elif mutation == "cpu_scope":
        stage["result"]["metric_scopes"]["compile_user_ms"] = "coordinator_only"
    elif mutation == "barrier":
        stage["result"]["publish_barrier_returncode"] = 1
    elif mutation == "reuse":
        stage["compile_measurement"]["result"] = pair["baseline"]["stage2"]["compile_measurement"]["result"]
    elif mutation == "control_source":
        fixture[1][0]["candidate"]["stage2"]["source_sha256"] = C
    elif mutation == "control_root":
        fixture[1][0]["candidate"]["stage1"]["source_snapshot"] = fixture[1][0]["baseline"]["stage1"]["source_snapshot"]
    elif mutation == "control_env":
        fixture[1][0]["candidate"]["stage1"]["normalized_environment_sha256"] = B
    if mutation != "missing_stage":
        _reseal(fixture)
    with pytest.raises(acceptance.StageAcceptanceError):
        _evaluate(fixture)


@pytest.mark.parametrize("group", [0, 1])
def test_missing_or_screening_pairs_cannot_pass(group):
    fixture = _fixture()
    fixture[group].pop()
    _reseal(fixture)
    with pytest.raises(acceptance.StageAcceptanceError, match="five"):
        _evaluate(fixture)


@pytest.mark.parametrize("field", acceptance.CAMPAIGN_KEYS)
def test_current_campaign_identity_changes_rejected(field):
    fixture = _fixture()
    fixture[3]["campaign"][field] = B
    with pytest.raises(acceptance.StageAcceptanceError, match="identity mismatch"):
        _evaluate(fixture)


def test_control_drift_is_retained_without_new_tolerance_or_correction():
    fixture = _fixture()
    fixture[1][0]["candidate"]["stage2"]["compile_measurement"]["wall_s"] *= 2
    _reseal(fixture)
    result = _evaluate(fixture)
    control = result["unchanged_control"]
    assert control["by_stage"]["stage2"]["wall_s"]["ratios"][0] == 2
    assert control["verdict"] == "OBSERVATION_ONLY"
    assert control["drift_subtracted_from_ab"] is False
    assert control["ab_thresholds_relaxed"] is False
    assert result["automatic_acceptance"] is False


@pytest.mark.parametrize("field,value,diagnostic", [
    ("scope", "outer", "scope/outcome"), ("sample_count", 0, "sample evidence"),
    ("sample_count", True, "sample evidence"), ("clean_exit", False, "scope/outcome"),
    ("interval_s", 1.0, "scope/outcome"),
    ("max_tree_rss_bytes", 4 * 1024**3, "cap mismatch"),
    ("root_pid", -1, "root PID"), ("root_start_identity", [1], "root identity"),
    ("samples_sha256", "invalid", "invalid SHA256"),
    ("command", [], "invalid command"), ("cwd", "relative", "absolute path"),
])
def test_intrinsic_window_contracts_reject_resealed_bad_evidence(field, value, diagnostic):
    fixture = _fixture()
    fixture[0][0]["candidate"]["stage2"]["compile_measurement"][field] = value
    _reseal(fixture)
    with pytest.raises(acceptance.StageAcceptanceError, match=diagnostic):
        _evaluate(fixture)


def test_ab_and_control_cannot_reuse_same_compile_result():
    fixture = _fixture()
    fixture[1][0]["baseline"]["stage1"]["compile_measurement"]["result"] = (
        fixture[0][0]["baseline"]["stage1"]["compile_measurement"]["result"])
    _reseal(fixture)
    with pytest.raises(acceptance.StageAcceptanceError, match="reused compile measurement"):
        _evaluate(fixture)


def test_shape_builder_is_only_data_not_authority():
    pairs, control, expected, _ = _fixture()
    bindings = acceptance.calculation_bindings(
        pairs, control, campaign_identity=expected["campaign"])
    assert bindings == expected
    calculated = acceptance.evaluate_stage_campaign(
        pairs, control, expected=bindings, observed=deepcopy(bindings))
    assert calculated["calculation_only"] is True
    assert calculated["automatic_acceptance"] is False
    assert calculated["promotion"] == "NOT_AUTHORIZED"
    assert calculated["control_qualification"] == "UNRESOLVED_OBSERVATION_ONLY"


def test_different_source_unchanged_control_is_not_baseline_aa():
    fixture = _fixture()
    for pair in fixture[1]:
        for arm in acceptance.ARMS:
            for stage in acceptance.STAGES:
                pair[arm][stage]["source_sha256"] = C
    fixture[2]["sources"]["control"] = C
    _reseal(fixture)
    with pytest.raises(acceptance.StageAcceptanceError, match="freeze baseline content"):
        _evaluate(fixture)
