"""Pinned Apache DataSketches releases: C++, Rust, Go, Java and the Python package.

One algorithm library in the four PCC source languages plus its Python
package (nanobind bindings over the C++ core), so the same sketches can be
compiled by each frontend and later compared for performance. Each
projects/<repository>-<version> directory holds its release tag archive
unchanged (scripts/pin_datasketches.py) with pcc-source-lock.json. This
loader verifies every byte and the exact file set before returning sources.
Missing dependency closures (third-party modules, test frameworks, the JDK
25 API, nanobind) are recorded in each lock, never fetched or substituted.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MAX_PROJECT_BYTES = 16 * 1024 * 1024

PROJECTS = {
    "cpp": "datasketches-cpp-5.2.0",
    "rust": "datasketches-rust-0.6.0",
    "go": "datasketches-go-0.3.0",
    "java": "datasketches-java-9.1.0",
    "python": "datasketches-python-5.2.0",
}

# The committed lock bytes; a changed lock is a changed pin, not a refresh.
LOCK_SHA256 = {
    "cpp": "72d5b25d1c5298134dee3c7978947db683e732be384f9c3fa24a6c85a64cc330",
    "rust": "51304e9ab298398dc3550f5fff5f918c029a55ed88c0379b06b66067dd759aea",
    "go": "47b0663825145e7bbe56fa38a03be46c663f9f8f3588a446106d4e766def4798",
    "java": "73853c9a5b819d4749fc3b8bb191118c865fd4aed72304c97369acd898cfcd52",
    "python": "2f70893fdd65f8bf31eb338c1f20a2500c6d4c5e537016bc9e038eac3959e337",
}

ABSENT = {"source_closure_present": False, "reference_tool_present": False}

RELEASES = {
    "cpp": {
        "repository": "https://github.com/apache/datasketches-cpp", "version": "5.2.0", "tag": "5.2.0",
        "tag_object": "cb82deb5050fd86ebe43eee278fb0d479877bf88",
        "revision": "de8553ba372e618382c2e7b44b0ffc9422b9458c",
        "dependencies": {
            "language": "C++11 (CMAKE_CXX_STANDARD 11); header-only library over the C++ standard library",
            "runtime": [],
            "test": [{"name": "Catch2", "version": "v2.13.9", "license": "BSL-1.0",
                      "declared_in": "common/test/CMakeLists.txt (FetchContent)", **ABSENT}],
            "build": [{"name": "CMake", "version": ">= 3.16.0", **ABSENT}],
        },
    },
    "rust": {
        "repository": "https://github.com/apache/datasketches-rust", "version": "0.6.0", "tag": "0.6.0",
        "tag_object": "d0a54134555f862331c38ac0d6718c4271f86bd2",
        "revision": "675e3e123fda6da19657d345038252b16adc2ebb",
        "dependencies": {
            "language": "Rust edition 2024, rust-version 1.86.0; one feature per sketch family",
            "runtime": [],
            "test": [{"name": "googletest", "version": "0.14.3", "license": "Apache-2.0",
                      "declared_in": "datasketches/Cargo.toml [dev-dependencies]", **ABSENT},
                     {"name": "insta", "version": "1.48.0", "license": "Apache-2.0",
                      "declared_in": "datasketches/Cargo.toml [dev-dependencies]", **ABSENT}],
            "build": [{"name": "Cargo workspace members benchmarks, tests-integration, xtask",
                       "note": "their crates.io dependencies (divan, clap, ureq, ...) are not part of the library"}],
        },
    },
    "go": {
        "repository": "https://github.com/apache/datasketches-go", "version": "0.3.0", "tag": "v0.3.0",
        "tag_object": "d02043ee5502ee931ddb81c9302a23a0a2657c67",
        "revision": "7c9421e3af8fadc6ae0ba805b508cefe9e57e738",
        "dependencies": {
            "language": "Go 1.24.11 (go.mod)",
            "runtime": [{"name": "github.com/twmb/murmur3", "version": "v1.1.8", "license": "BSD-3-Clause", **ABSENT},
                        {"name": "github.com/cespare/xxhash/v2", "version": "v2.3.0", "license": "MIT", **ABSENT},
                        {"name": "golang.org/x/exp", "version": "v0.0.0-20250408133849-7e4ce0ab07d0",
                         "license": "BSD-3-Clause", "packages": ["constraints"], **ABSENT}],
            "test": [{"name": "github.com/stretchr/testify", "version": "v1.8.4", "license": "MIT", **ABSENT}],
            "build": [{"name": "Go toolchain", "version": ">= 1.24.11",
                       "note": "the pinned go1.27.3 standard-library closure lacks slices, iter, encoding/binary"}],
        },
    },
    "java": {
        "repository": "https://github.com/apache/datasketches-java", "version": "9.1.0", "tag": "9.1.0",
        "tag_object": None,
        "revision": "d47c992b55c63d16c050711b3b9a0ac51b627031",
        "dependencies": {
            "language": "Java 25 (pom.xml java.version); java.lang.foreign memory segments",
            "runtime": [{"name": "JDK 25 API sources (java.lang.foreign and the java.* types imported)",
                         "license": "GPL-2.0-only WITH Classpath-exception-2.0", **ABSENT}],
            "test": [{"name": "org.testng:testng", "license": "Apache-2.0", "declared_in": "pom.xml", **ABSENT},
                     {"name": "org.slf4j:slf4j-simple", "license": "MIT", "declared_in": "pom.xml", **ABSENT}],
            "build": [{"name": "Maven", "note": "not used; sources are supplied directly"}],
        },
    },
    "python": {
        "repository": "https://github.com/apache/datasketches-python", "version": "5.2.0", "tag": "5.2.0",
        "tag_object": "ca4137230547dd9279df45c0efcc7b7181bc5433",
        "revision": "d40921a3d294eceab2c25e5bca5eba40d1899b8a",
        "dependencies": {
            "language": "Python package: nanobind C++ extension `_datasketches` plus pure-Python modules",
            "runtime": [{"name": "apache/datasketches-cpp", "version": "5.2.0",
                         "declared_in": "CMakeLists.txt ExternalProject GIT_TAG 5.2.0",
                         "pinned_as": "projects/datasketches-cpp-5.2.0"},
                        {"name": "numpy", "pinned_as": "projects/numpy-2.4.4",
                         "note": "pyproject build requirement and runtime import"}],
            "build": [{"name": "nanobind", "version": ">= 1.6", "license": "BSD-3-Clause", **ABSENT},
                      {"name": "CMake", "version": ">= 3.16", **ABSENT},
                      {"name": "setuptools/wheel", **ABSENT}],
        },
    },
}

AUXILIARY = ("pcc-source-lock.json", "PCC-README.md")


def blob_id(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def symlink_target(path, target):
    """The project-relative file a relative symbolic link names, or None."""
    parts = list(PurePosixPath(path).parent.parts)
    for part in PurePosixPath(target).parts:
        if part == "..":
            if not parts:
                return None
            parts.pop()
        elif part not in ("", "."):
            parts.append(part)
    return PurePosixPath(*parts).as_posix() if parts and not PurePosixPath(target).is_absolute() else None


def load_project(key, directory=None):
    """(lock, {path: bytes}) after verifying every pinned byte and the file set."""
    directory = Path(directory) if directory is not None else ROOT / "projects" / PROJECTS[key]
    raw = (directory / "pcc-source-lock.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != LOCK_SHA256[key]:
        raise ValueError("source lock SHA256 mismatch")
    lock = json.loads(raw)
    release = RELEASES[key]
    if (lock["schema_version"] != 1 or lock["repository"] != release["repository"]
            or lock["revision"] != release["revision"] or lock["tag"] != release["tag"]
            or lock["license"] != "Apache-2.0" or lock["complete_build_closure"] is not False
            or lock["dependencies"] != release["dependencies"]):
        raise ValueError("pinned DataSketches release identity differs")
    if directory.is_symlink():
        raise ValueError("project directory must not be a symlink")
    present = {path.relative_to(directory).as_posix() for path in directory.rglob("*")
               if path.is_file() or path.is_symlink()}
    pinned = {entry["path"] for entry in lock["files"]}
    if present != pinned | set(AUXILIARY):
        raise ValueError("project contains missing or unpinned files: "
                         + ", ".join(sorted(present ^ (pinned | set(AUXILIARY)))[:5]))
    files, total = {}, 0
    for entry in lock["files"]:
        path = directory / entry["path"]
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("pinned path escapes the project: " + entry["path"])
        if entry.get("symlink"):
            if not path.is_symlink():
                raise ValueError("pinned symbolic link was replaced: " + entry["path"])
            data = str(path.readlink()).encode("utf-8")
            if symlink_target(entry["path"], data.decode("utf-8")) not in pinned:
                raise ValueError("symbolic link leaves the pinned files: " + entry["path"])
        else:
            if path.is_symlink():
                raise ValueError("pinned file became a symbolic link: " + entry["path"])
            data = path.read_bytes()
        total += len(data)
        if (total > MAX_PROJECT_BYTES or len(data) != entry["size"]
                or hashlib.sha256(data).hexdigest() != entry["sha256"] or blob_id(data) != entry["git_blob"]):
            raise ValueError("source input checksum/size/bound mismatch: " + entry["path"])
        files[entry["path"]] = data
    if total != lock["total_bytes"]:
        raise ValueError("source selection byte total differs")
    return lock, files


def text_units(files, suffixes, *, under=""):
    """Decoded UTF-8 units with one of `suffixes` below `under`, in path order."""
    return {path: data.decode("utf-8") for path, data in sorted(files.items())
            if path.startswith(under) and path.endswith(suffixes)}
