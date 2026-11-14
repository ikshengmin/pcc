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
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/go/upstream/go1.27.3"
LOCK = "pcc-std-closure-lock.json"
LOCK_SHA256 = "a689035f1adcb1b8232028aa03713725d8f78b199d59f66773c8a2b13e102ac3"
REVISION = "2aae1000ea1d4b28923b96db28acf4e3b99d6aff"
MAX_FILES = 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024


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
