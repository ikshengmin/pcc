"""Whole-project input/runner contracts, never a math/bits package-pass count."""
import json
from pathlib import Path
import shutil

import pytest

from scripts import ci_go_math_bits_project as project
from scripts.ci_source_frontier_support import materialize_snapshot, sha256, verify_snapshot


def test_complete_original_package_and_license_are_locked_without_slices():
    lock, files = project.load_inputs()
    assert lock["revision"] == "2aae1000ea1d4b28923b96db28acf4e3b99d6aff"
    assert lock["version"] == "go1.27.3" and lock["license"] == "BSD-3-Clause"
    assert lock["package_inventory_complete"] is True
    assert lock["complete_build_closure"] is False
    assert len(files) == 12 and sum(map(len, files.values())) == 75520
    assert sha256(files["LICENSE"]) == "911f8f5782931320f5b8d1160a76365b83aea6447ee6c04fa6d5591467db9dad"
    assert files["VERSION"].splitlines()[0] == b"go1.27.3"
    assert set(name.rsplit("/", 1)[-1] for name in files if name.endswith(".go")) == {
        "bits.go", "bits_errors.go", "bits_errors_bootstrap.go", "bits_tables.go",
        "bits_test.go", "example_math_test.go", "example_test.go", "export_test.go",
        "make_examples.go", "make_tables.go",
    }


def test_normal_build_and_both_test_packages_retain_runtime_semantics():
    lock, files = project.load_inputs()
    assert project.NORMAL == ("bits.go", "bits_errors.go", "bits_tables.go")
    assert project.INTERNAL_TEST == ("export_test.go",)
    assert project.EXTERNAL_TEST == ("bits_test.go", "example_math_test.go", "example_test.go")
    assert b"//go:build !compiler_bootstrap" in files[project.PACKAGE_DIR + "/bits_errors.go"]
    assert b"//go:linkname overflowError runtime.overflowError" in files[project.PACKAGE_DIR + "/bits_errors.go"]
    tests = files[project.PACKAGE_DIR + "/bits_test.go"]
    assert b'package bits_test' in tests and b'. "math/bits"' in tests
    assert b"func init()" in tests and b"recover()" in tests and b"err.(runtime.Error)" in tests
    assert b"const DeBruijn64 = deBruijn64" in files[project.PACKAGE_DIR + "/export_test.go"]
    assert "TestDiv64PanicZero" in lock["test_entry"]["tests_and_examples"]
    assert "ExampleDiv64" in lock["test_entry"]["tests_and_examples"]
    assert set(project.DEPENDENCIES) == {"unsafe", "runtime", "testing", "fmt", "os", "testing/internal/testdeps"}


@pytest.mark.parametrize("name", ("src/math/bits/bits.go", "src/math/bits/bits_test.go", "LICENSE"))
def test_changed_original_bytes_are_rejected(tmp_path, name):
    destination = tmp_path / "inputs"
    shutil.copytree(project.FIXTURE, destination)
    path = destination / name
    raw = path.read_bytes()
    path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    with pytest.raises(ValueError, match="checksum"):
        project.load_inputs(destination)


def test_extra_package_source_cannot_enter_reference_selection(tmp_path):
    destination = tmp_path / "inputs"
    shutil.copytree(project.FIXTURE, destination)
    (destination / project.PACKAGE_DIR / "injected_test.go").write_text("package bits")
    with pytest.raises(ValueError, match="unpinned"):
        project.load_inputs(destination)


def test_materialized_source_contains_all_original_tests_without_a_fake_main(tmp_path):
    _, files = project.load_inputs()
    snapshot = tmp_path / "source"
    materialize_snapshot(snapshot, files)
    verify_snapshot(snapshot, files)
    assert (snapshot / project.PACKAGE_DIR / "example_math_test.go").read_bytes() == files[project.PACKAGE_DIR + "/example_math_test.go"]
    assert not (snapshot / project.PACKAGE_DIR / "main.go").exists()


def test_only_source_admission_is_attempted_by_the_owned_stage(tmp_path):
    _, files = project.load_inputs()
    report = project.initial_report()
    project.run_owned(report, files, tmp_path)
    row = report["owned"]["package_frontend"]
    assert row["status"] == "UNSUPPORTED"
    assert row["code"] == "go.unsupported.linkname-directives"
    assert (row["filename"], row["line"], row["column"]) == ("bits_errors.go", 11, 1)
    assert "owned object-symbol binding and runtime ownership" in row["diagnostic"]
    assert not (tmp_path / "package.ll").exists()
    assert report["project_accepted"] is False
    assert all(report["owned"][name]["status"] == "UNRUN" for name in (
        "dependency_closure", "test_entry", "test_build", "tests_and_examples",
    ))
    assert (tmp_path / "admission-main.go").read_text() == project.ADMISSION_MAIN


