"""CI-only whole math/bits target: owned admission and separate official tests.

The unchanged package/test inventory is complete. The owned testing/runtime
closure is not: a completed source-stage receipt must never imply package PASS.
Reference execution requires an explicitly supplied, matching Go 1.27.3 tree.
"""
from __future__ import annotations

from contextlib import contextmanager
import builtins
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))

from scripts.ci_source_frontier_support import (
    bounded_bytes,
    input_path,
    load_source_lock,
    materialize_snapshot,
    run_ci,
    save,
    sha256,
    source_identity,
    verify_snapshot,
)

FIXTURE = ROOT / "tests/go/upstream/go1.27.3"
LOCK_SHA256 = "01333873af479122f8e75ffbd52da4e5f312a90b059de2c17afac83b790427a8"
REVISION = "2aae1000ea1d4b28923b96db28acf4e3b99d6aff"
PACKAGE = "math/bits"
PACKAGE_DIR = "src/math/bits"
RELEASE = "go1.27.3"
NORMAL = ("bits.go", "bits_errors.go", "bits_tables.go")
INTERNAL_TEST = ("export_test.go",)
EXTERNAL_TEST = ("bits_test.go", "example_math_test.go", "example_test.go")
EXCLUDED = ("bits_errors_bootstrap.go", "make_examples.go", "make_tables.go")
DEPENDENCIES = ("unsafe", "runtime", "testing", "fmt", "os", "testing/internal/testdeps")
TEST_COMMAND = ("test", "-json", "-count=1", "-timeout=60s", "-p=2", PACKAGE)
# Only an import-admission root for the public PCC package API, never a test.
ADMISSION_MAIN = 'package main;import "math/bits";func main(){_=bits.LeadingZeros(1)}'
DRIVERS = (
    "scripts/ci_go_math_bits_project.py", "scripts/ci_source_frontier_support.py",
    "scripts/cpython_conformance/run.py", "scripts/ci_validation_run.py",
    "scripts/run_process_tree_sample.py", "scripts/platform_process_watchdog.py",
)


def load_inputs(directory=FIXTURE):
    lock, files = load_source_lock(directory, LOCK_SHA256)
    names = set(NORMAL + INTERNAL_TEST + EXTERNAL_TEST + EXCLUDED)
    expected = {"LICENSE", "VERSION"} | {PACKAGE_DIR + "/" + name for name in names}
    if (lock["project"] != PACKAGE or lock["version"] != RELEASE
            or lock["revision"] != REVISION or lock["license"] != "BSD-3-Clause"
            or lock["package_inventory_complete"] is not True or set(files) != expected
            or tuple(lock["normal_go_files"]) != NORMAL
            or tuple(lock["test_go_files"]) != INTERNAL_TEST
            or tuple(lock["xtest_go_files"]) != EXTERNAL_TEST
            or set(lock["excluded_go_files"]) != set(EXCLUDED)
            or tuple(lock["required_dependencies"]) != DEPENDENCIES
            or tuple(lock["test_entry"]["command"]) != ("go", *TEST_COMMAND)):
        raise ValueError("whole math/bits identity or normal/test file selection differs")
    verify_originals(directory, files)
    declared = []
    for name in EXTERNAL_TEST:
        declared.extend(re.findall(r"(?m)^func ((?:Test|Example)[A-Za-z0-9_]*)\(",
                                   files[PACKAGE_DIR + "/" + name].decode("utf-8")))
    if declared != lock["test_entry"]["tests_and_examples"] or len(set(declared)) != len(declared):
        raise ValueError("whole original test/example entry inventory differs")
    return lock, files


def verify_originals(directory, files):
    for name, value in files.items():
        if bounded_bytes(input_path(directory, name), 1024 * 1024) != value:
            raise ValueError("original source differs: " + name)
    package = input_path(directory, PACKAGE_DIR)
    if {path.name for path in package.iterdir()} != set(NORMAL + INTERNAL_TEST + EXTERNAL_TEST + EXCLUDED):
        raise ValueError("math/bits directory has missing or unpinned inputs")


def unrun(reason):
    return {"status": "UNRUN", "blocked_by": reason}


