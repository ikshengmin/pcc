"""Pure full-stage arithmetic, NOT execution or source-promotion authority.

Consumes the production run_pcc_stage_ab pair records and independently supplied
bindings. The guarded current-campaign caller MUST re-read source/tool/runtime,
child and compile-window receipts (including samples), verify command/cwd/images,
and own the live launch/guard/lock capability before supplying these bindings.
JSON, status strings, and this module's output can never grant that capability.
No filesystem reads, launch, guard admission, deadline reset or source verdict
are performed here. Ordinary stage CPU is retained diagnostically, never divided
across different scopes. Preparation, runtime build and smoke/barrier are excluded
from the matched full compiler launch-to-owned-tree-exit comparisons.
"""

import hashlib
import json
import math
from statistics import median

SCHEMA = "pcc.full-stage-calculation.v1"
SCOPE = "launch_to_observed_owned_tree_exit_including_compile_wrappers"
METRICS = ("wall_s", "peak_tree_rss_bytes")
ARMS = ("baseline", "candidate")
STAGES = ("stage1", "stage2")
ENVELOPE_KEYS = (
    "max_tree_rss_bytes", "cpu_count", "frontend_jobs", "self_backend_jobs",
    "macho_link_jobs", "gc_backend", "cache_policy",
)
MEASUREMENT_IDENTITY_KEYS = (
    "command", "cwd", "root_pid", "root_start_identity", "samples",
    "samples_sha256", "result", "result_sha256", "sampler_sha256",
    "support_tool_sha256", "lock_tool_sha256",
)
CAMPAIGN_KEYS = (
    "run_id", "parent_source_sha256", "producer_sha256", "runtime_sha256",
    "tools_sha256", "host_runtime_sha256", "environment_sha256",
    "input_sha256", "guard_contract_sha256",
)


class StageAcceptanceError(ValueError):
    """Incomplete, inconsistent or malformed calculation evidence."""


def canonical_sha256(value):
    """Content binding only; hashing caller JSON is not authentication."""
    try:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise StageAcceptanceError("noncanonical evidence") from exc
    return hashlib.sha256(data).hexdigest()


def _require(condition, message):
    if not condition:
        raise StageAcceptanceError(message)


def _object(value, label):
    _require(type(value) is dict, label + " must be an object")
    return value


def _sha(value, label):
    _require(type(value) is str and len(value) == 64
             and all(c in "0123456789abcdef" for c in value), label + " invalid SHA256")
    return value


def _number(value, label, *, positive=False):
    _require(type(value) in (int, float), label + " missing/non-numeric")
    try:
        result = float(value)
    except OverflowError as exc:
        raise StageAcceptanceError(label + " overflow") from exc
    _require(math.isfinite(result) and (result > 0 if positive else result >= 0),
             label + " invalid metric")
    return result


def _success(value, label):
    _object(value, label)
    _require(value.get("status") == "COMPLETE"
             and type(value.get("returncode")) is int and value["returncode"] == 0
             and not value.get("post_exit_cleanup_pids"), label + " incomplete child")


def _window(record, binding, envelope):
    measure = _object(record.get("compile_measurement"), "compile measurement")
    _require(measure.get("schema") == "pcc.compile-process-window.v1"
             and measure.get("scope") == SCOPE and measure.get("clean_exit") is True
             and measure.get("interval_s") == 0.25,
             "compile measurement scope/outcome mismatch")
    _require(type(measure.get("sample_count")) is int and measure["sample_count"] > 0,
             "missing sample evidence")
    _require(type(measure.get("max_tree_rss_bytes")) is int
             and measure["max_tree_rss_bytes"] == envelope["max_tree_rss_bytes"],
             "compile measurement cap mismatch")
    for key in MEASUREMENT_IDENTITY_KEYS:
        _require(key in binding and key in measure and measure[key] == binding[key],
                 "compile measurement identity mismatch: " + key)
        if key.endswith("sha256"):
            _sha(measure[key], key)
    _require(type(measure["command"]) is list and bool(measure["command"])
             and all(type(v) is str and v for v in measure["command"]), "invalid command")
    for key in ("cwd", "samples", "result"):
        _require(type(measure[key]) is str and measure[key].startswith("/"),
                 "invalid absolute path: " + key)
    _require(type(measure["root_pid"]) is int and measure["root_pid"] > 0,
             "invalid root PID")
    identity = measure["root_start_identity"]
    _require(type(identity) is list and len(identity) == 2
             and all(type(v) is int and v >= 0 for v in identity), "invalid root identity")
    # Equality to the independent re-read also binds every numeric observation,
    # not merely hashes copied from the submitted JSON.
    _require(canonical_sha256(measure) == binding.get("measurement_sha256"),
             "measurement differs from independent revalidation")
    values = {key: _number(measure.get(key), key, positive=True) for key in METRICS}
    _require(values["peak_tree_rss_bytes"] <= envelope["max_tree_rss_bytes"],
             "measured peak exceeds cap")
    return values


