"""Project packet/runner obligations, not a replacement for upstream Java tests.

No test here claims that Commons Numbers or its original JUnit suite passes.
Compiler/native/reference execution is delegated to CI.
"""
import json
import os
import shutil

import pytest

from scripts.ci_java_primes_project import (
    ENTRY, FIXTURE, LOCK_SHA256, PRODUCTION, REVISION, TESTS,
    compile_original_stages, initial_report, load_inputs, main, original_sources, run_worker,
)
from scripts.ci_source_frontier_support import materialize_snapshot, sha256, verify_snapshot


class Rejection(ValueError):
    pass


class Unsupported(Rejection):
    code = "java.unsupported.string-literals"


def test_packet_is_complete_unchanged_primes_module_and_original_tests():
    lock, files = load_inputs()
    assert lock["revision"] == REVISION == "77f715769e4039acb4babd9add80b78a06ca7279"
    assert len(files) == 17
    assert sum(name.startswith("commons-numbers-primes/") for name in files) == 12
    assert {name for name in files if name.endswith(".java")} == set(PRODUCTION + TESTS)
    assert {"LICENSE", "NOTICE", "pom.xml", "README.md", "RELEASE-NOTES.txt"} <= files.keys()
    assert sha256((FIXTURE / "pcc-source-lock.json").read_bytes()) == LOCK_SHA256
    assert lock["complete_module_source_closure"] is True
    assert lock["complete_upstream_test_source_closure"] is True
    assert lock["complete_build_closure"] is False
    assert len(lock["upstream_test_classes"]) == 3
    assert all(b"@Test" in files[name] for name in TESTS)
    assert b"String NUMBER_TOO_SMALL" in files[PRODUCTION[0]]
    assert b".modPow(" in files[PRODUCTION[1]]
    assert b"primeNumbers.stream()" in files[PRODUCTION[1]]


def test_parent_junit_versions_and_licenses_are_not_a_complete_dependency_closure():
    lock, _ = load_inputs()
    dependencies = lock["dependencies"]
    assert dependencies["parent"]["coordinate"] == "org.apache.commons:commons-parent:72"
    assert dependencies["parent"]["inherited_parent"] == "org.apache:apache:33"
    assert dependencies["parent"]["junit_bom"] == "org.junit:junit-bom:5.11.0-M2"
    assert dependencies["parent"]["surefire_plugin"] == "3.3.1"
    assert dependencies["junit"]["platform_version"] == "1.11.0-M2"
    assert dependencies["junit"]["license"] == "EPL-2.0"
    assert dependencies["junit"]["source_closure_present"] is False
    assert dependencies["junit"]["binary_reference_closure_present"] is False
    assert dependencies["junit"]["reference_console_sha256"] is None
    assert {entry["coordinate"] for entry in dependencies["transitives"]} == {
        "org.apiguardian:apiguardian-api:1.1.2", "org.opentest4j:opentest4j:1.3.0",
    }
    assert all(entry["license"] == "Apache-2.0" for entry in dependencies["transitives"])
    assert dependencies["missing"]


def test_every_production_and_upstream_test_byte_survives_materialization(tmp_path):
    _, files = load_inputs()
    snapshot = tmp_path / "source"
    materialize_snapshot(snapshot, files)
    verify_snapshot(snapshot, files)
    sources = original_sources(files, PRODUCTION + TESTS)
    assert tuple(sources) == PRODUCTION + TESTS
    assert all(sources[name].encode("utf-8") == files[name] for name in sources)
    assert all((snapshot / name).read_bytes() == files[name] for name in files)
    (snapshot / "extra.java").write_text("class Extra {}")
    with pytest.raises(ValueError, match="unpinned"):
        verify_snapshot(snapshot, files)


@pytest.mark.parametrize("name", (*PRODUCTION, *TESTS, "LICENSE", "NOTICE", "pom.xml"))
def test_changed_original_source_test_or_notice_fails_closed(tmp_path, name):
    destination = tmp_path / "fixture"
    shutil.copytree(FIXTURE, destination)
    path = destination / name
    original = path.read_bytes()
    path.write_bytes(bytes((original[0] ^ 1,)) + original[1:])
    with pytest.raises(ValueError, match="checksum"):
        load_inputs(destination)


