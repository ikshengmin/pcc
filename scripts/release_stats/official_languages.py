"""Bounded official-suite execution and receipt-based release statistics.

The existing Go adapter owns compilation and upstream recipe semantics. This
module selects it, replays its retained observations, and never equates pytest
nodes, compile-only inputs, or expected rejections with executed programs.
"""

from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys

from common import REPO, read_json, sha256_file, write_json

sys.path.insert(0, str(REPO / "scripts"))
import conformance_ledger as ledger  # noqa: E402
import go_conformance as go  # noqa: E402
import platform_process_watchdog as watchdog  # noqa: E402


GO_MANIFEST = REPO / "specs/conformance/suites/go1.27.3-representative.json"
OUTCOMES = ("run_pass", "compile_only_pass", "expected_reject_pass", "failed",
            "unsupported", "missing_prerequisite", "missing_evidence", "unrun")
KINDS = {"run": "run_pass", "accept": "compile_only", "reject": "expected_reject"}


def inventory_summary(suite: dict, inventory: dict | None) -> dict:
    """Never manufacture a whole-suite denominator from a representative shard."""
    if inventory is None:
        return {"complete": False, "denominator": None, "unobserved": None,
                "scope": "official driver inventory not supplied"}
    for key in ("version", "source_revision", "archive_sha256", "license_sha256"):
        if inventory.get(key) != suite[key]:
            raise ValueError("official inventory identity mismatch: " + key)
    if inventory.get("inventory_complete") is not True or any(
            inventory.get(key) != [] for key in ("missing_inputs", "extra_inputs", "changed_inputs")):
        raise ValueError("official inventory is not complete and unchanged")
    rows = inventory.get("tests", [])
    sources = {row["path"]: row["sha256"] for row in rows}
    if len(sources) != len(rows) or inventory.get("driver_test_count") != len(rows):
        raise ValueError("official inventory denominator/duplicate mismatch")
    if any(sources.get(test["path"]) != test["sha256"] for test in suite["tests"]):
        raise ValueError("official selection absent or changed in inventory")
    recipes = dict(sorted(Counter(row["action"] for row in rows).items()))
    admitted = sum(row["supported"] is True for row in rows)
    if recipes != inventory.get("recipes") or admitted != inventory.get("adapter_supported_count"):
        raise ValueError("official inventory recipe/admission count mismatch")
    return {"complete": True, "denominator": len(rows), "unobserved": len(rows),
            "scope": inventory["scope"], "recipe_counts": recipes,
            "unobserved_by_recipe": dict(recipes),
            "literal_plain_run_count": sum(row.get("recipe") == "run" for row in rows),
            "adapter_admitted_not_passed": admitted,
            "standard_library": inventory.get("standard_library", {
                "inventory_complete": False, "execution": "not_run", "source_file_count": None})}


def _phase(directory: Path, prefix: str, phase: dict) -> tuple[bytes, bytes] | None:
    if phase["outcome"] not in ("accepted", "rejected", "completed"):
        return None
    receipt = read_json(directory / (prefix + ".receipt.json"))
    if (receipt.get("returncode") != phase["exit_code"]
            or receipt.get("timed_out") is not False or not receipt.get("command")):
        raise ValueError("retained process receipt contradicts observation")
    streams = []
    for name in ("stdout", "stderr"):
        path = directory / (prefix + "." + name)
        if sha256_file(path) != phase[name + "_sha256"]:
            raise ValueError("retained process stream hash mismatch")
        streams.append(path.read_bytes())
    return tuple(streams)