def initial_report():
    return {
        "schema_version": 1, "project": PACKAGE, "version": RELEASE,
        "upstream_revision": REVISION, "source_lock_sha256": LOCK_SHA256,
        "owner": "host-pcc0/owned Go frontend; NOT native pcc1",
        "project_accepted": False, "complete_build_closure": False,
        "source_rewritten": False, "source_integrity": {"status": "UNRUN"},
        "package_selection": {"normal": list(NORMAL), "internal_test": list(INTERNAL_TEST),
                              "external_test": list(EXTERNAL_TEST), "excluded": list(EXCLUDED)},
        "owned": {
            "package_frontend": {"status": "UNRUN"},
            "dependency_closure": unrun("owned source/build closure is missing: " + ", ".join(DEPENDENCIES)),
            "test_entry": unrun("owned package-test generation and testing.MainStart integration missing"),
            "test_build": unrun("package admission, original dependency closure and test entry required"),
            "tests_and_examples": unrun("whole upstream test binary has not been built"),
        },
        "external_reference": {
            "owner": "official Go, never an owned-compiler fallback", "required_version": RELEASE,
            "status": "UNRUN", "prerequisite": {"status": "UNRUN"},
            "dependency_graph": {"status": "UNRUN"}, "tests_and_examples": {"status": "UNRUN"},
        },
    }


