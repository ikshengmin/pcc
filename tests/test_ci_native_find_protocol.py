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
import os
from pathlib import Path
import re
import subprocess
import struct
import sys
import types

import pytest

from scripts.owned_image_linkage import inspect_owned_image


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
    from tests.python.test_exact_int_container_bitsets import PROGRAM as BITSETS_PROGRAM

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
            "tests/python/test_exact_int_container_bitsets.py::test_container_bitsets_owned_native_five_gc[pcc0]",
            "tests/python/test_exact_int_container_bitsets.py::test_container_bitsets_owned_native_five_gc[pcc1]",
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
        for case, mode in ((case, mode) for case in ("find", "container_bitsets")
                           for mode in ("pcc0", "pcc1")):
            case_fault = fault if case == state["fault_case"] else "none"
            directory = Path(out) / name / case / mode
            directory.mkdir(parents=True)
            program = FIND_AND_STR_LEN_NATIVE_SOURCE if case == "find" else BITSETS_PROGRAM
            if case == "find":
                source, expected, module_hashes = _write_native_find_and_str_len_suite(directory)
                assert expected == FIND_AND_STR_LEN_NATIVE_OUTPUT
                assert source.read_text(encoding="utf-8") == FIND_AND_STR_LEN_NATIVE_SOURCE
                assert tuple(module_hashes) == tuple(FIND_AND_STR_LEN_NATIVE_MODULES)
                for member_name, member_program in FIND_AND_STR_LEN_NATIVE_MODULES.items():
                    member = directory / member_name
                    assert member.read_bytes() == member_program.encode("utf-8")
                    assert module_hashes[member_name] == sha(member)
            else:
                source = directory / "program.py"
                source.write_text(program, encoding="utf-8")
                expected = "CONTAINER_BITSETS_OK\n"
                module_hashes = {}
            binary = directory / "program.out"
            # Exercise the actual producer, not a handwritten success schema.
            # Only the compiler/process boundaries are inert; the emitted-image
            # checker and consumer both inspect the same header fixture bytes.
            from tests.python import owned_regression_support as producer

            def compile_fixture(source_path, output_path, **options):
                assert options == {
                    "backend": "self", "ir_scaffold_mode": "on",
                    "runtime_archive": str(state["archive"]),
                }
                payload = static_elf()
                if mode == "pcc1" and case_fault == "host_output":
                    payload = b"#!/usr/bin/python\n"
                if mode == "pcc1" and case_fault == "python_linkage":
                    payload = macho(("/Library/Frameworks/Python.framework/Python",))
                Path(output_path).write_bytes(payload)

            if mode == "pcc1" and case_fault == "routed_producer":
                compile_fixture.__module__ = "tests.pcc1_route"

            def execute_fixture(directory, label, command, environment):
                return {"command": list(command), "returncode": 0,
                        "stdout": expected, "stderr": ""}

            with monkeypatch.context() as process_patch:
                process_patch.setattr(producer, "_record_execution", execute_fixture)
                producer.assert_owned_program(
                    program, expected, directory,
                    compile_fixture, mode, state["archive"],
                    types.SimpleNamespace(readouterr=lambda: types.SimpleNamespace(out="", err="")),
                )
            record = json.loads((directory / "ownership-regression.json").read_text())
            if mode == "pcc1":
                if case_fault == "missing_receipt":
                    (directory / "ownership-regression.json").unlink()
                    continue
                if case_fault == "duplicate_mode":
                    record["compiler_fixture_parameter"] = "pcc0"
                elif case_fault == "unknown_mode":
                    record["compiler_fixture_parameter"] = "unknown"
                elif case_fault == "wrong_case":
                    _write_native_find_and_str_len_suite(directory)
                    record["source_sha256"] = sha(source)
                    record["reference"]["stdout"] = FIND_AND_STR_LEN_NATIVE_OUTPUT
                    for row in record["executions"]:
                        row["stdout"] = FIND_AND_STR_LEN_NATIVE_OUTPUT
                elif case_fault == "routed_mode":
                    record["compiler_mode"] = "pcc1-routed"
                elif case_fault == "not_pass":
                    record["status"] = "RUNNING"
                elif case_fault == "wrong_backend":
                    record["backend"] = "llvm"
                elif case_fault == "linkage_hash":
                    record["linkage"]["binary_sha256"] = "0" * 64
                elif case_fault == "libpython_missing":
                    del record["libpython"]
                elif case_fault == "linkage_missing":
                    del record["linkage"]
                elif case_fault == "libpython":
                    record["libpython"] = "on"
                elif case_fault == "source_hash":
                    record["source_sha256"] = "0" * 64
                elif case_fault == "source_bytes":
                    source.write_text(program + "print('extra')\n")
                    record["source_sha256"] = sha(source)
                elif case_fault == "source_newlines":
                    newline = b"\n" if os.linesep == "\r\n" else b"\r\n"
                    source.write_bytes(program.encode("utf-8").replace(b"\n", newline))
                    record["source_sha256"] = sha(source)
                elif case_fault == "len_entry_missing":
                    source.write_text(NATIVE_SOURCE)
                    record["source_sha256"] = sha(source)
                elif case_fault == "runtime_path":
                    record["runtime_archive"] += ".other"
                elif case_fault == "runtime_hash":
                    record["runtime_sha256"] = "0" * 64
                elif case_fault == "binary_hash":
                    record["binary_sha256"] = "0" * 64
                elif case_fault == "reference_output":
                    record["reference"]["stdout"] = "BOUND_FIND_OK\n"
                elif case_fault == "len_reference_output":
                    record["reference"]["stdout"] = NATIVE_OUTPUT
                elif case_fault == "gc_missing":
                    record["executions"].pop()
                elif case_fault == "gc_duplicate":
                    record["executions"][-1]["gc_backend"] = 3
                elif case_fault == "native_command":
                    record["executions"][0]["command"] = ["HOST-STUB", str(source)]
                elif case_fault == "native_output":
                    record["executions"][0]["stdout"] = "BOUND_FIND_OK\n"
                elif case_fault == "len_native_output":
                    record["executions"][0]["stdout"] = NATIVE_OUTPUT
                elif case_fault == "native_returncode":
                    record["executions"][0]["returncode"] = 1
                elif case_fault == "module_bytes":
                    member = directory / next(iter(NATIVE_MODULES))
                    member.write_text("# replaced case\n")
                    module_hashes[member.name] = sha(member)
                    save(directory / "native-module-manifest.json", module_hashes)
                elif case_fault == "module_hash":
                    module_hashes[next(iter(module_hashes))] = "0" * 64
                    save(directory / "native-module-manifest.json", module_hashes)
                elif case_fault == "module_inventory":
                    module_hashes["unrequested_case.py"] = "0" * 64
                    save(directory / "native-module-manifest.json", module_hashes)
                elif case_fault == "len_module_missing":
                    (directory / "str_len_protocol.py").unlink()
                    del module_hashes["str_len_protocol.py"]
                    save(directory / "native-module-manifest.json", module_hashes)
                elif case_fault in ("len_module_bytes", "len_module_newlines"):
                    member = directory / "str_len_protocol.py"
                    if case_fault == "len_module_bytes":
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
        elif fault == "bitsets_test_input_after":
            (state["root"] / "tests/python/test_exact_int_container_bitsets.py").write_text("changed bitsets input")
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
    assert len(gate.NODES) == 4
    assert gate.PROGRAM_CASES == ("find", "container_bitsets")
    assert gate.BITSETS_TEST in gate.TEST_INPUTS
    assert "tests/python/test_str_len_protocol.py" in gate.TEST_INPUTS
    assert "tests/python/owned_regression_support.py" in gate.TEST_INPUTS
    assert "tests/python/process_timeout.py" in gate.TEST_INPUTS
    # Inspect the real source without invoking pytest or a native fixture.
    # A full integration selection must not collect the old six-module and
    # standalone length wrappers in addition to the shared seven-module pair.
    native_nodes = []
    for test_file in (gate.TEST, "tests/python/test_str_len_protocol.py", gate.BITSETS_TEST):
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
            expected_function = ("test_container_bitsets_owned_native_five_gc"
                                 if test_file == gate.BITSETS_TEST
                                 else "test_bound_find_and_str_len_owned_native_five_gc")
            assert function.name == expected_function
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
    bitsets_tree = ast.parse((ROOT / gate.BITSETS_TEST).read_text(encoding="utf-8"))
    ir_tests = [node for node in bitsets_tree.body if isinstance(node, ast.FunctionDef)
                and node.name == "test_container_bitsets_scaffold_ir_keeps_object_operands"]
    assert len(ir_tests) == 1 and not ir_tests[0].decorator_list
    assert not any("scaffold_ir" in node for node in gate.NODES)
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
        "routed_producer", "host_output", "python_linkage", "linkage_hash",
        "linkage_missing", "libpython_missing",
        "source_hash", "source_bytes", "runtime_path", "runtime_hash", "binary_hash",
        "reference_output", "gc_missing", "gc_duplicate", "native_command",
        "native_output", "native_returncode", "module_bytes", "module_hash",
        "module_inventory", "source_after", "compiler_after", "runtime_after",
        "test_input_after", "len_entry_missing", "len_reference_output",
        "len_native_output", "len_module_missing", "len_module_bytes",
        "len_module_newlines", "len_test_input_after",
    )
    bitset_faults = (
        "missing_receipt", "duplicate_mode", "unknown_mode", "wrong_case",
        "routed_mode", "routed_producer", "not_pass", "wrong_backend",
        "host_output", "python_linkage", "linkage_hash", "linkage_missing",
        "libpython", "libpython_missing", "source_hash", "source_bytes", "source_newlines",
        "runtime_path", "runtime_hash", "binary_hash", "reference_output",
        "gc_missing", "gc_duplicate", "native_command", "native_output",
        "native_returncode",
    )
    scenarios = [(fault, "find") for fault in ("none",) + preflight_faults + receipt_faults]
    scenarios += [(fault, "container_bitsets") for fault in bitset_faults]
    scenarios.append(("bitsets_test_input_after", "container_bitsets"))
    for fault, fault_case in scenarios:
        directory = tmp_path / (fault_case + "_" + fault)
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
            fault=fault, fault_case=fault_case, root=source_root, compiler=compiler, archive=archive,
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
            assert len(result["programs"]) == 4
            assert {(row["program_case"], row["mode"]) for row in result["programs"]} == {
                (case, mode) for case in gate.PROGRAM_CASES for mode in gate.MODES
            }
            assert all(len(record["executions"]) == 5 for record in result["programs"])
            assert all(
                record["cases"] == [*gate.CASES, "str_len_protocol"]
                and set(record["module_sha256"]) == set(FIND_AND_STR_LEN_NATIVE_MODULES)
                for record in result["programs"] if record["program_case"] == "find"
            )
            assert all(
                row["cases"] == ["container_bitsets"] and row["module_sha256"] == {}
                for row in result["programs"] if row["program_case"] == "container_bitsets"
            )
            assert result["qualifies_fixed_point"] is False
            assert result["observed_collector_witness"] is False
        else:
            with pytest.raises((AssertionError, FileNotFoundError, RuntimeError, KeyboardInterrupt, ValueError, KeyError)):
                gate.run(out)
        record_path = out / "native-find-protocol.json"
        record = json.loads(record_path.read_text())
        assert record["status"] == ("PASS" if fault == "none" else "FAIL"), fault
        if fault != "none":
            assert record["error"], fault
        if fault in ("host_output", "python_linkage"):
            failed = json.loads((out / gate.NAME / fault_case / "pcc1" / "ownership-regression.json").read_text())
            assert failed["status"] == "LINKAGE_FAILED"
            assert failed["executions"] == []
            assert "libpython" not in failed and "linkage" not in failed
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


