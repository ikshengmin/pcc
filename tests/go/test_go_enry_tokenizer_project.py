"""Unchanged go-enry 2.9.6 tokenizer slice: strict owned gate plus Go reference.

projects/go-enry-2.9.6/PCC-README.md records the pin and the default-build
selection. The owned gate requires both original packages to compile; a
compiler rejection is a failing project test. testify is not supplied, so
owned and reference upstream-test execution remain explicitly UNRUN.
"""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from scripts.ci_source_frontier_support import load_source_lock, materialize_snapshot, verify_snapshot

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "projects/go-enry-2.9.6"
LOCK_SHA256 = "0455a699e1a43ce780d3c39b292573031f9eb8ce323cf4ba2bbf1814aadc37ae"
REVISION = "71719e4bdde528496011d030354a97b7f3ed4ff6"
MODULE = "github.com/go-enry/go-enry/v2"
REGEX, TOKENIZER = MODULE + "/regex", MODULE + "/internal/tokenizer"
DEFAULT_BUILD = {
    REGEX: ("regex/regex.go", "regex/standard.go"),
    TOKENIZER: ("internal/tokenizer/common.go", "internal/tokenizer/tokenize.go"),
}
TAGGED = {"regex/oniguruma.go": "oniguruma", "internal/tokenizer/tokenize_c.go": "flex"}
TESTS = ("regex/standard_test.go", "internal/tokenizer/tokenize_test.go")
# Admission only: exports Tokenize; every function of both packages is checked.
ADMISSION_MAIN = 'package main;import "' + TOKENIZER + '";func main(){_=tokenizer.Tokenize(nil)}'


def load_inputs(directory=PROJECT):
    lock, files = load_source_lock(directory, LOCK_SHA256)
    if (lock["project"] != "go-enry-tokenizer" or lock["version"] != "2.9.6" or lock["revision"] != REVISION
            or lock["module"] != MODULE or lock["complete_package_source_closure"] is not True
            or {path: tuple(row["default_build"]) for path, row in lock["packages"].items()} != DEFAULT_BUILD
            or tuple(lock["upstream_test_units"]) != TESTS
            or lock["dependencies"]["state"] != "MISSING_SOURCE_AND_TOOL_CLOSURE"):
        raise ValueError("original go-enry tokenizer selection differs")
    return lock, files


def owned_inputs(files):
    inputs = {"main": [("admission-main.go", ADMISSION_MAIN)]}
    for package, names in DEFAULT_BUILD.items():
        inputs[package] = [(name.rsplit("/", 1)[1], files[name].decode("utf-8")) for name in names]
    return inputs


def build_constraint(data):
    match = re.search(rb"^//go:build (.+)$", data, re.M) or re.search(rb"^// \+build (.+)$", data, re.M)
    return None if match is None else match.group(1).decode()


def test_lock_pins_both_complete_packages_and_their_upstream_tests():
    lock, files = load_inputs()
    assert len(files) == 12 and lock["total_bytes"] == sum(map(len, files.values()))
    assert all(entry["url"].startswith("https://raw.githubusercontent.com/go-enry/go-enry/" + REVISION + "/")
               for entry in lock["files"])
    assert files["go.mod"].startswith(b"module " + MODULE.encode() + b"\n")
    assert all(b"github.com/stretchr/testify" in files[name] for name in ("internal/tokenizer/tokenize_test.go",))
    assert lock["dependencies"]["testify"]["source_closure_present"] is False


def test_default_build_selection_follows_the_original_build_constraints():
    _, files = load_inputs()
    for name, tag in TAGGED.items():
        assert build_constraint(files[name]) == tag
        assert all(name not in names for names in DEFAULT_BUILD.values())
    assert build_constraint(files["regex/standard.go"]) == "!oniguruma"
    assert build_constraint(files["internal/tokenizer/tokenize.go"]) == "!flex"
    assert build_constraint(files["regex/regex.go"]) is None
    assert build_constraint(files["internal/tokenizer/common.go"]) is None


@pytest.mark.parametrize("name", [*DEFAULT_BUILD[REGEX], *DEFAULT_BUILD[TOKENIZER], *TESTS, "LICENSE"])
def test_changed_original_bytes_are_rejected(tmp_path, name):
    copy = tmp_path / "project"
    shutil.copytree(PROJECT, copy)
    target = copy / name
    data = bytearray(target.read_bytes())
    data[len(data) // 2] ^= 1
    target.write_bytes(bytes(data))
    with pytest.raises(ValueError, match="checksum"):
        load_inputs(copy)


def test_materialized_snapshot_keeps_every_original_byte(tmp_path):
    _, files = load_inputs()
    materialize_snapshot(tmp_path / "source", files)
    verify_snapshot(tmp_path / "source", files)


@pytest.mark.integration
def test_original_tokenizer_packages_compile_with_the_owned_go_frontend(monkeypatch):
    """Strict project gate: both original default-build packages, no rewrite."""
    from pcc.frontends.go import compile_go_packages
    _, files = load_inputs()
    before = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("owned compile launched a tool"))
    module = compile_go_packages(owned_inputs(files), target_triple="x86_64-unknown-linux-gnu")
    assert "define" in str(module)
    assert {name: hashlib.sha256(data).hexdigest() for name, data in load_inputs()[1].items()} == before


def reference_go():
    selected = os.environ.get("PCC_GO_REFERENCE")
    return None if not selected else Path(selected).resolve(strict=True)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if os.environ.get("PCC_GO_REFERENCE") else
                      "set PCC_GO_REFERENCE to an explicit official Go 1.27.3 go executable")
def test_original_tokenizer_packages_build_with_the_official_go_toolchain(tmp_path):
    """Reference only: GOPATH-mode build of the same originals, no downloads."""
    go = reference_go()
    goroot = go.parent.parent
    _, files = load_inputs()
    gopath = tmp_path / "gopath"
    module_root = gopath / "src" / MODULE
    materialize_snapshot(module_root, files)
    environment = {"HOME": str(tmp_path), "PATH": str(go.parent) + ":/usr/bin:/bin", "GOROOT": str(goroot),
                   "GOPATH": str(gopath), "GO111MODULE": "off", "GOTOOLCHAIN": "local", "GOPROXY": "off",
                   "GOFLAGS": "", "CGO_ENABLED": "0", "GOCACHE": str(tmp_path / "cache")}
    version = subprocess.run([str(go), "version"], env=environment, capture_output=True, timeout=30)
    assert version.returncode == 0 and b"go1.27.3 " in version.stdout, version
    listed = subprocess.run([str(go), "list", "-f", "{{.ImportPath}} {{join .GoFiles \",\"}}", REGEX, TOKENIZER],
                            env=environment, capture_output=True, timeout=120)
    assert listed.returncode == 0, listed.stderr
    observed = dict(line.split(" ") for line in listed.stdout.decode().splitlines())
    assert observed == {package: ",".join(name.rsplit("/", 1)[1] for name in names)
                        for package, names in DEFAULT_BUILD.items()}
    built = subprocess.run([str(go), "build", REGEX, TOKENIZER], env=environment, capture_output=True, timeout=300)
    assert built.returncode == 0, built.stderr
    verify_snapshot(module_root, files)