@contextmanager
def owned_compilation():
    """Confine the compiler call; official tool use remains outside this scope."""
    from unittest.mock import patch
    original_import = builtins.__import__

    def owned_import(name, *args, **kwargs):
        if name == "llvmlite" or name.startswith("llvmlite."):
            raise RuntimeError("external LLVM import forbidden in owned Go admission")
        return original_import(name, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise RuntimeError("external process forbidden in owned Go admission")

    with patch.object(builtins, "__import__", owned_import), patch.object(subprocess, "Popen", forbidden):
        yield


def run_owned(report, files, directory):
    row = report["owned"]
    row["package_frontend"] = {"status": "RUNNING"}
    save(directory / "frontier.json", report)
    (directory / "admission-main.go").write_text(ADMISSION_MAIN, encoding="utf-8")
    try:
        with owned_compilation():
            from pcc.frontends.go import GoDiagnostic, compile_go_packages
            inputs = {"main": [("admission-main.go", ADMISSION_MAIN)], PACKAGE: [
                (name, files[PACKAGE_DIR + "/" + name].decode("utf-8")) for name in NORMAL
            ]}
            module = compile_go_packages(inputs, target_triple="x86_64-unknown-linux-gnu")
    except GoDiagnostic as error:
        row["package_frontend"] = {
            "status": "UNSUPPORTED" if getattr(error, "kind", None) == "unsupported" else "FAIL",
            "diagnostic": str(error), "code": getattr(error, "code", None),
            "filename": error.filename, "line": error.line, "column": error.column,
        }
    else:
        raw = str(module).encode("utf-8")
        (directory / "package.ll").write_bytes(raw)
        row["package_frontend"] = {"status": "PASS", "ir_sha256": sha256(raw),
                                   "claim": "all normal production source files to IR only; NOT upstream tests"}
    save(directory / "frontier.json", report)


def process(command, directory, name, environment, timeout):
    from scripts.cpython_conformance.run import run_process
    out, err = directory / (name + ".stdout"), directory / (name + ".stderr")
    try:
        result = run_process(command, cwd=directory, stdout=out, stderr=err,
                             timeout=timeout, env=environment)
    except OSError as error:
        result = {"status": "BLOCKED", "diagnostic": str(error)}
    else:
        result["status"] = ("PASS" if result["returncode"] == 0 and not result["timed_out"]
                            else "FAIL")
        result.update(stdout_sha256=sha256(out.read_bytes()), stderr_sha256=sha256(err.read_bytes()))
    save(directory / (name + ".receipt.json"), {"command": command, **result})
    return result


def decode_graph(raw):
    text, decoder, values = raw.decode("utf-8"), json.JSONDecoder(), []
    while text.strip():
        value, end = decoder.raw_decode(text.lstrip())
        values.append(value)
        text = text.lstrip()[end:]
    return values


def validate_graph(rows, goroot):
    """Validate the actual reference graph, not a fabricated owned dependency map."""
    if not rows or any(row.get("Error") or row.get("DepsErrors") or row.get("Incomplete") for row in rows):
        raise ValueError("official package-test dependency graph is incomplete")
    paths = {row["ImportPath"].split(" [", 1)[0] for row in rows}
    if not set((*DEPENDENCIES, PACKAGE, PACKAGE + "_test", PACKAGE + ".test")) <= paths:
        raise ValueError("official package-test entry or required dependency is missing")
    selections = (("bits", False, NORMAL), ("bits", True, NORMAL + INTERNAL_TEST),
                  ("bits_test", True, EXTERNAL_TEST))
    for name, for_test, wanted in selections:
        selected = [row for row in rows if row.get("Name") == name
                    and bool(row.get("ForTest")) == for_test
                    and row["ImportPath"].split(" [", 1)[0] in (PACKAGE, PACKAGE + "_test")]
        if len(selected) != 1 or set(selected[0].get("GoFiles", [])) != set(wanted):
            raise ValueError("official normal/internal/external test file selection differs")
    sources = {}
    for row in rows:
        if row["ImportPath"] == PACKAGE + ".test":
            continue  # _testmain.go is generated by cmd/go, not an original source file.
        directory = Path(row.get("Dir", "")).resolve()
        if not directory.is_relative_to((goroot / "src").resolve()):
            raise ValueError("reference dependency escaped the explicit GOROOT: " + row["ImportPath"])
        for field in ("GoFiles", "CgoFiles", "CFiles", "CXXFiles", "HFiles", "SFiles", "SysoFiles", "EmbedFiles"):
            for name in row.get(field, []):
                path = input_path(directory, name)
                sources[path.relative_to(goroot).as_posix()] = sha256(path.read_bytes())
    return sources


def test_events(raw, expected):
    """Require every original Test/Example, no skipped cases, and package success."""
    events = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    if any(row.get("Action") in ("skip", "fail") for row in events):
        raise ValueError("upstream test/example failed or was skipped")
    events = [row for row in events if row.get("Package") == PACKAGE]
    started = [row["Test"] for row in events if row.get("Action") == "run" and "Test" in row]
    passed = [row["Test"] for row in events if row.get("Action") == "pass" and "Test" in row]
    if (set(started) != set(expected) or set(passed) != set(expected)
            or len(started) != len(expected) or len(passed) != len(expected)
            or sum(row.get("Action") == "pass" and "Test" not in row for row in events) != 1):
        raise ValueError("whole original test/example execution is incomplete")
    return {"status": "PASS", "executed": passed, "scope": "whole package official reference only"}


def reference_tools(goroot):
    directory = input_path(goroot, "pkg/tool/linux_amd64")
    names = {path.name for path in directory.iterdir()}
    if not {"asm", "compile", "link", "vet"} <= names:
        raise ValueError("explicit Go 1.27.3 compiler/linker/assembler/vet tools are missing")
    paths = ["bin/go", *("pkg/tool/linux_amd64/" + name for name in sorted(names))]
    return {name: sha256(input_path(goroot, name).read_bytes()) for name in paths}


def verify_reference_tools(goroot, before):
    if reference_tools(goroot) != before:
        raise ValueError("official Go driver/tool binaries changed during package tests")


def run_reference(report, lock, files, directory):
    row = report["external_reference"]
    go_name, root_name = os.environ.get("PCC_GO_MATH_BITS_REFERENCE"), os.environ.get("PCC_GO_MATH_BITS_GOROOT")
    if not go_name or not root_name:
        row.update(status="BLOCKED", prerequisite={"status": "BLOCKED", "diagnostic":
            "explicit PCC_GO_MATH_BITS_REFERENCE and PCC_GO_MATH_BITS_GOROOT for Go 1.27.3 required"})
        return
    go, goroot = Path(go_name), Path(root_name)
    if not go.is_absolute() or not goroot.is_absolute() or not go.is_file():
        raise ValueError("official Go tool and GOROOT must be explicit existing absolute paths")
    go, goroot = go.resolve(), goroot.resolve()
    if go != (goroot / "bin/go").resolve():
        raise ValueError("official tool is not the explicit GOROOT/bin/go")
    verify_originals(goroot, files)
    tools_before = reference_tools(goroot)
    row["prerequisite"]["tool_sha256"] = tools_before
    save(directory / "frontier.json", report)
    try:
        _run_reference(report, lock, files, directory, go, goroot, tools_before)
    finally:
        try:
            verify_reference_tools(goroot, tools_before)
        except (OSError, ValueError) as error:
            row["prerequisite"].update(status="FAIL", tools_unchanged=False, diagnostic=str(error))
            row["status"] = "FAIL"
            raise
        else:
            row["prerequisite"]["tools_unchanged"] = True
    if row["status"] == "UNRUN":
        row["status"] = row["tests_and_examples"]["status"]


def _run_reference(report, lock, files, directory, go, goroot, tools_before):
    row = report["external_reference"]
    environment = dict(os.environ)
    environment.update(GOROOT=str(goroot), GOTOOLCHAIN="local", GOENV="off", GOWORK="off",
                       GOPROXY="off", GOSUMDB="off", GO111MODULE="off", CGO_ENABLED="0",
                       GOFLAGS="", GOEXPERIMENT="", GODEBUG="", GOCACHEPROG="",
                       GOOS="linux", GOARCH="amd64", GOAMD64="v1", GOMAXPROCS="2")
    for variable, name in (("GOCACHE", "go-cache"), ("GOTMPDIR", "go-tmp"), ("GOPATH", "go-path")):
        path = directory / name
        path.mkdir()
        environment[variable] = str(path)
    save(directory / "reference-environment.json", {name: environment[name] for name in (
        "GOROOT", "GOTOOLCHAIN", "GOENV", "GOWORK", "GOPROXY", "GOSUMDB", "GO111MODULE",
        "CGO_ENABLED", "GOFLAGS", "GOEXPERIMENT", "GODEBUG", "GOCACHEPROG", "GOOS", "GOARCH",
        "GOAMD64", "GOMAXPROCS", "GOCACHE", "GOTMPDIR", "GOPATH",
    )})
    version = process([str(go), "version"], directory, "reference-version", environment, 5)
    if (version["status"] != "PASS" or (directory / "reference-version.stdout").read_bytes().strip()
            != b"go version go1.27.3 linux/amd64"):
        row.update(status="BLOCKED", prerequisite={"status": "BLOCKED",
                   "diagnostic": "official Go must report exactly go1.27.3 linux/amd64", "process": version,
                   "tool_sha256": tools_before})
        return
    row["prerequisite"] = {"status": "PASS", "version_process": version, "tool_sha256": tools_before}
    save(directory / "frontier.json", report)
    graph = process([str(go), "list", "-deps", "-test", "-json", PACKAGE], directory,
                    "reference-dependencies", environment, 25)
    row["dependency_graph"] = {"status": "RUNNING", "process": graph}
    if graph["status"] != "PASS":
        row["dependency_graph"]["status"] = "BLOCKED"
        row["status"] = "BLOCKED"
        return
    rows = decode_graph((directory / "reference-dependencies.stdout").read_bytes())
    before = validate_graph(rows, goroot)
    row["dependency_graph"].update(
        status="PASS", source_sha256=before,
        identity_scope="observed reference GOROOT closure; package originals are independently pinned",
    )
    save(directory / "frontier.json", report)
    row["tests_and_examples"] = {"status": "RUNNING"}
    save(directory / "frontier.json", report)
    result = process([str(go), *TEST_COMMAND], directory, "reference-tests", environment, 80)
    row["tests_and_examples"]["process"] = result
    verify_originals(goroot, files)
    recheck = process([str(go), "list", "-deps", "-test", "-json", PACKAGE], directory,
                      "reference-dependencies-after", environment, 10)
    row["dependency_graph"]["recheck"] = recheck
    if recheck["status"] != "PASS":
        raise ValueError("official dependency graph could not be rechecked")
    after_rows = decode_graph((directory / "reference-dependencies-after.stdout").read_bytes())
    if validate_graph(after_rows, goroot) != before:
        raise ValueError("official dependency source changed during whole-package tests")
    if result["status"] == "PASS":
        row["tests_and_examples"].update(test_events((directory / "reference-tests.stdout").read_bytes(),
                                                   lock["test_entry"]["tests_and_examples"]))
    else:
        row["tests_and_examples"]["status"] = result["status"]


def run_worker(directory):
    receipt, report = directory / "frontier.json", initial_report()
    save(receipt, report)
    lock, files = load_inputs()
    before = source_identity(ROOT, DRIVERS)
    report["pcc_source_sha256"] = before
    report["pcc_revision"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
        check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    report["inputs"] = lock["files"]
    report["test_entry"] = lock["test_entry"]
    snapshot = directory / "source"
    materialize_snapshot(snapshot, files)
    report["source_integrity"] = {"status": "PASS", "scope": "preflight", "package_inventory_complete": True}
    save(receipt, report)
    run_owned(report, files, directory)
    try:
        run_reference(report, lock, files, directory)
    except (OSError, ValueError) as error:
        reference = report["external_reference"]
        reference.update(status="FAIL" if any(reference[name]["status"] == "FAIL" for name in
                         ("prerequisite", "dependency_graph", "tests_and_examples")) else "BLOCKED",
                         diagnostic=str(error))
        for name in ("prerequisite", "dependency_graph", "tests_and_examples"):
            if reference[name]["status"] == "RUNNING":
                reference[name].update(status="FAIL", diagnostic=str(error))
                reference["status"] = "FAIL"
        if reference["prerequisite"]["status"] == "UNRUN":
            reference["prerequisite"].update(status="BLOCKED", diagnostic=str(error))
    verify_snapshot(snapshot, files)
    if source_identity(ROOT, DRIVERS) != before or load_inputs()[1] != files:
        raise ValueError("original package/test/compiler inputs changed")
    report["source_integrity"]["scope"] = "identical before and after all attempted stages"
    report["terminal"] = True
    save(receipt, report)
    return 1  # Owned test build/execution is still UNRUN even if the oracle passes.


def main(argv=None):
    return run_ci(ROOT, FIXTURE, Path(__file__).resolve(), initial_report, run_worker, argv)


if __name__ == "__main__":
    raise SystemExit(main())
