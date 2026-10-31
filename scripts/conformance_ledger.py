#!/usr/bin/env python3
"""Import immutable suite/result metadata, plan shards, and gate passed allowlists.

Uses the existing language tests and scripts/cpython_conformance/run.py as
execution owners; this metadata tool never downloads, edits, or executes suites.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from language_feature_ledger import (
    AXES,
    REVISION,
    SHA256,
    LedgerError,
    execution_axes,
    nonempty,
    process_result,
    reference_identity,
    require,
    same_result,
    source_identity,
    string_list,
    unique,
    validate_ledger,
)

EXPECTATIONS = {"accept", "reject", "run"}
PHASE_OUTCOMES = {"accepted", "rejected", "completed", "blocked", "not_run"}


def validate_suite(suite, root=None):
    require(suite.get("schema_version") == 1, "suite schema_version must be 1")
    for key in ("id", "language", "version", "license", "source_url", "source_revision", "adapter"):
        nonempty(suite.get(key), "suite." + key)
    require(bool(REVISION.fullmatch(suite.get("source_revision", ""))),
            "suite.source_revision must be an immutable commit or sha256 content identity")
    require(bool(SHA256.fullmatch(suite.get("source_sha256", ""))), "suite.source_sha256 required")
    require(suite.get("source_unchanged") is True, "suite source must be unchanged")
    require(type(suite.get("inventory_complete")) is bool, "suite.inventory_complete required")
    require(suite.get("scope") in ("official", "upstream_project", "pcc_regression", "inventory_only"),
            "suite scope required")
    tests = unique(suite.get("tests"), "suite.tests")
    for row in tests.values():
        source_identity(row, "suite test " + row["id"], root)
        require(row.get("expectation") in EXPECTATIONS, "suite test: invalid expectation")
        string_list(row.get("feature_ids"), "suite test.feature_ids")
    return suite


def validate_phase(row, name, phase):
    require(isinstance(row, dict), name + ": phase object required")
    allowed = {"accepted", "rejected", "blocked", "not_run"} if phase == "compile" else {
        "completed", "blocked", "not_run"}
    require(row.get("outcome") in allowed, name + ": invalid phase outcome")
    if row["outcome"] not in ("blocked", "not_run"):
        process_result(row, name)
        if phase == "compile":
            require((row["outcome"] == "accepted") == (row["exit_code"] == 0),
                    name + ": compile outcome contradicts exit_code")


def validate_run(run, suite):
    validate_suite(suite)
    require(run.get("schema_version") == 1, "run schema_version must be 1")
    for key, expected in (("suite_id", suite["id"]), ("suite_version", suite["version"]),
                          ("suite_source_sha256", suite["source_sha256"])):
        require(run.get(key) == expected, "run " + key + ": suite identity mismatch")
    execution_axes(run, "run")
    require(bool(REVISION.fullmatch(run.get("compiler_revision", ""))), "run compiler_revision required")
    tests = {row["id"]: row for row in suite["tests"]}
    rows = unique(run.get("results"), "run.results", "test_id")
    for test_id, row in rows.items():
        require(test_id in tests, "run: unknown test " + test_id)
        require(row.get("input_sha256") == tests[test_id]["sha256"], "run: test input hash mismatch")
        for phase in ("compile", "run"):
            validate_phase(row.get(phase), test_id + "." + phase, phase)
        if row["run"]["outcome"] == "completed":
            require(row["compile"]["outcome"] == "accepted", test_id + ": run without accepted compile")
        reference = row.get("reference")
        if reference is not None:
            reference_identity(reference, test_id)
            require(reference.get("input_sha256") == row["input_sha256"],
                    test_id + ": reference original input hash mismatch")
            for phase in ("compile", "run"):
                validate_phase(reference.get(phase), test_id + ".reference." + phase, phase)
            if reference["run"]["outcome"] == "completed":
                require(reference["compile"]["outcome"] == "accepted",
                        test_id + ": reference run without accepted compile")
        string_list(row.get("diagnostic_codes", []), test_id + ".diagnostic_codes")
        string_list(row.get("blocker_feature_ids", []), test_id + ".blocker_feature_ids")
    return run


def test_passed(row, test):
    """No PASS imported from aggregate counts, skips, flags, or compile-only runs."""
    reference = row.get("reference")
    if reference is None:
        return False
    expectation = test["expectation"]
    actual_compile = row["compile"]["outcome"]
    reference_compile = reference["compile"]["outcome"]
    if expectation == "reject":
        return actual_compile == reference_compile == "rejected"
    if actual_compile != "accepted" or reference_compile != "accepted":
        return False
    if expectation == "accept":
        return True
    return (reference["kind"] == "native_execution"
            and row["run"]["outcome"] == reference["run"]["outcome"] == "completed"
            and same_result(row["run"], reference["run"]))


def summarize_run(run, suite):
    validate_run(run, suite)
    tests = {row["id"]: row for row in suite["tests"]}
    rows = {row["test_id"]: row for row in run["results"]}
    passed = []
    differential = []
    for test_id, row in rows.items():
        if test_passed(row, tests[test_id]):
            passed.append(test_id)
        reference = row.get("reference")
        if reference is not None and row["run"]["outcome"] == reference["run"]["outcome"] == "completed":
            differential.append({"test_id": test_id, **{
                field + "_equal": row["run"][field] == reference["run"][field]
                for field in ("stdout_sha256", "stderr_sha256", "exit_code")}})
    return {"suite_id": suite["id"], "scope": suite["scope"],
            "inventory_complete": suite["inventory_complete"], "denominator": len(tests),
            "observed": len(rows), "not_observed": sorted(tests.keys() - rows.keys()),
            "accepted": sum(row["compile"]["outcome"] == "accepted" for row in rows.values()),
            "rejected": sum(row["compile"]["outcome"] == "rejected" for row in rows.values()),
            "executed": sum(row["run"]["outcome"] == "completed" for row in rows.values()),
            "passed": sorted(passed), "differential": differential,
            **{key: run[key] for key in AXES}}


def passed_allowlist(run, suite, receipt_index=None):
    validate_run(run, suite)
    tests = {row["id"]: row for row in suite["tests"]}
    entries = []
    for row in run["results"]:
        if not test_passed(row, tests[row["test_id"]]):
            continue
        reference = row["reference"]
        require(receipt_index is not None, "retained process receipt index required for passed allowlist")
        records = unique(receipt_index.get("records"), "private suite receipts", "test_id")
        require(row["test_id"] in records, "retained suite test receipt absent")
        record = records[row["test_id"]]
        require(record.get("compiler_revision") == run["compiler_revision"],
                "retained suite compiler revision mismatch")
        original = verified_file(record.get("input"), "suite original input")
        require(hashlib.sha256(original).hexdigest() == row["input_sha256"],
                "suite original input hash mismatch")
        reference_original = verified_file(record.get("reference", {}).get("input"), "reference original input")
        require(original == reference_original, "reference original bytes differ")
        verification = {"level": "retained_process_artifacts",
                        "case_binding": "audited_private_receipt_index",
                        "test_source_sha256": row["input_sha256"],
                        "compiler_revision": run["compiler_revision"]}
        for owner, observed in (("actual", row), ("reference", reference)):
            private = record if owner == "actual" else record["reference"]
            for phase in ("compile", "run"):
                if observed[phase]["outcome"] not in ("not_run", "blocked"):
                    verification[owner + "_" + phase + "_receipt_sha256"] = verify_execution(
                        private.get(phase, {}), observed[phase], owner + "." + phase)
        entries.append({
            **{key: run[key] for key in ("suite_id", "suite_version", "suite_source_sha256",
                                       "compiler_revision", *AXES)},
            "test_id": row["test_id"], "input_sha256": row["input_sha256"],
            "expectation": tests[row["test_id"]]["expectation"],
            "reference": {key: reference[key] for key in ("name", "version", "platform", "role", "kind")},
            "outcome": "passed", "verification": verification,
        })
    return {"schema_version": 1, "entries": sorted(entries, key=lambda row: row["test_id"])}


def allowlist_key(row):
    # Compiler revision intentionally changes during regression testing; record
    # its provenance, but compare identical test+suite+execution/reference modes.
    identity = {key: value for key, value in row.items()
                if key not in ("compiler_revision", "verification")}
    identity["test_source_sha256"] = row["verification"]["test_source_sha256"]
    return json.dumps(identity, sort_keys=True, separators=(",", ":"))


def validate_allowlist(data):
    require(data.get("schema_version") == 1, "allowlist schema_version must be 1")
    require(isinstance(data.get("entries"), list), "allowlist.entries required")
    keys = set()
    for row in data["entries"]:
        execution_axes(row, "allowlist")
        for key in ("suite_id", "suite_version", "test_id"):
            nonempty(row.get(key), "allowlist." + key)
        for key in ("suite_source_sha256", "input_sha256"):
            require(bool(SHA256.fullmatch(row.get(key, ""))), "allowlist." + key + " required")
        require(bool(REVISION.fullmatch(row.get("compiler_revision", ""))), "allowlist compiler_revision required")
        require(row.get("expectation") in EXPECTATIONS, "allowlist expectation required")
        require(row.get("outcome") == "passed", "allowlist must contain only verified passed tests")
        verification = row.get("verification", {})
        require(verification.get("level") == "retained_process_artifacts"
                and verification.get("case_binding") == "audited_private_receipt_index",
                "allowlist retained artifact provenance level required")
        require(verification.get("compiler_revision") == row["compiler_revision"],
                "allowlist verification compiler identity required")
        require(bool(SHA256.fullmatch(verification.get("test_source_sha256", ""))),
                "allowlist verified test source hash required")
        generic_pair = all(owner + "_receipt_sha256" in verification for owner in ("actual", "reference"))
        required_phases = ("compile", "run") if row["expectation"] == "run" else ("compile",)
        phase_pairs = all(owner + "_" + phase + "_receipt_sha256" in verification
                          for owner in ("actual", "reference") for phase in required_phases)
        require((row["expectation"] == "run" and generic_pair) or phase_pairs,
                "allowlist requires complete actual/reference phase receipt pairs")
        for owner in ("actual", "reference"):
            fields = [key for key in verification
                      if key.startswith(owner + "_") and key.endswith("_receipt_sha256")]
            require(bool(fields), "allowlist retained " + owner + " receipt proof required")
            require(all(bool(SHA256.fullmatch(verification[key])) for key in fields),
                    "allowlist invalid retained receipt hash")
        reference_identity(row.get("reference"), "allowlist")
        if row["expectation"] == "run":
            require(row["reference"]["kind"] == "native_execution", "runtime allowlist needs native reference")
        key = allowlist_key(row)
        require(key not in keys, "duplicate allowlist entry")
        keys.add(key)
    return data


def regression_gate(baseline, current, expected_current_revision):
    validate_allowlist(baseline)
    validate_allowlist(current)
    require(bool(REVISION.fullmatch(expected_current_revision)), "expected current immutable revision required")
    require(all(row["compiler_revision"] == expected_current_revision for row in current["entries"]),
            "current allowlist does not match expected compiler revision")
    observed = {allowlist_key(row) for row in current["entries"]}
    missing = [row for row in baseline["entries"] if allowlist_key(row) not in observed]
    return {"passed": not missing, "required": len(baseline["entries"]), "regressions": missing}



def verified_file(value, name):
    """Private paths are consumed locally; only their content hashes are exported."""
    require(isinstance(value, dict), name + ": artifact descriptor required")
    require(bool(SHA256.fullmatch(value.get("sha256", ""))), name + ": artifact sha256 required")
    path = Path(value.get("path", ""))
    require(path.is_file(), name + ": retained artifact absent")
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == value["sha256"], name + ": retained artifact hash mismatch")
    return raw


def verify_execution(record, expected, name):
    receipt = json.loads(verified_file(record.get("result"), name + ".result"))
    binary_receipt = (("returncode" in receipt or "exit_code" in receipt)
                      and all(key in receipt for key in ("stdout_hex", "stderr_hex", "binary_sha256")))
    tree_receipt = receipt.get("schema") == "pcc.process_tree_sample.v1"
    require(binary_receipt or tree_receipt,
            name + ": recognized retained execution receipt required, not an outcome-only record")
    if binary_receipt:
        require("binary" in record, name + ": retained binary descriptor required")
        if "binary" in receipt:
            require(Path(receipt["binary"]).resolve() == Path(record["binary"]["path"]).resolve(),
                    name + ": receipt binary path mismatch")
    else:
        require(receipt.get("status") == "COMPLETE", name + ": process receipt is not terminal COMPLETE")
        require(isinstance(record.get("command"), list) and record["command"]
                and record["command"] == receipt.get("command"),
                name + ": exact retained runner command required")
    field = record.get("exit_field")
    require(field in ("returncode", "exit_code"), name + ": explicit process exit field required")
    require(type(receipt.get(field)) is int and receipt[field] == expected["exit_code"],
            name + ": retained process exit mismatch")
    for stream in ("stdout", "stderr"):
        raw = verified_file(record.get(stream), name + "." + stream)
        require(hashlib.sha256(raw).hexdigest() == expected[stream + "_sha256"],
                name + ": retained " + stream + " mismatch")
        if stream + "_hex" in receipt:
            require(bytes.fromhex(receipt[stream + "_hex"]) == raw,
                    name + ": receipt stream bytes disagree")
    if "binary" in record:
        binary = verified_file(record["binary"], name + ".binary")
        if "binary_sha256" in receipt:
            require(hashlib.sha256(binary).hexdigest() == receipt["binary_sha256"],
                    name + ": receipt binary hash mismatch")
    if receipt.get("derived_from_existing_execution_only"):
        parent = json.loads(verified_file(receipt.get("parent_receipt"), name + ".parent"))
        require(parent.get("command") == receipt.get("command") and bool(parent.get("command")),
                name + ": parent captured command mismatch")
        require(parent.get("returncode") == expected["exit_code"]
                and all(parent.get(stream + "_sha256") == expected[stream + "_sha256"]
                        for stream in ("stdout", "stderr")),
                name + ": parent captured raw process observation mismatch")
        original = verified_file(receipt.get("original_input"), name + ".parent.input")
        require(hashlib.sha256(original).hexdigest() == record.get("input", {}).get("sha256"),
                name + ": parent captured original input mismatch")
        comparison = json.loads(verified_file(receipt.get("comparison_record"), name + ".comparison"))
        require(comparison.get("comparison") == "raw_process_streams"
                and comparison.get("original_modifiers_or_source_rewritten") is False
                and comparison.get("source_sha256") == hashlib.sha256(original).hexdigest(),
                name + ": parent comparison is not unchanged-source raw streams")
    if "derived_from_recorded_observation" in receipt:
        parent = json.loads(verified_file(receipt["derived_from_recorded_observation"], name + ".parent"))
        require(parent.get("purpose") == "runtime" and parent.get("reference_kind") == "native_execution"
                and parent.get("source_unchanged") is True,
                name + ": derived reference is not unchanged-source native execution")
        require(parent.get("binary_sha256") == receipt.get("binary_sha256"),
                name + ": parent reference binary mismatch")
        require(parent.get("source_sha256") == record.get("input", {}).get("sha256"),
                name + ": parent original source mismatch")
        require(parent.get("actual") == expected, name + ": parent actual reference observation mismatch")
    return record["result"]["sha256"]


def qualified_allowlist(data, receipt_index):
    """Export only exact raw differential cases with reverified retained receipts.

    An author-supplied 'passed' flag or a reference-execution flag is insufficient.
    No raw logs, absolute paths, or private receipt index are copied to the result.
    """
    validate_ledger(data)
    records = unique(receipt_index.get("records"), "private receipt records")
    entries = []
    for row in data["evidence"]:
        if not (row["kind"] == "differential" and row["outcome"] == "passed"
                and row["purpose"] == "runtime"
                and row.get("comparison", {}).get("kind") == "raw_process_streams"):
            continue
        require(row["id"] in records, "no retained receipt for " + row["id"])
        record = records[row["id"]]
        require(record.get("test_id") == row["test_id"]
                and record.get("source_revision") == row["source_revision"],
                row["id"] + ": retained test/revision identity mismatch")
        source = verified_file(record.get("source"), row["id"] + ".source")
        require(hashlib.sha256(source).hexdigest() == row["source"]["sha256"],
                row["id"] + ": retained test source mismatch")
        original = verified_file(record.get("input"), row["id"] + ".input")
        require(hashlib.sha256(original).hexdigest() == row["input_sha256"],
                row["id"] + ": retained original input mismatch")
        actual_receipt = verify_execution(record, row["actual"], row["id"])
        reference = record.get("reference", {})
        reference_input = verified_file(reference.get("input"), row["id"] + ".reference.input")
        require(reference_input == original, row["id"] + ": reference original bytes differ")
        reference_receipt = verify_execution(reference, row["expected"], row["id"] + ".reference")
        entries.append({
            "suite_id": "pcc-regression-" + data["language"],
            "suite_version": "original-input-sha256:" + row["input_sha256"],
            "suite_source_sha256": row["input_sha256"],
            "compiler_revision": row["source_revision"],
            **{axis: row[axis] for axis in AXES},
            "test_id": row["test_id"], "input_sha256": row["input_sha256"],
            "expectation": "run", "reference": {
                key: row["reference"][key] for key in ("name", "version", "platform", "role", "kind")},
            "outcome": "passed",
            "verification": {"level": "retained_process_artifacts",
                             "case_binding": "audited_private_receipt_index",
                             "compiler_revision": row["source_revision"],
                             "actual_receipt_sha256": actual_receipt,
                             "reference_receipt_sha256": reference_receipt,
                             "test_source_sha256": row["source"]["sha256"]},
        })
    result = {"schema_version": 1, "entries": sorted(entries, key=lambda row: row["test_id"])}
    validate_allowlist(result)
    return result


def shard_plan(suite, count):
    validate_suite(suite)
    require(type(count) is int and count > 0, "positive shard count required")
    shards = [[] for _ in range(count)]
    for row in sorted(suite["tests"], key=lambda row: row["id"]):
        index = int(hashlib.sha256(row["id"].encode()).hexdigest(), 16) % count
        shards[index].append(row["id"])
    return {"suite_id": suite["id"], "suite_source_sha256": suite["source_sha256"],
            "adapter": suite["adapter"], "execution_performed": False, "shards": shards}


def blockers(runs_and_suites):
    """Rank observed blockers by distinct tests and projects, never by log spam."""
    groups = defaultdict(lambda: {"tests": set(), "projects": set(), "modes": set()})
    for run, suite in runs_and_suites:
        validate_run(run, suite)
        tests = {row["id"]: row for row in suite["tests"]}
        for row in run["results"]:
            if test_passed(row, tests[row["test_id"]]):
                continue
            keys = [("diagnostic", key) for key in row.get("diagnostic_codes", [])]
            keys += [("feature", key) for key in row.get("blocker_feature_ids", [])]
            if not keys:
                keys = [("unclassified", "unmapped")]
            for key in keys:
                group = groups[key]
                group["tests"].add((suite["id"], row["test_id"]))
                if row.get("project"):
                    group["projects"].add(row["project"])
                group["modes"].add(tuple(run[axis] for axis in AXES))
    return sorted([{"kind": key[0], "id": key[1], "test_count": len(value["tests"]),
                    "project_count": len(value["projects"]), "projects": sorted(value["projects"]),
                    "modes": [dict(zip(AXES, mode)) for mode in sorted(value["modes"])]}
                   for key, value in groups.items()],
                  key=lambda row: (-row["project_count"], -row["test_count"], row["kind"], row["id"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    for operation in ("summary", "allowlist"):
        command = sub.add_parser(operation)
        command.add_argument("suite", type=Path)
        command.add_argument("run", type=Path)
        if operation == "allowlist":
            command.add_argument("--receipt-index", type=Path, required=True)
    qualified = sub.add_parser("qualified-allowlist")
    qualified.add_argument("ledger", type=Path)
    qualified.add_argument("receipt_index", type=Path)
    plan = sub.add_parser("plan")
    plan.add_argument("suite", type=Path)
    plan.add_argument("--shards", type=int, default=6)
    gate = sub.add_parser("gate")
    gate.add_argument("baseline", type=Path)
    gate.add_argument("current", type=Path)
    gate.add_argument("--current-revision", required=True)
    args = parser.parse_args(argv)

    def read(path):
        return json.loads(path.read_text())

    try:
        if args.operation == "qualified-allowlist":
            output = qualified_allowlist(read(args.ledger), read(args.receipt_index))
        elif args.operation == "plan":
            output = shard_plan(read(args.suite), args.shards)
        elif args.operation == "gate":
            output = regression_gate(read(args.baseline), read(args.current), args.current_revision)
        else:
            suite, run = read(args.suite), read(args.run)
            output = (summarize_run(run, suite) if args.operation == "summary"
                      else passed_allowlist(run, suite, read(args.receipt_index)))
        print(json.dumps(output, indent=2))
        return int(args.operation == "gate" and not output["passed"])
    except (LedgerError, OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f"conformance metadata rejected: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
