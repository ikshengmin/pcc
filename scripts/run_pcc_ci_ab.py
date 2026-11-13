#!/usr/bin/env python3
"""CI-only, qualification-gated compiler A/B under one admitted tree budget.

This is a compile-slice orchestration owner, not a replacement for the original
correctness, five-GC or fixed-point owners, nor a Stage2 <= Stage1 measurement.
It never applies a patch, downloads sources, builds a runtime, or runs a shell.
Both complete read-only source trees and a qualified runtime are explicit inputs.
Input expected_output is complete stdout and must end in a newline, matching
the existing compile harness; it is never whitespace-normalized.
All preparation, two strict Stage1 builds, explicit candidate mechanism assertions,
six compile pairs and baseline A/A controls share one absolute job deadline and one sampled process-tree guard.
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
    "scripts/pcc_ci_ab_mechanism.py",
    "scripts/data/ci_ab_import_scan/identity_cases.py",
    "scripts/data/ci_ab_import_scan/native_cases.py",
)
_MODULES = {}
_MECHANISM_PROOFS = {}
_PARENT_LAUNCHES = {}
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


def _json(path, *, max_bytes=None):
    if max_bytes is None:
        content = path.read_bytes()
    else:
        mode = path.lstat()
        if (path.is_symlink() or not stat.S_ISREG(mode.st_mode) or mode.st_nlink != 1
                or not 1 <= mode.st_size <= max_bytes):
            raise CampaignError("terminal metadata must be a bounded private regular file: " + str(path))
        with path.open("rb") as stream:
            content = stream.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise CampaignError("terminal metadata grew beyond its bound")
    value = json.loads(content)
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



def _mechanism_inputs(frozen, roots, trees, arms, ab):
    """Fresh strict receipt, full candidate tree and unchanged literal artifacts."""
    mechanism = _load("pcc_ci_ab_mechanism")
    if frozen["allowed_changed_source"] != mechanism.SOURCE:
        raise CampaignError("no explicit candidate mechanism for this compiler source")
    row = arms["candidate"]
    compiler = _safe_path(row["compiler"])
    receipt = _file({"path": row["receipt"], "sha256": row["receipt_sha256"]})
    loaded = ab._load_build_receipt(
        receipt, arm="candidate", compiler_sha256=_sha(compiler),
        compiler_size_bytes=compiler.stat().st_size,
        runtime_sha256=frozen["runtime_archive"]["sha256"], expected_gc_backend=0)
    source_files = loaded["source_manifest"]["files"]
    if (source_files.get(mechanism.SOURCE) != trees["candidate"][mechanism.SOURCE]["sha256"]
            or any(trees["candidate"].get(name, {}).get("sha256") != digest
                   for name, digest in source_files.items())):
        raise CampaignError("candidate mechanism compiler is from another source tree")
    if _tree(roots["candidate"]) != trees["candidate"]:
        raise CampaignError("candidate mechanism source changed")
    for name, digest in mechanism.ARTIFACTS.items():
        path = roots["candidate"] / mechanism.DATA / name
        if _sha(path) != digest:
            raise CampaignError("preserved candidate mechanism assertions changed: " + name)
    return mechanism, loaded


def _run_mechanism(args, output, frozen, roots, trees, arms, ab):
    """An additional bounded phase, never a substitute for original gates."""
    mechanism, loaded = _mechanism_inputs(frozen, roots, trees, arms, ab)
    guard = ab._validated_worker_budget(**_guard_kwargs(args, output))
    root = output / "candidate-mechanism"
    root.mkdir()
    inputs = root / "inputs"
    inputs.mkdir()
    inputs_receipts = {}
    for name, digest in mechanism.ARTIFACTS.items():
        path = inputs / name
        shutil.copy2(roots["candidate"] / mechanism.DATA / name, path)
        path.chmod(0o444)
        inputs_receipts[name] = {"path": str(path), "sha256": digest}
        _file(inputs_receipts[name])
    (inputs / "conftest.py").write_text(mechanism.CONFTEST, encoding="utf-8")
    (inputs / "pytest.ini").write_text(
        "[pytest]\naddopts =\nmarkers =\n    integration: explicit candidate native acceptance\n    xdist_group(name): one bound mechanism evidence group\n", encoding="utf-8")
    _seal(inputs)
    for name in ("conftest.py", "pytest.ini"):
        inputs_receipts[name] = {"path": str(inputs / name), "sha256": _sha(inputs / name)}
    for name, digest in frozen["runtime_files"].items():
        path = Path(frozen["runtime_archive"]["path"]).parent / name
        inputs_receipts["runtime/" + name] = {"path": str(path), "sha256": digest}
    for name in TOOLS:
        inputs_receipts["tool/" + name] = {"path": str(REPO_ROOT / name), "sha256": _sha(REPO_ROOT / name)}
    # Live source is not chmod'ed: its full identity is rechecked independently.
    # Tool receipts are checked with readonly=False in the runner, which also
    # checks against this exact qualified candidate full-tree manifest.
    tool_receipts = {key: inputs_receipts.pop(key) for key in list(inputs_receipts) if key.startswith("tool/")}
    phase_timeout = guard.remaining_timeout(_remaining(args.campaign_deadline_monotonic, mechanism.PHASE_SECONDS))
    request = {
        "schema": mechanism.SCHEMA, "source_root": str(roots["candidate"]),
        "source_files": trees["candidate"], "target_sha": frozen["target_sha"], "run_token": args.run_token,
        "compiler": arms["candidate"]["compiler"], "receipt": arms["candidate"]["receipt"],
        "receipt_sha256": arms["candidate"]["receipt_sha256"],
        "runtime_archive": frozen["runtime_archive"]["path"], "inputs": inputs_receipts,
        "tools": tool_receipts, "compiler_sha256": loaded["receipt"]["compiler_sha256"],
        "phase_deadline_monotonic": time.monotonic() + phase_timeout,
        "guard": {key: str(value) if isinstance(value, Path) else value
                  for key, value in _guard_kwargs(args, output).items()},
    }
    request_path = root / "request.json"
    _persist(request_path, request)
    request_path.chmod(0o444)
    env = ab._measurement_env(
        Path(frozen["runtime_archive"]["path"]), 0,
        host_source_root=roots["candidate"], host_python=Path(sys.executable).resolve(),
        private_root=root / "private", frontend_jobs=1, self_backend_jobs=2, worker_budget=guard)
    env.pop("PYTHONPYCACHEPREFIX", None)
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTEST_ADDOPTS="",
               PYTHONPATH=str(roots["candidate"]) + os.pathsep + str(REPO_ROOT / "scripts"), PCC_NO_AUTO_PCC1="1",
               PCC_CURRENT_PCC1=arms["candidate"]["compiler"], PCC_REQUIRE_PCC1="1")
    command = [str(Path(sys.executable).resolve()), str(REPO_ROOT / "scripts/pcc_ci_ab_mechanism.py"),
               "--request", str(request_path)]
    started = time.monotonic()
    result = ab._run_process(command, timeout=_remaining(request["phase_deadline_monotonic"], phase_timeout),
                             env=env, cwd=root)
    (root / "runner.stdout").write_text(result.stdout, encoding="utf-8")
    (root / "runner.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise CampaignError("candidate mechanism assertion phase failed: " + str(result.returncode))
    guard.verify()
    evidence = _json(root / "result.json")
    mechanism.validate_result(evidence, request=request_path, root=root)
    _mechanism_inputs(frozen, roots, trees, arms, ab)
    _verify_live_source(trees["candidate"])
    _verify_live_import_origins(trees["candidate"])
    _remaining(request["phase_deadline_monotonic"], mechanism.PHASE_SECONDS)
    _seal(root)
    files = _tree(root)
    proof = {"schema": mechanism.SCHEMA, "status": "PASSED", "root": str(root),
             "files": files, "sha256": _identity(files), "request_sha256": _sha(request_path),
             "result_sha256": _sha(root / "result.json"), "command": command,
             "environment": env, "returncode": result.returncode,
             "elapsed_seconds": time.monotonic() - started, "phase_ceiling_seconds": mechanism.PHASE_SECONDS,
             "source_sha256": _identity(trees["candidate"]), "candidate_sha": frozen["target_sha"], "run_token": args.run_token,
             "compiler_receipt_sha256": loaded["sha256"], "budget": guard.receipt,
             "scope": "candidate exact-str identity; CPython and host/native emitted execution on GC0-4",
             "does_not_prove": ["total allocation count", "Stage2 <= Stage1", "original gates"]}
    _remaining(request["phase_deadline_monotonic"], mechanism.PHASE_SECONDS)
    _MECHANISM_PROOFS[id(proof)] = (proof, _identity(proof))
    return proof


def _verify_mechanism(proof, *, output, ab, launched=False):
    if (not isinstance(proof, dict) or proof.get("status") != "PASSED"
            or proof.get("returncode") != 0 or proof.get("root") != str(output / "candidate-mechanism")):
        raise CampaignError("candidate mechanism proof is missing or belongs to another campaign")
    if launched:
        registered = _MECHANISM_PROOFS.get(id(proof))
        if registered is None or registered[0] is not proof or registered[1] != _identity(proof):
            raise CampaignError("candidate mechanism proof was not produced by this campaign launch")
    root = _safe_path(proof["root"])
    files = _tree(root)
    if files != proof.get("files") or _identity(files) != proof.get("sha256"):
        raise CampaignError("candidate mechanism frozen evidence changed after use")
    request_path = _file({"path": str(root / "request.json"), "sha256": proof.get("request_sha256")})
    result_path = _file({"path": str(root / "result.json"), "sha256": proof.get("result_sha256")})
    mechanism = _load("pcc_ci_ab_mechanism")
    evidence = _json(result_path)
    mechanism.validate_result(evidence, request=request_path, root=root)
    request = _json(request_path)
    campaign = _json(output / "manifest.json")
    if (request.get("run_token") != proof.get("run_token") or proof.get("run_token") != campaign.get("run_token")
            or request.get("guard", {}).get("guard_result_path") != str(output / "guard.json")
            or request["guard"].get("expected_guard_command") != proof.get("budget", {}).get("guard_command")
            or proof.get("command") != [str(Path(sys.executable).resolve()),
                    str(REPO_ROOT / "scripts/pcc_ci_ab_mechanism.py"), "--request", str(request_path)]):
        raise CampaignError("candidate mechanism proof belongs to a different campaign launch")
    actual_guard = _json(output / "guard.json")
    if (actual_guard.get("command") != request["guard"]["expected_guard_command"]
            or actual_guard.get("cwd") != str(REPO_ROOT)
            or actual_guard.get("max_tree_rss_bytes") != proof["budget"].get("max_tree_rss_bytes")
            or any(actual_guard.get("environment", {}).get(key) != value
                   for key, value in proof["budget"].get("worker_environment", {}).items())):
        raise CampaignError("candidate mechanism enclosing guard identity changed")
    if (_tree(Path(request["source_root"])) != request["source_files"]
            or _identity(request["source_files"]) != proof["source_sha256"]
            or request["target_sha"] != proof["candidate_sha"]):
        raise CampaignError("candidate mechanism source identity changed after use")
    for row in request["inputs"].values():
        _file(row)
    for row in request["tools"].values():
        _file(row, readonly=False)
    compiler = _safe_path(request["compiler"])
    loaded = ab._load_build_receipt(
        _file({"path": request["receipt"], "sha256": request["receipt_sha256"]}), arm="candidate",
        compiler_sha256=_sha(compiler), compiler_size_bytes=compiler.stat().st_size,
        runtime_sha256=_sha(Path(request["runtime_archive"])), expected_gc_backend=0)
    if (loaded["sha256"] != proof["compiler_receipt_sha256"] or _sha(compiler) != request["compiler_sha256"]
            or evidence["host_python_runtime"] != loaded["host_python_runtime"]):
        raise CampaignError("candidate mechanism compiler/receipt changed after use")
    for receipt in loaded["external_tools"]:
        ab.verify_portable_file_receipt(receipt, "candidate mechanism external tool")
    if ab.host_python_runtime_evidence(Path(sys.executable).resolve()) != evidence["host_python_runtime"]:
        raise CampaignError("candidate mechanism CPython runtime changed after use")
    return proof


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
        manifest.update(phase="VERIFYING_CANDIDATE_MECHANISM", arms=arms)
        _persist(output / "manifest.json", manifest)
        mechanism_proof = _run_mechanism(args, output, frozen, roots, trees, arms, ab)
        _verify_mechanism(mechanism_proof, output=output, ab=ab, launched=True)
        _verify_frozen_qualification(proof)
        manifest.update(phase="MEASURING", arms=arms, candidate_mechanism=mechanism_proof)
        _persist(output / "manifest.json", manifest)
        result = ab.run_guarded(_compile_args(frozen, output, arms, ab),
                                performance_lock_token=token, **_guard_kwargs(args, output))
        manifest.update(phase="MEASURED", compile_summary=result.get("summary"),
                        measurement_status=result.get("status"),
                        compile_manifest=str(output / "compile-ab/manifest.json"))
        if result.get("status") == "ACCEPTED":
            context, completion, compile_hash = _finish_guarded(
                args, output, manifest, spec, trees, proof, mechanism_proof,
                result, ab, prepared_root=prepared_root)
            manifest.update(campaign_context=context, completion=completion,
                            compile_manifest_sha256=compile_hash, candidate_mechanism_sha256=_identity(mechanism_proof))
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



COMPLETION_SCHEMA = "pcc.ci-compile-ab-completion.v1"
COMPLETION_MAX_BYTES = 64 * 1024
TERMINAL_MANIFEST_MAX_BYTES = 8 * 1024 * 1024
TERMINAL_GUARD_MAX_BYTES = 1024 * 1024
_COMPLETION_RESULT_FIELDS = (
    "schema", "phase", "compile_summary", "measurement_status", "source_identities",
    "arms", "qualification", "compile_manifest", "worker_policy", "measurement_mode",
    "runtime_build", "enforcement",
)
_GUARD_CONTRACT_KEYS = (
    "schema", "status", "started_at_utc", "command", "cwd", "environment",
    "timeout_s", "interval_s", "max_tree_rss_bytes",
    "darwin_preflight_reserve_bytes", "memory_budget_selection", "resource_preflight",
)


def _launch_contract(args, output, *, input_kind, input_sha256, target_sha):
    return {"run_token": args.run_token, "input_kind": input_kind,
            "input_sha256": input_sha256, "target_sha": target_sha,
            "command": _child_command(args, output), "cwd": str(REPO_ROOT),
            "output": str(output), "job_deadline_epoch": args.job_deadline_epoch,
            "campaign_deadline_monotonic": args.campaign_deadline_monotonic}


def _guard_contract_digest(guard):
    # The original sampler changes terminal state/telemetry, not its launch
    # contract. Match its already-validated RUNNING contract without importing
    # a producer or reading source/runtime trees in the outer process.
    contract = {key: guard.get(key) for key in _GUARD_CONTRACT_KEYS}
    contract["status"] = "RUNNING"
    return _identity(contract)


def _finish_guarded(args, output, manifest, spec, trees, qualification,
                    mechanism, result, ab, *, prepared_root):
    """All expensive final verification stays inside the existing watchdog.

    The outer process can be uv's venv while this source-owned child uses the
    receipt-bound resolved base interpreter. Only this child rechecks runtime
    identity. Its compact completion attests to those checks, not new evidence.
    """
    if result.get("status") != "ACCEPTED":
        raise CampaignError("cannot complete a rejected compile experiment")
    _verify_frozen_qualification(qualification)
    _verify_live_source(trees["candidate"])
    _verify_live_import_origins(trees["candidate"])
    _verify_mechanism(mechanism, output=output, ab=ab, launched=True)
    _validate_spec(spec, output, ab, prepared_root=prepared_root)
    _verify_frozen_qualification(qualification)
    _verify_live_import_origins(trees["candidate"])
    compile_path = output / "compile-ab/manifest.json"
    if (compile_path.is_symlink() or not compile_path.is_file()
            or compile_path.stat().st_nlink != 1 or _json(compile_path) != result):
        raise CampaignError("accepted compile manifest changed before completion")
    compile_hash = _sha(compile_path)
    compile_path.chmod(0o444)
    context = _load("ci_pcc_ab_gate").finish_campaign(qualification)
    guard = ab._validated_worker_budget(**_guard_kwargs(args, output))
    guard.verify()
    completion = {
        "schema": COMPLETION_SCHEMA, "status": "VERIFIED_ACCEPTED",
        "launch": _launch_contract(args, output, input_kind=manifest["input_kind"],
                                   input_sha256=manifest["input_sha256"], target_sha=spec["target_sha"]),
        "guard_contract_sha256": guard.receipt["guard_result"]["contract_sha256"],
        "campaign_context_sha256": _identity(context),
        "result_body_sha256": _identity({key: manifest.get(key) for key in _COMPLETION_RESULT_FIELDS}),
        "mechanism": {"proof_sha256": _identity(mechanism),
                      "request_sha256": mechanism["request_sha256"],
                      "result_sha256": mechanism["result_sha256"],
                      "tree_sha256": mechanism["sha256"],
                      "source_sha256": mechanism["source_sha256"],
                      "compiler_receipt_sha256": mechanism["compiler_receipt_sha256"]},
        "compile_manifest_sha256": compile_hash,
        "verified_at_monotonic": time.monotonic(),
    }
    encoded = json.dumps(completion, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if len(encoded.encode()) > COMPLETION_MAX_BYTES:
        raise CampaignError("compact completion exceeded its metadata limit")
    path = output / "completion.json"
    if path.exists() or path.is_symlink():
        raise CampaignError("refusing existing completion receipt")
    _remaining(args.campaign_deadline_monotonic, JOB_SECONDS)
    _persist(path, completion)
    path.chmod(0o444)
    guard.verify()
    if _sha(compile_path) != compile_hash:
        raise CampaignError("compile manifest changed while finalizing completion")
    return context, {"path": str(path), "sha256": _sha(path), "size_bytes": path.stat().st_size}, compile_hash


def _verify_completion(manifest, guard, *, output, expected):
    """Bounded metadata only; never re-import helpers or walk inputs here."""
    if (not isinstance(expected, dict) or set(expected) != {"launch", "started_monotonic"}
            or not isinstance(expected["launch"], dict)):
        raise CampaignError("terminal acceptance lacks the parent's original launch contract")
    registered = _PARENT_LAUNCHES.get(id(expected))
    if registered is None or registered[0] is not expected or registered[1] != _identity(expected):
        raise CampaignError("completion expectations were not retained by this parent launch")
    launch = expected["launch"]
    if (guard.get("schema") != "pcc.process_tree_sample.v1" or guard.get("status") != "COMPLETE"
            or type(guard.get("returncode")) is not int or guard["returncode"] != 0
            or guard.get("command") != launch["command"] or guard.get("cwd") != launch["cwd"]
            or launch["output"] != str(output) or manifest.get("run_token") != launch["run_token"]
            or manifest.get("input_kind") != launch["input_kind"]
            or manifest.get("input_sha256") != launch["input_sha256"]
            or manifest.get("absolute_deadline_monotonic") != launch["campaign_deadline_monotonic"]):
        raise CampaignError("completion does not match the actual parent-owned guard launch")
    receipt = manifest.get("completion")
    if (not isinstance(receipt, dict) or set(receipt) != {"path", "sha256", "size_bytes"}
            or receipt.get("path") != str(output / "completion.json")
            or type(receipt.get("size_bytes")) is not int
            or not 1 <= receipt["size_bytes"] <= COMPLETION_MAX_BYTES):
        raise CampaignError("missing or unbounded compact completion receipt")
    path = _safe_path(receipt["path"])
    mode = path.lstat()
    if (not stat.S_ISREG(mode.st_mode) or mode.st_nlink != 1 or mode.st_mode & 0o222
            or mode.st_size != receipt["size_bytes"]):
        raise CampaignError("completion receipt is not private, read-only bounded metadata")
    with path.open("rb") as stream:
        content = stream.read(COMPLETION_MAX_BYTES + 1)
    if len(content) > COMPLETION_MAX_BYTES or hashlib.sha256(content).hexdigest() != receipt.get("sha256"):
        raise CampaignError("completion receipt changed after guarded verification")
    value = json.loads(content)
    fields = {"schema", "status", "launch", "guard_contract_sha256", "campaign_context_sha256",
              "result_body_sha256", "mechanism", "compile_manifest_sha256", "verified_at_monotonic"}
    if (not isinstance(value, dict) or set(value) != fields or value.get("schema") != COMPLETION_SCHEMA
            or value.get("status") != "VERIFIED_ACCEPTED" or value.get("launch") != launch
            or value.get("guard_contract_sha256") != _guard_contract_digest(guard)):
        raise CampaignError("compact completion identity or original guard contract differs")
    verified = value["verified_at_monotonic"]
    if (type(verified) not in (int, float) or not math.isfinite(verified)
            or not expected["started_monotonic"] <= verified <= launch["campaign_deadline_monotonic"]):
        raise CampaignError("final verification was not completed inside the original deadline")
    if value["result_body_sha256"] != _identity({key: manifest.get(key) for key in _COMPLETION_RESULT_FIELDS}):
        raise CampaignError("verified child result body changed after guarded completion")
    context = manifest.get("campaign_context")
    if (not isinstance(context, dict) or context.get("final_identity_status") != "EXACT_PAIR_REAUTHENTICATED"
            or value["campaign_context_sha256"] != _identity(context)):
        raise CampaignError("authenticated campaign context changed after completion")
    mechanism = manifest.get("candidate_mechanism")
    if (not isinstance(mechanism, dict) or mechanism.get("status") != "PASSED"
            or mechanism.get("run_token") != launch["run_token"] or mechanism.get("candidate_sha") != launch["target_sha"]
            or mechanism.get("root") != str(output / "candidate-mechanism")):
        raise CampaignError("completion has no matching candidate mechanism identity")
    if _identity(mechanism) != manifest.get("candidate_mechanism_sha256"):
        raise CampaignError("bounded mechanism proof metadata changed after guarded verification")
    compact = {"proof_sha256": manifest.get("candidate_mechanism_sha256"),
               "request_sha256": mechanism.get("request_sha256"), "result_sha256": mechanism.get("result_sha256"),
               "tree_sha256": mechanism.get("sha256"), "source_sha256": mechanism.get("source_sha256"),
               "compiler_receipt_sha256": mechanism.get("compiler_receipt_sha256")}
    digests = [*compact.values(), value["compile_manifest_sha256"], value["campaign_context_sha256"]]
    if (value["mechanism"] != compact or any(not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest) for digest in digests)
            or manifest.get("compile_manifest") != str(output / "compile-ab/manifest.json")
            or manifest.get("compile_manifest_sha256") != value["compile_manifest_sha256"]):
        raise CampaignError("completion artifact identities changed")
    # Do not reread mechanism/compile bundles here: their full verification was
    # performed under the watchdog, and their immutable hashes remain evidence.
    return value


def _terminal(manifest, guard, error=None, *, output=None, expected=None):
    status = guard.get("status") if isinstance(guard, dict) else None
    if (status == "COMPLETE" and guard.get("returncode") == 0
            and manifest.get("phase") == "MEASURED" and manifest.get("measurement_status") == "ACCEPTED"
            and manifest.get("campaign_context", {}).get("final_identity_status") == "EXACT_PAIR_REAUTHENTICATED"
            and error is None):
        try:
            if output is None:
                raise CampaignError("terminal acceptance requires owned completion metadata")
            _verify_completion(manifest, guard, output=output, expected=expected)
            terminal = "SUCCEEDED"
        except Exception as exc:
            error, terminal = exc, "FAILED"
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
    error, guard, terminal_expected = None, None, None
    terminal_result = None
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
        terminal_expected = {
            "launch": _launch_contract(args, output, input_kind=kind, input_sha256=spec_hash,
                                       target_sha=os.environ.get("GITHUB_SHA")),
            "started_monotonic": time.monotonic(),
        }
        _PARENT_LAUNCHES[id(terminal_expected)] = (terminal_expected, _identity(terminal_expected))
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
        terminal_path = output / "manifest.json"
        try:
            manifest = _json(terminal_path, max_bytes=TERMINAL_MANIFEST_MAX_BYTES)
        except Exception as exc:
            # Preserve malformed/oversized child evidence; write a small failure
            # receipt separately rather than replacing the child's raw artifact.
            error = error or exc
            manifest = {"schema": SCHEMA, "phase": "TERMINAL_METADATA_REJECTED", "run_token": args.run_token}
            terminal_path = output / "terminal.json"
        if guard is None and (output / "guard.json").is_file():
            try:
                guard = _json(output / "guard.json", max_bytes=TERMINAL_GUARD_MAX_BYTES)
            except Exception as exc:
                error = error or exc
        terminal_result = _terminal(manifest, guard, error, output=output, expected=terminal_expected)
        _persist(terminal_path, terminal_result)
    return 0 if terminal_result["status"] == "SUCCEEDED" else 1


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
