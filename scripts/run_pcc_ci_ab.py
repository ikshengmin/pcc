#!/usr/bin/env python3
"""CI-only, qualification-gated compiler A/B under one admitted tree budget.

This is a compile-slice orchestration owner, not a replacement for the original
correctness, five-GC or fixed-point owners, nor a Stage2 <= Stage1 measurement.
It never applies a patch, downloads sources, builds a runtime, or runs a shell.
Both complete read-only source trees and a qualified runtime are explicit inputs.
Input expected_output is complete stdout and must end in a newline, matching
the existing compile harness; it is never whitespace-normalized.
All preparation, two strict Stage1 builds, six compile pairs and baseline A/A
controls share one absolute job deadline and one sampled process-tree guard.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import signal
import stat
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
# CI deliberately avoids installing the project/build hook. Existing runtime
# receipt verification imports its owned helpers from this exact checkout.
if str(REPO_ROOT) in sys.path:
    sys.path.remove(str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT))
SCHEMA = "pcc.ci-compile-ab.v1"
CEILING = 4 * 1024 ** 3
FLOOR = 2 * 1024 ** 3
RESERVE = 512 * 1024 ** 2
JOB_SECONDS = 200 * 60
STAGE1_SECONDS = 7200
BUILD_FRONTEND_JOBS = 2
MEASURE_FRONTEND_JOBS = 1
SELF_BACKEND_JOBS = 2
TOOLS = (
    "scripts/run_pcc_ci_ab.py", "scripts/ci_pcc_ab_gate.py",
    "scripts/run_pcc_stage1_build.py", "scripts/run_pcc_compile_ab.py",
    "scripts/run_process_tree_sample.py", "scripts/file_lock.py",
    "pcc/tools/runtime_archive_provenance.py",
)
_MODULES = {}
NOT_PROVEN = [
    "Stage2 <= Stage1 wall time or process-tree memory",
    "new Stage1 -> Stage2 -> Stage3 fixed point",
    "new five-GC production qualification",
]


class CampaignError(RuntimeError):
    pass


def _load(name):
    if name in _MODULES:
        return _MODULES[name]
    path = REPO_ROOT / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CampaignError("missing campaign tool: " + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _MODULES[name] = module
    return module


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _persist(path, payload):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True,
                                    allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _json(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CampaignError("expected JSON object: " + str(path))
    return value


def _safe_path(raw, *, exists=True):
    if not isinstance(raw, str) or not raw:
        raise CampaignError("missing canonical absolute path")
    path = Path(raw)
    if not path.is_absolute() or str(path) != raw or ".." in path.parts:
        raise CampaignError("unsafe path: " + raw)
    if path.resolve(strict=exists) != path or path.is_symlink():
        raise CampaignError("noncanonical or symlink path: " + raw)
    return path


def _file(row, *, readonly=True):
    if not isinstance(row, dict):
        raise CampaignError("invalid file receipt")
    path = _safe_path(row.get("path"))
    mode = path.lstat()
    if (not stat.S_ISREG(mode.st_mode) or mode.st_nlink != 1
            or (readonly and mode.st_mode & 0o222)):
        raise CampaignError("file must be private, regular and read-only: " + str(path))
    if not isinstance(row.get("sha256"), str) or _sha(path) != row["sha256"]:
        raise CampaignError("file digest mismatch: " + str(path))
    return path


def _tree(root, *, readonly=True):
    """Full-tree identity; no skip patterns which could hide a second variable."""
    root = _safe_path(str(root))
    if not root.is_dir():
        raise CampaignError("source root must be a directory")
    files = {}
    for path in [root, *sorted(root.rglob("*"))]:
        item = path.lstat()
        if path.is_symlink() or not (stat.S_ISREG(item.st_mode) or stat.S_ISDIR(item.st_mode)):
            raise CampaignError("symlink or special entry: " + str(path))
        if readonly and item.st_mode & 0o222:
            raise CampaignError("input tree must be read-only: " + str(path))
        if path != root and any(part in {".git", "__pycache__", ".pytest_cache"}
                                for part in path.relative_to(root).parts):
            raise CampaignError("input must be an exported source tree: " + str(path))
        if stat.S_ISREG(item.st_mode):
            if item.st_nlink != 1:
                raise CampaignError("hard-linked input: " + str(path))
            files[path.relative_to(root).as_posix()] = {
                "sha256": _sha(path), "size_bytes": item.st_size,
                "executable": bool(item.st_mode & 0o111),
            }
    if not files:
        raise CampaignError("empty input tree")
    return files


def _seal(root):
    # Only call on directories exclusively created or renamed by this run.
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise CampaignError("cannot seal a symlink: " + str(path))
        if path.is_dir():
            path.chmod(0o555)
        elif path.is_file():
            path.chmod(0o555 if path.stat().st_mode & 0o111 else 0o444)
        else:
            raise CampaignError("cannot seal a special file: " + str(path))
    root.chmod(0o555)


def _copy_tree(source, target, expected):
    if target.exists() or target.is_symlink():
        raise CampaignError("refusing existing owned source alias: " + str(target))
    if _tree(source) != expected:
        raise CampaignError("source changed before copy")
    shutil.copytree(source, target, symlinks=True)
    _seal(target)
    if _tree(source) != expected or _tree(target) != expected:
        raise CampaignError("source changed while being copied")


def _remaining(deadline, ceiling):
    remaining = min(float(ceiling), deadline - time.monotonic())
    if not math.isfinite(remaining) or remaining < 1:
        raise TimeoutError("campaign absolute deadline exhausted")
    return remaining


def _disjoint(paths):
    for index, left in enumerate(paths):
        for right in paths[index + 1:]:
            if left == right or left in right.parents or right in left.parents:
                raise CampaignError("input/output roots overlap: " + str(left) + " / " + str(right))


def _verify_live_source(candidate_files):
    """Bind live imports used by BOTH this owner and the Stage1 CLI.

    The fresh CI job disables bytecode writes at interpreter startup. Reject
    cached bytecode, rather than assuming a source-name match proves its code.
    """
    expected = {name: row for name, row in candidate_files.items()
                if name.startswith(("pcc/", "scripts/"))}
    actual = {}
    for prefix in ("pcc", "scripts"):
        root = REPO_ROOT / prefix
        _safe_path(str(root))
        for path in sorted(root.rglob("*")):
            item = path.lstat()
            if path.is_symlink() or not (stat.S_ISREG(item.st_mode) or stat.S_ISDIR(item.st_mode)):
                raise CampaignError("unsafe live helper entry: " + str(path))
            name = path.relative_to(REPO_ROOT).as_posix()
            if "__pycache__" in path.relative_to(root).parts or path.suffix == ".pyc":
                if path.is_file():
                    raise CampaignError("cached live helper bytecode is not admitted: " + name)
                continue
            if path.is_file():
                actual[name] = {"sha256": _sha(path), "size_bytes": item.st_size,
                                "executable": bool(item.st_mode & 0o111)}
    if actual != expected:
        raise CampaignError("live helper/source checkout differs from the qualified candidate")


def _verify_live_import_origins(candidate_files):
    for name, module in list(sys.modules.items()):
        if not (name in {"pcc", "scripts"} or name.startswith(("pcc.", "scripts."))):
            continue
        filename = getattr(module, "__file__", None)
        if filename is not None:
            path = _safe_path(str(filename))
            try:
                relative = path.relative_to(REPO_ROOT).as_posix()
            except ValueError as exc:
                raise CampaignError("foreign helper module already imported: " + name) from exc
            if relative not in candidate_files or _sha(path) != candidate_files[relative]["sha256"]:
                raise CampaignError("helper module origin is not qualified: " + name)
        elif name != "scripts":
            raise CampaignError("unbound helper module: " + name)
        package_paths = list(getattr(module, "__path__", ()))
        if filename is None and not package_paths:
            raise CampaignError("unbound helper package: " + name)
        for directory in package_paths:
            path = _safe_path(str(directory))
            if path != REPO_ROOT.joinpath(*name.split(".")):
                raise CampaignError("helper package search path escapes checkout: " + name)


def _validate_spec(spec, output, ab, *, prepared_root=None):
    required = {"schema", "target_sha", "baseline_source", "candidate_source",
                "runtime_archive", "runtime_files", "source_diff", "allowed_changed_source",
                "warmup", "inputs", "qualification"}
    if set(spec) != required or spec.get("schema") != SCHEMA:
        raise CampaignError("campaign specification fields/schema mismatch")
    if (spec["target_sha"] != os.environ.get("GITHUB_SHA")
            or len(spec["target_sha"]) != 40
            or any(c not in "0123456789abcdef" for c in spec["target_sha"])):
        raise CampaignError("candidate target must be exact GitHub workflow SHA")
    roots, trees = {}, {}
    for arm in ("baseline", "candidate"):
        row = spec[arm + "_source"]
        roots[arm] = _safe_path(row["path"])
        trees[arm] = _tree(roots[arm])
        if _identity(trees[arm]) != row["sha256"]:
            raise CampaignError(arm + " full source identity mismatch")
        for name in TOOLS:
            if name not in trees[arm] or trees[arm][name]["sha256"] != _sha(REPO_ROOT / name):
                raise CampaignError(arm + " validation/producer tool differs: " + name)
    _disjoint([roots["baseline"], roots["candidate"], REPO_ROOT])
    for root in roots.values():
        if output == root or output in root.parents or root in output.parents:
            if prepared_root is None or prepared_root not in root.parents:
                raise CampaignError("source overlaps campaign-owned output")
    changed = sorted(name for name in set(trees["baseline"]) | set(trees["candidate"])
                     if trees["baseline"].get(name) != trees["candidate"].get(name))
    variable = spec["allowed_changed_source"]
    if variable not in ab.COMPILER_VARIABLE_SOURCES or changed != [variable]:
        raise CampaignError("full source trees must differ in exactly one admitted compiler source")
    source_diff = _file(spec["source_diff"])
    expected_diff = "".join(difflib.unified_diff(
        (roots["baseline"] / variable).read_text(encoding="utf-8").splitlines(keepends=True),
        (roots["candidate"] / variable).read_text(encoding="utf-8").splitlines(keepends=True),
        fromfile="baseline/" + variable, tofile="candidate/" + variable, lineterm="\n"))
    if not expected_diff or source_diff.read_text(encoding="utf-8") != expected_diff:
        raise CampaignError("source diff must be the exact canonical compiler-only diff")
    runtime = _file(spec["runtime_archive"])
    expected_names = {runtime.name} | {
        runtime.name + suffix for suffix in ab.RUNTIME_SIDECAR_SUFFIXES
        if suffix != ".target" or Path(str(runtime) + suffix).exists()
    }
    if not isinstance(spec["runtime_files"], dict) or set(spec["runtime_files"]) != expected_names:
        raise CampaignError("runtime sidecar inventory mismatch")
    for name, digest in spec["runtime_files"].items():
        _file({"path": str(runtime.parent / name), "sha256": digest})
    if spec["runtime_files"][runtime.name] != spec["runtime_archive"]["sha256"]:
        raise CampaignError("runtime archive identity is inconsistent")
    warmup = _file(spec["warmup"])
    rows = spec["inputs"]
    if not isinstance(rows, list) or len(rows) != 6:
        raise CampaignError("exactly six distinct compile pairs are required")
    inputs = []
    for row in rows:
        if (set(row) != {"path", "sha256", "expected_output"}
                or not isinstance(row["expected_output"], str)
                or not row["expected_output"].endswith("\n") or "\x00" in row["expected_output"]):
            raise CampaignError("invalid input/expected output receipt")
        inputs.append(_file(row))
    if len({row["sha256"] for row in rows}) != 6:
        raise CampaignError("all six measured contents must be distinct")
    qualification = _file(spec["qualification"])
    for path in [runtime, warmup, source_diff, qualification, *inputs]:
        if output == path or output in path.parents:
            if prepared_root is None or prepared_root not in path.parents:
                raise CampaignError("caller input overlaps campaign output")
    _verify_live_source(trees["candidate"])
    return roots, trees


def _qualification(spec, trees, deadline):
    # This verifier owns original producer schemas and receipt provenance. A
    # caller-provided PASS string, Stage1 receipt or A/B result cannot qualify.
    return _load("ci_pcc_ab_gate").verify_campaign(
        spec, baseline_files=trees["baseline"], candidate_files=trees["candidate"],
        deadline_monotonic=deadline,
    )


def _verify_frozen_qualification(proof):
    _load("ci_pcc_ab_gate").verify_frozen_campaign(proof)


def _freeze_inputs(spec, output):
    root = output / "frozen-inputs"
    root.mkdir()
    frozen = json.loads(json.dumps(spec))
    for key in ("warmup", "source_diff"):
        source = _file(spec[key])
        destination = root / key
        shutil.copy2(source, destination)
        frozen[key]["path"] = str(destination)
        _file(frozen[key])
    for index, row in enumerate(spec["inputs"]):
        destination = root / ("pair" + str(index + 1) + ".py")
        shutil.copy2(_file(row), destination)
        frozen["inputs"][index]["path"] = str(destination)
        _file(frozen["inputs"][index])
    runtime_root = output / "frozen-runtime"
    runtime_root.mkdir()
    archive = _file(spec["runtime_archive"])
    for name, digest in spec["runtime_files"].items():
        destination = runtime_root / name
        shutil.copy2(_file({"path": str(archive.parent / name), "sha256": digest}), destination)
        _file({"path": str(destination), "sha256": digest})
    frozen["runtime_archive"]["path"] = str(runtime_root / archive.name)
    _seal(root)
    # Runtime files are read-only. Its directory stays writable solely for the
    # existing runtime snapshot lock, which is acquired/released by the tool.
    return frozen


def _canonical_paths(output):
    return output / "canonical-origin", output / "canonical-build"


def _archive_arm(output, arm, ab, runtime_sha256):
    """Rename whole owned bundles; never rewrite any absolute receipt fields."""
    origin, build = _canonical_paths(output)
    archive = output / "arms" / arm
    archive.mkdir(parents=True)
    receipt = build / "build-receipt.json"
    receipt_hash = _sha(receipt)
    # Keep logs, runtime, work/private state, source snapshot and producer tools.
    build.rename(archive / "build")
    origin.rename(archive / "origin")
    _seal(archive)
    archived = archive / "build"
    if _sha(archived / "build-receipt.json") != receipt_hash:
        raise CampaignError("archiving rewrote the strict build receipt")
    compiler = archived / "pcc1"
    loaded = ab._load_build_receipt(
        archived / "build-receipt.json", arm=arm,
        compiler_sha256=_sha(compiler), runtime_sha256=runtime_sha256,
        compiler_size_bytes=compiler.stat().st_size, expected_gc_backend=0,
    )
    if (loaded["origin_source_root"] != str(origin)
            or loaded["source_root"] != str(build / "source-snapshot")
            or loaded["build_output_root"] != str(build)):
        raise CampaignError("strict producer did not consume the canonical aliases")
    return {"compiler": str(compiler), "receipt": str(archived / "build-receipt.json"),
            "receipt_sha256": receipt_hash, "archive": str(archive)}


def _guard_kwargs(args, output):
    return {
        "guard_result_path": output / "guard.json",
        "expected_guard_command": _child_command(args, output),
        "expected_guard_cwd": REPO_ROOT,
        "campaign_deadline_monotonic": args.campaign_deadline_monotonic,
    }


def _build_arm(args, output, arm, root, files, frozen, ab):
    guard = ab._validated_worker_budget(**_guard_kwargs(args, output))
    cap = guard.max_tree_rss_bytes
    origin, build = _canonical_paths(output)
    _copy_tree(root, origin, files)
    timeout = guard.remaining_timeout(_remaining(args.campaign_deadline_monotonic, STAGE1_SECONDS))
    if timeout < 1:
        raise TimeoutError("not enough campaign time remains to start Stage1")
    command = [str(Path(sys.executable).resolve()), str(REPO_ROOT / "scripts/run_pcc_stage1_build.py"),
               "--arm", arm, "--source-root", str(origin),
               "--runtime-archive", frozen["runtime_archive"]["path"],
               "--output-dir", str(build), "--timeout", str(int(timeout)),
               "--smoke-timeout", "30", "--jobs", str(BUILD_FRONTEND_JOBS),
               "--self-backend-jobs", str(SELF_BACKEND_JOBS), "--memory-budget-bytes", str(cap),
               "--gc-backend", "0", "--no-performance-lock"]
    try:
        result = ab._run_process(command, timeout=timeout, env=os.environ.copy(), cwd=REPO_ROOT)
    except ab.CompileABError as exc:
        if str(exc).startswith("command timed out after "):
            raise TimeoutError(str(exc)) from exc
        raise
    (output / (arm + "-producer.stdout")).write_text(result.stdout, encoding="utf-8")
    (output / (arm + "-producer.stderr")).write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise CampaignError(arm + " strict Stage1 build failed: " + str(result.returncode))
    return _archive_arm(output, arm, ab, frozen["runtime_archive"]["sha256"])


def _compile_args(frozen, output, arms, ab):
    argv = ["--candidate", arms["candidate"]["compiler"], "--baseline", arms["baseline"]["compiler"],
            "--candidate-build-receipt", arms["candidate"]["receipt"],
            "--baseline-build-receipt", arms["baseline"]["receipt"],
            "--runtime-archive", frozen["runtime_archive"]["path"],
            "--source-diff", frozen["source_diff"]["path"],
            "--allowed-changed-source", frozen["allowed_changed_source"],
            "--warmup", frozen["warmup"]["path"], "--output-dir", str(output / "compile-ab"),
            "--frontend-jobs", str(MEASURE_FRONTEND_JOBS), "--self-backend-jobs", str(SELF_BACKEND_JOBS),
            "--compile-timeout", "120", "--run-timeout", "20", "--gc-backend", "0",
            "--unchanged-control"]
    for row in frozen["inputs"]:
        argv += ["--input", row["path"], "--expected-output=" + row["expected_output"][:-1]]
    return ab._parser().parse_args(argv)


def _guarded(args, output, ab):
    manifest = _json(output / "manifest.json")
    if manifest.get("run_token") != args.run_token or manifest.get("status") != "IN_PROGRESS":
        raise CampaignError("campaign ownership mismatch")
    ab.require_claim_platform()
    guard = ab._validated_worker_budget(**_guard_kwargs(args, output))
    kind = manifest["input_kind"]
    captured = output / ("campaign-" + kind + ".json")
    captured_value = _json(_file({"path": str(captured), "sha256": manifest["input_sha256"]}))
    with ab._performance_lock() as token:
        prepared_root = None
        if kind == "request":
            spec_path, proof = _load("ci_pcc_ab_gate").prepare_campaign(
                captured, output, guard.deadline_monotonic,
            )
            expected = output / "prepared/campaign-spec.json"
            if _safe_path(str(spec_path)) != expected:
                raise CampaignError("qualification preparation returned an unowned specification")
            spec = _json(expected)
            prepared_root = output / "prepared"
            roots, trees = _validate_spec(spec, output, ab, prepared_root=prepared_root)
            _verify_frozen_qualification(proof)
        elif kind == "spec":
            spec = captured_value
            roots, trees = _validate_spec(spec, output, ab)
            proof = _qualification(spec, trees, guard.deadline_monotonic)
        else:
            raise CampaignError("unknown campaign input kind")
        manifest.update(phase="PREPARING", qualification=proof,
                        source_identities={arm: _identity(tree) for arm, tree in trees.items()})
        _persist(output / "manifest.json", manifest)
        _verify_live_import_origins(trees["candidate"])
        frozen = _freeze_inputs(spec, output)
        arms = {}
        for arm in ("baseline", "candidate"):
            manifest.update(phase="BUILDING_" + arm.upper(), arms=arms)
            _persist(output / "manifest.json", manifest)
            _verify_live_source(trees["candidate"])
            arms[arm] = _build_arm(args, output, arm, roots[arm], trees[arm], frozen, ab)
            _verify_live_source(trees["candidate"])
            _verify_live_import_origins(trees["candidate"])
            manifest.update(phase="BUILT_" + arm.upper(), arms=arms)
            _persist(output / "manifest.json", manifest)
        # Recheck qualification and every caller-owned full tree before any
        # measurement. Strict Stage1 receipts are build evidence, not gates.
        for arm in ("baseline", "candidate"):
            if _tree(roots[arm]) != trees[arm]:
                raise CampaignError("source changed after strict arm build")
        _verify_frozen_qualification(proof)
        # Recheck validation and producer bytes as well as caller input trees.
        _validate_spec(spec, output, ab, prepared_root=prepared_root)
        _verify_live_import_origins(trees["candidate"])
        manifest.update(phase="MEASURING", arms=arms)
        _persist(output / "manifest.json", manifest)
        result = ab.run_guarded(_compile_args(frozen, output, arms, ab),
                                performance_lock_token=token, **_guard_kwargs(args, output))
        _verify_frozen_qualification(proof)
        _verify_live_source(trees["candidate"])
        _verify_live_import_origins(trees["candidate"])
        manifest.update(phase="MEASURED", compile_summary=result.get("summary"),
                        measurement_status=result.get("status"),
                        compile_manifest=str(output / "compile-ab/manifest.json"))
        _persist(output / "manifest.json", manifest)
        return 0 if result.get("status") == "ACCEPTED" else 1


def _child_command(args, output):
    return [str(Path(sys.executable).resolve()), str(Path(__file__).resolve()),
            "--campaign-request" if args.campaign_request else "--campaign-spec",
            str(output / ("campaign-request.json" if args.campaign_request else "campaign-spec.json")),
            "--output-dir", str(output),
            "--job-deadline-epoch", str(args.job_deadline_epoch), "--guarded",
            "--campaign-deadline-monotonic", str(args.campaign_deadline_monotonic),
            "--run-token", args.run_token]


def _terminal(manifest, guard, error=None):
    status = guard.get("status") if isinstance(guard, dict) else None
    if status == "COMPLETE" and guard.get("returncode") == 0 and manifest.get("phase") == "MEASURED":
        terminal = "SUCCEEDED"
    elif status in {"TIMEOUT", "MEMORY_LIMIT", "INTERRUPTED", "PREFLIGHT_REJECTED"}:
        terminal = status
    elif manifest.get("child_status") in {"TIMEOUT", "INTERRUPTED"}:
        terminal = manifest["child_status"]
    elif isinstance(error, KeyboardInterrupt):
        terminal = "INTERRUPTED"
    elif isinstance(error, TimeoutError):
        terminal = "TIMEOUT"
    else:
        terminal = "FAILED"
    manifest.update(status=terminal, completed_at_epoch=time.time(),
                    guard=guard, does_not_prove=NOT_PROVEN,
                    claim_level="compile-slice-only")
    if error is not None:
        manifest["error"] = type(error).__name__ + ": " + str(error)
    # Never promote A/B acceptance or a sampled total RSS into stage equality.
    return manifest


def run(args):
    output = _safe_path(args.output_dir, exists=False)
    if output.exists() or output.is_symlink():
        raise CampaignError("refusing existing campaign output")
    _disjoint([output, REPO_ROOT])
    output.mkdir(parents=False)
    args.run_token = os.urandom(16).hex()
    manifest = {"schema": SCHEMA, "status": "IN_PROGRESS", "phase": "ADMISSION",
                "run_token": args.run_token, "started_at_epoch": time.time(),
                "does_not_prove": NOT_PROVEN, "claim_level": "compile-slice-only",
                "worker_policy": {"stage1": {"frontend": 2, "self_backend": 2, "link": 8},
                                  "measured": {"frontend": 1, "self_backend": 2, "link": 2}},
                "measurement_mode": "serial-native-frontend optimization slice; not production Stage2 auto",
                "runtime_build": "not_performed; frozen qualified runtime required",
                "enforcement": "sampled_campaign_tree; not kernel hard limit or per-arm tree RSS"}
    _persist(output / "manifest.json", manifest)
    error, guard = None, None
    reserve_key = "PCC_BOOTSTRAP_HOST_MEMORY_RESERVE_BYTES"
    previous_reserve = os.environ.get(reserve_key)
    try:
        if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "macOS":
            raise CampaignError("this campaign may run only in macOS GitHub Actions")
        if os.environ.get("PYTHONPYCACHEPREFIX") or sys.pycache_prefix is not None:
            raise CampaignError("external Python bytecode cache prefixes are not admitted")
        if os.environ.get("PYTHONDONTWRITEBYTECODE") != "1" or not sys.dont_write_bytecode:
            raise CampaignError("CI interpreter must start with PYTHONDONTWRITEBYTECODE=1")
        ab = _load("run_pcc_compile_ab")
        ab.require_claim_platform()
        if float(os.environ.get("PCC_CI_AB_DEADLINE_EPOCH", "nan")) != args.job_deadline_epoch:
            raise CampaignError("CLI deadline differs from the first CI job step")
        remaining = args.job_deadline_epoch - time.time()
        if not math.isfinite(remaining) or not 1 <= remaining <= JOB_SECONDS:
            raise CampaignError("missing, expired or widened absolute job deadline")
        args.campaign_deadline_monotonic = time.monotonic() + remaining
        kind = "request" if args.campaign_request else "spec"
        source = _safe_path(args.campaign_request or args.campaign_spec)
        # Capture only the specification before entering the tree guard. All
        # full-tree preparation, qualification and execution are guarded.
        if (not source.is_file() or source.stat().st_nlink != 1
                or source.stat().st_size > 4 * 1024 * 1024):
            raise CampaignError("campaign request/specification must be a private regular file <=4 MiB")
        spec_hash = _sha(source)
        target = output / ("campaign-" + kind + ".json")
        shutil.copy2(source, target)
        target.chmod(0o444)
        if _sha(source) != spec_hash or _sha(target) != spec_hash:
            raise CampaignError("specification changed during capture")
        manifest["input_sha256"] = spec_hash
        manifest["input_kind"] = kind
        manifest["absolute_deadline_monotonic"] = args.campaign_deadline_monotonic
        _persist(output / "manifest.json", manifest)
        os.environ[reserve_key] = str(RESERVE)
        sampler = _load("run_process_tree_sample")
        guard_args = sampler._parser().parse_args([
            "--result", str(output / "guard.json"), "--samples", str(output / "samples.tsv"),
            "--stdout", str(output / "campaign.stdout"), "--stderr", str(output / "campaign.stderr"),
            "--cwd", str(REPO_ROOT), "--timeout", str(_remaining(args.campaign_deadline_monotonic, JOB_SECONDS) - 1),
            "--auto-tree-rss-ceiling-bytes", str(CEILING), "--min-tree-rss-bytes", str(FLOOR),
            "--darwin-preflight-reserve-bytes", str(RESERVE), "--no-performance-lock",
            "--", *_child_command(args, output)])
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, sampler._request_interrupt)
        try:
            guard = sampler.run(guard_args)
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)
    except BaseException as exc:
        error = exc
    finally:
        if previous_reserve is None:
            os.environ.pop(reserve_key, None)
        else:
            os.environ[reserve_key] = previous_reserve
        manifest = _json(output / "manifest.json")
        if guard is None and (output / "guard.json").is_file():
            guard = _json(output / "guard.json")
        _persist(output / "manifest.json", _terminal(manifest, guard, error))
    return 0 if _json(output / "manifest.json")["status"] == "SUCCEEDED" else 1


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--campaign-spec")
    inputs.add_argument("--campaign-request")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--job-deadline-epoch", required=True, type=float)
    parser.add_argument("--guarded", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--campaign-deadline-monotonic", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--run-token", help=argparse.SUPPRESS)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.guarded:
            output = _safe_path(args.output_dir)
            try:
                return _guarded(args, output, _load("run_pcc_compile_ab"))
            except BaseException as exc:
                manifest = _json(output / "manifest.json")
                if manifest.get("run_token") == args.run_token:
                    manifest.update(child_status=("INTERRUPTED" if isinstance(exc, KeyboardInterrupt)
                                                  else "TIMEOUT" if isinstance(exc, TimeoutError) else "ERROR"),
                                    error=type(exc).__name__ + ": " + str(exc))
                    _persist(output / "manifest.json", manifest)
                raise
        return run(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print("CI A/B campaign error: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    def interrupt(_signum, _frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupt)
    raise SystemExit(main())