def _cpu(record, stage):
    result = _object(record.get("result"), "stage result")
    _require(type(result.get("returncode")) is int and result["returncode"] == 0,
             "failed or absent stage result")
    if stage == "stage1":
        _require(result.get("schema") == "pcc.stage1-build-result.v1",
                 "Stage1 result schema mismatch")
        scopes = _object(result.get("metric_scopes"), "Stage1 metric scopes")
        _require(scopes.get("cpu_s") == "timed_command_plus_waited_children_cpu_sum",
                 "Stage1 CPU scope mismatch")
        metrics = _object(result.get("metrics"), "Stage1 metrics")
        return _number(metrics.get("cpu_s"), "Stage1 CPU")
    scopes = {
        "compile_wall_ms": "end_to_end_elapsed",
        "compile_time_real_ms": "end_to_end_elapsed",
        "compile_user_ms": "timed_command_plus_waited_children_cpu",
        "compile_sys_ms": "timed_command_plus_waited_children_cpu",
        "publish_barrier_ms": "end_to_end_elapsed",
        "wall_ms": "end_to_end_elapsed_including_publish_barrier",
    }
    _require(result.get("metric_scopes") == scopes, "Stage2 CPU/phase scope mismatch")
    _require(result.get("schema") == "pcc.bootstrap_stage_result.v1"
             and result.get("stage") == 2 and result.get("backend") == "self"
             and type(result.get("publish_barrier_returncode")) is int
             and result["publish_barrier_returncode"] == 0, "Stage2 result/barrier mismatch")
    for key in ("compile_wall_ms", "publish_barrier_ms", "wall_ms"):
        _number(result.get(key), key)
    # Missing timed child CPU must not silently become zero or an acceptance.
    user = _number(result.get("compile_user_ms"), "Stage2 user CPU")
    system = _number(result.get("compile_sys_ms"), "Stage2 system CPU")
    return _number((user + system) / 1000.0, "Stage2 CPU")


def _series(left, right):
    ratios = [b / a for a, b in zip(left, right, strict=True)]
    _require(all(math.isfinite(v) for v in ratios), "nonfinite ratio")
    return {
        "left": left, "right": right, "ratios": ratios,
        "median_left": median(left), "median_right": median(right),
        "median_paired_ratio": median(ratios),
        "pair_wins": {"left": sum(a < b for a, b in zip(left, right)),
                      "right": sum(b < a for a, b in zip(left, right)),
                      "ties": sum(a == b for a, b in zip(left, right))},
        "every_pair_non_regression": all(b <= a for a, b in zip(left, right)),
    }


