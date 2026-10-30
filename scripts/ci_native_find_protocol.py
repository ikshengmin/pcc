#!/usr/bin/env python3
"""Reuse a successful Stage1 compiler/runtime for find and str length qualification.

Two explicit pcc0/pcc1 compiles and ten requested five-GC executions.
Each executable imports all six find cases, then the complete str length case.
No compiler or runtime provisioning, timeout extension, or Stage1 replay.
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
    environment,
    run_pytest,
    runtime_identity,
    save,
    sha,
    source_identity,
)


NAME = "native-find-protocol"
TIMEOUT_S = 300
TEST = "tests/python/test_bound_find_protocol.py"
CASES = (
    "exact_binary", "str_payload", "binary_order", "str_order",
    "arity_kwargs", "custom_lookup",
)
MODES = ("pcc0", "pcc1")
NODES = tuple(
    TEST + "::test_bound_find_and_str_len_owned_native_five_gc[" + mode + "]"
    for mode in MODES
)
TEST_INPUTS = (
    TEST,
    "tests/python/test_str_len_protocol.py",
    "scripts/ci_native_find_protocol.py",
    "scripts/ci_worker_size_prior_gate.py",
    ".github/workflows/pcc1-package-parity.yml",
    "tests/python/owned_regression_support.py",
    "tests/python/process_timeout.py",
    "tests/runtime_fixture_provenance.py",
    "tests/python/conftest.py",
    "tests/python/pcc1_gate.py",
    "tests/conftest.py",
    "conftest.py",
    "pyproject.toml",
    ".python-version",
)


def verify_receipts(directory, runtime):
    from tests.python.test_bound_find_protocol import (
        FIND_AND_STR_LEN_NATIVE_MODULES,
        FIND_AND_STR_LEN_NATIVE_OUTPUT,
        FIND_AND_STR_LEN_NATIVE_SOURCE,
        NATIVE_MODULES,
        NATIVE_OUTPUT,
        NATIVE_SOURCE,
        PROGRAMS,
    )
    from tests.python.test_str_len_protocol import EXPECTED, PROGRAM

    assert tuple(PROGRAMS) == CASES, "find node/program inventory differs"
    assert tuple(NATIVE_MODULES.values()) == tuple(PROGRAMS.values())
    assert tuple(NATIVE_MODULES) == tuple("bound_find_" + case + ".py" for case in CASES)
    assert NATIVE_SOURCE == "".join("import " + name[:-3] + "\n" for name in NATIVE_MODULES)
    assert NATIVE_OUTPUT == "BOUND_FIND_OK\n" * 6
    assert tuple(FIND_AND_STR_LEN_NATIVE_MODULES) == (*NATIVE_MODULES, "str_len_protocol.py")
    assert tuple(FIND_AND_STR_LEN_NATIVE_MODULES.values()) == (*PROGRAMS.values(), PROGRAM)
    assert FIND_AND_STR_LEN_NATIVE_SOURCE == NATIVE_SOURCE + "import str_len_protocol\n"
    assert EXPECTED == "STR_LEN_OK\n"
    assert FIND_AND_STR_LEN_NATIVE_OUTPUT == "BOUND_FIND_OK\n" * 6 + EXPECTED
    remaining = set(MODES)
    paths = sorted(directory.rglob("ownership-regression.json"))
    assert len(paths) == len(NODES), "missing or extra native find receipts"
    results = []
    for path in paths:
        record = json.loads(path.read_text(encoding="utf-8"))
        source = path.parent / "program.py"
        binary = path.parent / "program.out"
        assert source.read_text(encoding="utf-8") == FIND_AND_STR_LEN_NATIVE_SOURCE
        module_hashes = json.loads((path.parent / "native-module-manifest.json").read_text(encoding="utf-8"))
        assert set(module_hashes) == set(FIND_AND_STR_LEN_NATIVE_MODULES), "find/length module inventory differs"
        for name, text in FIND_AND_STR_LEN_NATIVE_MODULES.items():
            member = path.parent / name
            assert member.read_bytes() == text.encode("utf-8"), "find/length case source changed"
            assert module_hashes[name] == sha(member), "find/length case bytes changed"
        mode = record["compiler_fixture_parameter"]
        assert mode in remaining, "duplicate or unexpected native find mode"
        remaining.remove(mode)
        assert record["status"] == "PASS" and record["compiler_mode"] == mode
        assert record["backend"] == "self" and record["libpython"] == "off"
        assert record["source_sha256"] == sha(source)
        assert record["runtime_archive"] == runtime["path"]
        assert record["runtime_sha256"] == runtime["hashes"][""]
        assert record["binary_sha256"] == sha(binary)
        expected = (0, FIND_AND_STR_LEN_NATIVE_OUTPUT, "")
        reference = record["reference"]
        assert tuple(reference[key] for key in ("returncode", "stdout", "stderr")) == expected
        executions = record["executions"]
        assert [row["gc_backend"] for row in executions] == list(range(5))
        for row in executions:
            assert row["command"] == [str(binary)]
            assert tuple(row[key] for key in ("returncode", "stdout", "stderr")) == expected
        results.append({"cases": [*CASES, "str_len_protocol"], "mode": mode, "receipt": str(path),
                        "module_sha256": module_hashes, **record})
    assert not remaining, "missing native find compiler mode"
    return results


def run(out):
    out = Path(out).resolve()
    path = out / (NAME + ".json")
    assert not path.exists(), "refuse a stale native find gate receipt"
    receipt = {
        "schema": "pcc.native-find-protocol.v1", "status": "RUNNING",
        "nodes": list(NODES), "timeout_s": TIMEOUT_S,
        "compiler_modes": list(MODES), "qualifies_fixed_point": False,
        "observed_collector_witness": False,
        "collector_evidence": "gc_backend records the requested PCC_GC_BACKEND; "
                              "collector selection is not independently observed",
    }
    save(path, receipt)
    try:
        stage = json.loads((out / "stage1.json").read_text(encoding="utf-8"))
        assert stage["phase"] == "stage1" and stage["status"] == "PASS"
        source = source_identity()
        assert source["clean_commit"] is True and source["commit"] == os.environ["GITHUB_SHA"]
        assert stage["source"] == source, "find gate source differs from successful Stage1"
        compiler = stage["compiler"]
        binary = Path(compiler["path"])
        assert binary.is_absolute() and binary.is_file(), "Stage1 pcc1 is missing"
        assert sha(binary) == compiler["sha256"], "Stage1 pcc1 identity changed"
        archive = Path(stage["runtime"]["path"])
        assert archive.is_absolute() and archive.is_file(), "Stage1 runtime is missing"
        runtime = runtime_identity(archive)
        assert runtime == stage["runtime"], "find gate runtime differs from Stage1"
        assert runtime["config"]["threads"] is True and runtime["config"]["refcount"] == "atomic"
        inputs = {name: sha(ROOT / name) for name in TEST_INPUTS}
        env = dict(environment())
        env.update(
            PCC_RUNTIME_ARCHIVE=str(archive), PCC_CURRENT_PCC1=str(binary),
            PCC_REQUIRE_CURRENT_PCC1="1", PCC_TEST_NO_NATIVE_PROVISIONING="1",
            PCC_NO_AUTO_PCC1="1", UV_PYTHON_DOWNLOADS="never",
        )
        receipt.update(source=source, compiler=compiler, runtime=runtime, test_inputs=inputs)
        save(path, receipt)
        try:
            run_pytest(out, NAME, NODES, env, integration=True, timeout=TIMEOUT_S)
            receipt["programs"] = verify_receipts(out / NAME, runtime)
        finally:
            receipt["source_after"] = source_identity()
            receipt["runtime_after"] = runtime_identity(archive)
            receipt["compiler_sha256_after"] = sha(binary)
            assert receipt["source_after"] == source, "source changed during find tests"
            assert receipt["runtime_after"] == runtime, "runtime changed during find tests"
            assert receipt["compiler_sha256_after"] == compiler["sha256"], "pcc1 changed during find tests"
            assert {name: sha(ROOT / name) for name in TEST_INPUTS} == inputs
        receipt["status"] = "PASS"
    except BaseException as error:
        receipt.update(status="FAIL", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        save(path, receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    run(args.out_dir)


if __name__ == "__main__":
    main()