# Header-only fixtures below are never runnable native acceptance evidence.


def static_elf(machine=62, program_type=1):
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    return (struct.pack("<16sHHIQQQIHHHHHH", ident, 2, machine, 1,
                        0x400078, 64, 0, 0, 64, 56, 1, 0, 0, 0)
            + struct.pack("<IIQQQQQQ", program_type, 5, 0, 0x400000,
                          0x400000, 120, 120, 4096))


def macho(libraries=(), command=0xC):
    commands = []
    for library in libraries:
        name = library.encode() + b"\0"
        size = (24 + len(name) + 7) & ~7
        commands.append(struct.pack("<IIIIII", command, size, 24, 0, 0, 0)
                        + name + bytes(size - 24 - len(name)))
    body = b"".join(commands)
    return struct.pack("<IIIIIIII", 0xFEEDFACF, 0x100000C, 0, 2,
                       len(commands), len(body), 0, 0) + body


@pytest.mark.parametrize("machine", (62, 183))
def test_static_elf_needs_no_nonempty_library_inventory(machine):
    assert inspect_owned_image(static_elf(machine))["libraries"] == []


@pytest.mark.parametrize("command", (0xC, 0x80000018, 0x8000001F, 0x20, 0x80000023))
def test_all_macho_dependency_commands_are_checked(command):
    assert inspect_owned_image(macho(("/usr/lib/libSystem.B.dylib",), command))["libpython"] == "off"
    for library in ("libpython3.15.dylib", "/Library/Frameworks/Python.framework/Python"):
        with pytest.raises(ValueError, match="Python runtime"):
            inspect_owned_image(macho((library,), command))


