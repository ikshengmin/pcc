"""Metadata contracts; synthetic outcomes never enter the public passed baseline."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import conformance_ledger as conformance
import language_feature_ledger as ledger

EMPTY = hashlib.sha256(b"").hexdigest()
SOURCE = ROOT / "tests/multilanguage/fixtures/scalars.go"
SOURCE_HASH = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
REVISION = "1" * 40


def process(exit_code=0, stdout=b"", stderr=b""):
    return {"exit_code": exit_code, "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr).hexdigest()}


def observation(kind):
    return {"id": kind, "kind": kind, "purpose": "unit" if kind == "unit" else "runtime",
            "test_id": "tests/multilanguage/test_scalar_c_abi.py::test_go", "outcome": "passed",
            "input_sha256": SOURCE_HASH,
            "source_revision": REVISION, "source": {"path": SOURCE.relative_to(ROOT).as_posix(),
                                                  "sha256": SOURCE_HASH},
            "mode": "pcc0", "platform": "linux-x86_64", "backend": "self", "gc": "not_applicable",
            "command": ["pytest", "-m", ""], "command_kind": "reproduction", "actual": process(),
            **({"comparison": {"kind": "raw_process_streams"}, "reference": {"name": "go", "version": "test-fixture-version",
                               "role": "external_reference", "kind": "native_execution", "input_sha256": SOURCE_HASH,
                               "platform": "linux-x86_64"}, "expected": process()}
               if kind == "differential" else {})}


def document():
    return {"schema_version": 1, "language": "go",
            "specification": {"title": "Go specification", "version": "test-fixture",
                              "url": "https://go.dev/ref/spec"},
            "inventory": {"chapters_complete": False, "features_complete": False,
                          "notes": "Synthetic metadata test, not a language coverage claim."},
            "chapters": [{"id": "expressions", "title": "Expressions",
                          "url": "https://go.dev/ref/spec#Expressions"},
                         {"id": "unmapped", "title": "Not inventoried",
                          "url": "https://go.dev/ref/spec"}],
            "features": [{"id": "go.fold", "title": "Fixture shape", "chapter_ids": ["expressions"],
                          "scope": "Only the existing scalar fixture shape.",
                          "status": "supported",
                          "tests": ["tests/multilanguage/test_scalar_c_abi.py::test_go"],
                          "diagnostics": [], "evidence": ["unit", "program", "differential"]}],
            "diagnostics": {"inventory_complete": False, "codes": [], "message_only": [],
                            "notes": "No complete unsupported diagnostic inventory asserted."},
            "evidence": [observation(kind) for kind in ("unit", "program", "differential")]}


def suite():
    return {"schema_version": 1, "id": "existing-scalar-fixture", "language": "go",
            "version": "test-fixture-version", "license": "MIT", "source_url": "https://github.com/allstoalls/pcc",
            "source_revision": REVISION, "source_sha256": SOURCE_HASH, "source_unchanged": True,
            "inventory_complete": False, "scope": "pcc_regression",
            "adapter": "tests/multilanguage/test_scalar_c_abi.py",
            "tests": [{"id": "scalar", "path": SOURCE.relative_to(ROOT).as_posix(),
                       "sha256": SOURCE_HASH, "feature_ids": ["go.fold"], "expectation": "run"}]}


def run():
    return {"schema_version": 1, "suite_id": suite()["id"], "suite_version": suite()["version"],
            "suite_source_sha256": SOURCE_HASH, "compiler_revision": REVISION,
            "mode": "pcc0", "platform": "linux-x86_64", "backend": "self", "gc": "not_applicable",
            "results": [{"test_id": "scalar", "input_sha256": SOURCE_HASH,
                         "compile": {"outcome": "accepted", **process()},
                         "run": {"outcome": "completed", **process()},
                         "reference": {"name": "go", "version": "test-fixture-version",
                                       "role": "external_reference", "kind": "native_execution", "input_sha256": SOURCE_HASH,
                                       "platform": "linux-x86_64",
                                       "compile": {"outcome": "accepted", **process()},
                                       "run": {"outcome": "completed", **process()}},
                         "diagnostic_codes": [], "blocker_feature_ids": []}]}


def test_ledger_validates_source_hash_and_exact_empty_argument():
    ledger.validate_ledger(document(), ROOT)


@pytest.mark.parametrize("status", ["passed", "partial", "out_of_scope"])
def test_only_three_feature_statuses(status):
    data = document()
    data["features"][0]["status"] = status
    with pytest.raises(ledger.LedgerError, match="invalid feature status"):
        ledger.validate_ledger(data)


@pytest.mark.parametrize("axis,new_value", [("mode", "pcc1"), ("platform", "darwin-arm64"),
                                           ("backend", "llvm"), ("gc", "generational"),
                                           ("source_revision", "2" * 40)])
def test_support_cannot_mix_execution_axes_or_compiler_revisions(axis, new_value):
    data = document()
    data["evidence"][2][axis] = new_value
    with pytest.raises(ledger.LedgerError, match="supported requires"):
        ledger.validate_ledger(data)


@pytest.mark.parametrize("field,new_value", [("exit_code", 1), ("stdout_sha256", "a" * 64),
                                            ("stderr_sha256", "b" * 64)])
def test_differential_compares_all_three_outputs(field, new_value):
    data = document()
    data["evidence"][2]["actual"][field] = new_value
    with pytest.raises(ledger.LedgerError, match="disagrees"):
        ledger.validate_ledger(data)


def test_metadata_typecheck_is_not_native_runtime_support():
    data = document()
    data["evidence"][2]["purpose"] = "typecheck"
    data["evidence"][2]["reference"]["kind"] = "metadata_typecheck"
    with pytest.raises(ledger.LedgerError, match="supported requires"):
        ledger.validate_ledger(data)
    data["features"][0]["status"] = "missing"
    ledger.validate_ledger(data)


def test_out_of_scope_is_orthogonal_and_empty_chapter_has_no_fake_percentage():
    data = document()
    data["features"][0]["out_of_scope_reason"] = "Only a scoped fixture, not all expressions."
    report = ledger.coverage(data)
    assert report["features"]["denominator"] == 1
    assert report["chapters"][1]["denominator"] == 0
    assert report["chapters"][1]["supported_fraction"] is None
    assert report["inventory"]["features_complete"] is False


def test_unmapped_declared_diagnostic_and_stale_source_fail():
    data = document()
    data["features"][0]["diagnostics"] = ["go.unsupported.example"]
    with pytest.raises(ledger.LedgerError, match="unmapped diagnostic"):
        ledger.validate_ledger(data)
    data = document()
    data["evidence"][0]["source"]["sha256"] = "f" * 64
    with pytest.raises(ledger.LedgerError, match="hash mismatch"):
        ledger.validate_ledger(data, ROOT)


def test_message_only_gap_cannot_claim_stable_code_completeness():
    data = document()
    data["diagnostics"]["message_only"] = [
        {"message": "unimplemented", "source": observation("unit")["source"],
         "reason": "No stable code exists", "feature_id": "go.fold"}]
    data["diagnostics"]["inventory_complete"] = True
    with pytest.raises(ledger.LedgerError, match="message-only gaps"):
        ledger.validate_ledger(data)


def test_actual_diagnostic_scan_catches_new_codes_and_resolves_finite_helper(tmp_path):
    frontend = tmp_path / "pcc/frontends/go"
    frontend.mkdir(parents=True)
    (frontend / "syntax.py").write_text(
        'FEATURES = {"pointer": "pointers", "array": "arrays"}\n'
        'def unsupported(feature):\n'
        '    raise Error("go.unsupported." + feature)\n'
        'unsupported("switch")\n'
        'unsupported(FEATURES[kind])\n'
        'raise Error("go.unsupported.new-feature")\n')
    result = ledger.audit_diagnostics(document(), tmp_path)
    assert result["unmapped_codes"] == ["go.unsupported.arrays", "go.unsupported.new-feature",
                                        "go.unsupported.pointers", "go.unsupported.switch"]
    assert result["unmapped_count"] == 4
    assert not result["unresolved_dynamic_sites"]
    assert not result["code_inventory_complete"]


def test_dynamic_diagnostic_cannot_claim_complete_inventory(tmp_path):
    frontend = tmp_path / "pcc/frontends/go"
    frontend.mkdir(parents=True)
    (frontend / "syntax.py").write_text('raise Error("go.unsupported." + runtime_name)\n')
    result = ledger.scan_unsupported_codes(tmp_path, "go")
    assert len(result["unresolved_dynamic_sites"]) == 1
    assert result["codes"] == []


def test_suite_source_unchanged_and_hash_are_enforced():
    conformance.validate_suite(suite(), ROOT)
    data = suite()
    data["source_unchanged"] = False
    with pytest.raises(ledger.LedgerError, match="unchanged"):
        conformance.validate_suite(data)


def test_summary_separates_accept_reject_execute_and_absent_denominator():
    manifest, result = suite(), run()
    manifest["tests"].append({**manifest["tests"][0], "id": "not-observed"})
    summary = conformance.summarize_run(result, manifest)
    assert (summary["denominator"], summary["observed"], summary["accepted"],
            summary["rejected"], summary["executed"]) == (2, 1, 1, 0, 1)
    assert summary["not_observed"] == ["not-observed"]
    assert summary["passed"] == ["scalar"]


@pytest.mark.parametrize("field,value", [("exit_code", 1), ("stdout_sha256", "f" * 64),
                                        ("stderr_sha256", "a" * 64)])
def test_allowlist_excludes_any_runtime_disagreement(field, value):
    result = run()
    result["results"][0]["run"][field] = value
    assert conformance.passed_allowlist(result, suite())["entries"] == []
    report = conformance.summarize_run(result, suite())
    assert report["differential"][0][field + "_equal"] is False


@pytest.mark.parametrize("mode", ["not_run", "blocked"])
def test_no_compile_only_or_blocked_runtime_allowlist(mode):
    result = run()
    result["results"][0]["run"] = {"outcome": mode}
    assert conformance.passed_allowlist(result, suite())["entries"] == []


def test_native_reference_flag_without_completed_run_is_not_a_pass():
    result = run()
    result["results"][0]["reference"]["run"] = {"outcome": "not_run"}
    assert conformance.passed_allowlist(result, suite())["entries"] == []


def test_expected_rejection_is_separate_from_runtime_differential():
    manifest, result = suite(), run()
    manifest["tests"][0]["expectation"] = "reject"
    row = result["results"][0]
    row["compile"] = {"outcome": "rejected", **process(1, stderr=b"owned diagnostic")}
    row["run"] = {"outcome": "not_run"}
    row["reference"]["kind"] = "expected_rejection"
    row["reference"]["compile"] = {"outcome": "rejected", **process(2, stderr=b"reference diagnostic")}
    row["reference"]["run"] = {"outcome": "not_run"}
    summary = conformance.summarize_run(result, manifest)
    assert summary["passed"] == ["scalar"]
    assert summary["executed"] == 0
    assert summary["differential"] == []


@pytest.mark.parametrize("field,value", [("mode", "pcc1"), ("platform", "darwin-arm64"),
                                        ("gc", "generational"), ("backend", "llvm")])
def test_regression_gate_does_not_mix_platform_or_execution_modes(field, value, tmp_path):
    baseline = conformance.passed_allowlist(run(), suite(), suite_receipts(tmp_path))
    current = copy.deepcopy(baseline)
    current["entries"][0][field] = value
    assert not conformance.regression_gate(baseline, current, current["entries"][0]["compiler_revision"])["passed"]


def test_regression_gate_requires_every_previously_passed_test(tmp_path):
    baseline = conformance.passed_allowlist(run(), suite(), suite_receipts(tmp_path))
    current = copy.deepcopy(baseline)
    current["entries"][0]["compiler_revision"] = "2" * 40
    current["entries"][0]["verification"]["compiler_revision"] = "2" * 40
    assert conformance.regression_gate(baseline, current, current["entries"][0]["compiler_revision"])["passed"]
    current["entries"] = []
    assert len(conformance.regression_gate(baseline, current, "2" * 40)["regressions"]) == 1


def test_deterministic_six_shards_cover_inventory_without_execution():
    manifest = suite()
    manifest["tests"] = [{**manifest["tests"][0], "id": f"case-{index}"} for index in range(23)]
    first = conformance.shard_plan(manifest, 6)
    manifest["tests"].reverse()
    assert first == conformance.shard_plan(manifest, 6)
    assert len([item for shard in first["shards"] for item in shard]) == 23
    assert not first["execution_performed"]


def test_blocker_frequency_counts_distinct_projects_not_duplicate_logs():
    result = run()
    row = result["results"][0]
    row["run"] = {"outcome": "blocked"}
    row["blocker_feature_ids"] = ["go.fold"]
    row["project"] = "real-project"
    report = conformance.blockers([(result, suite()), (result, suite())])
    assert report[0]["test_count"] == report[0]["project_count"] == 1


def test_existing_cpython_runner_results_are_not_silently_reinterpreted():
    # Reuse the existing parser, not a second CPython discovery/execution harness.
    path = ROOT / "scripts/cpython_conformance/run.py"
    spec = importlib.util.spec_from_file_location("existing_conformance", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.score({"case": ("PASS", "")}, {"case": ("SKIP", "")})["native_success"] == 0
    with pytest.raises(ledger.LedgerError, match="schema_version"):
        conformance.validate_run({"native_cases": {"case": "PASS"}}, suite())


def test_committed_language_ledgers_validate():
    for path in (ROOT / "specs/features").glob("*.json"):
        ledger.validate_ledger(json.loads(path.read_text()))


def test_failed_unit_cannot_be_labeled_passed():
    data = document()
    data["evidence"][0]["actual"]["exit_code"] = 1
    with pytest.raises(ledger.LedgerError, match="unit must exit zero"):
        ledger.validate_ledger(data)


def test_different_reference_source_cannot_qualify():
    data = document()
    data["evidence"][2]["reference"]["input_sha256"] = "f" * 64
    with pytest.raises(ledger.LedgerError, match="original input hash mismatch"):
        ledger.validate_ledger(data)
    result = run()
    result["results"][0]["reference"]["input_sha256"] = "f" * 64
    with pytest.raises(ledger.LedgerError, match="original input hash mismatch"):
        conformance.passed_allowlist(result, suite())


def test_official_suite_mutable_branch_is_not_a_version_pin():
    data = suite()
    data["source_revision"] = "main"
    with pytest.raises(ledger.LedgerError, match="immutable"):
        conformance.validate_suite(data)


@pytest.mark.parametrize("expression", [
    '"go." + "unsupported." + runtime_name',
    '"go.unsupported.dynamic." + runtime_name',
    'f"go.unsupported.{runtime_name}"',
    '"go.unsupported.{}".format(runtime_name)',
])
def test_composed_dynamic_diagnostic_prefix_is_not_silently_complete(tmp_path, expression):
    frontend = tmp_path / "pcc/frontends/go"
    frontend.mkdir(parents=True)
    (frontend / "syntax.py").write_text("raise Error(" + expression + ")\n")
    result = ledger.scan_unsupported_codes(tmp_path, "go")
    assert result["codes"] == []
    assert len(result["unresolved_dynamic_sites"]) == 1


def test_normalized_reference_bytes_cannot_qualify_raw_stream_support():
    data = document()
    row = data["evidence"][2]
    row["comparison"] = {"kind": "normalized_primitive_value",
                         "description": "Historical decimal return converted to eight-byte lane."}
    row["raw_reference"] = process(stdout=b"42\n")
    with pytest.raises(ledger.LedgerError, match="supported requires"):
        ledger.validate_ledger(data)
    data["features"][0]["status"] = "missing"
    ledger.validate_ledger(data)


def artifact(tmp_path, name, raw):
    path = tmp_path / name
    path.write_bytes(raw)
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def process_receipts(tmp_path, name, value):
    binary = artifact(tmp_path, name + ".binary", b"synthetic parser fixture, not an executed program")
    receipt = {"returncode": value["exit_code"], "stdout_hex": "", "stderr_hex": "",
               "binary": binary["path"], "binary_sha256": binary["sha256"]}
    return {"result": artifact(tmp_path, name + ".json", json.dumps(receipt).encode()),
            "stdout": artifact(tmp_path, name + ".stdout", b""),
            "stderr": artifact(tmp_path, name + ".stderr", b""), "exit_field": "returncode",
            "binary": binary}


def suite_receipts(tmp_path):
    original = artifact(tmp_path, "original.go", SOURCE.read_bytes())
    return {"records": [{"test_id": "scalar", "compiler_revision": REVISION, "input": original,
                         "compile": process_receipts(tmp_path, "compile", process()),
                         "run": process_receipts(tmp_path, "run", process()),
                         "reference": {"input": original,
                                       "compile": process_receipts(tmp_path, "ref-compile", process()),
                                       "run": process_receipts(tmp_path, "ref-run", process())}}]}


def feature_receipts(tmp_path):
    row = document()["evidence"][2]
    original = artifact(tmp_path, "original.go", SOURCE.read_bytes())
    return {"records": [{"id": row["id"], "test_id": row["test_id"],
                         "source_revision": row["source_revision"], "source": original,
                         "input": original, **process_receipts(tmp_path, "owned", process()),
                         "reference": {"input": original,
                                       **process_receipts(tmp_path, "reference", process())}}]}


def test_allowlist_cannot_be_exported_from_fabricated_pass_flags():
    with pytest.raises(ledger.LedgerError, match="retained process receipt"):
        conformance.passed_allowlist(run(), suite())
    with pytest.raises(ledger.LedgerError, match="no retained receipt"):
        conformance.qualified_allowlist(document(), {"records": []})


def test_feature_baseline_rechecks_retained_source_streams_and_exit(tmp_path):
    index = feature_receipts(tmp_path)
    baseline = conformance.qualified_allowlist(document(), index)
    assert len(baseline["entries"]) == 1
    assert "/tmp/" not in json.dumps(baseline)
    Path(index["records"][0]["stdout"]["path"]).write_bytes(b"tampered")
    with pytest.raises(ledger.LedgerError, match="retained artifact hash mismatch"):
        conformance.qualified_allowlist(document(), index)


def test_stale_compiler_allowlist_cannot_satisfy_current_revision(tmp_path):
    baseline = conformance.passed_allowlist(run(), suite(), suite_receipts(tmp_path))
    with pytest.raises(ledger.LedgerError, match="expected compiler revision"):
        conformance.regression_gate(baseline, baseline, "2" * 40)


def test_explicit_unimplemented_requires_source_bound_diagnostic():
    data = document()
    data["features"][0]["status"] = "explicitly_unimplemented"
    with pytest.raises(ledger.LedgerError, match="requires a mapped source-bound diagnostic"):
        ledger.validate_ledger(data)


@pytest.mark.parametrize("source", [
    'FEATURE="global"\ndef f(FEATURE):\n raise Error("go.unsupported."+FEATURE)\nf(runtime_name)\n',
    'def f():\n feature="known"\n feature += runtime_name\n raise Error("go.unsupported."+feature)\n',
    'def unsupported(feature):\n raise Error("go.unsupported."+feature)\nunsupported("known")\nalias=unsupported\nalias(runtime_name)\n',
])
def test_dynamic_shadow_mutation_or_escaped_helper_is_incomplete(tmp_path, source):
    frontend = tmp_path / "pcc/frontends/go"
    frontend.mkdir(parents=True)
    (frontend / "syntax.py").write_text(source)
    result = ledger.scan_unsupported_codes(tmp_path, "go")
    assert result["unresolved_dynamic_sites"]


def test_erased_receipt_provenance_cannot_pass_regression_gate(tmp_path):
    baseline = conformance.passed_allowlist(run(), suite(), suite_receipts(tmp_path))
    current = copy.deepcopy(baseline)
    del current["entries"][0]["verification"]
    with pytest.raises(ledger.LedgerError, match="artifact provenance level"):
        conformance.regression_gate(baseline, current, REVISION)


def test_changed_test_harness_source_is_not_same_baseline_case(tmp_path):
    baseline = conformance.passed_allowlist(run(), suite(), suite_receipts(tmp_path))
    current = copy.deepcopy(baseline)
    current["entries"][0]["verification"]["test_source_sha256"] = "f" * 64
    assert not conformance.regression_gate(baseline, current, REVISION)["passed"]


def test_outcome_only_receipt_is_not_execution_proof(tmp_path):
    record = process_receipts(tmp_path, "minimal", process())
    record["result"] = artifact(tmp_path, "outcome-only.json", b'{"returncode": 0}')
    with pytest.raises(ledger.LedgerError, match="recognized retained execution receipt"):
        conformance.verify_execution(record, process(), "minimal")


def test_repointed_binary_receipt_is_not_same_execution(tmp_path):
    record = process_receipts(tmp_path, "bound", process())
    record["binary"] = artifact(tmp_path, "other.binary", b"unrelated")
    with pytest.raises(ledger.LedgerError, match="binary path mismatch"):
        conformance.verify_execution(record, process(), "repointed")


def test_compile_receipts_cannot_stand_in_for_runtime_receipt_proof(tmp_path):
    baseline = conformance.passed_allowlist(run(), suite(), suite_receipts(tmp_path))
    del baseline["entries"][0]["verification"]["actual_run_receipt_sha256"]
    del baseline["entries"][0]["verification"]["reference_run_receipt_sha256"]
    with pytest.raises(ledger.LedgerError, match="complete actual/reference phase"):
        conformance.validate_allowlist(baseline)


def test_report_exposes_only_actually_qualified_execution_lane():
    report = ledger.coverage(document())
    assert report["qualified_scopes"][0]["execution_lanes"] == [
        {"mode": "pcc0", "platform": "linux-x86_64", "backend": "self",
         "gc": "not_applicable", "source_revision": REVISION}]


def test_binary_hash_receipt_without_path_still_binds_artifact_bytes(tmp_path):
    record = process_receipts(tmp_path, "hash-bound", process())
    receipt = json.loads(Path(record["result"]["path"]).read_text())
    del receipt["binary"]
    record["result"] = artifact(tmp_path, "hash-bound-only.json", json.dumps(receipt).encode())
    conformance.verify_execution(record, process(), "hash-bound")
    Path(record["binary"]["path"]).write_bytes(b"wrong program")
    with pytest.raises(ledger.LedgerError, match="artifact hash mismatch"):
        conformance.verify_execution(record, process(), "hash-bound")


def test_derived_capture_cannot_change_parent_process_observation(tmp_path):
    record = process_receipts(tmp_path, "derived", process())
    receipt = json.loads(Path(record["result"]["path"]).read_text())
    original = artifact(tmp_path, "captured-original.go", SOURCE.read_bytes())
    receipt.update({"derived_from_existing_execution_only": True, "command": ["program"],
                    "parent_receipt": artifact(tmp_path, "parent.json", json.dumps({
                        "command": ["program"], "returncode": 1,
                        "stdout_sha256": EMPTY, "stderr_sha256": EMPTY}).encode()),
                    "original_input": original,
                    "comparison_record": artifact(tmp_path, "comparison.json", json.dumps({
                        "comparison": "raw_process_streams", "original_modifiers_or_source_rewritten": False,
                        "source_sha256": SOURCE_HASH}).encode())})
    record["input"] = original
    record["result"] = artifact(tmp_path, "derived-capture.json", json.dumps(receipt).encode())
    with pytest.raises(ledger.LedgerError, match="parent captured raw process observation mismatch"):
        conformance.verify_execution(record, process(), "derived")