def _replay(directory: Path, test: dict, row: dict) -> tuple[bool, bool]:
    """Re-read original input, raw process bytes/exits, and upstream assertions.

    This is a report observation, not a passed-allowlist export or proof of the
    entire compiler ownership closure. The ledger's baseline gate stays closed.
    """
    source = directory / Path(test["path"]).name
    if sha256_file(source) != test["sha256"] or go.parse_directive(source.read_bytes()) != test["directive"]:
        raise ValueError("retained original input/directive mismatch")
    actual_compile = _phase(directory, "pcc-compile", row["compile"])
    actual_run = _phase(directory, "pcc-run", row["run"])
    reference = row.get("reference")
    if reference is None:
        return actual_run is not None, False
    reference_compile = _phase(directory, "go-compile", reference["compile"])
    reference_run = _phase(directory, "go-run", reference["run"])
    if not ledger.test_passed(row, test):
        return actual_run is not None, False
    if test["expectation"] == "run":
        passed = actual_run == reference_run and actual_run is not None
        for owner, streams in ((row, actual_run), (reference, reference_run)):
            passed = passed and streams is not None and owner["run"]["exit_code"] == 0
            if streams is not None:
                stdout, stderr = streams
                passed = (passed and not (stdout and stderr)
                          and go.sha((stdout + stderr).replace(b"\r\n", b"\n"))
                          == test["expected_output"]["sha256"])
        return actual_run is not None, bool(passed)
    if test["expectation"] == "accept":
        return False, (directory / "owned").is_file() and (directory / "reference").is_file()
    diagnostic = read_json(directory / "diagnostic.json")
    passed = diagnostic.get("kind") == "GoDiagnostic" and diagnostic.get("implementation_unsupported") is False
    for streams in (actual_compile, reference_compile):
        passed = passed and streams is not None and go.errorcheck(
            test["directive"]["errors"], b"".join(streams), source.name) == "passed"
    return False, bool(passed)


def summarize_go(run_dir: Path | None, identity: dict, inventory: dict | None = None) -> dict:
    suite = read_json(GO_MANIFEST)
    ledger.validate_suite(suite)
    result = {key: suite[key] for key in (
        "id", "language", "version", "license", "license_sha256", "source_revision",
        "source_sha256", "source_url", "adapter", "inventory_complete")}
    result.update(status="unrun", selection="tiny_representative_shard",
                  selected_denominator=len(suite["tests"]),
                  selected_by_kind=dict(Counter(KINDS[test["expectation"]] for test in suite["tests"])),
                  inventory=inventory_summary(suite, inventory),
                  executed_programs=0, reference_matched_programs=0,
                  outcomes=dict.fromkeys(OUTCOMES, 0), cases=[], observed_compiler_revision=None,
                  current_compiler_revision=identity["pcc"]["revision"], current_revision_match=False)
    run = None
    reference_available = False
    if run_dir is not None:
        run = read_json(run_dir / "run.json")
        ledger.validate_run(run, suite)
        if (run["mode"], run["backend"], run["platform"], run["gc"]) != (
                "pcc0", "self", "linux-x86_64", "not_exercised"):
            raise ValueError("official Go run has incompatible execution axes")
        compiler = read_json(run_dir / "compiler-identity.json")
        adapter = read_json(run_dir / "adapter-identity.json")
        hashes = compiler.get("source_hashes")
        if (compiler.get("revision") != run["compiler_revision"] or not isinstance(hashes, dict)
                or not hashes or compiler.get("closure_sha256") != go.sha(json.dumps(hashes, sort_keys=True).encode())):
            raise ValueError("retained compiler identity mismatch")
        if adapter.get("manifest_sha256") != sha256_file(GO_MANIFEST):
            raise ValueError("retained adapter manifest identity mismatch")
        reference = read_json(run_dir / "reference-identity.json")
        if reference.get("source_archive_sha256") != suite["archive_sha256"]:
            raise ValueError("retained reference archive identity mismatch")
        version = _phase(run_dir, "reference-version", reference["version_process"])
        reference_available = (version is not None and reference["version_process"].get("exit_code") == 0
                               and version[0].strip() == b"go version go1.27.3 linux/amd64")
        for row in run["results"]:
            oracle = row.get("reference")
            if oracle is not None and (oracle["version"] != suite["version"] or oracle["platform"] != run["platform"]):
                raise ValueError("retained reference version/platform mismatch")
        result.update(status="observed", observed_compiler_revision=run["compiler_revision"],
                      current_revision_match=run["compiler_revision"] == identity["pcc"]["revision"],
                      **{key: run[key] for key in ("mode", "backend", "platform", "gc")})
    rows = {row["test_id"]: row for row in run["results"]} if run else {}
    for index, test in enumerate(suite["tests"]):
        row = rows.get(test["id"])
        outcome, category = "unrun", "not_observed"
        if row is not None:
            category = go.blocker_category(row)
            try:
                executed, passed = _replay(run_dir / f"case-{index:02d}", test, row)
                result["executed_programs"] += executed
                if passed and reference_available:
                    outcome = {"run": "run_pass", "accept": "compile_only_pass",
                               "reject": "expected_reject_pass"}[test["expectation"]]
                    category = "passed"
                    result["reference_matched_programs"] += test["expectation"] == "run"
                elif category == "missing_tool" or (passed and not reference_available):
                    outcome = "missing_prerequisite"
                elif (category in ("harness_recipe", "frontend_unsupported", "library_runtime_dependency")
                      or "unsupported" in row.get("checks", {}).values()):
                    outcome = "unsupported"
                elif all(row[key]["outcome"] == "not_run" for key in ("compile", "run")):
                    outcome = "unrun"
                else:
                    outcome = "failed"
            except (OSError, ValueError, KeyError, TypeError):
                outcome, category = "missing_evidence", "retained_artifact_missing_or_inconsistent"
        result["outcomes"][outcome] += 1
        result["cases"].append({"id": test["id"], "kind": KINDS[test["expectation"]],
                                "outcome": outcome, "category": category})
    result["observed"] = len(rows)
    if result["inventory"]["complete"]:
        result["inventory"]["unobserved"] -= len(rows)
        for test in suite["tests"]:
            if test["id"] in rows:
                result["inventory"]["unobserved_by_recipe"][test["directive"]["action"]] -= 1
    return result


