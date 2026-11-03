"""Shared bounded-source receipt helpers; no compiler imports or project execution."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys

MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
RSS_BYTES = 1073741824


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def input_path(directory, name):
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name or "\0" in name
            or PurePosixPath(name).is_absolute()
            or any(part in {"", ".", ".."} for part in name.split("/"))):
        raise ValueError("input needs a canonical relative POSIX path")
    if directory.is_symlink():
        raise ValueError("fixture directory must not be a symlink")
    path = directory
    for part in name.split("/"):
        path = path / part
        if path.is_symlink():
            raise ValueError("fixture input symlinks are not admitted")
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError("input escapes fixture directory")
    return path


def bounded_bytes(path, limit):
    with path.open("rb") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError("source input exceeds byte bound: " + str(path))
    return value


def load_source_lock(directory, expected_lock_sha256):
    raw = bounded_bytes(input_path(directory, "pcc-source-lock.json"), 65536)
    if sha256(raw) != expected_lock_sha256:
        raise ValueError("source lock SHA256 mismatch")
    lock = json.loads(raw)
    if lock["schema_version"] != 1 or lock["complete_build_closure"] is not False:
        raise ValueError("source lock schema/partial-closure boundary differs")
    if not re.fullmatch("[0-9a-f]{40}", lock["revision"]) or not 1 <= len(lock["files"]) <= 32:
        raise ValueError("source lock revision/file count differs")
    repository = lock["repository"].removeprefix("https://github.com/")
    if not re.fullmatch("[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("source lock needs an explicit official GitHub repository")
    files = {}
    total = 0
    for entry in lock["files"]:
        name = entry["path"]
        if name in files or not re.fullmatch("[0-9a-f]{64}", entry["sha256"]):
            raise ValueError("duplicate input or invalid SHA256")
        if (not re.fullmatch("[0-9a-f]{40}", entry["git_blob"])
                or type(entry["size"]) is not int or not 0 <= entry["size"] <= MAX_FILE_BYTES
                or not entry["license"] or not entry["license_evidence"]):
            raise ValueError("invalid source identity, size or license evidence")
        expected_url = "https://raw.githubusercontent.com/" + repository + "/" + lock["revision"] + "/" + name
        if entry["url"] != expected_url:
            raise ValueError("source URL is not pinned to its exact repository/revision/path")
        value = bounded_bytes(input_path(directory, name), MAX_FILE_BYTES)
        total += len(value)
        blob = hashlib.sha1(b"blob " + str(len(value)).encode() + b"\0" + value).hexdigest()
        if (total > MAX_TOTAL_BYTES or len(value) != entry["size"]
                or sha256(value) != entry["sha256"] or blob != entry["git_blob"]):
            raise ValueError("source input checksum/size/bound mismatch: " + name)
        value.decode("utf-8")  # No universal-newline conversion, replacement or token stripping.
        files[name] = value
    if total != lock["total_bytes"]:
        raise ValueError("source selection byte total differs")
    return lock, files


def source_identity(root, driver_paths):
    paths = set((root / "pcc").rglob("*.py"))
    paths.update(path for path in (root / "utils/fake_libc_include").rglob("*") if path.is_file())
    paths.update(root / name for name in driver_paths)
    return {path.relative_to(root).as_posix(): sha256(path.read_bytes()) for path in sorted(paths)}


def materialize_snapshot(directory, files):
    """Copy only frozen originals, never the surrounding unpinned directory."""
    directory.mkdir(parents=True, exist_ok=False)
    for name, value in files.items():
        path = input_path(directory, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    verify_snapshot(directory, files)


def verify_snapshot(directory, files):
    paths = list(directory.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ValueError("source snapshot gained a symlink")
    actual = {path.relative_to(directory).as_posix() for path in paths if path.is_file()}
    if actual != set(files):
        raise ValueError("source snapshot contains missing or unpinned files")
    for name, value in files.items():
        if bounded_bytes(input_path(directory, name), MAX_FILE_BYTES) != value:
            raise ValueError("source snapshot changed: " + name)


def diagnostic_status(error):
    diagnostic = str(error).lower()
    return "UNSUPPORTED" if any(marker in diagnostic for marker in (
        "not implemented", "unsupported", "has no borrow checker",
        "requires an owned", "requires an actual checkedrustsource",
    )) else "FAIL"


def mark_interrupted(value):
    if isinstance(value, dict):
        if value.get("status") == "RUNNING":
            value.update(status="FAIL", diagnostic="worker interrupted; inspect retained raw guard evidence")
        for child in value.values():
            mark_interrupted(child)
    elif isinstance(value, list):
        for child in value:
            mark_interrupted(child)


def run_ci(root, fixture, script, initial_report, worker, argv=None):
    parser = argparse.ArgumentParser(description="CI-only original-source frontier; not project acceptance")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    if os.environ.get("CI", "").lower() != "true":
        parser.error("CI-only frontier; local compiler execution is not authorized")
    output = arguments.output.resolve()
    if output == fixture.resolve() or output.is_relative_to(fixture.resolve()):
        parser.error("output must not overwrite original inputs")
    if arguments.worker:
        if os.environ.get("PCC_SOURCE_FRONTIER_WORKER") != "1":
            parser.error("worker must be launched by the bounded CI parent")
        return worker(output)

    output.mkdir(parents=True, exist_ok=False)
    receipt = output / "frontier.json"
    save(receipt, initial_report())
    if shutil.disk_usage(output).free < RSS_BYTES:
        report = initial_report()
        report["runner"] = {"status": "FAIL", "diagnostic": "original 1-GiB free-disk floor not met"}
        save(receipt, report)
        return 2
    guard = output / "guard"
    guard.mkdir()
    from scripts.ci_validation_run import guarded_command
    command = [sys.executable, str(script), "--output", str(output), "--worker"]
    environment = dict(os.environ)
    environment.update(
        PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1",
        PCC_SOURCE_ROOT=str(root), PCC_REPO_ROOT=str(root),
        PCC_USE_PLY_C_PARSER="0", PCC_SOURCE_FRONTIER_WORKER="1",
    )
    guarded = guarded_command(guard, command)
    save(output / "invocation.json", {
        "command": guarded, "cwd": str(root),
        "environment_overrides": {key: environment[key] for key in (
            "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", "PCC_SOURCE_ROOT",
            "PCC_REPO_ROOT", "PCC_USE_PLY_C_PARSER", "PCC_SOURCE_FRONTIER_WORKER",
        )},
    })
    try:
        result = subprocess.run(guarded, cwd=root, env=environment, check=False)
        report = json.loads(receipt.read_text())
        resource = json.loads((guard / "result.json").read_text())
        report["guard"] = resource
        complete = (
            resource.get("status") == "COMPLETE" and resource.get("timeout_s") == 90
            and resource.get("max_tree_rss_bytes") == RSS_BYTES
            and resource.get("returncode") == result.returncode
            and 0 <= resource.get("peak_tree_rss_bytes", RSS_BYTES + 1) <= RSS_BYTES
            and shutil.disk_usage(output).free >= RSS_BYTES and report.get("terminal") is True
        )
        if not complete:
            raise ValueError("incomplete worker or resource/exit/disk guard mismatch")
    except Exception as error:
        try:
            report = json.loads(receipt.read_text())
        except Exception:
            report = initial_report()
        report["runner"] = {"status": "FAIL", "exception": type(error).__name__, "diagnostic": str(error)}
        mark_interrupted(report)
        save(receipt, report)
        return 2
    report["runner"] = {"status": "PASS", "claim": "bounded report completed; NOT project acceptance"}
    save(receipt, report)
    # No green project check from successful reporting of an incomplete chain.
    return 1

