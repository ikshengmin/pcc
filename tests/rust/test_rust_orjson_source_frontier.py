"""Source-lock/harness tests, NOT compiler, orjson import or upstream-test acceptance."""
import ast
import json
import shutil
import tomllib

import pytest

from scripts.ci_orjson_source_frontier import (
    C_UNIT,
    DEFINES,
    FIXTURE,
    LOCK_SHA256,
    ROOT,
    RUST_ROOTS,
    UPSTREAM_REVISION,
    attempt,
    initial_report,
    load_inputs,
    main,
)
from scripts.ci_source_frontier_support import (
    MAX_FILE_BYTES,
    MAX_TOTAL_BYTES,
    RSS_BYTES,
    bounded_bytes,
    diagnostic_status,
    input_path,
    mark_interrupted,
    materialize_snapshot,
    sha256,
    verify_snapshot,
)


def test_orjson_release_identity_and_complete_original_byte_pins():
    lock, files = load_inputs()
    assert lock["revision"] == UPSTREAM_REVISION == "bd00937c5c46ab20eb5d7fe1cfc034b906c4578f"
    assert lock["version"] == "3.13.0"
    assert len(files) == 15
    assert sum(map(len, files.values())) == 810604
    assert sha256((FIXTURE / "pcc-source-lock.json").read_bytes()) == LOCK_SHA256
    assert lock["complete_build_closure"] is False
    assert set(RUST_ROOTS) <= files.keys()
    assert C_UNIT in files and "include/yyjson/yyjson.h" in files
    assert all(item["url"].split("/")[5] == UPSTREAM_REVISION for item in lock["files"])


def test_licenses_are_per_file_not_a_false_project_wide_dual_license():
    lock, files = load_inputs()
    entries = {item["path"]: item for item in lock["files"]}
    assert lock["license"] == "MPL-2.0 AND (Apache-2.0 OR MIT)"
    for name in ("src/lib.rs", "build.rs", "pysrc/orjson/__init__.py",
                 "test/test_roundtrip.py", "test/util.py"):
        assert entries[name]["license"] == "MPL-2.0"
        assert b"SPDX-License-Identifier: MPL-2.0" in files[name]
    assert entries["test/test_api.py"]["license"] == "Apache-2.0 OR MIT"
    for name in (C_UNIT, "include/yyjson/yyjson.h"):
        assert entries[name]["license"] == "MIT"
        assert b"Copyright (c) 2020 YaoYuan" in files[name]
        assert b"Permission is hereby granted, free of charge" in files[name]
    assert {"LICENSE-APACHE", "LICENSE-MIT", "LICENSE-MPL-2.0"} <= files.keys()


def test_msrv_pyo3_ffi_maturin_and_cdylib_are_real_upstream_declarations():
    lock, files = load_inputs()
    cargo = tomllib.loads(files["Cargo.toml"].decode())
    assert (cargo["package"]["edition"], cargo["package"]["rust-version"]) == ("2024", "1.95")
    assert cargo["lib"]["crate-type"] == ["cdylib"]
    assert "pyo3-ffi" in cargo["dependencies"] and "pyo3" not in cargo["dependencies"]
    packages = {item["name"]: item for item in tomllib.loads(files["Cargo.lock"].decode())["package"]}
    assert packages["pyo3-ffi"]["version"] == "0.29.3"
    assert packages["pyo3-build-config"]["version"] == "0.29.3"
    assert lock["build_backend"] == "maturin"
    reference = initial_report()["external_reference"]
    assert reference["status"] == "UNRUN"
    assert reference["prior_pcc_reference_version"] == "1.91.1"
    assert reference["prior_reference_compatibility"] == "BELOW_UPSTREAM_MSRV"
    assert reference["toolchain_observed_in_this_run"] is False


def test_parser_and_public_admission_bind_locked_edition_without_granting_admission():
    lock, _ = load_inputs()
    report = initial_report()
    assert report["schema_version"] == 2
    assert report["options"]["rust_parser_edition"] == lock["edition"] == 2024
    assert report["options"]["public_rust_api_edition"] == lock["edition"]
    assert "NOT complete edition semantics or Cargo crate context" in report["options"]["rust_edition_scope"]
    tree = ast.parse((ROOT / "scripts/ci_orjson_source_frontier.py").read_text())
    for entry in ("Parser", "compile_rust"):
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == entry]
        assert len(calls) == 1
        call = calls[0]
        assert len(call.args) == 1 and isinstance(call.args[0], ast.Name)
        assert call.args[0].id == "source"
        options = {keyword.arg: keyword.value for keyword in call.keywords}
        assert ast.literal_eval(options["edition"]) == lock["edition"]
        assert not ({"experimental_rustc_checked", "rustc_check", None} & options.keys())
    assert report["borrow_certified"] is False and report["project_accepted"] is False
    assert report["owned_cargo_graph"]["status"] == "UNRUN"
    assert all(row["frontend_object"]["status"] == "UNRUN" for row in report["rust"].values())


