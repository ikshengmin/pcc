#!/usr/bin/env python3
"""Run adjacent, receipt-bound Stage1/Stage2 baseline pairs.

This harness compares two frozen compiler source lines under one host/runtime/
jobs/cache contract.  It reports timed-tree CPU as the primary compute metric;
wall remains an adjacent end-to-end observation.  It deliberately emits no
automatic source ACCEPT/DENY verdict.
"""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
COMPILE_AB_TOOL = REPO_ROOT / "scripts" / "run_pcc_compile_ab.py"
STAGE1_TOOL = REPO_ROOT / "scripts" / "run_pcc_stage1_build.py"
PROCESS_SAMPLER = REPO_ROOT / "scripts" / "run_process_tree_sample.py"
DEFAULT_MAX_TREE_RSS_BYTES = 8 * 1024 * 1024 * 1024
MAX_ALLOWED_TREE_RSS_BYTES = 16 * 1024 * 1024 * 1024

STAGE2_METRIC_SCOPES = {
    "compile_wall_ms": "end_to_end_elapsed",
    "compile_time_real_ms": "end_to_end_elapsed",
    "compile_user_ms": "timed_command_plus_waited_children_cpu",
    "compile_sys_ms": "timed_command_plus_waited_children_cpu",
    "publish_barrier_ms": "end_to_end_elapsed",
    "wall_ms": "end_to_end_elapsed_including_publish_barrier",
    "peak_tree_rss_bytes": "sampled_process_tree_sum",
}

COMPARISON_CONTRACT = {
    "primary_compute_metric": "cpu_s",
    "wall_metric_role": "paired_end_to_end_observation",
    "required_order": "adjacent_alternating_pairs",
    "single_wall_verdict_allowed": False,
    "automatic_source_verdict": False,
}


class StageABError(RuntimeError):
    pass


