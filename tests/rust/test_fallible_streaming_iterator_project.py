"""Unchanged real crate: strict PCC gate and separately labeled reference.

The PCC project test requires success, not an expected capability rejection.
No new execution has been performed locally; these are CI obligations.
"""
from hashlib import sha256
import os
import platform
import shutil
import subprocess
import sys

import pytest

from scripts.ci_fallible_streaming_iterator import (
    EDITION, FIXTURE, LOCK_SHA256, ORIGINAL_PATHS, ROOT, TARGET,
    attempt, initial_report, load_inputs, main,
)
from scripts.ci_source_frontier_support import (
    RSS_BYTES, materialize_snapshot, save, verify_snapshot,
)
from tests.rust.certification_support import actual_check_prerequisite, darwin_check_prerequisite

CONSUMER = ROOT / "tests/rust/project_consumers/fallible_streaming_iterator.rs"
EXPECTED = bytes.fromhex("1032547698badcfe")


def ci_prerequisite():
    if os.environ.get("CI", "").lower() != "true":
        return "CI-only real-project compilation; local execution is not authorized"
    return None


def reference_prerequisite():
    missing = ci_prerequisite() or actual_check_prerequisite()
    if missing:
        return missing
    if not sys.platform.startswith("linux") or platform.machine() not in {"x86_64", "AMD64"}:
        return "native reference requires Linux x86-64"
    if not shutil.which("cc"):
        return "external reference system linker is unavailable; no PCC fallback"
    return None


def digest(raw):
    return sha256(raw).hexdigest()


def test_complete_original_crate_pin_and_default_feature_contract():
    lock, files = load_inputs()
    assert set(files) == ORIGINAL_PATHS and len(files) == 7
    assert lock["complete_upstream_tree"] is True
    assert lock["complete_build_closure"] is False
    assert lock["edition"] == 2015 and lock["features"] == [] and lock["dependencies"] == []
    assert len(files["src/lib.rs"]) == 21146
    assert digest(files["src/lib.rs"]) == "7fb7428997bdd89c3ee0818ecee642f8d93882c9c66171c9c6194b732f337089"
    assert b'#![cfg_attr(not(feature = "std"), no_std)]' in files["src/lib.rs"]
    assert b"fn next(&mut self) -> Result<Option<&Self::Item>, Self::Error>" in files["src/lib.rs"]
    assert b"type Item: ?Sized;" in files["src/lib.rs"]
    assert b"#[cfg(test)]" in files["src/lib.rs"] and b"#[test]" not in files["src/lib.rs"]
    assert all(row["license"] == "MIT OR Apache-2.0" for row in lock["files"])
    assert digest((FIXTURE / "pcc-source-lock.json").read_bytes()) == LOCK_SHA256


@pytest.mark.parametrize("name", sorted(ORIGINAL_PATHS))
def test_same_size_edits_to_any_original_are_rejected(tmp_path, name):
    copy = tmp_path / "crate"
    shutil.copytree(FIXTURE, copy)
    path = copy / name
    original = path.read_bytes()
    path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    with pytest.raises(ValueError, match="checksum"):
        load_inputs(copy)


def test_materialized_project_has_no_unpinned_source_or_implicit_build_script(tmp_path):
    _, files = load_inputs()
    snapshot = tmp_path / "source"
    materialize_snapshot(snapshot, files)
    verify_snapshot(snapshot, files)
    (snapshot / "build.rs").write_text("fn main() {}")
    with pytest.raises(ValueError, match="unpinned"):
        verify_snapshot(snapshot, files)