def test_c_configuration_retains_original_depth_and_disables_without_running_build_script():
    lock, files = load_inputs()
    assert tuple(map(tuple, lock["c_configuration"]["defines"])) == DEFINES
    assert lock["c_configuration"]["executed"] is False
    assert DEFINES[-1] == ("YYJSON_READER_DEPTH_LIMIT", "1024")
    for name, value in DEFINES:
        assert ('.define("' + name + '", "' + value + '")').encode() in files["build.rs"]
    assert b'#include "yyjson.h"' in files[C_UNIT]
    assert b"pyo3_build_config::get()" in files["build.rs"]
    assert b'fn PyInit_orjson() -> *mut PyModuleDef' in files["src/lib.rs"]
    assert b"from .orjson import *" in files["pysrc/orjson/__init__.py"]


def test_upstream_python_tests_cannot_be_collected_as_rust_harness_tests():
    assert FIXTURE.relative_to(ROOT).parts[0] == "projects"
    lock, files = load_inputs()
    assert all(path.endswith(".rs") for path in RUST_ROOTS)
    assert lock["rust_roots"] == list(RUST_ROOTS)
    assert b"import orjson" in files["test/test_api.py"]
    assert b"@needs_data" in files["test/test_roundtrip.py"]
    assert b"not Path(data_dir).exists()" in files["test/util.py"]
    assert initial_report()["upstream_tests"]["status"] == "UNRUN"
    assert list((ROOT / "tests/rust").glob("**/orjson-3.13.0/**/test_*.py")) == []


def test_every_project_stage_starts_unrun_and_no_lane_can_claim_runtime_success():
    report = initial_report()
    for key in (
        "source_integrity", "owned_cargo_graph", "build_rs_execution", "procedural_macros",
        "core_alloc_std", "rust_c_dependency_objects", "complete_build", "shared_extension_link",
        "python_capi_load", "pcc_native_import", "dumps", "loads", "upstream_tests",
        "five_gc", "external_reference",
    ):
        assert report[key]["status"] == "UNRUN"
    assert set(report["five_gc"]["backends"]) == {"0", "1", "2", "3", "4"}
    assert all(value["status"] == "UNRUN" for value in report["five_gc"]["backends"].values())
    for flag in ("project_accepted", "complete_build_closure", "source_rewritten",
                 "build_script_executed", "external_compiler_used", "borrow_certified",
                 "high_level_pyo3_qualified"):
        assert report[flag] is False
    for row in report["rust"].values():
        assert all(value["status"] == "UNRUN" for value in row.values())
    assert report["c_dependency"]["frontend_object"]["status"] == "UNRUN"


@pytest.mark.parametrize("name", ("", "../bad", "/bad", "a//b", "./bad", "a/../b", "C:/bad", "a\\b", "a\0b"))
def test_pinned_sources_reject_noncanonical_names(tmp_path, name):
    with pytest.raises(ValueError, match="canonical"):
        input_path(tmp_path, name)


def test_pinned_sources_reject_internal_symlink_alias(tmp_path):
    (tmp_path / "real").write_bytes(b"x")
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    with pytest.raises(ValueError, match="symlink"):
        input_path(tmp_path, "alias")


def test_pinned_source_read_is_bounded_before_parsing(tmp_path):
    source = tmp_path / "large"
    source.write_bytes(b"12345")
    with pytest.raises(ValueError, match="byte bound"):
        bounded_bytes(source, 4)
    assert bounded_bytes(source, 5) == b"12345"
    assert MAX_FILE_BYTES == 1024 * 1024
    assert MAX_TOTAL_BYTES == 2 * 1024 * 1024
    assert RSS_BYTES == 1073741824


def test_c_snapshot_contains_only_locked_original_bytes(tmp_path):
    _, files = load_inputs()
    originals = {name: files[name] for name in (C_UNIT, "include/yyjson/yyjson.h")}
    snapshot = tmp_path / "source"
    materialize_snapshot(snapshot, originals)
    assert (snapshot / C_UNIT).read_bytes() == originals[C_UNIT]
    verify_snapshot(snapshot, originals)
    (snapshot / "include/yyjson/math.h").write_bytes(b"unlocked header")
    with pytest.raises(ValueError, match="unpinned"):
        verify_snapshot(snapshot, originals)