def test_no_implicit_reference_or_old_ci_tool_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("PCC_GO_MATH_BITS_REFERENCE", raising=False)
    monkeypatch.delenv("PCC_GO_MATH_BITS_GOROOT", raising=False)
    monkeypatch.setenv("PCC_GO_REFERENCE", "/old/go1.24.13/bin/go")
    lock, files = project.load_inputs()
    report = project.initial_report()
    project.run_reference(report, lock, files, tmp_path)
    row = report["external_reference"]
    assert row["status"] == row["prerequisite"]["status"] == "BLOCKED"
    assert "Go 1.27.3 required" in row["prerequisite"]["diagnostic"]
    assert row["tests_and_examples"]["status"] == "UNRUN"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("mutate_tool", [False, True])
def test_wrong_reference_version_stops_before_dependency_or_test_commands(tmp_path, monkeypatch, mutate_tool):
    lock, files = project.load_inputs()
    goroot = tmp_path / "goroot"
    materialize_snapshot(goroot, files)
    (goroot / "bin").mkdir()
    go = goroot / "bin/go"
    go.write_bytes(b"model only; never executed")
    tools = goroot / "pkg/tool/linux_amd64"
    tools.mkdir(parents=True)
    for name in ("asm", "compile", "link", "vet"):
        (tools / name).write_bytes(b"model only; never executed")
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setenv("PCC_GO_MATH_BITS_REFERENCE", str(go))
    monkeypatch.setenv("PCC_GO_MATH_BITS_GOROOT", str(goroot))
    calls = []

    def wrong_version(command, directory, name, environment, timeout):
        calls.append(command)
        assert environment["GOTOOLCHAIN"] == "local" and environment["GOFLAGS"] == ""
        (directory / (name + ".stdout")).write_bytes(b"go version go1.24.13 linux/amd64\n")
        if mutate_tool:
            go.write_bytes(b"changed during version; model only")
        return {"status": "PASS", "returncode": 0}

    monkeypatch.setattr(project, "process", wrong_version)
    report = project.initial_report()
    if mutate_tool:
        with pytest.raises(ValueError, match="binaries changed"):
            project.run_reference(report, lock, files, output)
        assert report["external_reference"]["prerequisite"]["tools_unchanged"] is False
    else:
        project.run_reference(report, lock, files, output)
        assert report["external_reference"]["prerequisite"]["tools_unchanged"] is True
    assert calls == [[str(go), "version"]]
    assert report["external_reference"]["status"] == ("FAIL" if mutate_tool else "BLOCKED")
    assert report["external_reference"]["tests_and_examples"]["status"] == "UNRUN"


@pytest.mark.parametrize("name", ("bin/go", "pkg/tool/linux_amd64/compile", "pkg/tool/linux_amd64/link"))
def test_reference_tool_replacement_cannot_keep_an_unchanged_identity(tmp_path, name):
    for path in ("bin/go", *("pkg/tool/linux_amd64/" + item for item in ("asm", "compile", "link", "vet"))):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"model only; never executed")
    before = project.reference_tools(tmp_path)
    project.verify_reference_tools(tmp_path, before)
    (tmp_path / name).write_bytes(b"replacement model; never executed")
    with pytest.raises(ValueError, match="binaries changed"):
        project.verify_reference_tools(tmp_path, before)


def events(names):
    rows = []
    for name in names:
        rows.extend({"Package": project.PACKAGE, "Action": action, "Test": name} for action in ("run", "pass"))
    rows.append({"Package": project.PACKAGE, "Action": "pass"})
    return rows


def encode_events(rows):
    return ("\n".join(json.dumps(row) for row in rows) + "\n").encode()


def test_reference_requires_every_real_test_and_example_not_benchmarks():
    lock, _ = project.load_inputs()
    expected = lock["test_entry"]["tests_and_examples"]
    assert project.TEST_COMMAND == ("test", "-json", "-count=1", "-timeout=60s", "-p=2", "math/bits")
    assert not any(name.startswith("Benchmark") for name in expected)
    result = project.test_events(encode_events(events(expected)), expected)
    assert result["status"] == "PASS" and set(result["executed"]) == set(expected)
    assert project.initial_report()["project_accepted"] is False