def test_project_entry_refuses_local_execution_and_keeps_original_limits(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    destination = tmp_path / "uncreated"
    with pytest.raises(SystemExit) as error:
        main(["--output", str(destination)])
    assert error.value.code == 2 and not destination.exists()
    assert RSS_BYTES == 1073741824
    report = initial_report()
    assert report["first_blocker"] is None
    assert report["project_accepted"] is False and report["external_compiler_used"] is False
    assert report["owned_self_test"]["status"] == "UNRUN"


def test_recording_a_source_blocker_cannot_accept_the_project(tmp_path):
    report = initial_report()

    def reject():
        raise ValueError("unsupported original source")

    assert attempt(report, tmp_path / "frontier.json", "source_parse", reject, ValueError) is False
    assert report["first_blocker"]["stage"] == "source_parse"
    assert report["source_parse"]["status"] == "UNSUPPORTED"
    assert report["project_accepted"] is False
    assert report["owned_object"]["status"] == report["owned_self_test"]["status"] == "UNRUN"


def certify_original(files, directory):
    from pcc.frontends.rust import check_rust_source
    from tests.rust.test_rust_certified_references import toolchain
    audited = toolchain()
    source = files["src/lib.rs"].decode("utf-8")
    checked = check_rust_source(source, toolchain=audited, evidence_directory=directory,
                                target_triple=TARGET, overflow_checks=False, edition=EDITION)
    assert (directory / "original.rs").read_bytes() == files["src/lib.rs"]
    assert checked.provenance.source_sha256 == digest(files["src/lib.rs"])
    assert checked.provenance.edition == "2015"
    return audited, checked


@pytest.mark.integration
@pytest.mark.xdist_group(name="rust-native-reference")
@pytest.mark.pcc_gate(unavailable=ci_prerequisite() or actual_check_prerequisite())
def test_actual_same_original_certification_then_pcc_object(tmp_path, monkeypatch):
    from pcc.frontends.rust import compile_rust
    _, files = load_inputs()
    _, checked = certify_original(files, tmp_path / "actual-check")
    receipt = tmp_path / "checked-pcc.json"
    record = {"source_sha256": digest(files["src/lib.rs"]), "edition": EDITION,
              "actual_external_check": "PASS", "pcc_object": "RUNNING", "pcc_native": "UNRUN"}
    save(receipt, record)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("external tool entered owned PCC phase"))
            result = compile_rust(files["src/lib.rs"].decode("utf-8"), rustc_check=checked,
                                  edition=EDITION, target_triple=TARGET, overflow_checks=False,
                                  module_name="fallible_streaming_iterator")
            raw = result.emit_object()
            assert raw.startswith(b"\x7fELF")
            assert result.externally_checked and not result.borrow_checked_by_pcc
        (tmp_path / "pcc-library.o").write_bytes(raw)
        record.update(pcc_object="PASS", object_sha256=digest(raw))
    except Exception as error:
        record.update(pcc_object="FAIL", exception=type(error).__name__, diagnostic=str(error))
        raise  # No expected-RustError escape hatch for this real crate.
    finally:
        try:
            record["source_unchanged"] = load_inputs()[1] == files
            assert record["source_unchanged"]
        except Exception as error:
            record.update(pcc_object="FAIL", source_unchanged=False,
                          exception=type(error).__name__, diagnostic=str(error))
            raise
        finally:
            save(receipt, record)


def reference_process(directory, label, command, observations, *, timeout=15):
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        stdout = getattr(error, "stdout", None) or b""
        stderr = getattr(error, "stderr", None) or b""
        (directory / (label + ".stdout")).write_bytes(stdout)
        (directory / (label + ".stderr")).write_bytes(stderr)
        observations.append({"label": label, "command": command, "status": type(error).__name__,
                             "exit_code": None, "stdout_sha256": digest(stdout), "stderr_sha256": digest(stderr)})
        save(directory / "processes.json", observations)
        raise
    (directory / (label + ".stdout")).write_bytes(result.stdout)
    (directory / (label + ".stderr")).write_bytes(result.stderr)
    observations.append({"label": label, "command": command, "status": "COMPLETE", "exit_code": result.returncode,
                         "stdout_sha256": digest(result.stdout), "stderr_sha256": digest(result.stderr)})
    save(directory / "processes.json", observations)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result