def test_snapshot_rejects_changed_header_and_preserves_original_map(tmp_path):
    originals = {"include/header.h": b"original"}
    snapshot = tmp_path / "source"
    materialize_snapshot(snapshot, originals)
    (snapshot / "include/header.h").write_bytes(b"modified")
    with pytest.raises(ValueError, match="snapshot changed"):
        verify_snapshot(snapshot, originals)
    assert originals == {"include/header.h": b"original"}


def test_lock_tampering_cannot_claim_a_complete_closure(tmp_path):
    lock = json.loads((FIXTURE / "pcc-source-lock.json").read_bytes())
    lock["complete_build_closure"] = True
    (tmp_path / "pcc-source-lock.json").write_text(json.dumps(lock))
    with pytest.raises(ValueError, match="lock SHA256"):
        load_inputs(tmp_path)


@pytest.mark.parametrize("name", ("Cargo.lock", "build.rs", "src/lib.rs", C_UNIT, "LICENSE-MPL-2.0"))
def test_same_size_original_byte_changes_fail_closed(tmp_path, name):
    destination = tmp_path / "source"
    shutil.copytree(FIXTURE, destination)
    target = destination / name
    original = target.read_bytes()
    target.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    with pytest.raises(ValueError, match="checksum"):
        load_inputs(destination)


@pytest.mark.parametrize("message,status", (
    ("unsupported semantic crate attribute", "UNSUPPORTED"),
    ("experimental Rust frontend has no borrow checker", "UNSUPPORTED"),
    ("expected fn, found use", "FAIL"),
    ("owned preprocessor: header not found: math.h", "FAIL"),
    ("unexpected internal exception", "FAIL"),
))
def test_only_explicit_capability_diagnostics_are_classified_unsupported(message, status):
    assert diagnostic_status(ValueError(message)) == status


def test_failed_source_operation_does_not_mark_dependent_stage_passed(tmp_path):
    report = initial_report()
    row = report["c_dependency"]
    receipt = tmp_path / "receipt.json"

    def rejection():
        raise ValueError("unsupported source")

    success, value = attempt(report, receipt, row, "preprocess", rejection, (ValueError,))
    assert (success, value) == (False, None)
    saved = json.loads(receipt.read_text())
    assert saved["c_dependency"]["preprocess"]["status"] == "UNSUPPORTED"
    assert saved["c_dependency"]["frontend_object"]["status"] == "UNRUN"
    assert saved["project_accepted"] is False


def test_unexpected_harness_exception_is_failure_not_an_expected_frontier(tmp_path):
    report = initial_report()

    def crash():
        raise TypeError("internal shape error")

    with pytest.raises(TypeError):
        attempt(report, tmp_path / "receipt.json", report["c_dependency"], "preprocess", crash, (ValueError,))
    assert report["c_dependency"]["preprocess"]["status"] == "FAIL"
    assert not report.get("terminal")


def test_interruption_retains_unstarted_and_completed_stages():
    report = initial_report()
    report["rust"]["src/lib.rs"]["source_parse"]["status"] = "PASS"
    report["c_dependency"]["preprocess"]["status"] = "RUNNING"
    mark_interrupted(report)
    assert report["rust"]["src/lib.rs"]["source_parse"]["status"] == "PASS"
    assert report["c_dependency"]["preprocess"]["status"] == "FAIL"
    assert report["pcc_native_import"]["status"] == "UNRUN"


def test_entrypoint_refuses_non_ci_before_output_or_compiler_work(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    destination = tmp_path / "must-not-exist"
    with pytest.raises(SystemExit) as error:
        main(["--output", str(destination)])
    assert error.value.code == 2
    assert not destination.exists()


def test_driver_has_no_external_certification_compiler_or_extension_execution_route():
    source = (ROOT / "scripts/ci_orjson_source_frontier.py").read_text()
    tree = ast.parse(source)
    forbidden = {"check_rust_source", "compile_rust_checked", "evaluate", "evaluate_translation_units",
                 "emit_object", "exec", "eval", "run_path", "run_module"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else None
            )
            assert name not in forbidden
            assert all(keyword.arg not in {"experimental_rustc_checked", "rustc_check"} for keyword in node.keywords)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("pcc.")
    assert "import orjson" not in source
    assert '"cargo"' not in source and '"rustc"' not in source

