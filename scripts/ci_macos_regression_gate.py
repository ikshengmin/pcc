#!/usr/bin/env python3
"""Focused Darwin pcc0 execution gates; never certify Stage1 or a fixed point.

Run under scripts/run_process_tree_sample.py. The workflow bounds the complete
process tree, then collects small receipts even after a timeout. Runtime and
executable artifacts stay on the runner. No test/runtime failure becomes a skip.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OWNERSHIP_NODE = (
    "tests/python/test_valueclass_aggregate_ownership_native.py::"
    "test_aggregate_ownership_native_five_gc"
)
GATES = (
    ("clock", (
        "tests/python/test_worker_state_clock_contract.py::"
        "test_emitted_host_clock_shares_cpython_epoch_across_processes",
        "tests/python/test_worker_state_clock_contract.py::"
        "test_darwin_clock_abi_matches_host_state_after_system_suspend[17924]",
    )),
    # These unchanged programs request all five GC configurations; the shared
    # helper checks native results, not collector-selection telemetry.
    ("oserror", (
        "tests/python/test_os_error_constructors.py::"
        "test_os_error_constructor_native_five_gc[pcc0]",
        "tests/python/test_native_open_errno.py::"
        "test_open_errno_native_five_gc[pcc0]",
    )),
    ("time", (
        "tests/python/test_owned_darwin_time_runtime.py::"
        "test_darwin_import_time_and_gmtime_execute[pcc0]",
    )),
    ("native-worker-clock", (
        "tests/python/test_native_worker_state_clock.py::"
        "test_native_worker_state_reader_matches_host_clock[pcc0]",
    )),
    ("worker-handles", (
        "tests/python/test_worker_resource_plan.py::"
        "test_resource_acceptance_driver_matches_cpython[handles]",
        "tests/python/test_worker_resource_setpgid.py::"
        "test_darwin_resource_fixture_handles_execute[pcc0]",
    )),
    ("root-joins", tuple(
        OWNERSHIP_NODE + "[pcc0-" + case + "]"
        for case in (
            "typed-list-unbox", "typed-loop-unbox", "typed-comprehension-unbox",
            "original-direct-payload", "original-valuebox",
        )
    )),
    ("float-protocol", (
        "tests/python/test_native_float_protocol.py::"
        "test_float_protocol_native_five_gc[pcc0]",
    )),
    ("class-docstrings", (
        "tests/python/test_native_class_docstrings.py::"
        "test_class_docstrings_native_five_gc[pcc0]",
    )),
    ("float-callback-movement", (
        "tests/python/test_native_float_callback_movement.py::"
        "test_canonical_float_callback_movement_gc4[pcc0]",
    )),
)
PROFILES = {
    "default": (
        "clock", "runtime-build", "oserror", "time", "native-worker-clock",
        "worker-handles", "root-joins",
    ),
    "float-doc": (
        "runtime-build", "float-protocol", "class-docstrings", "float-callback-movement",
    ),
}
EVIDENCE_LIMIT = 16 * 1024 * 1024
RECEIPT_NAMES = {
    "qualification.json", "command.json", "result.json", "runtime-identity.json",
    "live.jsonl", "junit.xml", "ownership-regression.json", "execution.json",
    "darwin-resource-handles.json", "native-worker-clock.json", "reference.json",
    "float-callback-movement.json",
    *(f"gc{backend}.json" for backend in range(5)),
}


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_execution_report(path, expected):
    """Require every exact node to execute, including setup and teardown."""
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    starts = [row for row in rows if row["event"] == "start"]
    finishes = [row for row in rows if row["event"] == "finish"]
    collections = [row["nodeids"] for row in rows if row["event"] == "collected"]
    assert len(starts) == 1 and not starts[0]["collect_only"], "collection is not execution"
    assert collections == [list(expected)], ("missing/reordered gates", collections, expected)
    assert not any(row["event"] == "deselected" for row in rows), "a required gate was deselected"
    assert len(finishes) == 1 and finishes[0]["exitstatus"] == 0, "pytest did not finish successfully"
    assert finishes[0]["testscollected"] == len(expected)
    assert finishes[0]["testsfailed"] == 0
    reports = [row for row in rows if row["event"] == "report"]
    assert len(reports) == 3 * len(expected), "missing or duplicate execution reports"
    for node in expected:
        actual = [row for row in reports if row["nodeid"] == node]
        assert [row["when"] for row in actual] == ["setup", "call", "teardown"], node
        assert all(row["outcome"] == "passed" and "wasxfail" not in row for row in actual), actual
    return list(expected)


def _command(command, directory, environment):
    directory.mkdir(parents=True, exist_ok=True)
    _save(directory / "command.json", {
        "command": command,
        "worker_memory_guard": {
            key: environment.get(key, "") for key in (
                "PCC_WORKER_TREE_BUDGET_BYTES", "PCC_WORKER_TREE_STATE_PATH",
            )
        },
    })
    with (directory / "target.stdout").open("wb") as stdout, \
            (directory / "target.stderr").open("wb") as stderr:
        return subprocess.run(command, cwd=ROOT, env=environment,
                              stdout=stdout, stderr=stderr).returncode


def _pytest_gate(name, nodes, out, environment):
    directory = out / "work" / name
    command = [
        sys.executable, "-m", "pytest", "-o", "addopts=", "-x", "-n0",
        "-vv", "--tb=short", "-p", "scripts.pytest_live_report",
        "--pcc-live-report", str(directory / "live.jsonl"),
        "--junitxml", str(directory / "junit.xml"),
        "--basetemp", str(directory / "tests"), *nodes,
    ]
    code = _command(command, directory, environment)
    assert code == 0, f"{name}: pytest exited {code}; see {directory}/target.stderr and target.stdout"
    return verify_execution_report(directory / "live.jsonl", nodes)


def run(out, profile="default"):
    if profile not in PROFILES:
        raise ValueError("unknown Mac regression profile: " + str(profile))
    order = PROFILES[profile]
    assert sys.platform == "darwin" and platform.machine() == "arm64", "Darwin arm64 execution is required"
    assert not os.environ.get("PCC_TEST_COMPILER"), "these gates require the host pcc0 compiler"
    environment = dict(os.environ)
    environment.pop("LC_ALL", None)
    environment.update(PCC_NO_AUTO_PCC1="1", PCC_TEST_COMPILER_STRICT="1")
    receipt = {
        "schema": "pcc.macos-regression-ci.v1", "status": "RUNNING",
        "profile": profile,
        "source_commit": environment.get("GITHUB_SHA", ""),
        "scope": "host pcc0 self/off emitted execution; Stage1 is a separate job",
        "gates": {name: {"status": "NOT_RUN", "expected_nodes": list(dict(GATES).get(name, ()))}
                  for name in order},
    }
    if profile == "default":
        receipt["clock_scope"] = "real host/native clock epoch; simulated suspend; emitted native worker-state reader"
    else:
        receipt["collector_scope"] = (
            "float protocol and class docstrings request GC0-4 without collector-selection telemetry; "
            "float callback movement asserts actual GC4 and positive selection/relocation "
            "in both callback and cleanup"
        )
    receipt_path = out / "work" / "qualification.json"
    _save(receipt_path, receipt)
    current = order[0]
    try:
        for name in order:
            current = name
            receipt["gates"][name]["status"] = "RUNNING"
            _save(receipt_path, receipt)
            print("MACOS_REGRESSION_START " + name, flush=True)
            if name == "runtime-build":
                environment.update(
                    PCC_WITH_THREADS="1", PCC_REFCOUNT_KIND="atomic",
                    PCC_RUNTIME_BUILD="owned", PCC_RUNTIME_CC="pcc",
                    PCC_RUNTIME_HIGH="py", PCC_PYTHON_IR_PASSES="off",
                    PCC_SELF_LINK="pcc", PCC_SELF_OBJ="pcc",
                    PCC_IR_TO_OBJ_EMITTER="pcc",
                )
                archive = out / "runtime" / "libpy_runtime_pcc_py.a"
                code = _command([
                    sys.executable, "-m", "pcc.frontends.python.owned_runtime_build",
                    "--output", str(archive),
                ], out / "work" / name, environment)
                assert code == 0, f"owned threaded runtime build exited {code}"
                from tests.runtime_fixture_provenance import _verified_test_runtime_archive
                checked, manifest = _verified_test_runtime_archive(archive, threads=True)
                assert checked == archive
                assert all(member["runtime_build_config"] == {
                    "threads": True, "refcount": "atomic",
                } for member in manifest["members"])
                _save(out / "work" / name / "runtime-identity.json", {
                    "archive_sha256": _sha256(archive),
                    "provenance_sha256": _sha256(Path(str(archive) + ".provenance.json")),
                    "capi_inventory_sha256": _sha256(Path(str(archive) + ".capi_syms")),
                    "target": manifest["target_triple"],
                    "member_count": manifest["member_count"],
                    "config": {"threads": True, "refcount": "atomic"},
                    "codegen_sha256": sorted({member["codegen_checksum"] for member in manifest["members"]}),
                })
                environment.update(PCC_RUNTIME_ARCHIVE=str(archive),
                                   PCC_THREADED_RUNTIME_ARCHIVE=str(archive),
                                   PCC_TEST_NO_NATIVE_PROVISIONING="1")
            else:
                nodes = dict(GATES)[name]
                executed = _pytest_gate(name, nodes, out, environment)
                receipt["gates"][name]["executed_nodes"] = executed
            receipt["gates"][name]["status"] = "PASS"
            _save(receipt_path, receipt)
            print("MACOS_REGRESSION_PASS " + name, flush=True)
    except Exception as error:
        receipt["gates"][current].update(status="FAIL", error=str(error))
        receipt["status"] = "FAIL"
        _save(receipt_path, receipt)
        raise
    receipt["status"] = "PASS"
    _save(receipt_path, receipt)


def collect_evidence(out):
    """Compress complete selected receipts, never archive binaries or IR.

    The cap is explicit and fail-closed. If compressed receipts exceed 16 MiB,
    the index names and hashes the omitted files; no truncated log is presented
    as complete. Full originals remain on the runner for that job's lifetime.
    """
    destination = out / "evidence"
    destination.mkdir(parents=True, exist_ok=True)
    index, used, omitted = [], 0, False
    for path in sorted((out / "work").rglob("*")):
        if not path.is_file() or not (
            path.name in RECEIPT_NAMES or path.suffix in {".stdout", ".stderr", ".tsv"}
        ):
            continue
        relative = path.relative_to(out / "work")
        target = destination / relative.with_name(relative.name + ".gz")
        target.parent.mkdir(parents=True, exist_ok=True)
        with path.open("rb") as source, gzip.open(target, "wb") as output:
            shutil.copyfileobj(source, output)
        size = target.stat().st_size
        record = {"source": str(relative), "bytes": path.stat().st_size,
                  "sha256": _sha256(path), "compressed_bytes": size}
        if used + size > EVIDENCE_LIMIT:
            target.unlink()
            record["status"] = "OMITTED_EVIDENCE_LIMIT"
            omitted = True
        else:
            used += size
            record.update(status="COMPLETE", artifact=str(target.relative_to(destination)))
        index.append(record)
    _save(destination / "index.json", {"limit_bytes": EVIDENCE_LIMIT,
          "compressed_bytes": used, "status": "LIMIT_EXCEEDED" if omitted else "COMPLETE",
          "files": index})
    assert not omitted, "Evidence exceeded the 16 MiB compressed cap; index.json identifies omitted complete files"
    assert index, "No regression evidence was produced"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--profile", choices=tuple(PROFILES), default="default")
    parser.add_argument("--collect-evidence", action="store_true")
    args = parser.parse_args(argv)
    out = args.out_dir.resolve()
    if args.collect_evidence:
        collect_evidence(out)
    else:
        run(out, profile=args.profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