def execute_go(options, stage_dir: Path, identity: dict) -> tuple[Path | None, dict]:
    """Opt-in real adapter dispatch; never downloads tools or widens budgets."""
    if options.official_selection == "full":
        return None, {"status": "unsupported", "reason": "full-suite adapter/inventory selection is not implemented"}
    names = ("official_go_src", "official_go_tool", "official_go_reference_cache")
    if any(getattr(options, name, None) is None for name in names):
        return None, {"status": "missing_prerequisite", "reason": "explicit Go source, tool and existing cache required"}
    if not Path(options.official_go_reference_cache).is_dir():
        return None, {"status": "missing_prerequisite", "reason": "reference cache directory is missing"}
    out = stage_dir / "official-go"
    if out.exists():
        raise ValueError("official execution output already exists; import it explicitly or choose a fresh --out")
    pcc_source = Path(getattr(options, "official_pcc_source", None) or REPO).resolve()
    command = [sys.executable, str(REPO / "scripts/go_conformance.py"), "run",
               "--manifest", str(GO_MANIFEST), "--go-src", str(Path(options.official_go_src).resolve()),
               "--go", str(Path(options.official_go_tool).resolve()),
               "--reference-cache", str(Path(options.official_go_reference_cache).resolve()),
               "--pcc-source", str(pcc_source), "--compiler-revision", identity["pcc"]["revision"],
               "--out", str(out), "--timeout", "6"]
    execution = {"status": "complete", "returncode": 0, "timeout_seconds": 90, "rss_limit_bytes": 1 << 30}
    try:
        watchdog.run(command, cwd=REPO, env=dict(os.environ, PCC_NO_AUTO_PCC1="1"),
                     log_path=stage_dir / "official-go.log", timeout=90, rss_limit=1 << 30)
    except subprocess.CalledProcessError as error:
        execution.update(status="failed", returncode=error.returncode)
    except (OSError, RuntimeError, TimeoutError) as error:
        execution.update(status="interrupted", reason=type(error).__name__)
    write_json(stage_dir / "official-go-execution.json", execution)
    return (out if (out / "run.json").is_file() else None), execution


def collect(options, stage_dir: Path, identity: dict) -> dict:
    imported = getattr(options, "official_go_run", None)
    requested = getattr(options, "official_go_execute", False)
    if imported and requested:
        raise ValueError("choose official Go import or execution, not both")
    inventory_path = getattr(options, "official_go_inventory", None)
    inventory = read_json(Path(inventory_path)) if inventory_path else None
    # Reject stale or mismatched inventory before launching any compiler.
    inventory_summary(read_json(GO_MANIFEST), inventory)
    execution = {"status": "not_requested"}
    if requested:
        imported, execution = execute_go(options, stage_dir, identity)
    elif getattr(options, "official_selection", "fast") == "full":
        execution = {"status": "unsupported", "reason": "full-suite selection unavailable; retained shard stays partial"}
    entry = summarize_go(Path(imported) if imported else None, identity, inventory)
    entry["execution"] = execution
    entry["requested_selection"] = getattr(options, "official_selection", "fast")
    return {"go": entry, **{language: {
        "language": language, "status": "missing_adapter", "selected_denominator": None,
        "reason": "no pinned official-suite manifest/runner; authored regression tests are separate",
    } for language in ("cpp", "rust", "java")}}