@pytest.mark.integration
@pytest.mark.xdist_group(name="rust-native-reference")
@pytest.mark.pcc_gate(unavailable=reference_prerequisite())
def test_original_package_self_checks_and_real_api_native_reference(tmp_path):
    _, files = load_inputs()
    audited, _ = certify_original(files, tmp_path / "actual-check")
    compiler = audited.root / "bin/rustc"
    assert digest(compiler.read_bytes()) == audited.compiler_sha256
    snapshot = tmp_path / "source"
    materialize_snapshot(snapshot, files)
    consumer = CONSUMER.read_bytes()
    observer = tmp_path / "consumer.rs"
    observer.write_bytes(consumer)
    source = snapshot / "src/lib.rs"
    library = tmp_path / "libfallible_streaming_iterator.rlib"
    binary = tmp_path / "reference-consumer"
    self_test = tmp_path / "upstream-self-test"
    common = ["--edition=2015", "--target=" + TARGET, "-Coverflow-checks=off"]
    observations = []
    receipt = {"reference_only": True, "project_accepted": False, "pcc_native": "UNRUN",
               "source_sha256": digest(files["src/lib.rs"]), "consumer_sha256": digest(consumer),
               "features": [], "edition": EDITION, "status": "RUNNING"}
    save(tmp_path / "reference.json", receipt)
    try:
        reference_process(tmp_path, "library", [str(compiler), *common, "-Cpanic=abort",
            "--crate-name=fallible_streaming_iterator", "--crate-type=rlib", str(source), "-o", str(library)], observations)
        reference_process(tmp_path, "upstream-compile-checks", [str(compiler), *common,
            "-Clinker=" + shutil.which("cc"), "--crate-name=fallible_streaming_iterator",
            "--test", str(source), "-o", str(self_test)], observations)
        reference_process(tmp_path, "upstream-self-test", [str(self_test)], observations, timeout=5)
        # The upstream cfg(test) module contains two object-safety compile
        # checks, not #[test] cases. Runtime API assertions belong to this
        # separately identified consumer, never to an invented upstream test.
        receipt["upstream_runtime_test_count"] = 0
        reference_process(tmp_path, "consumer-link", [str(compiler), *common, "-Cpanic=abort",
            "-Clinker=" + shutil.which("cc"), "--extern", "fallible_streaming_iterator=" + str(library),
            str(observer), "-o", str(binary)], observations)
        actual = reference_process(tmp_path, "consumer-run", [str(binary)], observations, timeout=5)
        assert actual.stdout == EXPECTED and actual.stderr == b""
        receipt.update(status="PASS", actual_stdout_sha256=digest(actual.stdout),
                       expected_stdout_sha256=digest(EXPECTED), binary_sha256=digest(binary.read_bytes()))
    except Exception as error:
        receipt.update(status="FAIL", exception=type(error).__name__, diagnostic=str(error))
        raise
    finally:
        try:
            verify_snapshot(snapshot, files)
            assert load_inputs()[1] == files
            assert CONSUMER.read_bytes() == consumer == observer.read_bytes()
            assert digest(compiler.read_bytes()) == audited.compiler_sha256
            receipt["source_and_compiler_unchanged"] = True
        except Exception as error:
            receipt.update(status="FAIL", source_and_compiler_unchanged=False,
                           exception=type(error).__name__, diagnostic=str(error))
            raise
        finally:
            save(tmp_path / "reference.json", receipt)


@pytest.mark.xdist_group(name='rust-native-reference')
@pytest.mark.pcc_gate(unavailable=darwin_check_prerequisite())
def test_parser_frontier_under_an_actual_darwin_target_check(tmp_path):
    """The same frontier, certified for the local aarch64-apple-darwin target
    (the Linux-target lanes need x86_64-unknown-linux-gnu core/std)."""
    from pcc.frontends.rust import RustError, check_rust_source
    from pcc.frontends.rust.syntax import Parser
    from tests.rust.test_rust_certified_references import toolchain
    _, files = load_inputs()
    source = files['src/lib.rs'].decode()
    checked = check_rust_source(source, toolchain=toolchain(), evidence_directory=tmp_path / 'actual-check',
                                target_triple='aarch64-apple-darwin', edition=2015)
    parser = Parser(source, source_path='src/lib.rs', edition=2015, rustc_check=checked)
    # Formerly 367:16, the bound Iterator<Item = Result<&'a T, E>> of
    # `convert`, then 373:28, the outlives bound `T: 'a` of `struct Convert`;
    # then 427:23, the tuple struct `pub struct Empty<T, E>(PhantomData<(T, E)>);`
    # (tests/rust/test_rust_tuple_structs.py); now line 836.
    with pytest.raises(RustError, match=r"^src/lib\.rs: 836:13: expected an identifier$"):
        parser.parse()
    # The trait impls before it, including both for Convert and Empty, were retained.
    assert [str(item.owner) for item in parser.implementations if item.trait][:5] == [
        "&'a mut I", "Convert<'a, I, T>", "Convert<'a, I, T>", 'Empty<T, E>', 'Empty<T, E>']
    convert = parser.records[0]
    assert [record.token.text for record in parser.records if record.tuple_fields] == ['Empty']
    assert [(name.text, [item.text for item in lifetimes]) for name, lifetimes in convert.outlives_bounds] == [
        ('T', ["'a"])]
