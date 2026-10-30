"""One bounded host contract for native-find selection and receipt rejection.

This executes the real CI script with a stubbed process boundary and inert
binary files. It proves gate bookkeeping/fail-closed behavior, never native
compilation, collector execution, or Stage1 qualification.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_native_find_gate_selection_identity_and_fail_closed_receipts(tmp_path, monkeypatch):
    from tests.python.test_bound_find_protocol import (
        FIND_AND_STR_LEN_NATIVE_MODULES,
        FIND_AND_STR_LEN_NATIVE_OUTPUT,
        FIND_AND_STR_LEN_NATIVE_SOURCE,
        NATIVE_MODULES,
        NATIVE_OUTPUT,
        NATIVE_SOURCE,
        PROGRAMS,
        _write_native_find_and_str_len_suite,
    )
    from tests.python.test_str_len_protocol import EXPECTED, PROGRAM

    state = {}
    invocations = []

    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def save(path, value):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(value, indent=2) + "\n")

    def source_identity():
        result = dict(state["source"])
        if state.get("after") and state["fault"] == "source_after":
            result["commit"] = "changed-after-test"
        return result

    def runtime_identity(path):
        return {
            "path": str(path), "hashes": {"": sha(path)},
            "config": {
                "threads": state.get("fault") != "unthreaded_runtime",
                "refcount": "plain" if state.get("fault") == "nonatomic_runtime" else "atomic",
            },
        }

    def forbidden_process(*args, **kwargs):
        pytest.fail("host contract must not execute a compiler or native process")

    monkeypatch.setattr(subprocess, "run", forbidden_process)
    monkeypatch.setattr(subprocess, "Popen", forbidden_process)

    def run_pytest(out, name, nodes, env, *, integration, timeout):
        invocations.append((name, tuple(nodes), dict(env), integration, timeout))
        assert name == "native-find-protocol"
        assert tuple(nodes) == (
            "tests/python/test_bound_find_protocol.py::test_bound_find_and_str_len_owned_native_five_gc[pcc0]",
            "tests/python/test_bound_find_protocol.py::test_bound_find_and_str_len_owned_native_five_gc[pcc1]",
        )
        assert integration is True and timeout == 300
        assert env["PCC_RUNTIME_ARCHIVE"] == str(state["archive"])
        assert env["PCC_CURRENT_PCC1"] == str(state["compiler"])
        for key in ("PCC_REQUIRE_CURRENT_PCC1", "PCC_TEST_NO_NATIVE_PROVISIONING", "PCC_NO_AUTO_PCC1"):
            assert env[key] == "1"
        assert env["UV_PYTHON_DOWNLOADS"] == "never"
        assert not any(key in env for key in ("PCC_PYTEST_WORKERS", "PYTEST_ADDOPTS"))
        fault = state["fault"]
        if fault == "pytest_failure":
            raise RuntimeError("stub pytest failure")
        if fault == "pytest_interrupt":
            raise KeyboardInterrupt("stub interruption")
        for mode in ("pcc0", "pcc1"):
            directory = Path(out) / name / mode
            directory.mkdir(parents=True)
            source, expected, module_hashes = _write_native_find_and_str_len_suite(directory)
            assert expected == FIND_AND_STR_LEN_NATIVE_OUTPUT
            assert source.read_text(encoding="utf-8") == FIND_AND_STR_LEN_NATIVE_SOURCE
            assert tuple(module_hashes) == tuple(FIND_AND_STR_LEN_NATIVE_MODULES)
            for member_name, program in FIND_AND_STR_LEN_NATIVE_MODULES.items():
                member = directory / member_name
                assert member.read_bytes() == program.encode("utf-8")
                assert module_hashes[member_name] == sha(member)
            binary = directory / "program.out"
            binary.write_bytes(b"INERT HOST FIXTURE: NEVER EXECUTED\n" + mode.encode())
            row = {"returncode": 0, "stdout": expected, "stderr": ""}
            record = {
                "status": "PASS", "compiler_fixture_parameter": mode,
                "compiler_mode": mode, "backend": "self", "libpython": "off",
                "source_sha256": sha(source), "runtime_archive": str(state["archive"]),
                "runtime_sha256": sha(state["archive"]), "binary_sha256": sha(binary),
                "reference": {"command": ["HOST-STUB", str(source)], **row},
                "executions": [
                    {"gc_backend": gc, "command": [str(binary)], **row}
                    for gc in range(5)
                ],
            }
            if mode == "pcc1":
                if fault == "missing_receipt":
                    continue
                if fault == "duplicate_mode":
                    record["compiler_fixture_parameter"] = "pcc0"
                elif fault == "routed_mode":
                    record["compiler_mode"] = "pcc1-routed"
                elif fault == "not_pass":
                    record["status"] = "RUNNING"
                elif fault == "wrong_backend":
                    record["backend"] = "llvm"
                elif fault == "libpython":
                    record["libpython"] = "on"
                elif fault == "source_hash":
                    record["source_sha256"] = "0" * 64
                elif fault == "source_bytes":
                    source.write_text(FIND_AND_STR_LEN_NATIVE_SOURCE + "print('extra')\n")
                    record["source_sha256"] = sha(source)
                elif fault == "len_entry_missing":
                    source.write_text(NATIVE_SOURCE)
                    record["source_sha256"] = sha(source)
                elif fault == "runtime_path":
                    record["runtime_archive"] += ".other"
                elif fault == "runtime_hash":
                    record["runtime_sha256"] = "0" * 64
                elif fault == "binary_hash":
                    record["binary_sha256"] = "0" * 64
                elif fault == "reference_output":
                    record["reference"]["stdout"] = "BOUND_FIND_OK\n"
                elif fault == "len_reference_output":
                    record["reference"]["stdout"] = NATIVE_OUTPUT
                elif fault == "gc_missing":
                    record["executions"].pop()
                elif fault == "gc_duplicate":
                    record["executions"][-1]["gc_backend"] = 3
                elif fault == "native_command":
                    record["executions"][0]["command"] = ["HOST-STUB", str(source)]
                elif fault == "native_output":
                    record["executions"][0]["stdout"] = "BOUND_FIND_OK\n"
                elif fault == "len_native_output":
                    record["executions"][0]["stdout"] = NATIVE_OUTPUT
                elif fault == "native_returncode":
                    record["executions"][0]["returncode"] = 1
                elif fault == "module_bytes":
                    member = directory / next(iter(NATIVE_MODULES))
                    member.write_text("# replaced case\n")
                    module_hashes[member.name] = sha(member)
                    save(directory / "native-module-manifest.json", module_hashes)
                elif fault == "module_hash":
                    module_hashes[next(iter(module_hashes))] = "0" * 64
                    save(directory / "native-module-manifest.json", module_hashes)
                elif fault == "module_inventory":
                    module_hashes["unrequested_case.py"] = "0" * 64
                    save(directory / "native-module-manifest.json", module_hashes)
                elif fault == "len_module_missing":
                    (directory / "str_len_protocol.py").unlink()
                    del module_hashes["str_len_protocol.py"]
                    save(directory / "native-module-manifest.json", module_hashes)
                elif fault in ("len_module_bytes", "len_module_newlines"):
                    member = directory / "str_len_protocol.py"
                    if fault == "len_module_bytes":
                        member.write_text("print('STR_LEN_OK')\n")
                    else:
                        member.write_bytes(PROGRAM.replace("\n", "\r\n").encode("utf-8"))
                    module_hashes[member.name] = sha(member)
                    save(directory / "native-module-manifest.json", module_hashes)
            save(directory / "ownership-regression.json", record)
        if fault == "extra_receipt":
            save(Path(out) / name / "extra" / "ownership-regression.json", {})
        elif fault == "compiler_after":
            state["compiler"].write_bytes(b"changed compiler")
        elif fault == "runtime_after":
            state["archive"].write_bytes(b"changed runtime")
        elif fault == "test_input_after":
            (state["root"] / "pyproject.toml").write_text("changed input")
        elif fault == "len_test_input_after":
            (state["root"] / "tests/python/test_str_len_protocol.py").write_text("changed length input")
        state["after"] = True

    boundary = types.ModuleType("scripts.ci_worker_size_prior_gate")
    boundary.environment = lambda: {"HOST_STUB_ONLY": "1"}
    boundary.run_pytest = run_pytest
    boundary.runtime_identity = runtime_identity
    boundary.save = save
    boundary.sha = sha
    boundary.source_identity = source_identity
    monkeypatch.setitem(sys.modules, boundary.__name__, boundary)
    spec = importlib.util.spec_from_file_location(
        "_native_find_ci_contract", ROOT / "scripts/ci_native_find_protocol.py",
    )
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    assert gate.TIMEOUT_S == 300 and gate.MODES == ("pcc0", "pcc1")
    assert tuple(PROGRAMS) == gate.CASES
    assert tuple(NATIVE_MODULES.values()) == tuple(PROGRAMS.values())
    assert tuple(NATIVE_MODULES) == tuple("bound_find_" + case + ".py" for case in gate.CASES)
    assert NATIVE_SOURCE == "".join("import " + name[:-3] + "\n" for name in NATIVE_MODULES)
    assert NATIVE_OUTPUT == "BOUND_FIND_OK\n" * 6
    assert tuple(FIND_AND_STR_LEN_NATIVE_MODULES) == (*NATIVE_MODULES, "str_len_protocol.py")
    assert tuple(FIND_AND_STR_LEN_NATIVE_MODULES.values()) == (*PROGRAMS.values(), PROGRAM)
    assert FIND_AND_STR_LEN_NATIVE_SOURCE == NATIVE_SOURCE + "import str_len_protocol\n"
    assert EXPECTED == "STR_LEN_OK\n"
    assert FIND_AND_STR_LEN_NATIVE_OUTPUT == "BOUND_FIND_OK\n" * 6 + EXPECTED
    assert len(gate.NODES) == 2
    assert "tests/python/test_str_len_protocol.py" in gate.TEST_INPUTS
    assert "tests/python/owned_regression_support.py" in gate.TEST_INPUTS
    assert "tests/python/process_timeout.py" in gate.TEST_INPUTS
    # Inspect the real source without invoking pytest or a native fixture.
    # A full integration selection must not collect the old six-module and
    # standalone length wrappers in addition to the shared seven-module pair.
    native_nodes = []
    for test_file in (gate.TEST, "tests/python/test_str_len_protocol.py"):
        tree = ast.parse((ROOT / test_file).read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not function.name.startswith("test_"):
                continue
            marks = [ast.unparse(mark) for mark in function.decorator_list]
            has_compiler = any(arg.arg == "python_program_compiler" for arg in function.args.args)
            if "pytest.mark.integration" not in marks and not has_compiler:
                continue
            assert function.name == "test_bound_find_and_str_len_owned_native_five_gc"
            assert "pytest.mark.integration" in marks and has_compiler
            parameters = [
                mark for mark in function.decorator_list
                if isinstance(mark, ast.Call)
                and ast.unparse(mark.func) == "pytest.mark.parametrize"
            ]
            assert len(parameters) == 1
            parameter = parameters[0]
            assert ast.literal_eval(parameter.args[0]) == "python_program_compiler"
            assert ast.literal_eval(parameter.args[1]) == ("pcc0", "pcc1")
            assert len(parameter.keywords) == 1
            assert parameter.keywords[0].arg == "indirect"
            assert ast.literal_eval(parameter.keywords[0].value) is True
            native_nodes.extend(
                test_file + "::" + function.name + "[" + mode + "]"
                for mode in ast.literal_eval(parameter.args[1])
            )
    assert tuple(native_nodes) == gate.NODES
    monkeypatch.setenv("GITHUB_SHA", "verified-source")

    preflight_faults = (
        "missing_stage", "stage_failed", "wrong_phase", "dirty_source",
        "different_commit", "stage_source", "compiler_missing", "compiler_relative",
        "compiler_hash", "archive_missing", "runtime_identity",
        "unthreaded_runtime", "nonatomic_runtime",
    )
    receipt_faults = (
        "pytest_failure", "pytest_interrupt", "missing_receipt", "extra_receipt",
        "duplicate_mode", "routed_mode", "not_pass", "wrong_backend", "libpython",
        "source_hash", "source_bytes", "runtime_path", "runtime_hash", "binary_hash",
        "reference_output", "gc_missing", "gc_duplicate", "native_command",
        "native_output", "native_returncode", "module_bytes", "module_hash",
        "module_inventory", "source_after", "compiler_after", "runtime_after",
        "test_input_after", "len_entry_missing", "len_reference_output",
        "len_native_output", "len_module_missing", "len_module_bytes",
        "len_module_newlines", "len_test_input_after",
    )
    for fault in ("none",) + preflight_faults + receipt_faults:
        directory = tmp_path / fault
        directory.mkdir()
        source_root = directory / "input"
        source_root.mkdir()
        for name in gate.TEST_INPUTS:
            target = source_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("frozen input: " + name)
        compiler, archive = directory / "pcc1", directory / "runtime.a"
        compiler.write_bytes(b"INERT STAGE1 COMPILER")
        archive.write_bytes(b"INERT RUNTIME ARCHIVE")
        state.clear()
        state.update(
            fault=fault, root=source_root, compiler=compiler, archive=archive,
            source={"clean_commit": True, "commit": "verified-source"}, after=False,
        )
        stage = {
            "phase": "stage1", "status": "PASS", "source": dict(state["source"]),
            "compiler": {"path": str(compiler), "sha256": sha(compiler)},
            "runtime": runtime_identity(archive),
        }
        if fault == "stage_failed":
            stage["status"] = "FAIL"
        elif fault == "wrong_phase":
            stage["phase"] = "pcc1"
        elif fault == "dirty_source":
            state["source"]["clean_commit"] = False
        elif fault == "different_commit":
            state["source"]["commit"] = "not-github-sha"
        elif fault == "stage_source":
            stage["source"]["commit"] = "stale-stage"
        elif fault == "compiler_missing":
            stage["compiler"]["path"] += ".missing"
        elif fault == "compiler_relative":
            stage["compiler"]["path"] = "relative-pcc1"
        elif fault == "compiler_hash":
            stage["compiler"]["sha256"] = "0" * 64
        elif fault == "archive_missing":
            stage["runtime"]["path"] += ".missing"
        elif fault == "runtime_identity":
            stage["runtime"]["hashes"][""] = "0" * 64
        out = directory / "out"
        out.mkdir()
        if fault != "missing_stage":
            save(out / "stage1.json", stage)
        monkeypatch.setattr(gate, "ROOT", source_root)
        calls_before = len(invocations)
        if fault == "none":
            result = gate.run(out)
            assert result["status"] == "PASS"
            assert len(result["programs"]) == 2
            assert all(len(record["executions"]) == 5 for record in result["programs"])
            assert all(
                record["cases"] == [*gate.CASES, "str_len_protocol"]
                and set(record["module_sha256"]) == set(FIND_AND_STR_LEN_NATIVE_MODULES)
                for record in result["programs"]
            )
            assert result["qualifies_fixed_point"] is False
            assert result["observed_collector_witness"] is False
        else:
            with pytest.raises((AssertionError, FileNotFoundError, RuntimeError, KeyboardInterrupt)):
                gate.run(out)
        record_path = out / "native-find-protocol.json"
        record = json.loads(record_path.read_text())
        assert record["status"] == ("PASS" if fault == "none" else "FAIL"), fault
        if fault != "none":
            assert record["error"], fault
        assert len(invocations) - calls_before == (0 if fault in preflight_faults else 1), fault
        if fault == "none":
            before = record_path.read_bytes()
            with pytest.raises(AssertionError, match="stale"):
                gate.run(out)
            assert record_path.read_bytes() == before

    workflow = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    steps = list(re.finditer(
        r"(?ms)^      - name: Verify bound find with the existing pcc0 and pcc1 runtime\n"
        r"(?P<body>.*?)(?=^      - name:|\Z)", workflow,
    ))
    assert len(steps) == 2
    conditions = ("steps.platform_qualification.outcome", "steps.mac_stage1.outcome")
    for step, condition in zip(steps, conditions):
        body = step.group("body")
        assert condition + " == 'success'" in body and "!cancelled()" in body
        assert "timeout-minutes: 6" in body
        assert "scripts/ci_native_find_protocol.py --out-dir build/worker-prior-gate" in body
        assert "PCC_TEST_NO_NATIVE_PROVISIONING: \"1\"" in body
        assert "PCC_NO_AUTO_PCC1: \"1\"" in body
        assert "UV_PYTHON_DOWNLOADS: \"never\"" in body
        prior = workflow.rfind("--out-dir build/worker-prior-gate --phase pcc1", 0, step.start())
        assert prior >= 0 and prior < step.start()
        assert "continue-on-error" not in body
    assert re.search(r"(?s)  other-platform-native-wheel:.*?timeout-minutes: 360", workflow)
    assert re.search(r"(?s)  pcc1-package-parity:.*?timeout-minutes: 200", workflow)