def _validate_pairs(pairs, bindings, *, control, expected_envelope, seen_results):
    _require(type(pairs) is list and len(pairs) >= 5, "requires at least five complete pairs")
    _require(type(bindings) is list and len(bindings) == len(pairs), "binding pair count mismatch")
    values = {arm: {stage: {metric: [] for metric in METRICS} for stage in STAGES}
              for arm in ARMS}
    cpu = {arm: {stage: [] for stage in STAGES} for arm in ARMS}
    frozen_sources = {}
    frozen_env = None
    for offset, (pair, pair_bindings) in enumerate(zip(pairs, bindings, strict=True)):
        _object(pair, "pair")
        _object(pair_bindings, "pair bindings")
        index = offset + 1
        order = list(ARMS if index % 2 else reversed(ARMS))
        _require(type(pair.get("index")) is int and pair["index"] == index
                 and type(pair.get("order")) in (list, tuple)
                 and list(pair["order"]) == order, "nonadjacent/nonalternating pair")
        for arm in ARMS:
            arm_record = _object(pair.get(arm), "arm")
            _require(arm_record.get("stage_status") == {stage: "COMPLETE" for stage in STAGES},
                     "incomplete stage pair")
            arm_bindings = _object(pair_bindings.get(arm), "arm bindings")
            for stage in STAGES:
                record = _object(arm_record.get(stage), stage)
                binding = _object(arm_bindings.get(stage), "stage binding")
                for key in ("source_sha256", "runtime_sha256"):
                    _sha(record.get(key), key)
                    _require(record[key] == binding.get(key), key + " binding mismatch")
                _require(record["runtime_sha256"] == binding.get("campaign_runtime_sha256"),
                         "mixed runtime")
                source = record["source_sha256"]
                _require(frozen_sources.setdefault(arm, source) == source, "source changed")
                envelope = _object(record.get("resource_envelope"), "resource envelope")
                for key in ENVELOPE_KEYS:
                    _require(key in envelope and envelope[key] == expected_envelope[key],
                             "resource envelope mismatch: " + key)
                _success(record.get("process"), "stage process")
                _require(canonical_sha256(record) == binding.get("record_sha256"),
                         "stage receipt differs from independent revalidation")
                measured = _window(record, binding, envelope)
                result_path = record["compile_measurement"]["result"]
                _require(result_path not in seen_results, "reused compile measurement")
                seen_results.add(result_path)
                cpu[arm][stage].append(_cpu(record, stage))
                for metric, value in measured.items():
                    values[arm][stage][metric].append(value)
            env = arm_record["stage1"].get("normalized_environment_sha256")
            _sha(env, "normalized environment")
            frozen_env = env if frozen_env is None else frozen_env
            _require(env == frozen_env, "normalized environment drift")
        if control:
            roots = [pair[arm]["stage1"].get("source_snapshot") for arm in ARMS]
            _require(all(type(root) is str and root.startswith("/") for root in roots)
                     and roots[0] != roots[1], "control requires distinct frozen roots")
            _require(frozen_sources["baseline"] == frozen_sources["candidate"],
                     "unchanged control source content differs")
    return values, cpu, frozen_sources, frozen_env


def evaluate_stage_campaign(pairs, control_pairs, *, expected, observed):
    """Return a passive calculation, or raise on incomplete/mismatched evidence.

    ``expected`` is frozen by the current guarded owner before launches;
    ``observed`` is rebuilt independently after launches/revalidation. Each has
    ``campaign``, ``envelope``, ``sources`` (baseline/candidate/control SHA256),
    and ``pairs``/``control_pairs`` stage bindings. Per-stage bindings contain
    MEASUREMENT_IDENTITY_KEYS, source/runtime/campaign_runtime SHA256,
    measurement_sha256 and record_sha256. Hashes expected for output receipts
    are sealed as each source-owned launch completes, not guessed in advance.

    This intentionally cannot certify that the caller actually performed those
    operations. The integration must reject imported JSON and require its live
    same-process producer capability; no boolean or token string here replaces it.
    """
    _object(expected, "expected bindings")
    _object(observed, "observed bindings")
    _require(canonical_sha256(expected) == canonical_sha256(observed),
             "current/frozen campaign identity mismatch")
    campaign = _object(expected.get("campaign"), "campaign identity")
    for key in CAMPAIGN_KEYS:
        _sha(campaign.get(key), key)
    envelope = _object(expected.get("envelope"), "campaign envelope")
    _require(all(key in envelope for key in ENVELOPE_KEYS), "incomplete campaign envelope")
    cap = envelope["max_tree_rss_bytes"]
    _require(type(cap) is int and 2 * 1024**3 <= cap <= 4 * 1024**3,
             "campaign cap outside existing limits")
    for key in ("cpu_count", "frontend_jobs", "self_backend_jobs"):
        _require(type(envelope[key]) is int and envelope[key] > 0, "invalid " + key)
    _object(envelope["cache_policy"], "cache policy")
    sources = _object(expected.get("sources"), "frozen source identities")
    for key in (*ARMS, "control"):
        _sha(sources.get(key), key)
    _require(sources["control"] == sources["baseline"], "control must freeze baseline content")
    seen_results = set()
    ab, cpu, actual_sources, env = _validate_pairs(
        pairs, expected.get("pairs"), control=False, expected_envelope=envelope, seen_results=seen_results)
    control, control_cpu, control_sources, control_env = _validate_pairs(
        control_pairs, expected.get("control_pairs"), control=True, expected_envelope=envelope, seen_results=seen_results)
    _require(actual_sources == {arm: sources[arm] for arm in ARMS}, "wrong A/B sources")
    _require(all(value == sources["control"] for value in control_sources.values()),
             "wrong unchanged control source")
    _require(env == control_env == campaign["environment_sha256"], "control environment differs")
    for group in (expected["pairs"], expected["control_pairs"]):
        for pair in group:
            for arm in ARMS:
                for stage in STAGES:
                    _require(pair[arm][stage]["campaign_runtime_sha256"] == campaign["runtime_sha256"],
                             "wrong campaign runtime")
    relation = {arm: {metric: _series(ab[arm]["stage1"][metric], ab[arm]["stage2"][metric])
                      for metric in METRICS} for arm in ARMS}
    native_ab = {metric: _series(ab["baseline"]["stage2"][metric], ab["candidate"]["stage2"][metric])
                 for metric in METRICS}
    drift = {stage: {metric: _series(control["baseline"][stage][metric],
                                   control["candidate"][stage][metric]) for metric in METRICS}
             for stage in STAGES}
    return {
        "schema": SCHEMA, "scope": SCOPE, "calculation_only": True,
        "automatic_acceptance": False, "source_promotion_authorized": False,
        "control_qualification": "UNRESOLVED_OBSERVATION_ONLY", "promotion": "NOT_AUTHORIZED",
        "pair_count": len(pairs), "control_pair_count": len(control_pairs),
        "candidate_stage2_le_stage1": all(relation["candidate"][metric]["every_pair_non_regression"]
                                          for metric in METRICS),
        "criterion": "every_candidate_pair_wall_and_sampled_tree_rss_le_stage1_no_tolerance",
        "stage2_over_stage1": relation, "native_candidate_over_baseline": native_ab,
        "diagnostic_cpu_s_not_cross_scope_ratios": cpu,
        "unchanged_control": {"verdict": "OBSERVATION_ONLY", "by_stage": drift,
                              "diagnostic_cpu_s": control_cpu,
                              "drift_subtracted_from_ab": False, "ab_thresholds_relaxed": False},
        "remaining_gates": ["live_current_campaign_revalidation", "original_compile_slice_acceptance",
                            "native_public_cli", "five_gc", "stage1_stage2_stage3_fixed_point"],
    }