@pytest.mark.parametrize("fault", ("missing", "skip", "fail", "duplicate", "no-package-pass"))
def test_reference_cannot_pass_missing_skipped_failed_or_duplicate_cases(fault):
    expected = ["TestUintSize", "ExampleDiv64"]
    rows = events(expected)
    if fault == "missing":
        rows = events(expected[:1])
    elif fault in ("skip", "fail"):
        rows[1]["Action"] = fault
    elif fault == "duplicate":
        rows.insert(0, dict(rows[0]))
    else:
        rows.pop()
    with pytest.raises(ValueError):
        project.test_events(encode_events(rows), expected)


def graph_rows(root):
    directory = root / project.PACKAGE_DIR
    directory.mkdir(parents=True)
    for name in project.NORMAL + project.INTERNAL_TEST + project.EXTERNAL_TEST:
        (directory / name).write_bytes(b"graph protocol model; not Go input")
    rows = [
        {"Name": "bits", "ImportPath": "math/bits", "Dir": str(directory), "GoFiles": list(project.NORMAL)},
        {"Name": "bits", "ImportPath": "math/bits [math/bits.test]", "ForTest": "math/bits",
         "Dir": str(directory), "GoFiles": list(project.NORMAL + project.INTERNAL_TEST)},
        {"Name": "bits_test", "ImportPath": "math/bits_test [math/bits.test]", "ForTest": "math/bits",
         "Dir": str(directory), "GoFiles": list(project.EXTERNAL_TEST)},
        {"Name": "main", "ImportPath": "math/bits.test", "Dir": str(directory), "GoFiles": ["_testmain.go"]},
    ]
    for name in project.DEPENDENCIES:
        dep = root / "src" / name
        dep.mkdir(parents=True)
        rows.append({"ImportPath": name, "Dir": str(dep)})
    return rows


def test_actual_reference_graph_includes_original_test_packages_and_sources(tmp_path):
    rows = graph_rows(tmp_path)
    decoded = project.decode_graph(b"\n ".join(json.dumps(row).encode() for row in rows))
    assert decoded == rows
    sources = project.validate_graph(decoded, tmp_path)
    assert set(sources) == {project.PACKAGE_DIR + "/" + name for name in
                            project.NORMAL + project.INTERNAL_TEST + project.EXTERNAL_TEST}


@pytest.mark.parametrize("fault", ("missing-testing", "missing-example", "bootstrap", "incomplete", "escaping"))
def test_reference_graph_cannot_hide_missing_or_replaced_dependencies(tmp_path, fault):
    rows = graph_rows(tmp_path)
    if fault == "missing-testing":
        rows = [row for row in rows if row["ImportPath"] != "testing"]
    elif fault == "missing-example":
        rows[2]["GoFiles"].remove("example_test.go")
    elif fault == "bootstrap":
        rows[0]["GoFiles"].remove("bits_errors.go")
        rows[0]["GoFiles"].append("bits_errors_bootstrap.go")
    elif fault == "incomplete":
        rows[-1]["Incomplete"] = True
    else:
        rows[-1]["Dir"] = str(tmp_path.parent)
    with pytest.raises(ValueError):
        project.validate_graph(rows, tmp_path)


def test_cli_is_ci_only_and_keeps_existing_guard_owner(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(SystemExit):
        project.main(["--output", str(tmp_path / "not-created")])
    assert not (tmp_path / "not-created").exists()
    source = (project.ROOT / "scripts/ci_go_math_bits_project.py").read_text()
    assert "return run_ci(ROOT, FIXTURE," in source
    assert "return 1  # Owned test build/execution is still UNRUN" in source


@pytest.mark.integration
def test_whole_math_bits_project_owned_and_original_tests(tmp_path):
    # The existing Go integration worker owns the 90-second/1-GiB guard.
    # Calling main() here would nest its guarded-command performance lease.
    # Real project attempt: no expected rejection,
    # skip, xfail or official-tool fallback can turn an incomplete chain green.
    output = tmp_path / "whole-project"
    output.mkdir()
    returncode = project.run_worker(output)
    report = json.loads((output / "frontier.json").read_text())
    assert returncode == 0 and report["project_accepted"] is True, (
        "whole math/bits project is incomplete; "
        + json.dumps({"owned": report["owned"], "external_reference_status": report["external_reference"]["status"],
                      "reference_prerequisite": report["external_reference"]["prerequisite"],
                      "runner": report.get("runner"), "receipt": str(output / "frontier.json")}, sort_keys=True)
    )