def test_real_production_rejection_blocks_dependent_tests_without_rewriting(tmp_path):
    _, files = load_inputs()
    report = initial_report()
    seen = []

    def reject(sources, **options):
        seen.append((sources, options))
        raise Unsupported("Primes.java:31:44: unsupported Java source token")

    receipt = tmp_path / "frontier.json"
    compile_original_stages(report, receipt, files, reject, Rejection, Unsupported)
    assert len(seen) == 1
    assert seen[0] == (original_sources(files, PRODUCTION), ENTRY)
    assert report["owned"]["production_compile"]["status"] == "UNSUPPORTED"
    assert report["owned"]["production_compile"]["code"] == Unsupported.code
    assert report["owned"]["upstream_test_compile"]["status"] == "UNRUN"
    assert report["owned"]["upstream_test_execution"]["status"] == "UNRUN"
    assert json.loads(receipt.read_text())["project_accepted"] is False


def test_successful_production_stage_submits_all_three_original_test_classes(tmp_path):
    _, files = load_inputs()
    report = initial_report()
    seen = []

    def record(sources, **options):
        seen.append((sources, options))

    compile_original_stages(report, tmp_path / "frontier.json", files, record, Rejection, Unsupported)
    assert seen == [(original_sources(files, PRODUCTION), ENTRY),
                    (original_sources(files, PRODUCTION + TESTS), ENTRY)]
    assert report["owned"]["upstream_test_compile"]["status"] == "PASS"
    assert report["owned"]["upstream_test_execution"]["status"] == "UNRUN"
    assert report["reference"]["production_compile"]["status"] == "UNRUN"
    assert report["complete_build_closure"] is False and report["project_accepted"] is False


def test_internal_failure_cannot_be_relabelled_as_an_expected_project_frontier(tmp_path):
    _, files = load_inputs()
    report = initial_report()

    def crash(*args, **kwargs):
        raise TypeError("internal error")

    with pytest.raises(TypeError, match="internal error"):
        compile_original_stages(report, tmp_path / "frontier.json", files, crash, Rejection, Unsupported)
    assert report["owned"]["production_compile"]["status"] == "FAIL"
    assert report["owned"]["upstream_test_compile"]["status"] == "UNRUN"


def test_runner_refuses_local_execution_before_creating_evidence(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    output = tmp_path / "uncreated"
    with pytest.raises(SystemExit) as error:
        main(["--output", str(output)])
    assert error.value.code == 2 and not output.exists()


def test_jdk_and_junit_reference_stages_cannot_stand_in_for_owned_project_execution():
    report = initial_report()
    assert report["reference"]["upstream_test_units"] == list(TESTS)
    assert report["reference"]["upstream_test_compile"]["status"] == "UNRUN"
    assert report["reference"]["upstream_test_execution"]["status"] == "UNRUN"
    assert report["production_bytecode_or_jar_used"] is False
    assert report["native_pcc1"]["status"] == report["five_gc"]["status"] == "UNRUN"
    assert report["source_rewritten"] is False and report["project_accepted"] is False


@pytest.mark.integration
@pytest.mark.xdist_group(name="java-original-project")
@pytest.mark.pcc_gate(unavailable=None if os.environ.get("CI", "").lower() == "true"
                      else "original-project compiler execution is CI-only")
def test_original_primes_whole_project_owned_source_frontier(tmp_path, monkeypatch):
    """Real project gate: the current first compiler rejection is a test FAIL.

    No mocked compiler, xfail, accepted-rejection assertion or test rewriting.
    External JDK reference execution cannot satisfy this owned lane.
    """
    monkeypatch.delenv("PCC_JAVA_RAW_REFERENCE", raising=False)
    output = tmp_path / "original-primes-project"
    # ci_validation_run already owns the 90-second/1-GiB process-tree guard.
    # Calling the standalone guarded main here would reacquire its global lock.
    output.mkdir()
    returncode = run_worker(output)
    report = json.loads((output / "frontier.json").read_text(encoding="utf-8"))
    assert report.get("terminal") is True, report.get("runner")
    assert report["source_integrity"]["status"] == "PASS", report["source_integrity"]
    for stage in ("production_compile", "upstream_test_compile", "upstream_test_execution"):
        assert report["owned"][stage]["status"] == "PASS", report["owned"][stage]
    assert returncode == 0 and report["project_accepted"] is True, report["dependency_closure"]
