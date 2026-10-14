#!/usr/bin/env python3
"""Bounded host-pcc0 object regressions using the terminal Stage1 runtime.

Five compiles and twenty-five native executions, with no archive/compiler provisioning.
This independent gate does not qualify Stage1, pcc1 or a self-host fixed point.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.ci_worker_size_prior_gate import (
    TWO_ROOT_CLEANUP_INPUTS,
    environment,
    run_pytest,
    runtime_identity,
    save,
    sha,
    source_identity,
)


NAME = "native-object-regressions"
TIMEOUT_S = 300
NODES = (
    "tests/python/test_object_type_identity.py::test_object_identity_native_five_gc[pcc0]",
    "tests/python/test_super_call_result_roots.py::"
    "test_super_result_native_matches_cpython_under_five_collectors",
    "tests/python/test_dict_method_result_ownership.py::"
    "test_dict_method_result_owners_native_five_gc[pcc0-copy]",
    "tests/python/test_dict_method_result_ownership.py::"
    "test_dict_method_result_owners_native_five_gc[pcc0-pop]",
    "tests/python/test_slot_call_exception_cleanup.py::"
    "test_two_root_cleanup_error_path_native_five_gc[pcc0]",
)
TEST_INPUTS = (
    *(node.split("::")[0] for node in NODES),
    *TWO_ROOT_CLEANUP_INPUTS,
    "tests/python/owned_regression_support.py",
    "tests/python/process_timeout.py",
    "tests/python/conftest.py",
    "tests/conftest.py",
    "tests/runtime_fixture_provenance.py",
    "tests/test_ci_native_object_regressions.py",
    "conftest.py",
    "pyproject.toml",
)
WINDOWS_SYSTEM_LIBRARIES = {
    "kernel32.dll", "ws2_32.dll", "ntdll.dll", "shell32.dll", "psapi.dll", "bcrypt.dll",
}


def expected_programs():
    from tests.python.test_dict_method_result_ownership import (
        _program as dict_method_program,
    )
    from tests.python.test_object_type_identity import PROGRAM
    from tests.python.test_slot_call_exception_cleanup import TWO_ROOT_PROGRAM
    from tests.python.test_super_call_result_roots import NATIVE_SOURCE

    return {
        "object-identity": (PROGRAM, "OBJECT_TYPE_IDENTITY_OK\n"),
        "super-result": (NATIVE_SOURCE, "5\nTrue\ncaught\n"),
        "dict-copy-result": (dict_method_program("copy"), "DICT_METHOD_OWNERS_OK\n"),
        "dict-pop-result": (dict_method_program("pop"), "DICT_METHOD_OWNERS_OK\n"),
        "two-root-cleanup": (TWO_ROOT_PROGRAM, "TWO_ROOT_CLEANUP_EXCEPTION_OK\n"),
    }


def verify_native_image(binary):
    from scripts.verify_nolibpython import linked_libraries

    payload = binary.read_bytes()
    if payload[:4] == b"\x7fELF":
        from pcc.backend.elf_x86_64 import parse_static_executable

        # Both ELF targets must have no interpreter or dynamic program header.
        parse_static_executable(payload)
        return {"format": "static-elf", "libraries": []}
    assert payload[:2] == b"MZ", "expected a native Linux or Windows executable"
    libraries = linked_libraries(payload)
    assert libraries and set(libraries) <= WINDOWS_SYSTEM_LIBRARIES, libraries
    return {"format": "pe", "libraries": libraries}


def verify_program_receipts(directory, runtime):
    expected = expected_programs()
    paths = sorted(directory.rglob("ownership-regression.json"))
    assert len(expected) == len(NODES), "native node/source manifest count differs"
    assert len(paths) <= len(NODES), "unexpected native execution receipt count"
    results = {}
    for path in paths:
        record = json.loads(path.read_text(encoding="utf-8"))
        source = path.parent / "program.py"
        binary = path.parent / "program.out"
        # Text comparison is intentional: Windows writes CRLF, while the
        # recorded source digest still binds the exact bytes on that runner.
        program = source.read_text(encoding="utf-8")
        labels = [label for label, (text, _output) in expected.items() if text == program]
        assert len(labels) == 1, "unexpected or duplicate source-program receipt"
        label = labels[0]
        _program, output = expected.pop(label)
        assert record["status"] == "PASS"
        assert record["compiler_fixture_parameter"] == record["compiler_mode"] == "pcc0"
        assert record["backend"] == "self" and record["libpython"] == "off"
        assert record["source_sha256"] == sha(source)
        assert record["runtime_archive"] == runtime["path"]
        assert record["runtime_sha256"] == runtime["hashes"][""]
        assert record["binary_sha256"] == sha(binary)
        oracle = record["reference"]
        assert (oracle["returncode"], oracle["stdout"], oracle["stderr"]) == (0, output, "")
        executions = record["executions"]
        assert [row["gc_backend"] for row in executions] == list(range(5))
        for row in executions:
            assert row["command"] == [str(binary)]
            assert (row["returncode"], row["stdout"], row["stderr"]) == (0, output, "")
        results[label] = {
            "receipt": str(path), **record, "image": verify_native_image(binary),
            "executions": [
                {"requested_gc_backend": row["gc_backend"],
                 **{key: value for key, value in row.items() if key != "gc_backend"}}
                for row in executions
            ],
        }
    assert not expected, "missing native execution receipts: " + ", ".join(sorted(expected))
    return results


def run(out):
    out = Path(out).resolve()
    receipt_path = out / (NAME + ".json")
    assert not receipt_path.exists(), "refuse a stale native object gate receipt"
    receipt = {
        "schema": "pcc.native-object-regressions.v1",
        "status": "RUNNING",
        "compiler_mode": "host-pcc0",
        "qualifies_stage1": False,
        "nodes": list(NODES),
        "timeout_s": TIMEOUT_S,
        "tree_rss_limit_bytes": 17179869184,
        "observed_collector_witness": False,
        "collector_evidence": "requested_gc_backend records are PCC_GC_BACKEND requests; "
                              "collector selection is not independently observed",
    }
    save(receipt_path, receipt)
    try:
        assert sys.platform.startswith("linux") or sys.platform == "win32"
        env = dict(environment())
        env.pop("LC_ALL", None)
        # Stage1 enables timing telemetry, but these behavioral regressions
        # also assert quiet compilation. Keep diagnostics enabled for Stage1
        # itself and disable only its opt-in timing in this child environment.
        env.pop("PCC_PY_FRONTEND_WORKER_TIMING", None)
        env.update(PCC_TEST_NO_NATIVE_PROVISIONING="1", UV_PYTHON_DOWNLOADS="never")
        selected = env.get("PCC_RUNTIME_ARCHIVE")
        assert selected, "the existing Stage1 runtime must be admitted before this gate"
        archive = Path(selected)
        assert archive.is_absolute() and archive.is_file(), "admitted runtime archive is missing"
        source = source_identity()
        assert source["clean_commit"] is True and source["commit"] == os.environ["GITHUB_SHA"]
        stage = json.loads((out / "stage1.json").read_text(encoding="utf-8"))
        assert stage["phase"] == "stage1" and stage["status"] in ("PASS", "FAIL")
        assert stage["source"] == source, "source, Python or locked test tools differ from Stage1"
        runtime = runtime_identity(archive)
        assert runtime == stage["runtime"], "runtime does not match the terminal Stage1 receipt"
        inputs = {name: sha(ROOT / name) for name in TEST_INPUTS}
        receipt.update(source=source, runtime=runtime, test_inputs=inputs,
                       stage1_status=stage["status"])
        save(receipt_path, receipt)
        try:
            run_pytest(out, NAME, NODES, env, integration=True, timeout=TIMEOUT_S)
            receipt["programs"] = verify_program_receipts(out / NAME, runtime)
        finally:
            receipt["source_after"] = source_identity()
            receipt["runtime_after"] = runtime_identity(archive)
            assert receipt["source_after"] == source, "source changed during native tests"
            assert receipt["runtime_after"] == runtime, "runtime changed during native tests"
            assert {name: sha(ROOT / name) for name in TEST_INPUTS} == inputs
        receipt["status"] = "PASS"
    except BaseException as error:
        receipt.update(status="FAIL", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        save(receipt_path, receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    run(args.out_dir)


if __name__ == "__main__":
    main()
