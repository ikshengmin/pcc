"""The pinned Go 1.27.3 standard-library source closure of package testing.

tests/go/upstream/go1.27.3/pcc-std-closure-lock.json pins every non-test .go
file of the import closure of testing, fmt, os and testing/internal/testdeps
for linux/darwin on amd64/arm64, taken unchanged from the official
go1.27.3.src.tar.gz (go.dev checksum) and identified by sha256 and git blob
at the go1.27.3 commit. Compiler-owned packages (unsafe, runtime and its
subpackages) are not part of it. This loader verifies every byte and returns
the sources per import path; it never selects files for a build, synthesizes
a package or reads anything outside the fixture.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/go/upstream/go1.27.3"
LOCK = "pcc-std-closure-lock.json"
LOCK_SHA256 = "a689035f1adcb1b8232028aa03713725d8f78b199d59f66773c8a2b13e102ac3"
REVISION = "2aae1000ea1d4b28923b96db28acf4e3b99d6aff"
MAX_FILES = 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
LINUX_PROFILING_PIN = "pcc-linux-amd64-profiling-source-pin.json"
LINUX_PROFILING_PIN_SHA256 = "e062454ef84b7beae0fd0a7f5d786cf5ff69fbb8ac01e3388616e537d5e754d9"
LINUX_NORMAL_PLAN = "pcc-linux-amd64-math-bits-normal-plan.json"
LINUX_NORMAL_PLAN_SHA256 = "ba5d2ffe7b3e92c70aaa2ec9f0462a0fa749852fa75fd2c78fcbb0449dbabb3c"


def load_std_closure(directory=FIXTURE):
    """(lock, {import path: ((file name, source text), ...)}), all verified."""
    directory = Path(directory)
    raw = (directory / LOCK).read_bytes()
    if hashlib.sha256(raw).hexdigest() != LOCK_SHA256:
        raise ValueError("std closure lock SHA256 mismatch")
    lock = json.loads(raw)
    if (lock["schema_version"] != 1 or lock["revision"] != REVISION or lock["version"] != "go1.27.3"
            or lock["complete_build_closure"] is not False or lock["license"] != "BSD-3-Clause"
            or not 1 <= len(lock["files"]) <= MAX_FILES):
        raise ValueError("std closure lock identity differs")
    by_path, total = {}, 0
    for entry in lock["files"]:
        name = entry["path"]
        if name in by_path or not re.fullmatch(r"src/[A-Za-z0-9_./-]+\.go", name) or ".." in name:
            raise ValueError("invalid or duplicate std closure path: " + name)
        if entry["url"] != "https://raw.githubusercontent.com/golang/go/" + REVISION + "/" + name:
            raise ValueError("std closure URL is not pinned to the revision: " + name)
        value = (directory / name).read_bytes()
        total += len(value)
        blob = hashlib.sha1(b"blob " + str(len(value)).encode() + b"\0" + value).hexdigest()
        if (total > MAX_TOTAL_BYTES or len(value) != entry["size"]
                or hashlib.sha256(value).hexdigest() != entry["sha256"] or blob != entry["git_blob"]):
            raise ValueError("std closure checksum/size/bound mismatch: " + name)
        by_path[name] = value.decode("utf-8")
    if total != lock["total_bytes"]:
        raise ValueError("std closure byte total differs")
    packages = {}
    for package, names in lock["packages"].items():
        packages[package] = tuple((name, by_path["src/" + package + "/" + name]) for name in names)
    if sum(len(units) for units in packages.values()) != len(by_path):
        raise ValueError("std closure package inventory differs from its files")
    return lock, packages


def target_selection_gaps(selected, original_files, lock):
    """Compare validated official target hashes to unchanged fixture locks.

    Evidence only. Neither a selected source nor a complete byte match
    establishes owned runtime handling, successful compilation, or a test run.
    """
    union = {entry["path"]: entry["sha256"] for entry in lock["files"]}
    originals = {path: hashlib.sha256(data).hexdigest() for path, data in original_files.items()}
    for path in sorted(union.keys() & originals.keys()):
        if union[path] != originals[path]:
            raise ValueError("std closure disagrees with pinned original: " + path)
    locked = {**union, **originals}
    matching = {path for path, digest in selected.items() if locked.get(path) == digest}
    changed = sorted((selected.keys() & locked.keys()) - matching)
    missing = sorted(selected.keys() - locked.keys())
    missing_roles = {"compiler_owned": Counter(), "other": Counter()}
    excluded = lock["owned_excluded"]
    for path in missing:
        package = path.removeprefix("src/").rsplit("/", 1)[0]
        owned = any(package == prefix or prefix.endswith("/...") and package.startswith(prefix[:-3])
                    for prefix in excluded)
        role = {".go": "go", ".s": "assembly", ".h": "header"}.get(Path(path).suffix, "other")
        missing_roles["compiler_owned" if owned else "other"][role] += 1
    roots = {}
    for root in lock["roots"]:
        path_prefix = "src/" + root + "/"
        files = {path for path in selected if path.startswith(path_prefix)
                 and "/" not in path[len(path_prefix):] and path.endswith(".go")}
        roots[root] = {"selected_go": len(files), "matched_go": len(files & matching)}
    return {
        "status": "LOCKED_BYTES_DIFFER" if changed else "INCOMPLETE" if missing else "BYTES_MATCH",
        "selected_files": len(selected),
        "matching_files": len(matching),
        "missing_files": missing,
        "changed_locked_files": changed,
        "union_not_selected": len(union.keys() - selected.keys()),
        "missing_by_role": {owner: dict(sorted(roles.items())) for owner, roles in missing_roles.items()},
        "roots": roots,
        "scope": "reference source metadata only; not an owned build or test execution",
    }


def load_linux_profiling_pin(directory=FIXTURE):
    """Load the source identities, not buildable packages or runtime shims."""
    raw = (Path(directory) / LINUX_PROFILING_PIN).read_bytes()
    if hashlib.sha256(raw).hexdigest() != LINUX_PROFILING_PIN_SHA256:
        raise ValueError("Linux profiling source pin SHA256 mismatch")
    return json.loads(raw)


def verify_linux_profiling_selection(selected, build_ids_sha256):
    pin = load_linux_profiling_pin()
    if build_ids_sha256 != pin["reference_build_ids_sha256"]:
        raise ValueError("Linux profiling selection identity differs")
    for entry in pin["files"]:
        path = entry["path"]
        if selected.get(path) != entry["sha256"]:
            raise ValueError("Linux profiling source differs: " + path)
    return len(pin["files"])


def load_linux_normal_plan(directory=FIXTURE):
    """Return the pinned recipe and its identity, not an admitted dependency."""
    raw = (Path(directory) / LINUX_NORMAL_PLAN).read_bytes()
    if hashlib.sha256(raw).hexdigest() != LINUX_NORMAL_PLAN_SHA256:
        raise ValueError("Linux Go normal-package plan SHA256 mismatch")
    return raw, LINUX_NORMAL_PLAN_SHA256