def calculation_bindings(pairs, control_pairs, *, campaign_identity):
    """Pure shape builder, never a verifier or authority boundary.

    The guarded CI owner must call its exact frozen stage producer's
    verify_guarded_result on both LIVE registered results before this helper,
    and again after calculation. It freezes the flat campaign_identity before
    execution; host_runtime_sha256 must come from revalidated producer evidence.
    Source/env/envelope here are only data extracted from those checked records.
    Calling this on arbitrary JSON does not establish execution evidence.
    """
    from copy import deepcopy

    def bindings(records):
        output = []
        for pair in records:
            entry = {}
            for arm in ARMS:
                entry[arm] = {}
                for name in STAGES:
                    record = pair[arm][name]
                    measure = record["compile_measurement"]
                    entry[arm][name] = {
                        **{key: deepcopy(measure[key]) for key in MEASUREMENT_IDENTITY_KEYS},
                        "source_sha256": record["source_sha256"],
                        "runtime_sha256": record["runtime_sha256"],
                        "campaign_runtime_sha256": campaign_identity["runtime_sha256"],
                        "record_sha256": canonical_sha256(record),
                        "measurement_sha256": canonical_sha256(measure),
                    }
            output.append(entry)
        return output

    baseline = pairs[0]["baseline"]["stage1"]
    campaign = deepcopy(campaign_identity)
    environment = baseline["normalized_environment_sha256"]
    if "environment_sha256" in campaign:
        _require(campaign["environment_sha256"] == environment,
                 "frozen normalized environment differs")
    campaign["environment_sha256"] = environment
    return {
        "campaign": campaign,
        "envelope": {key: deepcopy(baseline["resource_envelope"][key]) for key in ENVELOPE_KEYS},
        "sources": {"baseline": baseline["source_sha256"],
                    "candidate": pairs[0]["candidate"]["stage1"]["source_sha256"],
                    "control": control_pairs[0]["baseline"]["stage1"]["source_sha256"]},
        "pairs": bindings(pairs), "control_pairs": bindings(control_pairs),
    }