def _validate_resource_limits(args: argparse.Namespace) -> None:
    if not 1 <= args.frontend_jobs <= 2:
        raise StageABError("Stage A/B frontend jobs must stay within 1..2")
    if not 1 <= args.self_backend_jobs <= 2:
        raise StageABError("Stage A/B self-backend jobs must stay within 1..2")
    if not 0 < args.max_tree_rss_bytes <= MAX_ALLOWED_TREE_RSS_BYTES:
        raise StageABError("Stage A/B RSS cap must be positive and <= 16 GiB")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise StageABError("cannot load tool: " + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# This is also the producer closure the CI caller must freeze. Deferred native
# execution remains owned by the existing bootstrap path.
GUARDED_PRODUCER_FILES = (
    "scripts/run_pcc_stage_ab.py", "scripts/run_pcc_stage1_build.py",
    "scripts/run_pcc_compile_ab.py", "scripts/run_process_tree_sample.py",
    "scripts/bootstrap.py", "scripts/run_pcc_native_deferred.py",
    "scripts/file_lock.py",
)


_GUARDED_RESULTS = {}


class _InheritedStageContext:
    def __init__(self, ab, token, budget):
        self.ab, self.token, self.budget = ab, token, budget
        self.functions = {name: getattr(ab, name) for name in (
            "_validated_worker_budget", "_run_process", "_performance_lock",
        )}
        self.tools = [ab._path_receipt(REPO_ROOT / name)
                      for name in GUARDED_PRODUCER_FILES]

    def verify(self):
        if (self.ab._HELD_PERFORMANCE_LOCK is not self.token
                or self.ab._ACTIVE_WORKER_BUDGET is not self.budget
                or any(getattr(self.ab, name) is not fn
                       for name, fn in self.functions.items())):
            raise StageABError("inherited stage lock/module ownership changed")
        self.budget.verify()
        for receipt in self.tools:
            self.ab._verify_receipt(receipt, "inherited stage producer")
        self.budget.verify()

    def timeout(self, requested):
        self.verify()
        return self.budget.remaining_timeout(requested)


def _verify_inherited(args):
    context = getattr(args, "_inherited_stage_context", None)
    if context is not None:
        context.verify()
    return context


def run_guarded(
    args: argparse.Namespace, *, ab, performance_lock_token: object,
    guard_result_path: Path, expected_guard_command: list[str],
    expected_guard_cwd: Path, campaign_deadline_monotonic: float,
) -> dict[str, Any]:
    """Reuse the caller's actual lock, admission and absolute campaign deadline.

    The original watchdog remains responsible for the entire campaign, including
    preparation and publication. Child samplers only observe bounded subtrees;
    their worker mailbox and recovery deadline remain those of the outer guard.
    This entry point produces evidence, never a promotion verdict.
    """
    if (Path(ab.__file__).resolve(strict=True) != COMPILE_AB_TOOL.resolve(strict=True)
            or ab._validated_worker_budget.__globals__ is not vars(ab)
            or ab._run_process.__globals__ is not vars(ab)
            or performance_lock_token is None
            or ab._HELD_PERFORMANCE_LOCK is not performance_lock_token
            or ab._ACTIVE_WORKER_BUDGET is not None):
        raise StageABError("guarded stages require the caller's live module and lock")
    if (not getattr(args, "measure_compile_window", False)
            or args.stages != "stage1-stage2"
            or args.frontend_jobs != 2 or args.self_backend_jobs != 2
            or args.pairs <= 0 or args.first_pair_index <= 0):
        raise StageABError("guarded stages require matched windows and Stage1 jobs=2/backend=2")
    budget = ab._validated_worker_budget(
        guard_result_path=guard_result_path,
        expected_guard_command=expected_guard_command,
        expected_guard_cwd=expected_guard_cwd,
        campaign_deadline_monotonic=campaign_deadline_monotonic,
    )
    if args.max_tree_rss_bytes != budget.max_tree_rss_bytes:
        raise StageABError("stage cap differs from original admitted campaign cap")
    # Do not mutate caller arguments or make an environment flag authoritative.
    owned_args = argparse.Namespace(**vars(args))
    context = _InheritedStageContext(ab, performance_lock_token, budget)
    owned_args._inherited_stage_context = context
    ab._ACTIVE_WORKER_BUDGET = budget
    try:
        context.verify()
        result = _run(owned_args, ab=ab)
        # Strong references retain both the caller module and the exact result;
        # parsed/caller-created JSON cannot acquire this finalization authority.
        _GUARDED_RESULTS[id(result)] = (result, context, _canonical_sha256(result))
        verify_guarded_result(result, ab=ab, performance_lock_token=performance_lock_token)
        return result
    except BaseException as exc:
        _record_inherited_failure(context, exc)
        raise
    finally:
        ab._ACTIVE_WORKER_BUDGET = None


def _record_inherited_failure(context, exc):
    path = getattr(context, "manifest_path", None)
    if path is None:
        return
    try:
        manifest = _read_json(path)
        manifest.update(status="FAILED", failure={"exception_type": type(exc).__name__,
                                                  "message": str(exc)})
        manifest.pop("summary", None)
        manifest.pop("completed_at_utc", None)
        _persist(path, manifest)
    except Exception:
        # Never replace the guard/child failure with a storage failure.
        pass


def _seal_stage_record(context, record, *, source_manifest=None):
    """Seal at completion, not after later pairs could mutate early artifacts."""
    context.verify()
    ab = context.ab
    measurement = record["compile_measurement"]
    for key in ("result", "samples"):
        if ab.sha256_path(Path(measurement[key])) != measurement[key + "_sha256"]:
            raise StageABError("compile-window artifact changed before sealing")
    if _read_json(Path(record["result_path"])) != record["result"]:
        raise StageABError("stage result changed before sealing")
    paths = {Path(record["compiler"]), Path(record["result_path"]),
             Path(measurement["result"]), Path(measurement["samples"])}
    if "compiler_sha256" in record:
        if ab.sha256_path(Path(record["compiler"])) != record["compiler_sha256"]:
            raise StageABError("stage compiler changed before sealing")
    if source_manifest is not None:
        snapshot = Path(record["source_snapshot"])
        if context.source_manifest(snapshot, ab) != source_manifest:
            raise StageABError("Stage1 snapshot differs from original input")
        context.sources.append((snapshot, source_manifest, "snapshot"))
        receipt_path = Path(record["receipt_path"])
        compiler = Path(record["compiler"])
        loaded = ab._load_build_receipt(
            receipt_path, arm=context.current_arm,
            compiler_sha256=ab.sha256_path(compiler),
            runtime_sha256=record["runtime_sha256"],
            compiler_size_bytes=compiler.stat().st_size,
        )
        build_manifest_path = receipt_path.parent / "manifest.json"
        build_manifest = _read_json(build_manifest_path)
        context.builds.append((loaded["receipt"], build_manifest["runtime_bundle"]))
        paths.update((build_manifest_path, Path(loaded["source_manifest_path"])))
        paths.update(Path(value) for value in loaded["artifact_paths"].values())
        # Private bytecode/work caches are mutable by native publication. Only
        # immutable producer/runtime evidence belongs in this closure.
        for directory in ("producer-tools", "runtime-bundle"):
            paths.update(path for path in (receipt_path.parent / directory).rglob("*")
                         if path.is_file())
        paths.update((Path(record["receipt_path"]), Path(record["runtime_archive"])))
        if ab.sha256_path(Path(record["runtime_archive"])) != record["runtime_sha256"]:
            raise StageABError("Stage1 runtime changed before sealing")
    # Preserve ordinary process-tree evidence separately from matched windows.
    process = record["process"]
    process_path = Path(record["process_result_path"])
    if _read_json(process_path) != process:
        raise StageABError("ordinary process observation changed before sealing")
    paths.add(process_path)
    ordinary_samples = process.get("artifacts", {}).get("samples")
    ordinary_digest = process.get("target_window", {}).get("samples_sha256")
    if (not isinstance(ordinary_samples, str) or not ordinary_samples
            or not ab._is_sha256(ordinary_digest)
            or ab.sha256_path(Path(ordinary_samples)) != ordinary_digest):
        raise StageABError("ordinary process samples changed before sealing")
    for key in ("result", "samples", "stdout", "stderr"):
        value = process.get("artifacts", {}).get(key)
        if value is not None:
            paths.add(Path(value))
    context.result_files.extend(ab._path_receipt(path) for path in sorted(paths))
    context.verify()


def _verify_stage_closure(context):
    ab = context.ab
    for receipt, runtime_bundle in context.builds:
        context.verify()
        ab._verify_runtime_bundle(runtime_bundle)
        for index, tool in enumerate(receipt["external_tools"]):
            ab.verify_portable_file_receipt(tool, "stage external tool " + str(index))
        host = Path(receipt["environment"]["PCC_HOST_PYTHON"])
        if ab.host_python_runtime_evidence(host) != receipt["host_python_runtime"]:
            raise StageABError("host Python runtime changed after Stage1")
        context.verify()


def _verify_stage_sources(context):
    for source, expected, kind in context.sources:
        context.verify()
        if kind == "snapshot":
            context.ab._verify_source_snapshot(
                source, expected, "retained stage source snapshot", require_read_only=True,
            )
        elif kind == "original":
            # Full frozen roots legitimately contain files outside the build
            # closure. Preserve their existing immutable-source contract.
            context.require_immutable_source(source, context.ab)
            if context.source_manifest(source, context.ab) != expected:
                raise StageABError("stage source changed after production")
        else:
            raise StageABError("unknown retained stage source scope")
        context.verify()


def verify_guarded_result(result, *, ab, performance_lock_token):
    """Revalidate a result produced here before campaign publication, in budget."""
    entry = _GUARDED_RESULTS.get(id(result))
    if entry is None or entry[0] is not result:
        raise StageABError("stage result has no live source-owned producer")
    _, context, identity = entry
    if context.ab is not ab or context.token is not performance_lock_token:
        raise StageABError("stage finalizer caller differs from original owner")
    previous = ab._ACTIVE_WORKER_BUDGET
    if previous is not None and previous is not context.budget:
        raise StageABError("another worker budget is active")
    ab._ACTIVE_WORKER_BUDGET = context.budget
    try:
        context.verify()
        if _canonical_sha256(result) != identity:
            raise StageABError("stage result changed after production")
        for receipt in context.result_files:
            ab._verify_receipt(receipt, "completed stage artifact")
            context.verify()
        _verify_stage_closure(context)
        _verify_stage_sources(context)
        context.verify()
    except BaseException as exc:
        _record_inherited_failure(context, exc)
        raise
    finally:
        ab._ACTIVE_WORKER_BUDGET = previous


def pair_order(pair_index: int) -> tuple[str, str]:
    if pair_index < 1:
        raise ValueError("pair index must be positive")
    return ("baseline", "candidate") if pair_index % 2 else (
        "candidate",
        "baseline",
    )


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _persist(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageABError("cannot read JSON receipt " + str(path)) from exc
    if not isinstance(value, dict):
        raise StageABError("JSON receipt is not an object: " + str(path))
    return value


def normalize_arm_environment(
    environment: dict[str, str],
    *,
    output_root: Path,
    source_root: Path,
) -> dict[str, str]:
    replacements = (
        (str(source_root.resolve()), "<ARM_SOURCE>"),
        (str(output_root.resolve()), "<ARM_OUTPUT>"),
    )
    normalized: dict[str, str] = {}
    for key, raw in sorted(environment.items()):
        value = str(raw)
        for prefix, marker in replacements:
            if value == prefix:
                value = marker
            elif value.startswith(prefix + os.sep):
                value = marker + value[len(prefix) :]
        normalized[str(key)] = value
    return normalized


def _measured_number(values: dict[str, Any], name: str) -> float:
    value = values.get(name)
    if type(value) not in (int, float):
        raise StageABError("invalid or missing measured metric: " + name)
    try:
        measured = float(value)
    except OverflowError as exc:
        raise StageABError("invalid measured metric: " + name) from exc
    if not math.isfinite(measured) or measured < 0:
        raise StageABError("invalid measured metric: " + name)
    return measured


def _stage_metrics(record: dict[str, Any], stage: str) -> dict[str, float | None]:
    if stage == "stage1":
        raw = record["result"]["metrics"]
        return {
            "wall_s": _measured_number(raw, "wall_s"),
            "cpu_s": _measured_number(raw, "cpu_s"),
            "peak_tree_rss_bytes": _measured_number(
                record["process"], "peak_tree_rss_bytes"
            ),
        }
    raw = record["result"]
    # Validate the producer's scope instead of replacing it with our label.
    # Tree RSS belongs to the outer sampler, not bootstrap's stage receipt.
    expected_scopes = {
        key: value
        for key, value in STAGE2_METRIC_SCOPES.items()
        if key != "peak_tree_rss_bytes"
    }
    if raw.get("metric_scopes") != expected_scopes:
        raise StageABError("Stage2 metric scope mismatch")
    has_user = "compile_user_ms" in raw
    has_system = "compile_sys_ms" in raw
    if has_user != has_system:
        raise StageABError("incomplete Stage2 timed CPU metrics")
    # bootstrap deliberately omits both fields when timed child CPU is
    # unavailable. Keep that absence; zero is a real measured observation.
    cpu_s = None
    if has_user:
        cpu_s = (
            _measured_number(raw, "compile_user_ms") / 1000.0
            + _measured_number(raw, "compile_sys_ms") / 1000.0
        )
    return {
        "wall_s": _measured_number(raw, "wall_ms") / 1000.0,
        "compile_wall_s": _measured_number(raw, "compile_wall_ms") / 1000.0,
        "cpu_s": cpu_s,
        "peak_tree_rss_bytes": _measured_number(
            record["process"], "peak_tree_rss_bytes"
        ),
    }


def _complete_median(values: list[float | None]) -> float | None:
    # Do not silently shrink the requested pair population to its measured
    # subset when one arm lacks an observation.
    if any(value is None for value in values):
        return None
    return float(statistics.median(values))


def _measured_ratio(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None or baseline == 0:
        return None
    return candidate / baseline


def _process_failure_summary(process: dict[str, Any]) -> str:
    owner = process.get("largest_process_observed")
    if not isinstance(owner, dict):
        owner = {}
    manifests = owner.get("manifest_paths")
    if not isinstance(manifests, list):
        manifests = []
    return (
        "status="
        + str(process.get("status", "UNKNOWN"))
        + " returncode="
        + str(process.get("returncode", "UNKNOWN"))
        + " peak_tree_rss_bytes="
        + str(process.get("peak_tree_rss_bytes", 0))
        + " largest_pid="
        + str(owner.get("pid", 0))
        + " largest_rss_bytes="
        + str(owner.get("rss_bytes", 0))
        + " manifests="
        + ",".join(str(path) for path in manifests)
        + " command="
        + str(owner.get("command", ""))
    )


def _stage2_environment_overrides(
    *,
    pair_index: int,
    arm: str,
    self_backend_jobs: int,
    max_tree_rss_bytes: int,
) -> dict[str, str]:
    identity = "stage-ab-p" + str(pair_index) + "-" + arm
    return {
        "PCC_PY_FRONTEND_IR_CACHE": "0",
        "PCC_SELF_BACKEND_OBJECT_CACHE": "0",
        "PCC_PY_FRONTEND_IR_CACHE_IDENTITY": identity + "-frontend",
        "PCC_SELF_BACKEND_OBJECT_CACHE_IDENTITY": identity + "-object",
        # Native auto lanes serialize the measured high-risk source/AST family
        # before admitting the at-most-two-worker safe lane. A numeric value
        # of two skips that split and can run the largest compiler modules
        # together, which is not memory-equivalent.
        "PCC_BOOTSTRAP_PY_FRONTEND_JOBS": "auto",
        # The stage1 receipt records the HOST build's frontend width (which
        # may legitimately be wide, e.g. 10); replaying it verbatim into a
        # compiled stage trips bootstrap.py's fail-closed worker-budget guard.
        # The compiled stage derives its own width from the auto policy.
        "PCC_BOOTSTRAP_STAGE1_PY_FRONTEND_JOBS": "auto",
        "PCC_PY_FRONTEND_JOBS": "auto",
        "PCC_SELF_BACKEND_JOBS": str(self_backend_jobs),
        "PCC_MACHO_LINK_JOBS": "8",
        "PCC_BOOTSTRAP_EXTERNAL_MEMORY_GUARD": "1",
        # The native admission scheduler and the external watchdog must use
        # the same tree budget. Otherwise it can admit work the guard kills.
        "PCC_BOOTSTRAP_MAX_TREE_RSS_BYTES": str(max_tree_rss_bytes),
    }


# Fields that define "same external resource envelope".  Two stages compared
# for a Stage2/Stage1 ratio must match on ALL of these; peak_tree_rss and the
# admitted worker count are observations INSIDE the envelope, recorded but not
# required equal (the scheduler may admit fewer workers when live RSS is high).
_ENVELOPE_PARITY_KEYS = (
    "max_tree_rss_bytes",
    "cpu_count",
    "frontend_jobs",
    "self_backend_jobs",
    "macho_link_jobs",
    "gc_backend",
    "cache_policy",
)


def _cache_policy(environment: dict[str, str]) -> dict[str, str]:
    """The cold-cache knobs that must match across the two stages."""
    return {
        "PCC_PY_FRONTEND_IR_CACHE": str(environment.get("PCC_PY_FRONTEND_IR_CACHE", "")),
        "PCC_SELF_BACKEND_OBJECT_CACHE": str(
            environment.get("PCC_SELF_BACKEND_OBJECT_CACHE", "")
        ),
        "private_pycache": str(
            "PYTHONPYCACHEPREFIX" in environment
            and "PYTHONDONTWRITEBYTECODE" not in environment
        ),
    }


def _resource_envelope(
    *,
    args: argparse.Namespace,
    environment: dict[str, str],
    process: dict[str, Any],
) -> dict[str, Any]:
    """Bind the external resource envelope and the observations inside it.

    The envelope checks the requested cap/CPU/jobs/gc/cache contract, not
    equality of effective frontend policy. Record that policy separately:
    numeric host and native auto policies are intentionally different.
    ``observed_*`` records what happened inside the requested envelope.
    """
    return {
        "max_tree_rss_bytes": int(args.max_tree_rss_bytes),
        "cpu_count": int(os.cpu_count() or 0),
        "frontend_jobs": int(args.frontend_jobs),
        # Keep the v1 requested-width field for existing receipt consumers.
        # It is not the effective policy: native Stage2 intentionally uses
        # auto, while the host Stage1 command receives a numeric width.
        "frontend_jobs_scope": "requested_cli_width",
        "effective_frontend_policy": {
            key: str(environment.get(key, ""))
            for key in (
                "PCC_PY_FRONTEND_JOBS",
                "PCC_BOOTSTRAP_PY_FRONTEND_JOBS",
                "PCC_BOOTSTRAP_STAGE1_PY_FRONTEND_JOBS",
            )
        },
        "frontend_policy_parity": "not_checked",
        "self_backend_jobs": int(args.self_backend_jobs),
        "macho_link_jobs": int(environment.get("PCC_MACHO_LINK_JOBS", "0")),
        "gc_backend": int(args.gc_backend),
        "cache_policy": _cache_policy(environment),
        "observed_peak_tree_rss_bytes": int(process.get("peak_tree_rss_bytes", 0)),
        "observed_peak_process_count": int(process.get("peak_process_count", 0)),
        "sampler_status": str(process.get("status", "UNKNOWN")),
        "sampler_returncode": process.get("returncode"),
        "termination_reason": str(process.get("termination_reason", process.get("status", "UNKNOWN"))),
    }


def envelope_parity_key(envelope: dict[str, Any]) -> tuple:
    """The subset of the envelope that two same-resource stages must share."""
    return tuple(_canonical_sha256(envelope.get(key)) for key in _ENVELOPE_PARITY_KEYS)


def assert_stage_envelope_parity(arm_record: dict[str, Any], *, arm: str) -> None:
    """A Stage2/Stage1 ratio may only come from one shared external envelope.

    Both stages already run under the same ``--max-tree-rss-bytes`` here, but
    the earlier operational pair (host Stage1 resolved against ~48 GiB, capped
    Stage2) mixed caps.  This makes the parity machine-checked and fails closed
    if any parity key drifts.
    """
    stage1_env = arm_record["stage1"].get("resource_envelope")
    stage2_record = arm_record.get("stage2")
    if stage2_record is None:
        return
    stage2_env = stage2_record.get("resource_envelope")
    if stage1_env is None or stage2_env is None:
        raise StageABError(arm + " stage record is missing its resource envelope")
    if envelope_parity_key(stage1_env) != envelope_parity_key(stage2_env):
        drift = [
            key
            for key in _ENVELOPE_PARITY_KEYS
            if stage1_env.get(key) != stage2_env.get(key)
        ]
        raise StageABError(
            arm
            + " Stage1/Stage2 resource envelopes differ on "
            + ",".join(drift)
            + "; a Stage2/Stage1 ratio must not mix resource envelopes"
        )


def _compile_measurement(directory: Path, *, expected_command, expected_cwd,
                         expected_sampler_sha256: str, expected_cap: int,
                         expected_support_sha256: str | None = None,
                         expected_lock_sha256: str | None = None) -> dict[str, Any]:
    """Read durable compile-only evidence. Never substitute outer observations."""
    raw = _read_json(directory / "result.json")
    window = raw.get("target_window", {})
    artifacts = raw.get("artifacts", {})
    if not isinstance(window, dict) or not isinstance(artifacts, dict):
        raise StageABError("compile-window and artifact receipts must be objects")
    samples_path = directory / "samples.tsv"
    if (raw.get("schema") != "pcc.process_tree_sample.v1"
            or raw.get("status") != "COMPLETE" or type(raw.get("returncode")) is not int
            or raw.get("returncode") != 0
            or window.get("schema") != "pcc.compile-process-window.v1"
            or window.get("clean_exit") is not True
            or raw.get("post_exit_cleanup_pids")
            or window.get("worker_budget_scope") != "inherited_outer_tree"
            or window.get("sampler_sha256") != expected_sampler_sha256
            or (expected_support_sha256 is not None and
                window.get("support_tool_sha256") != expected_support_sha256)
            or (expected_lock_sha256 is not None and
                window.get("lock_tool_sha256") != expected_lock_sha256)
            or raw.get("max_tree_rss_bytes") != expected_cap
            or raw.get("interval_s") != 0.25
            or raw.get("cwd") != str(expected_cwd)
            or (expected_command is not None and raw.get("command") != expected_command)
            or artifacts.get("samples") != str(samples_path)):
        raise StageABError("invalid compile-window identity, scope or outcome")
    count = raw.get("sample_count")
    if type(count) is not int or count <= 0:
        raise StageABError("compile-window has no valid sample count")
    data = samples_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != window.get("samples_sha256"):
        raise StageABError("compile-window samples hash mismatch")
    try:
        rows = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise StageABError("compile-window samples are not UTF-8") from exc
    if (len(rows) != count + 1 or rows[0] !=
            "elapsed_s\ttree_rss_bytes\tprocess_count\tlargest_pid\tlargest_rss_bytes\tlargest_command"):
        raise StageABError("compile-window sample rows mismatch")
    peaks = []
    previous = -1.0
    origin = _measured_number(window, "samples_elapsed_origin_monotonic_s")
    start = _measured_number(window, "start_monotonic_s")
    end = _measured_number(window, "end_monotonic_s")
    for row in rows[1:]:
        fields = row.split("\t")
        if len(fields) != 6:
            raise StageABError("malformed compile-window sample")
        try:
            elapsed = float(fields[0])
            rss, processes = int(fields[1]), int(fields[2])
            largest_pid, largest_rss = int(fields[3]), int(fields[4])
        except ValueError as exc:
            raise StageABError("compile-window sample contains invalid numbers") from exc
        if (not math.isfinite(elapsed) or elapsed < previous or rss < 0 or processes < 0
                or largest_pid < 0 or largest_rss < 0 or largest_rss > rss
                or (processes == 0 and (rss != 0 or largest_pid != 0 or largest_rss != 0))
                or origin + elapsed < start - 1e-6 or origin + elapsed > end + 1e-6):
            raise StageABError("invalid compile-window sample")
        previous = elapsed
        peaks.append(rss)
    wall = _measured_number(window, "wall_s")
    start = _measured_number(window, "start_monotonic_s")
    end = _measured_number(window, "end_monotonic_s")
    peak = _measured_number(raw, "peak_tree_rss_bytes")
    if (wall <= 0 or end <= start or abs((end - start) - wall) > 1e-6
            or peak <= 0 or max(peaks) != peak
            or type(window.get("root_pid")) is not int or window["root_pid"] <= 0
            or not isinstance(window.get("root_start_identity"), list)
            or len(window["root_start_identity"]) != 2
            or any(type(value) is not int or value < 0 for value in window["root_start_identity"])
            or window.get("scope") != "launch_to_observed_owned_tree_exit_including_compile_wrappers"):
        raise StageABError("invalid compile-window timing, root or RSS")
    return {
        "schema": window["schema"], "scope": window["scope"],
        "wall_s": wall, "peak_tree_rss_bytes": peak,
        "sample_count": count, "interval_s": raw["interval_s"],
        "observed_sample_elapsed_s": [float(rows[1].split("\t")[0]), float(rows[-1].split("\t")[0])],
        "samples_elapsed_origin_monotonic_s": window.get("samples_elapsed_origin_monotonic_s"),
        "max_tree_rss_bytes": expected_cap, "command": raw["command"],
        "cwd": raw["cwd"], "root_pid": window["root_pid"],
        "root_start_identity": window["root_start_identity"],
        "samples": str(samples_path), "samples_sha256": window["samples_sha256"],
        "result": str(directory / "result.json"),
        "result_sha256": hashlib.sha256((directory / "result.json").read_bytes()).hexdigest(),
        "sampler_sha256": expected_sampler_sha256,
        "support_tool_sha256": window.get("support_tool_sha256"),
        "lock_tool_sha256": window.get("lock_tool_sha256"),
        "clean_exit": True,
    }


def summarize_compile_windows(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """Same-source Stage2/Stage1 observations, never a correctness acceptance."""
    ratios = {arm: {name: [] for name in ("wall_s", "peak_tree_rss_bytes")}
              for arm in ("baseline", "candidate")}
    available = bool(pairs)
    alternating = bool(pairs)
    for offset, pair in enumerate(pairs):
        index = pair.get("index")
        if (type(index) is not int or index < 1
                or tuple(pair.get("order", ())) != pair_order(index)
                or index != pairs[0].get("index", 0) + offset):
            alternating = False
        for arm in ratios:
            record = pair.get(arm, {})
            first, second = record.get("stage1", {}), record.get("stage2", {})
            left, right = first.get("compile_measurement"), second.get("compile_measurement")
            valid = bool(left and right)
            if valid:
                valid = (first.get("source_sha256") is not None
                         and first.get("source_sha256") == second.get("source_sha256")
                         and first.get("runtime_sha256") is not None
                         and first.get("runtime_sha256") == second.get("runtime_sha256")
                         and first.get("resource_envelope") is not None
                         and envelope_parity_key(first["resource_envelope"]) ==
                             envelope_parity_key(second.get("resource_envelope", {})))
                for key in ("schema", "scope", "interval_s", "max_tree_rss_bytes", "sampler_sha256", "support_tool_sha256", "lock_tool_sha256"):
                    valid = valid and left.get(key) is not None and left.get(key) == right.get(key)
                valid = valid and left.get("clean_exit") is True and right.get("clean_exit") is True
                for measurement in (left, right):
                    valid = (valid and measurement.get("schema") == "pcc.compile-process-window.v1"
                             and measurement.get("scope") == "launch_to_observed_owned_tree_exit_including_compile_wrappers"
                             and type(measurement.get("sample_count")) is int
                             and measurement["sample_count"] > 0
                             and measurement.get("interval_s") == 0.25)
            for name in ratios[arm]:
                ratio = None
                if valid:
                    try:
                        ratio = _measured_ratio(_measured_number(right, name), _measured_number(left, name))
                    except StageABError:
                        pass
                if ratio is None:
                    available = False
                ratios[arm][name].append(ratio)
    return {
        "availability": "AVAILABLE" if available else "UNAVAILABLE",
        "qualification": ("UNAVAILABLE" if not available else
                          "SCREENING_UNQUALIFIED" if len(pairs) < 5 or not alternating else
                          "MEASURED_NOT_CORRECTNESS_QUALIFIED"),
        "pair_count": len(pairs), "alternating_pairs": alternating,
        "ratios_by_arm": ratios,
        "median_stage2_over_stage1": {
            arm: {name: _complete_median(values) if values else None
                  for name, values in metrics.items()} for arm, metrics in ratios.items()},
        "automatic_acceptance": False,
        "remaining_gates": ["five_gc", "native_public_cli", "stage1_stage2_stage3_fixed_point"],
    }


def summarize_pairs(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    if not pairs:
        raise StageABError("cannot summarize empty stage pairs")
    stages = ["stage1"]
    if all("stage2" in pair["baseline"] and "stage2" in pair["candidate"] for pair in pairs):
        stages.append("stage2")
    summary: dict[str, Any] = {
        "comparison_contract": COMPARISON_CONTRACT,
        "verdict": "MEASURED_NO_AUTOMATIC_ACCEPTANCE",
        "stages": {},
        "same_source_stage2_over_stage1": summarize_compile_windows(pairs),
    }
    for stage in stages:
        by_arm: dict[str, list[dict[str, float | None]]] = {
            "baseline": [], "candidate": []
        }
        for pair in pairs:
            for arm in by_arm:
                by_arm[arm].append(_stage_metrics(pair[arm][stage], stage))
        metric_names = sorted(by_arm["baseline"][0])
        medians: dict[str, dict[str, float | None]] = {}
        for arm in by_arm:
            medians[arm] = {
                metric: _complete_median([row[metric] for row in by_arm[arm]])
                for metric in metric_names
            }
        paired_ratios: dict[str, list[float | None]] = {}
        for metric in metric_names:
            paired_ratios[metric] = [
                _measured_ratio(
                    _stage_metrics(pair["candidate"][stage], stage)[metric],
                    _stage_metrics(pair["baseline"][stage], stage)[metric],
                )
                for pair in pairs
            ]
        summary["stages"][stage] = {
            "medians": medians,
            "candidate_over_baseline": {
                metric: _measured_ratio(
                    medians["candidate"][metric], medians["baseline"][metric]
                )
                for metric in metric_names
            },
            "paired_candidate_over_baseline": paired_ratios,
            "paired_median_candidate_over_baseline": {
                metric: _complete_median(values)
                for metric, values in paired_ratios.items()
            },
        }
    return summary


def _sampler_args(
    sampler,
    *,
    prefix: Path,
    cwd: Path,
    timeout: int,
    max_tree_rss_bytes: int,
    command: list[str],
    context=None,
):
    if context is not None:
        timeout = context.timeout(timeout)
    return sampler._parser().parse_args(
        [
            "--result",
            str(prefix.with_suffix(".result.json")),
            "--samples",
            str(prefix.with_suffix(".samples.tsv")),
            "--stdout",
            str(prefix.with_suffix(".stdout")),
            "--stderr",
            str(prefix.with_suffix(".stderr")),
            "--cwd",
            str(cwd),
            "--timeout",
            str(timeout),
            "--interval",
            "0.25",
            "--progress-interval",
            "30",
            "--max-tree-rss-bytes",
            str(max_tree_rss_bytes),
            "--no-performance-lock",
            *(["--preserve-worker-budget"] if context is not None else []),
            "--",
            *command,
        ]
    )


def _assert_stage1_admission_budget(
    receipt: dict[str, Any], *, expected: int, arm: str
) -> None:
    selection = receipt.get("memory_budget_selection")
    environment = receipt.get("environment")
    if not isinstance(selection, dict) or not isinstance(environment, dict):
        raise StageABError(arm + " Stage1 is missing its admission budget receipt")
    selected = selection.get("max_tree_rss_bytes")
    if (
        type(selected) is not int
        or selected != expected
        or environment.get("PCC_WORKER_TREE_BUDGET_BYTES") != str(expected)
    ):
        raise StageABError(arm + " Stage1 admission budget differs from the external cap")


def _run_stage1(
    *,
    arm: str,
    source_root: Path,
    runtime_archive: Path,
    arm_root: Path,
    args: argparse.Namespace,
    ab,
    stage1,
    sampler,
) -> dict[str, Any]:
    context = _verify_inherited(args)
    output = arm_root / "stage1"
    command = [
        sys.executable,
        str(STAGE1_TOOL),
        "--arm",
        arm,
        "--source-root",
        str(source_root),
        "--runtime-archive",
        str(runtime_archive),
        "--output-dir",
        str(output),
        "--timeout",
        str(args.stage1_timeout),
        "--smoke-timeout",
        str(args.smoke_timeout),
        "--jobs",
        str(args.frontend_jobs),
        "--self-backend-jobs",
        str(args.self_backend_jobs),
        "--memory-budget-bytes",
        str(args.max_tree_rss_bytes),
        "--gc-backend",
        str(args.gc_backend),
        "--with-threads",
        "1",
        "--direct-indexed-emit",
        "--no-performance-lock",
    ]
    if getattr(args, "measure_compile_window", False):
        command.append("--measure-compile-window")
    process = sampler.run(
        _sampler_args(
            sampler,
            prefix=arm_root / "stage1-process",
            cwd=REPO_ROOT,
            timeout=args.stage1_timeout + args.smoke_timeout * 3 + 30,
            max_tree_rss_bytes=args.max_tree_rss_bytes,
            command=command,
            context=context,
        )
    )
    _verify_inherited(args)
    if process.get("status") != "COMPLETE" or process.get("returncode") != 0:
        raise StageABError(arm + " Stage1 did not complete")
    result = _read_json(output / "stage1-result.json")
    receipt = _read_json(output / "build-receipt.json")
    _assert_stage1_admission_budget(
        receipt, expected=args.max_tree_rss_bytes, arm=arm
    )
    if result.get("metric_scopes") != stage1.STAGE1_METRIC_SCOPES:
        raise StageABError(arm + " Stage1 metric scope mismatch")
    if result.get("comparison_contract") != stage1.STAGE1_COMPARISON_CONTRACT:
        raise StageABError(arm + " Stage1 comparison contract mismatch")
    pycache_root = Path(receipt["environment"]["PYTHONPYCACHEPREFIX"])
    pycache_files = sum(1 for path in pycache_root.rglob("*.pyc") if path.is_file())
    if pycache_files <= 0 or "PYTHONDONTWRITEBYTECODE" in receipt["environment"]:
        raise StageABError(arm + " Stage1 private pycache contract failed")
    source_snapshot = output / str(receipt["source_snapshot"])
    normalized_environment = normalize_arm_environment(
        {str(key): str(value) for key, value in receipt["environment"].items()},
        output_root=output,
        source_root=source_snapshot,
    )
    compile_measurement = None
    if getattr(args, "measure_compile_window", False):
        compile_measurement = _compile_measurement(
            output / "compile-process", expected_command=receipt["command"],
            expected_cwd=source_snapshot,
            expected_sampler_sha256=ab.sha256_path(PROCESS_SAMPLER),
            expected_support_sha256=ab.sha256_path(COMPILE_AB_TOOL),
            expected_lock_sha256=ab.sha256_path(REPO_ROOT / "scripts/file_lock.py"),
            expected_cap=args.max_tree_rss_bytes,
        )
    return {
        "compile_measurement": compile_measurement,
        "host_runtime_sha256": receipt.get("host_python_runtime_sha256"),
        "source_sha256": receipt["bootstrap_source_sha256"],
        "runtime_sha256": receipt["runtime_archive_sha256"],
        "result_path": str(output / "stage1-result.json"),
        "receipt_path": str(output / "build-receipt.json"),
        "result": result,
        "process": process,
        "process_result_path": str(arm_root / "stage1-process.result.json"),
        "pycache_files": pycache_files,
        "normalized_environment": normalized_environment,
        "normalized_environment_sha256": _canonical_sha256(normalized_environment),
        "source_snapshot": str(source_snapshot),
        "runtime_archive": str(output / "runtime-bundle" / "libpy_runtime_pcc_py.a"),
        "compiler": str(output / "pcc1"),
        "resource_envelope": _resource_envelope(
            args=args,
            environment={
                str(key): str(value)
                for key, value in receipt["environment"].items()
            },
            process=process,
        ),
    }


def _run_stage2(
    *,
    arm: str,
    pair_index: int,
    stage1_record: dict[str, Any],
    arm_root: Path,
    args: argparse.Namespace,
    ab,
    sampler,
) -> dict[str, Any]:
    context = _verify_inherited(args)
    output = arm_root / "stage2"
    output.mkdir()
    compiler = output / "pcc1"
    shutil.copy2(stage1_record["compiler"], compiler)
    compiler.chmod(0o755)
    native_input = None
    if context is not None:
        native_input = ab._path_receipt(compiler)
        if native_input["sha256"] != stage1_record["result"]["compiler_sha256"]:
            raise StageABError("Stage2 launch compiler differs from Stage1 output")
        context.result_files.append(native_input)
        context.verify()
    source_root = Path(stage1_record["source_snapshot"])
    receipt = _read_json(Path(stage1_record["receipt_path"]))
    environment = {str(key): str(value) for key, value in receipt["environment"].items()}
    profile = output / "profile"
    environment.update({"PCC_BOOTSTRAP_PROFILE_DIR": str(profile)})
    environment.update(
        _stage2_environment_overrides(
            pair_index=pair_index,
            arm=arm,
            self_backend_jobs=args.self_backend_jobs,
            max_tree_rss_bytes=args.max_tree_rss_bytes,
        )
    )
    command = [
        sys.executable,
        str(source_root / "scripts" / "bootstrap.py"),
        "--out-dir",
        str(output),
        "--backend",
        "self",
        "--from-stage",
        "2",
        "--stage",
        "2",
        "--reuse-stage1",
    ]
    if getattr(args, "measure_compile_window", False):
        environment["PCC_BOOTSTRAP_MEASURE_COMPILE_WINDOW"] = "1"
        environment["PCC_BOOTSTRAP_STAGE_TIMEOUT"] = str(args.stage2_timeout)
    original_environment = os.environ.copy()
    os.environ.clear()
    os.environ.update(environment)
    try:
        process = sampler.run(
            _sampler_args(
                sampler,
                prefix=arm_root / "stage2-process",
                cwd=source_root,
                timeout=args.stage2_timeout,
                max_tree_rss_bytes=args.max_tree_rss_bytes,
                command=command,
                context=context,
            )
        )
    finally:
        os.environ.clear()
        os.environ.update(original_environment)
    _verify_inherited(args)
    if process.get("status") != "COMPLETE" or process.get("returncode") != 0:
        raise StageABError(
            arm + " Stage2 did not complete: " + _process_failure_summary(process)
        )
    if native_input is not None:
        ab._verify_receipt(native_input, "Stage2 actual launch compiler")
    result = _read_json(profile / "stage2.result.json")
    _stage_metrics({"result": result, "process": process}, "stage2")
    pcc2 = output / "pcc2"
    if result.get("returncode") != 0 or not pcc2.is_file() or not os.access(pcc2, os.X_OK):
        raise StageABError(arm + " Stage2 produced no executable pcc2")
    help_result = ab._run_process(
        [str(pcc2), "--help"],
        timeout=args.smoke_timeout,
        env=environment,
        cwd=output,
    )
    if help_result.returncode != 0:
        raise StageABError(arm + " Stage2 pcc2 --help failed")
    linkage = ab._linkage(
        pcc2,
        timeout=args.smoke_timeout,
        env=environment,
        cwd=output,
    )
    compile_measurement = None
    if getattr(args, "measure_compile_window", False):
        compile_measurement = _compile_measurement(
            output / "stage2.compile-process", expected_command=None,
            expected_cwd=source_root,
            expected_sampler_sha256=ab.sha256_path(PROCESS_SAMPLER),
            expected_support_sha256=ab.sha256_path(COMPILE_AB_TOOL),
            expected_lock_sha256=ab.sha256_path(REPO_ROOT / "scripts/file_lock.py"),
            expected_cap=args.max_tree_rss_bytes,
        )
        expected_target = [str(compiler), "--profile-json", str(profile / "stage2.json"),
                           "--backend", "self", str(source_root / "pcc/__main__.py"),
                           "-o", str(pcc2)]
        codegen_plan = (str(pcc2) + ".pcc-codegen-plan"
                        if (environment.get("PCC_BOOTSTRAP_DEFER_FRONTEND_CODEGEN") or "1") == "1" else "")
        link_plan = (str(pcc2) + ".pcc-link-plan"
                     if (environment.get("PCC_BOOTSTRAP_DEFER_SELF_LINK") or "1") == "1" else "")
        if codegen_plan or link_plan:
            expected_target = [sys.executable, str(source_root / "scripts/run_pcc_native_deferred.py"),
                               str(compiler), codegen_plan, link_plan, "--", *expected_target]
        if compile_measurement["command"] != expected_target:
            raise StageABError("Stage2 compile-window target differs from requested compile")
    return {
        "compile_measurement": compile_measurement,
        "host_runtime_sha256": receipt.get("host_python_runtime_sha256"),
        "source_sha256": receipt["bootstrap_source_sha256"],
        "runtime_sha256": receipt["runtime_archive_sha256"],
        "result_path": str(profile / "stage2.result.json"),
        "result": result,
        "metric_scopes": STAGE2_METRIC_SCOPES,
        "comparison_contract": COMPARISON_CONTRACT,
        "process": process,
        "process_result_path": str(arm_root / "stage2-process.result.json"),
        "native_input_compiler": native_input,
        "compiler": str(pcc2),
        "compiler_sha256": ab.sha256_path(pcc2),
        "linkage": linkage,
        "resource_envelope": _resource_envelope(
            args=args,
            environment=environment,
            process=process,
        ),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Standalone observation route; not inherited campaign qualification."""
    if hasattr(args, "_inherited_stage_context"):
        raise StageABError("use run_guarded for inherited stage execution")
    return _run(args, ab=_load_module(COMPILE_AB_TOOL, "pcc_stage_ab_compile"))


def _run(args: argparse.Namespace, *, ab) -> dict[str, Any]:
    _validate_resource_limits(args)
    context = _verify_inherited(args)
    output = Path(args.output_dir).expanduser().absolute()
    if output.exists():
        raise StageABError("refusing existing output directory: " + str(output))
    output.mkdir(parents=True)
    manifest_path = output / "manifest.json"
    if context is not None:
        context.manifest_path = manifest_path
    manifest = {"schema": "pcc.bootstrap-stage-ab.v1", "status": "PREPARING",
                "execution_scope": "inherited_campaign" if context else "standalone",
                "pairs": []}
    _persist(manifest_path, manifest)
    try:
        return _run_prepared(args, ab=ab, context=context, output=output,
                             manifest_path=manifest_path)
    except BaseException as exc:
        # Preparation can fail before the fuller manifest exists. Preserve any
        # already-checkpointed pairs instead of overwriting child evidence.
        # Receipt reads, writes and diagnostics are all best effort: none may
        # replace the original preparation/stage failure or interruption.
        try:
            if manifest_path.is_file():
                manifest = _read_json(manifest_path)
            manifest.update(status="INTERRUPTED" if isinstance(exc, (KeyboardInterrupt, SystemExit))
                            else "FAILED", failure={"exception_type": type(exc).__name__,
                                                   "message": str(exc)})
            manifest.pop("summary", None)
            manifest.pop("completed_at_utc", None)
            _persist(manifest_path, manifest)
        except Exception as receipt_error:
            try:
                print("stage A/B warning: could not persist failure receipt: "
                      + str(receipt_error), file=sys.stderr)
            except Exception:
                pass
        raise


def _run_prepared(args, *, ab, context, output, manifest_path):
    _verify_inherited(args)
    stage1 = _load_module(STAGE1_TOOL, "pcc_stage_ab_stage1")
    sampler = _load_module(PROCESS_SAMPLER, "pcc_stage_ab_sampler")
    baseline_source = Path(args.baseline_source).expanduser().resolve(strict=True)
    candidate_source = Path(args.candidate_source).expanduser().resolve(strict=True)
    runtime_archive = Path(args.runtime_archive).expanduser().resolve(strict=True)
    if getattr(args, "measure_compile_window", False):
        for source in (baseline_source, candidate_source):
            bootstrap_text = (source / "scripts/bootstrap.py").read_text(encoding="utf-8")
            if ('COMPILE_WINDOW_SCHEMA = "pcc.compile-process-window.v1"' not in bootstrap_text
                    or ab.sha256_path(source / "scripts/run_process_tree_sample.py") != ab.sha256_path(PROCESS_SAMPLER)
                    or ab.sha256_path(source / "scripts/run_pcc_compile_ab.py") != ab.sha256_path(COMPILE_AB_TOOL)
                    or ab.sha256_path(source / "scripts/file_lock.py") != ab.sha256_path(REPO_ROOT / "scripts/file_lock.py")):
                raise StageABError("source snapshot does not support this compile-window producer identity")
    stage1._validate_source_root(str(baseline_source), ab)
    stage1._validate_source_root(str(candidate_source), ab)
    stage1._require_immutable_source(baseline_source, ab)
    stage1._require_immutable_source(candidate_source, ab)
    source_manifests = {
        "baseline": stage1.source_manifest(baseline_source, ab),
        "candidate": stage1.source_manifest(candidate_source, ab),
    }
    _verify_inherited(args)
    manifest: dict[str, Any] = {
        "schema": "pcc.bootstrap-stage-ab.v1",
        "status": "RUNNING",
        "execution_scope": "inherited_campaign" if context else "standalone",
        "inherited_worker_budget": context.budget.receipt if context else None,
        "producer_tools": context.tools if context else None,
        "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "comparison_contract": COMPARISON_CONTRACT,
        "metric_scopes": {
            "stage1": stage1.STAGE1_METRIC_SCOPES,
            "stage2": STAGE2_METRIC_SCOPES,
        },
        "sources": source_manifests,
        "runtime_archive": str(runtime_archive),
        "runtime_archive_sha256": ab.sha256_path(runtime_archive),
        "requested_stages": args.stages,
        "resource_limits": {
            "stage1_timeout_s": args.stage1_timeout,
            "stage2_timeout_s": args.stage2_timeout,
            "smoke_timeout_s": args.smoke_timeout,
            "max_tree_rss_bytes": args.max_tree_rss_bytes,
            "frontend_jobs": args.frontend_jobs,
            "self_backend_jobs": args.self_backend_jobs,
            "gc_backend": args.gc_backend,
        },
        "pairs": [],
    }
    _persist(manifest_path, manifest)
    source_by_arm = {"baseline": baseline_source, "candidate": candidate_source}
    starting_runtime_hash = manifest["runtime_archive_sha256"]
    if context is not None:
        context.source_manifest = stage1.source_manifest
        context.require_immutable_source = stage1._require_immutable_source
        context.sources = [(source_by_arm[arm], source_manifests[arm], "original")
                           for arm in source_by_arm]
        context.result_files = [ab._path_receipt(runtime_archive)]
        context.builds = []
    try:
        with nullcontext() if context else ab._performance_lock():
            _verify_inherited(args)
            first_pair_index = args.first_pair_index
            for pair_index in range(first_pair_index, first_pair_index + args.pairs):
                pair_root = output / ("pair-" + str(pair_index))
                pair_root.mkdir()
                pair: dict[str, Any] = {"index": pair_index, "order": list(pair_order(pair_index))}
                for arm in pair["order"]:
                    pair[arm] = {
                        "stage_status": {
                            "stage1": "NOT_RUN",
                            "stage2": (
                                "NOT_RUN" if args.stages == "stage1-stage2"
                                else "NOT_REQUESTED"
                            ),
                        },
                    }
                manifest["active_pair"] = pair
                _persist(manifest_path, manifest)
                for arm in pair["order"]:
                    arm_root = pair_root / arm
                    arm_root.mkdir()
                    arm_record = pair[arm]
                    arm_record["stage_status"]["stage1"] = "RUNNING"
                    _persist(manifest_path, manifest)
                    print(
                        "[stage-ab] pair=" + str(pair_index) + " arm=" + arm + " stage1 begin",
                        flush=True,
                    )
                    _verify_inherited(args)
                    stage1_record = _run_stage1(
                        arm=arm,
                        source_root=source_by_arm[arm],
                        runtime_archive=runtime_archive,
                        arm_root=arm_root,
                        args=args,
                        ab=ab,
                        stage1=stage1,
                        sampler=sampler,
                    )
                    _verify_inherited(args)
                    if context is not None:
                        context.current_arm = arm
                        _seal_stage_record(context, stage1_record,
                                           source_manifest=source_manifests[arm])
                    # Retain the admitted Stage1 even if Stage2 never returns.
                    arm_record["stage1"] = stage1_record
                    arm_record["stage_status"]["stage1"] = "COMPLETE"
                    _persist(manifest_path, manifest)
                    if args.stages == "stage1-stage2":
                        arm_record["stage_status"]["stage2"] = "RUNNING"
                        _persist(manifest_path, manifest)
                        print(
                            "[stage-ab] pair=" + str(pair_index) + " arm=" + arm + " stage2 begin",
                            flush=True,
                        )
                        arm_record["stage2"] = _run_stage2(
                            arm=arm,
                            pair_index=pair_index,
                            stage1_record=stage1_record,
                            arm_root=arm_root,
                            args=args,
                            ab=ab,
                            sampler=sampler,
                        )
                        if context is not None:
                            _seal_stage_record(context, arm_record["stage2"])
                        arm_record["stage_status"]["stage2"] = "COMPLETE"
                        _persist(manifest_path, manifest)
                    _verify_inherited(args)
                    assert_stage_envelope_parity(arm_record, arm=arm)
                    manifest["active_pair"] = pair
                    _persist(manifest_path, manifest)
                manifest["pairs"].append(pair)
                manifest.pop("active_pair", None)
                _persist(manifest_path, manifest)
                if (
                    pair["baseline"]["stage1"]["normalized_environment_sha256"]
                    != pair["candidate"]["stage1"]["normalized_environment_sha256"]
                ):
                    raise StageABError("paired Stage1 environments differ after normalization")
            _verify_inherited(args)
            if ab.sha256_path(runtime_archive) != starting_runtime_hash:
                raise StageABError("runtime archive changed during stage A/B")
            ending_sources = {
                arm: stage1.source_manifest(source_by_arm[arm], ab)
                for arm in source_by_arm
            }
            if ending_sources != source_manifests:
                raise StageABError("source snapshot changed during stage A/B")
        _verify_inherited(args)
        manifest["summary"] = summarize_pairs(manifest["pairs"])
        _verify_inherited(args)
        if context is not None:
            for receipt in context.result_files:
                ab._verify_receipt(receipt, "completed stage artifact")
                context.verify()
            _verify_stage_closure(context)
            _verify_stage_sources(context)
        manifest["status"] = "COMPLETE"
        manifest["completed_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        _persist(manifest_path, manifest)
        if context is not None:
            manifest_path.chmod(0o444)
            context.result_files.append(ab._path_receipt(manifest_path))
        _verify_inherited(args)
    except BaseException as exc:
        # Preserve the original failure, including interruption. A process kill
        # cannot run this handler; its last durable checkpoint stays RUNNING.
        status = (
            "INTERRUPTED" if isinstance(exc, (KeyboardInterrupt, SystemExit))
            else "FAILED"
        )
        active_pair = manifest.get("active_pair", {})
        for arm in ("baseline", "candidate"):
            for stage, state in active_pair.get(arm, {}).get("stage_status", {}).items():
                if state == "RUNNING":
                    active_pair[arm]["stage_status"][stage] = status
        manifest["status"] = status
        manifest["failure"] = {
            "exception_type": type(exc).__name__,
            "message": str(exc),
        }
        manifest["finished_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        # A summary or final-write failure must not leave a success claim in
        # the failure receipt. Stage artifacts and completed pairs stay intact.
        manifest.pop("summary", None)
        manifest.pop("completed_at_utc", None)
        try:
            _persist(manifest_path, manifest)
        except Exception as receipt_error:
            try:
                print(
                    "stage A/B warning: could not persist failure receipt: "
                    + str(receipt_error),
                    file=sys.stderr,
                )
            except Exception:
                # Storage and stderr can fail together. Neither may replace
                # the stage exception that determines the caller's outcome.
                pass
        raise
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", required=True)
    parser.add_argument("--candidate-source", required=True)
    parser.add_argument("--runtime-archive", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--measure-compile-window", action="store_true")
    parser.add_argument("--pairs", type=int, default=2)
    parser.add_argument("--first-pair-index", type=int, default=1)
    parser.add_argument(
        "--stages",
        choices=("stage1", "stage1-stage2"),
        default="stage1-stage2",
    )
    parser.add_argument("--stage1-timeout", type=int, default=360)
    parser.add_argument("--stage2-timeout", type=int, default=540)
    parser.add_argument("--smoke-timeout", type=int, default=60)
    parser.add_argument("--frontend-jobs", type=int, default=2)
    parser.add_argument("--self-backend-jobs", type=int, default=2)
    parser.add_argument(
        "--max-tree-rss-bytes",
        type=int,
        default=DEFAULT_MAX_TREE_RSS_BYTES,
    )
    parser.add_argument("--gc-backend", type=int, choices=range(5), default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.pairs <= 0 or args.first_pair_index <= 0:
        print(
            "stage A/B error: --pairs and --first-pair-index must be positive",
            file=sys.stderr,
        )
        return 2
    try:
        manifest = run(args)
    except (OSError, ValueError, KeyError, StageABError, RuntimeError) as exc:
        # RuntimeError covers the dynamically-loaded tools' CompileABError /
        # Stage1BuildError subclasses, which run() re-raises verbatim after
        # checkpointing failure; KeyError covers a missing receipt field.
        print("stage A/B error: " + str(exc), file=sys.stderr)
        return 1
    print(
        "[stage-ab] complete pairs=" + str(len(manifest["pairs"])),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