def test_pe_import_inventory_is_checked():
    for libraries in ((), ("KERNEL32.dll",)):
        assert inspect_owned_image(_pe_import_table(libraries))["libpython"] == "off"
    for library in ("python3.dll", "python315.dll", "libpython.dll"):
        with pytest.raises(ValueError, match="Python runtime"):
            inspect_owned_image(_pe_import_table((library,)))
    payload = bytearray(_pe_import_table(("KERNEL32.dll",)))
    struct.pack_into("<II", payload, 128 + 24 + 112 + 13 * 8, 4096, 32)
    with pytest.raises(ValueError, match="delay imports"):
        inspect_owned_image(bytes(payload))


@pytest.mark.parametrize("payload", (
    b"#!/usr/bin/python\n", b"", b"MZ", b"\x7fELF", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe" + bytes(32),
    static_elf(program_type=2), static_elf(program_type=3),
    macho(("libSystem.B.dylib",))[:-1],
))
def test_uninspectable_images_never_become_empty_success(payload):
    with pytest.raises(Exception):
        inspect_owned_image(payload)


def _pe_import_table(libraries):
    pe = 128
    optional = pe + 24
    section = optional + 240
    raw = 512
    rva = 4096
    names = [name.encode("ascii") + b"\0" for name in libraries]
    descriptors_size = 20 * (len(names) + 1)
    payload_size = descriptors_size + sum(map(len, names))
    data = bytearray(raw + payload_size)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, pe)
    data[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<HH", data, pe + 4, 0x8664, 1)
    struct.pack_into("<H", data, pe + 20, 240)
    struct.pack_into("<H", data, optional, 0x20B)
    struct.pack_into("<I", data, optional + 108, 16)
    struct.pack_into("<II", data, optional + 120,
                     rva if names else 0, descriptors_size if names else 0)
    data[section:section + 8] = b".idata\0\0"
    struct.pack_into("<IIII", data, section + 8, payload_size, rva, payload_size, raw)
    cursor = descriptors_size
    for index, name in enumerate(names):
        struct.pack_into("<IIIII", data, raw + index * 20, 0, 0, 0, rva + cursor, 0)
        data[raw + cursor:raw + cursor + len(name)] = name
        cursor += len(name)
    return bytes(data)



@pytest.fixture(autouse=True)
def forbid_linkage_contract_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("linkage contracts may not execute native tools or processes")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.mark.parametrize("short_first", (True, False))
def test_overlapping_pe_section_cannot_hide_python_dependency(short_first):
    # The full table contains python3.dll at RVA 4096, followed by its null
    # descriptor. A second mapping covers only the first 20 bytes at that
    # RVA and points at zeros. A whole-table check followed by a different
    # first-match RVA reader previously certified this as an empty inventory.
    payload = bytearray(_pe_import_table(("python3.dll",)))
    section = 128 + 24 + 240
    long_section = bytes(payload[section:section + 40])
    zero_at = len(payload)
    payload.extend(bytes(20))
    short_section = bytearray(40)
    short_section[:8] = b".alias\0\0"
    struct.pack_into("<IIII", short_section, 8, 20, 4096, 20, zero_at)
    headers = (bytes(short_section), long_section) if short_first else (long_section, bytes(short_section))
    payload[section:section + 80] = b"".join(headers)
    struct.pack_into("<H", payload, 128 + 6, 2)
    assert payload[zero_at:zero_at + 20] == bytes(20)
    assert payload[552:563] == b"python3.dll"
    with pytest.raises(ValueError, match="overlapping PE section RVA mappings"):
        inspect_owned_image(bytes(payload))
