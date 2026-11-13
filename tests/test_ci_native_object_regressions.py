"""Host-only controls for the post-Stage1 native object gate; no native build."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts import ci_native_object_regressions as gate
from scripts import ci_worker_size_prior_gate as prior
from tests.python import owned_regression_support as support


ROOT = Path(__file__).resolve().parents[1]


RUN_ID = "host-control-run"


def _receipt_label(path):
    return {"gc0": "two-root-cleanup", "tracing": "two-root-tracing"}.get(
        path.parent.name, path.parent.name,
    )


def _slot_receipt(directory, runtime):
    from tests.python.two_root_lifecycle_support import SLOT_PROBE_PROGRAM, cleanup_slots_ir

    directory.mkdir(parents=True)
    source, binary = directory / "slots.py", directory / "slots.out"
    source.write_text(SLOT_PROBE_PROGRAM, encoding="utf-8")
    binary.write_bytes(b"host-only slot binary; never executed")
    ir_text = cleanup_slots_ir(runtime["target"])
    probe_ir, helper = directory / "slots.ll", directory / "slots.o"
    probe_ir.write_text(ir_text, encoding="utf-8", newline="\n")
    helper.write_bytes(gate.expected_slot_object(ir_text, runtime["target"]))
    record = {
        "schema": "pcc.two-root-slots.v1", "status": "PASS", "run_id": RUN_ID,
        "compiler_fixture_parameter": "pcc0", "compiler_mode": "pcc0", "backend": "self",
        "target": runtime["target"], "source_sha256": gate.sha(source),
        "runtime_archive": runtime["path"], "runtime_sha256": runtime["hashes"][""],
        "binary_path": str(binary), "binary_sha256": gate.sha(binary),
        "probe_ir_sha256": gate.sha(probe_ir), "probe_object_sha256": gate.sha(helper),
        "probe_object_producer": "host-pcc-owned-object-emitter",
        "linkage": {"schema": "pcc.owned-image-linkage.v1", "libpython": "off",
                    "binary_sha256": gate.sha(binary)},
        "gc_backends": list(range(5)),
        "executions": [{"command": [str(binary)], "gc_backend": gc, "returncode": 0,
                        "stdout": "TWO_ROOT_SLOTS_OK\n", "stderr": ""} for gc in range(5)],
    }
    path = directory / "slot-postcondition.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def _receipts(directory, runtime):
    from tests.python.test_two_root_lifecycle_support import EDGE_IR

    paths = []
    for label, (program, output) in gate.expected_programs().items():
        split = label in ("two-root-cleanup", "two-root-tracing")
        work = (directory / "two-root" / ("gc0" if label == "two-root-cleanup" else "tracing")
                if split else directory / label)
        work.mkdir(parents=True)
        source = work / "program.py"
        source.write_text(program, encoding="utf-8")
        binary = work / "program.out"
        binary.write_bytes(("host-only receipt control; never executed: " + label).encode())
        arguments = ["cyclic-reference"] if label == "two-root-tracing" else []
        oracle = {"command": [sys.executable, str(source), *arguments],
                  "returncode": 0, "stdout": output, "stderr": ""}
        backends = gate.expected_backends(label)
        record = {
            "status": "PASS", "run_id": RUN_ID,
            "compiler_fixture_parameter": "pcc0", "compiler_mode": "pcc0",
            "backend": "self", "source_sha256": gate.sha(source),
            "runtime_archive": runtime["path"], "runtime_sha256": runtime["hashes"][""],
            "binary_path": str(binary), "binary_sha256": gate.sha(binary), "reference": oracle,
            "gc_backends": backends,
            "executions": [{**oracle, "command": [str(binary)], "gc_backend": gc} for gc in backends],
        }
        if split:
            dump = work / "executable-ir"
            dump.mkdir()
            module = dump / "self_backend_input_0.ll"
            escaped = "".join("\\" + format(byte, "02X") for byte in output.encode())
            module.write_text(EDGE_IR + '@marker = constant [' + str(len(output))
                              + ' x i8] c"' + escaped + '"\n')
            record["executable_input_ir"] = {module.name: gate.sha(module)}
        path = work / "ownership-regression.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        paths.append(path)
    _slot_receipt(directory / "two-root" / "slots", runtime)
    return paths


@pytest.fixture
def admitted(tmp_path, monkeypatch):
    archive = tmp_path / "libpy_runtime_pcc_py.a"
    archive.write_bytes(b"host-only runtime placeholder")
    source = {"commit": "test-commit", "clean_commit": True, "source_sha256": "test-source",
              "python": {"executable": "host-python", "sha256": "python-hash", "version": "3.13"},
              "test_versions": {"pytest": "9.0.3", "xdist": "3.8.0"}}
    runtime = {"path": str(archive), "config": {"threads": False, "refcount": "atomic"},
               "target": "host-control", "hashes": {"": gate.sha(archive)}}
    (tmp_path / "stage1.json").write_text(json.dumps({
        "phase": "stage1", "status": "FAIL", "source": source, "runtime": runtime,
    }), encoding="utf-8")
    (tmp_path / "input.py").write_text("host-only source input", encoding="utf-8")
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "TEST_INPUTS", ("input.py",))
    monkeypatch.setenv("GITHUB_SHA", source["commit"])
    monkeypatch.setattr(gate, "source_identity", lambda: copy.deepcopy(source))
    monkeypatch.setattr(gate, "runtime_identity", lambda path: copy.deepcopy(runtime))
    monkeypatch.setattr(gate, "environment", lambda: {"PCC_RUNTIME_ARCHIVE": str(archive)})
    monkeypatch.setattr(gate, "verify_native_image", lambda path: {"format": "host-only control"})
    # Common-program controls remain platform-independent; the Windows
    # extension has its own selection and receipt controls below.
    monkeypatch.setattr(gate, "selected_nodes", lambda: gate.NODES)
    from tests.python import test_owned_cleanup_helper_ir as stackmaps
    monkeypatch.setattr(stackmaps, "verify_stackmaps", lambda text: None)
    monkeypatch.setattr(gate.uuid, "uuid4", lambda: SimpleNamespace(hex=RUN_ID))
    monkeypatch.setattr(gate, "expected_slot_object", lambda ir, target: b"host-only object:" + ir.encode())
    return source, runtime


@pytest.mark.parametrize("stage_status", ("PASS", "FAIL"))
def test_gate_reuses_terminal_runtime_and_records_split_lifetimes_and_thirty_executions(tmp_path, monkeypatch, admitted, stage_status):
    source, runtime = admitted
    path = tmp_path / "stage1.json"
    stage = json.loads(path.read_text())
    stage["status"] = stage_status
    path.write_text(json.dumps(stage))
    calls = []

    def run(out, name, nodes, env, **kwargs):
        calls.append((out, name, nodes, env, kwargs))
        _receipts(out / name, runtime)

    monkeypatch.setattr(gate, "run_pytest", run)
    result = gate.run(tmp_path)
    assert len(calls) == 1
    out, name, nodes, env, options = calls[0]
    assert (out, name, nodes) == (tmp_path, gate.NAME, gate.NODES)
    assert options == {"integration": True, "timeout": 300}
    assert env["PCC_RUNTIME_ARCHIVE"] == runtime["path"]
    assert env["PCC_TEST_NO_NATIVE_PROVISIONING"] == "1"
    assert env["UV_PYTHON_DOWNLOADS"] == "never"
    assert result["status"] == "PASS" and result["qualifies_stage1"] is False
    assert result["observed_collector_witness"] is False
    assert result["collector_evidence"] == (
        "requested_gc_backend records are PCC_GC_BACKEND requests; "
        "collector selection is not independently observed"
    )
    assert result["source"] == result["source_after"] == source
    assert result["runtime"] == result["runtime_after"] == runtime
    assert result["stage1_status"] == stage_status
    assert set(result["programs"]) == {
        "object-identity", "super-result", "dict-copy-result", "dict-pop-result", "two-root-cleanup",
        "two-root-tracing", "two-root-slots",
    }
    assert sum(len(row["executions"]) for row in result["programs"].values()) == 30
    for label, row in result["programs"].items():
        assert [item["requested_gc_backend"] for item in row["executions"]] == gate.expected_backends(label)
        assert all("gc_backend" not in item for item in row["executions"])
    assert json.loads((tmp_path / (gate.NAME + ".json")).read_text()) == result


def test_gate_isolates_stage1_timing_without_filtering_compiler_errors(
    tmp_path, monkeypatch, admitted,
):
    _source, runtime = admitted
    inherited = {"PCC_RUNTIME_ARCHIVE": runtime["path"],
                 "PCC_PY_FRONTEND_WORKER_TIMING": "1",
                 "PCC_DEBUG_WORKER_RERAISE": "1"}
    monkeypatch.setattr(gate, "environment", lambda: inherited)

    def run(out, name, nodes, env, **kwargs):
        assert "PCC_PY_FRONTEND_WORKER_TIMING" not in env
        assert env["PCC_DEBUG_WORKER_RERAISE"] == "1"
        raise RuntimeError("real compiler failure")

    monkeypatch.setattr(gate, "run_pytest", run)
    with pytest.raises(RuntimeError, match="real compiler failure"):
        gate.run(tmp_path)
    assert inherited["PCC_PY_FRONTEND_WORKER_TIMING"] == "1"
    result = json.loads((tmp_path / (gate.NAME + ".json")).read_text())
    assert result["status"] == "FAIL"
    assert "real compiler failure" in result["error"]


@pytest.mark.parametrize("change", (
    "no-admission", "missing-archive", "missing-stage", "running-stage", "wrong-stage",
    "wrong-commit", "dirty-source", "stale-source", "wrong-host-python", "wrong-test-tools",
    "wrong-runtime",
))
def test_missing_or_stale_admission_fails_before_any_test(tmp_path, monkeypatch, admitted, change):
    source, runtime = admitted
    stage_path = tmp_path / "stage1.json"
    stage = json.loads(stage_path.read_text())
    if change == "no-admission":
        monkeypatch.setattr(gate, "environment", lambda: {})
    elif change == "missing-archive":
        Path(runtime["path"]).unlink()
    elif change == "missing-stage":
        stage_path.unlink()
    elif change == "running-stage":
        stage["status"] = "RUNNING"
    elif change == "wrong-stage":
        stage["phase"] = "pcc1"
    elif change == "wrong-commit":
        monkeypatch.setenv("GITHUB_SHA", "another-commit")
    elif change == "dirty-source":
        source["clean_commit"] = False
    elif change == "stale-source":
        stage["source"]["source_sha256"] = "stale"
    elif change == "wrong-host-python":
        stage["source"]["python"]["sha256"] = "another-python"
    elif change == "wrong-test-tools":
        stage["source"]["test_versions"]["pytest"] = "another-pytest"
    else:
        stage["runtime"]["hashes"][""] = "stale"
    if stage_path.exists():
        stage_path.write_text(json.dumps(stage))
    monkeypatch.setattr(gate, "run_pytest", lambda *args, **kwargs: pytest.fail("unadmitted native test"))
    with pytest.raises((AssertionError, FileNotFoundError)):
        gate.run(tmp_path)
    result = json.loads((tmp_path / (gate.NAME + ".json")).read_text())
    assert result["status"] == "FAIL" and result["qualifies_stage1"] is False


@pytest.mark.parametrize("change", ("source", "runtime", "test-input", "pytest-failure"))
def test_run_failure_or_identity_drift_is_never_a_pass(tmp_path, monkeypatch, admitted, change):
    source, runtime = admitted

    def run(out, name, *args, **kwargs):
        _receipts(out / name, runtime)
        if change == "source":
            source["source_sha256"] = "changed"
        elif change == "runtime":
            runtime["hashes"][""] = "changed"
        elif change == "test-input":
            (tmp_path / "input.py").write_text("changed")
        else:
            raise RuntimeError("native execution failed or timed out")

    monkeypatch.setattr(gate, "run_pytest", run)
    with pytest.raises((AssertionError, RuntimeError)):
        gate.run(tmp_path)
    result = json.loads((tmp_path / (gate.NAME + ".json")).read_text())
    assert result["status"] == "FAIL"
    assert "source_after" in result and "runtime_after" in result


@pytest.mark.parametrize("change", (
    "missing-receipt", "failed-receipt", "pcc1", "routed-pcc1", "llvm",
    "source-digest", "runtime-path", "runtime-digest", "binary-digest", "oracle",
    "missing-gc", "duplicate-gc", "failed-gc", "wrong-output", "stderr", "wrong-command",
    "foreign-program", "duplicate-program",
))
@pytest.mark.parametrize("label", (
    "object-identity", "super-result", "dict-copy-result", "dict-pop-result",
    "two-root-cleanup", "two-root-tracing",
))
def test_native_evidence_requires_exact_source_and_requested_collectors(
    tmp_path, monkeypatch, admitted, change, label,
):
    _source, runtime = admitted
    directory = tmp_path / "receipts"
    paths = _receipts(directory, runtime)
    path = next(path for path in paths if _receipt_label(path) == label)
    record = json.loads(path.read_text())
    fields = {"failed-receipt": ("status", "FAIL"), "pcc1": ("compiler_fixture_parameter", "pcc1"),
              "routed-pcc1": ("compiler_mode", "pcc1-routed"), "llvm": ("backend", "llvm"),
              "source-digest": ("source_sha256", "wrong"),
              "runtime-path": ("runtime_archive", "another-runtime"),
              "runtime-digest": ("runtime_sha256", "wrong"), "binary-digest": ("binary_sha256", "wrong")}
    if change in fields:
        key, value = fields[change]
        record[key] = value
    elif change == "oracle":
        record["reference"]["returncode"] = 1
    elif change == "missing-gc":
        record["executions"].pop()
    elif change == "duplicate-gc":
        record["executions"].append(copy.deepcopy(record["executions"][0]))
    elif change == "failed-gc":
        record["executions"][-1]["returncode"] = 124
    elif change == "wrong-output":
        record["executions"][-1]["stdout"] = "wrong"
    elif change == "stderr":
        record["executions"][-1]["stderr"] = "unexpected diagnostic"
    elif change == "wrong-command":
        record["executions"][-1]["command"] = [sys.executable]
    elif change == "foreign-program":
        (path.parent / "program.py").write_text("print('unrelated')")
    elif change == "duplicate-program":
        other = next(other for other in paths if other != path).parent / "program.py"
        other.write_text((path.parent / "program.py").read_text())
    path.write_text(json.dumps(record))
    if change == "missing-receipt":
        path.unlink()
    with pytest.raises(AssertionError):
        gate.verify_program_receipts(directory, runtime, RUN_ID)


@pytest.mark.parametrize("missing", (
    ("dict-copy-result",), ("dict-pop-result",),
    ("dict-copy-result", "dict-pop-result"), ("two-root-cleanup",), ("two-root-tracing",),
), ids=("copy", "pop", "both", "two-root", "tracing"))
def test_missing_native_evidence_names_the_absent_program_and_fails_gate(
    tmp_path, monkeypatch, admitted, missing,
):
    _source, runtime = admitted

    def run(out, name, *args, **kwargs):
        for path in _receipts(out / name, runtime):
            if _receipt_label(path) in missing:
                path.unlink()

    monkeypatch.setattr(gate, "run_pytest", run)
    message = "missing native execution receipts: " + ", ".join(missing)
    with pytest.raises(AssertionError, match=message):
        gate.run(tmp_path)
    receipt = json.loads((tmp_path / (gate.NAME + ".json")).read_text())
    assert receipt["status"] == "FAIL"
    assert receipt["error"] == "AssertionError: " + message


def test_gate_selects_exact_dict_pcc0_nodes_and_program_sources():
    from tests.python.test_dict_method_result_ownership import _program

    assert gate.NODES[2:4] == (
        "tests/python/test_dict_method_result_ownership.py::"
        "test_dict_method_result_owners_native_five_gc[pcc0-copy]",
        "tests/python/test_dict_method_result_ownership.py::"
        "test_dict_method_result_owners_native_five_gc[pcc0-pop]",
    )
    programs = gate.expected_programs()
    assert programs["dict-copy-result"] == (_program("copy"), "DICT_METHOD_OWNERS_OK\n")
    assert programs["dict-pop-result"] == (_program("pop"), "DICT_METHOD_OWNERS_OK\n")
    assert programs["dict-copy-result"][0] != programs["dict-pop-result"][0]
    assert "tests/python/test_dict_method_result_ownership.py" in gate.TEST_INPUTS


def test_gate_selects_exact_two_root_pcc0_node_and_program_source():
    from tests.python.test_slot_call_exception_cleanup import TWO_ROOT_PROGRAM
    from tests.python.two_root_lifecycle_support import TRACING_TWO_ROOT_PROGRAM

    assert gate.NODES[4:] == (
        "tests/python/test_slot_call_exception_cleanup.py::"
        "test_two_root_cleanup_error_path_native_five_gc[pcc0]",
    )
    assert gate.expected_programs()["two-root-cleanup"] == (
        TWO_ROOT_PROGRAM, "TWO_ROOT_CLEANUP_EXCEPTION_OK\n",
    )
    assert gate.expected_programs()["two-root-tracing"] == (
        TRACING_TWO_ROOT_PROGRAM, "TWO_ROOT_TRACING_CLEANUP_OK\n",
    )
    assert set(prior.TWO_ROOT_CLEANUP_INPUTS) <= set(gate.TEST_INPUTS)
    assert len(gate.NODES) == len(set(gate.NODES)) == 5


def _elf_image_fixture(machine=62, program_header=1):
    """Header-only parser fixture; never native execution evidence."""
    payload = bytearray(128)
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    struct.pack_into("<16sHHIQQQIHHHHHH", payload, 0,
                     ident, 2, machine, 1, 0x400000, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    struct.pack_into("<IIQQQQQQ", payload, 64,
                     program_header, 5, 0, 0x400000, 0x400000, 128, 128, 4096)
    return bytes(payload)


@pytest.mark.parametrize("machine", (62, 183))
@pytest.mark.parametrize("program_header", (1, 2, 3))
def test_elf_admission_checks_program_headers_without_external_tools(tmp_path, machine, program_header):
    from pcc.backend.elf_x86_64 import ElfError

    binary = tmp_path / "program.out"
    binary.write_bytes(_elf_image_fixture(machine, program_header))
    if program_header == 1:
        assert gate.verify_native_image(binary) == {"format": "static-elf", "libraries": []}
    else:
        with pytest.raises(ElfError, match="unexpected program header"):
            gate.verify_native_image(binary)


@pytest.mark.parametrize("libraries", (
    ["kernel32.dll"], ["kernel32.dll", "ws2_32.dll"], [],
    ["python313.dll"], ["libLLVM.dll"], ["custom.dll"],
))
def test_windows_admission_allows_only_named_system_libraries(tmp_path, monkeypatch, libraries):
    from scripts import verify_nolibpython

    binary = tmp_path / "program.out"
    binary.write_bytes(b"MZ host-only image-parser control")
    monkeypatch.setattr(verify_nolibpython, "linked_libraries", lambda payload: libraries)
    if libraries and set(libraries) <= gate.WINDOWS_SYSTEM_LIBRARIES:
        assert gate.verify_native_image(binary) == {"format": "pe", "libraries": libraries}
    else:
        with pytest.raises(AssertionError):
            gate.verify_native_image(binary)


@pytest.mark.parametrize("fail_gc", (None, 2))
def test_shared_helper_runs_absolute_native_paths_with_no_host_fallback(tmp_path, monkeypatch, fail_gc):
    calls = []
    archive = tmp_path / "runtime.a"
    archive.write_bytes(b"host-only archive")
    monkeypatch.setenv("LC_ALL", "C")

    def execute(command, *, env, timeout):
        calls.append((command, env, timeout))
        rc = 124 if env.get("PCC_GC_BACKEND") == str(fail_gc) else 0
        return subprocess.CompletedProcess(command, rc, stdout="ok\n", stderr="")

    def compile_program(source, binary, **options):
        assert options == {"backend": "self",
                           "ir_scaffold_mode": "on", "runtime_archive": str(archive)}
        # Keep the real linkage parser active; only compile/run are simulated.
        Path(binary).write_bytes(_elf_image_fixture())

    monkeypatch.setattr(support, "run_process_group_timeout", execute)
    capfd = SimpleNamespace(readouterr=lambda: SimpleNamespace(out="", err=""))
    args = ("print('ok')\n", "ok\n", tmp_path, compile_program, "pcc0", archive, capfd)
    if fail_gc is None:
        support.assert_owned_program(*args)
    else:
        with pytest.raises(AssertionError):
            support.assert_owned_program(*args)
    assert calls[0][0] == [sys.executable, str(tmp_path / "program.py")]
    for gc, (command, env, timeout) in enumerate(calls[1:]):
        assert command == [str(tmp_path / "program.out")]
        assert Path(command[0]).is_absolute()
        assert env["PCC_GC_BACKEND"] == str(gc) and env["PATH"] == ""
        assert env["PCC_HOST_PYTHON"] == "/nonexistent/host-python"
        assert env["PCC_HOST_PCC"] == "/nonexistent/host-pcc"
        assert env["PCC_TEST_NO_NATIVE_PROVISIONING"] == env["PCC_NO_AUTO_PCC1"] == "1"
        assert "LC_ALL" not in env and timeout == 10
    record = json.loads((tmp_path / "ownership-regression.json").read_text())
    assert record["status"] == ("PASS" if fail_gc is None else "NATIVE_EXECUTION_FAILED")
    assert len(record["executions"]) == (5 if fail_gc is None else 3)
    linkage = record["linkage"]
    assert linkage["schema"] == "pcc.owned-image-linkage.v1"
    assert linkage["format"] == "static-elf64" and linkage["libraries"] == []
    assert linkage["libpython"] == record["libpython"] == "off"
    assert linkage["binary_sha256"] == record["binary_sha256"] == gate.sha(tmp_path / "program.out")


def test_super_regression_uses_explicit_owned_helper_without_pcc1(tmp_path, monkeypatch):
    from pcc.frontends.python.pipeline import compile_python
    from tests.python import test_super_call_result_roots as probe

    calls = []
    monkeypatch.setattr(probe, "assert_owned_program", lambda *args: calls.append(args))
    archive, capfd = object(), object()
    probe.test_super_result_native_matches_cpython_under_five_collectors(tmp_path, archive, capfd)
    assert calls == [(probe.NATIVE_SOURCE, "5\nTrue\ncaught\n", tmp_path,
                      compile_python, "pcc0", archive, capfd)]
    tree = ast.parse((ROOT / "tests/python/test_super_call_result_roots.py").read_text())
    native = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == gate.NODES[1].split("::")[1])
    assert [arg.arg for arg in native.args.args] == ["tmp_path", "explicit_owned_runtime", "capfd"]
    assert [ast.unparse(mark) for mark in native.decorator_list] == ["pytest.mark.integration"]


@pytest.mark.parametrize("method", ("copy", "pop"))
def test_dict_regressions_reuse_the_explicit_owned_helper(tmp_path, monkeypatch, method):
    from tests.python import test_dict_method_result_ownership as probe

    calls = []
    monkeypatch.setattr(probe, "assert_owned_program", lambda *args, **kwargs: calls.append((args, kwargs)))
    compiler, archive, capfd = object(), object(), object()
    request = SimpleNamespace(node=SimpleNamespace(
        callspec=SimpleNamespace(params={"python_program_compiler": "pcc0"}),
    ))
    probe.test_dict_method_result_owners_native_five_gc(
        compiler, request, archive, tmp_path, capfd, method,
    )
    assert calls == [((probe._program(method), "DICT_METHOD_OWNERS_OK\n", tmp_path,
                      compiler, "pcc0", archive, capfd),
                     {"provenance_probe": "2", "emitted_ir_check": probe._select_dict_program_ir, "retain_dump_evidence": True})]


def _program_ir(marker="OBJECT_TYPE_IDENTITY_OK"):
    # The owned serializer uses \\HH for every byte, including terminal NUL.
    raw = marker.encode() + b"\0"
    escaped = "".join("\\" + format(byte, "02X") for byte in raw)
    return ('@map = internal constant i32 1\n'
            '@message = internal constant [' + str(len(raw)) + ' x i8] c"' + escaped + '"\n'
            'define ptr @user_case_main() {\nentry:\n'
            '  ret ptr @message\n}\n')


@pytest.mark.parametrize("mutation, status", [
    ("missing", "MISSING"), ("comment", "MISSING"), ("declaration", "MISSING"),
    ("foreign-use", "MISSING"), ("comment-use", "MISSING"),
    ("ambiguous", "AMBIGUOUS"), ("array-length", "INVALID"), ("prefix-use", "MISSING"),
])
def test_program_ir_selection_requires_a_unique_local_definition(mutation, status):
    module = _program_ir()
    if mutation == "missing":
        modules = [_program_ir("unrelated")]
    elif mutation == "comment":
        modules = ["\n".join("; " + line for line in module.splitlines())]
    elif mutation == "declaration":
        modules = [module.split("define", 1)[0] + "declare ptr @user_case_main()\n"]
    elif mutation == "foreign-use":
        modules = [module.split("define", 1)[0], "define" + module.split("define", 1)[1]]
    elif mutation == "comment-use":
        modules = [module.replace("  ret ptr @message", "  ; ret ptr @message\n  ret ptr null")]
    elif mutation == "prefix-use":
        modules = [module.replace("ret ptr @message", "ret ptr @message-other")]
    elif mutation == "array-length":
        modules = [module.replace("[24 x i8]", "[25 x i8]")]
    else:
        modules = [module, module]
    with pytest.raises(AssertionError, match=status):
        support.select_program_ir(modules, "OBJECT_TYPE_IDENTITY_OK")


def _ir_evidence_inputs(directory):
    source, binary, archive = (directory / name for name in ("program.py", "program.out", "runtime.a"))
    source.write_bytes(b"unchanged source\n")
    binary.write_bytes(b"host-only image; never executed")
    archive.write_bytes(b"host-only archive; never linked")
    dump = directory / "executable-ir"
    dump.mkdir()
    (dump / "self_backend_input_0.ll").write_bytes(_program_ir("unrelated").encode())
    (dump / "self_backend_input_1.ll").write_bytes(_program_ir().replace("\n", "\r\n").encode())
    receipt = {
        "run_id": RUN_ID, "compiler_fixture_parameter": "pcc0", "compiler_mode": "pcc0",
        "backend": "self", "source_sha256": gate.sha(source), "binary_path": str(binary),
        "runtime_archive": str(archive), "runtime_sha256": gate.sha(archive),
    }
    return dump, receipt, source, binary, archive


def _select_program_ir(modules):
    return support.select_program_ir(modules, "OBJECT_TYPE_IDENTITY_OK")


def test_input_ir_json_keeps_raw_hashes_scope_and_source_runtime_binary_identity(tmp_path):
    dump, receipt, source, binary, archive = _ir_evidence_inputs(tmp_path)
    support._record_observed_ir_dump(dump, _select_program_ir, receipt, source, binary, archive, completed=True)
    path = tmp_path / "executable-ir-dump.json"
    evidence = json.loads(path.read_text())
    assert evidence["status"] == "DUMP_FILES_RETAINED" and evidence["compiler_completed"] is True
    assert evidence["run_id"] == RUN_ID
    assert evidence["producer_completeness"] == evidence["all_modules_captured"] == "UNPROVEN"
    assert evidence["qualification"] == "NOT_ASSESSED"
    assert evidence["source_sha256"] == gate.sha(source)
    assert evidence["runtime_sha256"] == gate.sha(archive)
    assert evidence["binary_sha256"] == gate.sha(binary)
    assert evidence["observed_dump_sha256"] == receipt["observed_ir_dump_sha256"]
    assert len(evidence["modules"]) == 1  # Both modules have @map and @message.
    module = evidence["modules"][0]
    raw = (dump / "self_backend_input_1.ll").read_bytes()
    assert module == {"name": "self_backend_input_1.ll", "text": raw.decode(),
                      "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    assert receipt["executable_ir_dump_evidence"]["sha256"] == gate.sha(path)
    assert len(path.read_bytes()) <= support._IR_JSON_LIMIT
    assert {p.suffix for p in dump.iterdir()} == {".ll"}


@pytest.mark.parametrize("mutation, status", [
    ("module-limit", "OVERSIZE"), ("total-limit", "OVERSIZE"), ("json-limit", "OVERSIZE"),
    ("source", "IDENTITY_CHANGED"), ("runtime", "IDENTITY_CHANGED"),
    ("missing", "MISSING"), ("ambiguous", "AMBIGUOUS"),
])
def test_input_ir_failure_is_bounded_and_never_truncates_a_module(tmp_path, monkeypatch, mutation, status):
    dump, receipt, source, binary, archive = _ir_evidence_inputs(tmp_path)
    if mutation == "module-limit":
        monkeypatch.setattr(support, "_IR_MODULE_LIMIT", 1)
    elif mutation == "total-limit":
        monkeypatch.setattr(support, "_IR_TOTAL_LIMIT", 1)
    elif mutation == "json-limit":
        # Metadata fits; escaped retained text does not. Check output bytes,
        # not a character count or only the source module's byte count.
        monkeypatch.setattr(support, "_IR_JSON_LIMIT", 4096)
        with (dump / "self_backend_input_1.ll").open("ab") as stream:
            stream.write(b"; " + b"\\" * 4096 + b"\n")
    elif mutation == "source":
        source.write_bytes(b"changed source")
    elif mutation == "runtime":
        archive.write_bytes(b"changed archive")
    elif mutation == "missing":
        (dump / "self_backend_input_1.ll").write_text(_program_ir("unrelated"))
    else:
        (dump / "self_backend_input_0.ll").write_text(_program_ir())
    with pytest.raises(AssertionError, match=status):
        support._record_observed_ir_dump(dump, _select_program_ir, receipt, source, binary, archive, completed=True)
    path = tmp_path / "executable-ir-dump.json"
    evidence = json.loads(path.read_text())
    assert evidence["status"] == status and evidence["modules"] == []
    assert evidence["source_sha256"] == receipt["source_sha256"]
    assert evidence["runtime_sha256"] == receipt["runtime_sha256"]
    assert len(path.read_bytes()) <= support._IR_JSON_LIMIT


@pytest.mark.parametrize("failure", ("compile-before-dump", "compile-after-dump", "diagnostic-write", "native", None))
def test_input_ir_uses_one_compile_and_survives_original_failure(tmp_path, monkeypatch, failure):
    archive = tmp_path / "runtime.a"
    archive.write_bytes(b"host-only archive")
    compiles, executions = [], []
    original_error = ValueError("original compiler failure")

    def compiler(source, binary, **options):
        compiles.append(options)
        dump = Path(os.environ["PCC_DEBUG_SELF_IR_DUMP_DIR"])
        assert list(dump.iterdir()) == []
        assert options == {"backend": "self", "ir_scaffold_mode": "on", "runtime_archive": str(archive)}
        if failure != "compile-before-dump":
            (dump / "self_backend_input_0.ll").write_text(_program_ir())
        if failure in ("compile-before-dump", "compile-after-dump", "diagnostic-write"):
            raise original_error
        Path(binary).write_bytes(_elf_image_fixture())

    def execute(command, *, env, timeout):
        backend = env.get("PCC_GC_BACKEND")
        executions.append(backend)
        assert timeout == 10
        return subprocess.CompletedProcess(command, 1 if failure == "native" and backend == "1" else 0,
                                           stdout="ok\n", stderr="")

    monkeypatch.setattr(support, "run_process_group_timeout", execute)
    if failure == "diagnostic-write":
        write_bytes = Path.write_bytes

        def write(path, payload):
            if path.name == "executable-ir-dump.json":
                raise OSError("diagnostic write failed")
            return write_bytes(path, payload)

        monkeypatch.setattr(Path, "write_bytes", write)
    before = os.environ.get("PCC_DEBUG_SELF_IR_DUMP_DIR")
    capfd = SimpleNamespace(readouterr=lambda: SimpleNamespace(out="", err=""))
    args = ("unchanged original program\n", "ok\n", tmp_path, compiler, "pcc0", archive, capfd)
    if failure in ("compile-before-dump", "compile-after-dump", "diagnostic-write"):
        with pytest.raises(ValueError) as caught:
            support.assert_owned_program(*args, emitted_ir_check=_select_program_ir, retain_dump_evidence=True)
        assert caught.value is original_error
    elif failure == "native":
        with pytest.raises(AssertionError):
            support.assert_owned_program(*args, emitted_ir_check=_select_program_ir, retain_dump_evidence=True)
    else:
        support.assert_owned_program(*args, emitted_ir_check=_select_program_ir, retain_dump_evidence=True)
    assert len(compiles) == 1
    assert os.environ.get("PCC_DEBUG_SELF_IR_DUMP_DIR") == before
    record = json.loads((tmp_path / "ownership-regression.json").read_text())
    if failure == "diagnostic-write":
        assert record["error"] == "ValueError: original compiler failure"
        assert record["ir_capture_error"] == "OSError: diagnostic write failed"
        assert record["status"] == "COMPILE_FAILED" and executions == [None]
        assert not (tmp_path / "executable-ir-dump.json").exists()
        return
    evidence = json.loads((tmp_path / "executable-ir-dump.json").read_text())
    assert record["gc_backends"] == list(range(5))
    assert record["source_sha256"] == gate.sha(tmp_path / "program.py")
    assert record["runtime_sha256"] == gate.sha(archive)
    if failure in ("compile-before-dump", "compile-after-dump"):
        assert record["error"] == "ValueError: original compiler failure"
        assert evidence["compile_error"] == record["error"]
        assert evidence["compiler_completed"] is False and "binary_sha256" not in evidence
        assert evidence["status"] == ("MISSING" if failure == "compile-before-dump" else "INCOMPLETE_COMPILE")
        assert executions == [None]
    else:
        assert evidence["status"] == "DUMP_FILES_RETAINED"
        assert record["status"] == ("NATIVE_EXECUTION_FAILED" if failure else "PASS")
        assert executions == ([None, "0", "1"] if failure else [None, "0", "1", "2", "3", "4"])


@pytest.mark.parametrize("limit", ("module", "total"))
def test_dump_limits_reject_before_readers_and_checker(tmp_path, monkeypatch, limit):
    dump, receipt, source, binary, archive = _ir_evidence_inputs(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("oversize input reached a reader or checker")

    with monkeypatch.context() as patch:
        patch.setattr(support, "_IR_MODULE_LIMIT" if limit == "module" else "_IR_TOTAL_LIMIT", 1)
        patch.setattr(support, "_read_bounded_dump", forbidden)
        patch.setattr(Path, "read_bytes", forbidden)
        patch.setattr(Path, "read_text", forbidden)
        with pytest.raises(AssertionError, match="OVERSIZE"):
            support._record_observed_ir_dump(dump, forbidden, receipt, source, binary, archive, completed=True)
    assert json.loads((tmp_path / "executable-ir-dump.json").read_text())["status"] == "OVERSIZE"


def test_dump_inventory_stops_before_unbounded_enumeration(tmp_path, monkeypatch):
    dump, receipt, source, binary, archive = _ir_evidence_inputs(tmp_path)
    seen, closed = [], []

    class Entries:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(True)

        def __iter__(self):
            for index in range(130):
                seen.append(index)
                yield SimpleNamespace(path=str(dump / ("self_backend_input_" + str(index) + ".ll")))

    def scandir(path):
        assert path == dump
        return Entries()

    def forbidden(*args, **kwargs):
        pytest.fail("eager directory enumeration")

    monkeypatch.setattr(support.os, "scandir", scandir)
    monkeypatch.setattr(support.os, "listdir", forbidden)
    monkeypatch.setattr(Path, "iterdir", forbidden)
    with pytest.raises(AssertionError, match="OVERSIZE"):
        support._record_observed_ir_dump(dump, _select_program_ir,
                                         receipt, source, binary, archive, completed=True)
    assert len(seen) == 129 and closed == [True]


@pytest.mark.parametrize("mutation", ("utf8", "filename", "directory", "function", "header", "constant"))
def test_dump_rejects_malformed_or_obviously_truncated_files(tmp_path, mutation):
    dump, receipt, source, binary, archive = _ir_evidence_inputs(tmp_path)
    selected = dump / "self_backend_input_1.ll"
    if mutation == "utf8":
        selected.write_bytes(b"\xff")
    elif mutation == "filename":
        selected.rename(dump / "self_backend_input_2.ll")
    elif mutation == "directory":
        selected.unlink()
        selected.mkdir()
    elif mutation == "function":
        selected.write_text(_program_ir().removesuffix("}\n"))
    elif mutation == "header":
        selected.write_text(_program_ir() + "define void @tail(\n")
    else:
        selected.write_text(_program_ir() + '@tail = internal constant [4 x i8] c"\\41\n')
    with pytest.raises(AssertionError, match="MALFORMED|MISSING"):
        support._record_observed_ir_dump(dump, _select_program_ir, receipt, source, binary, archive, completed=True)
    evidence = json.loads((tmp_path / "executable-ir-dump.json").read_text())
    assert evidence["status"] in ("MALFORMED", "MISSING") and evidence["modules"] == []


@pytest.mark.parametrize("difference", (-1, 0, 1))
def test_dump_chunk_boundary_requires_exact_inventoried_eof(tmp_path, difference):
    path = tmp_path / "dump.ll"
    payload = b"x" * 65536 + b"y"
    path.write_bytes(payload)
    if difference:
        with pytest.raises(AssertionError, match="IDENTITY_CHANGED"):
            support._read_bounded_dump(path, len(payload) + difference)
    else:
        assert support._read_bounded_dump(path, len(payload)) == (payload, hashlib.sha256(payload).hexdigest())


@pytest.mark.parametrize("grow", (False, True))
def test_dump_length_change_after_inventory_retains_no_module(tmp_path, monkeypatch, grow):
    dump, receipt, source, binary, archive = _ir_evidence_inputs(tmp_path)
    read = support._read_bounded_dump

    def change_then_read(path, size):
        raw = path.read_bytes()
        path.write_bytes(raw + b"x" if grow else raw[:-1])
        return read(path, size)

    monkeypatch.setattr(support, "_read_bounded_dump", change_then_read)
    with pytest.raises(AssertionError, match="IDENTITY_CHANGED"):
        support._record_observed_ir_dump(dump, _select_program_ir, receipt, source, binary, archive, completed=True)
    evidence = json.loads((tmp_path / "executable-ir-dump.json").read_text())
    assert evidence["status"] == "IDENTITY_CHANGED" and evidence["modules"] == []


@pytest.mark.parametrize("selection", (None, (), (True,), (2,), (0, 1)))
def test_retained_dump_requires_one_valid_selected_index(tmp_path, selection):
    dump, receipt, source, binary, archive = _ir_evidence_inputs(tmp_path)
    with pytest.raises(AssertionError, match="MALFORMED"):
        support._record_observed_ir_dump(dump, lambda modules: selection, receipt, source,
                                         binary, archive, completed=True)
    evidence = json.loads((tmp_path / "executable-ir-dump.json").read_text())
    assert evidence["status"] == "MALFORMED" and evidence["modules"] == []


def test_completed_compiler_without_binary_cannot_bind_dump_evidence(tmp_path):
    dump, receipt, source, binary, archive = _ir_evidence_inputs(tmp_path)
    binary.unlink()
    with pytest.raises(AssertionError, match="MISSING"):
        support._record_observed_ir_dump(dump, _select_program_ir, receipt, source, binary, archive, completed=True)
    evidence = json.loads((tmp_path / "executable-ir-dump.json").read_text())
    assert evidence["status"] == "MISSING" and evidence["modules"] == []
    assert "binary_sha256" not in evidence


def test_closed_dump_with_metadata_still_cannot_prove_producer_completeness(tmp_path):
    dump, receipt, source, binary, archive = _ir_evidence_inputs(tmp_path)
    (dump / "self_backend_input_1.ll").write_text(_program_ir() + "!0 = !{i32 1}\n")
    support._record_observed_ir_dump(dump, _select_program_ir, receipt, source, binary, archive, completed=True)
    evidence = json.loads((tmp_path / "executable-ir-dump.json").read_text())
    assert evidence["status"] == "DUMP_FILES_RETAINED"
    assert evidence["producer_completeness"] == evidence["all_modules_captured"] == "UNPROVEN"
    assert evidence["qualification"] == "NOT_ASSESSED"


@pytest.mark.parametrize("stale", ("directory", "binary"))
def test_dump_capture_refuses_stale_inputs_before_compiler(tmp_path, monkeypatch, stale):
    archive = tmp_path / "runtime.a"
    archive.write_bytes(b"host-only archive")
    if stale == "directory":
        (tmp_path / "executable-ir").mkdir()
    else:
        (tmp_path / "program.out").write_bytes(b"old image")
    monkeypatch.setattr(support, "run_process_group_timeout", lambda command, **options:
                        subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr=""))
    calls = []
    capfd = SimpleNamespace(readouterr=lambda: SimpleNamespace(out="", err=""))
    with pytest.raises((AssertionError, FileExistsError)):
        support.assert_owned_program("original\n", "ok\n", tmp_path,
                                     lambda *args, **kwargs: calls.append(args), "pcc0", archive, capfd,
                                     emitted_ir_check=_select_program_ir, retain_dump_evidence=True)
    assert calls == [] and not (tmp_path / "executable-ir-dump.json").exists()


def test_object_regression_selects_its_original_program_ir(tmp_path, monkeypatch):
    from tests.python import test_object_type_identity as probe

    calls = []
    monkeypatch.setattr(probe, "assert_owned_program", lambda *args, **kwargs: calls.append((args, kwargs)))
    (tmp_path / "compiler-wrapper.stderr").write_text("")
    compiler, archive, capfd = object(), object(), object()
    request = SimpleNamespace(node=SimpleNamespace(callspec=SimpleNamespace(params={"python_program_compiler": "pcc0"})))
    probe.test_object_identity_native_five_gc(compiler, request, archive, tmp_path, capfd)
    assert calls == [((probe.PROGRAM, "OBJECT_TYPE_IDENTITY_OK\n", tmp_path,
                      compiler, "pcc0", archive, capfd),
                     {"provenance_probe": "2", "emitted_ir_check": probe._select_identity_program_ir, "retain_dump_evidence": True})]


def test_two_root_regression_reuses_the_explicit_owned_helper(tmp_path, monkeypatch):
    from tests.python import test_slot_call_exception_cleanup as probe

    from tests.python import two_root_lifecycle_support as lifecycle

    calls, slots, ir_programs = [], [], []
    monkeypatch.setattr(probe, "assert_owned_program", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(lifecycle, "assert_two_root_slots_native", lambda *args: slots.append(args))

    def check(program=probe.TWO_ROOT_PROGRAM):
        ir_programs.append(program)
        return "host-only IR control\n"

    monkeypatch.setattr(probe, "_assert_two_root_program_error_edge", check)
    compiler, archive, capfd = object(), object(), object()
    request = SimpleNamespace(node=SimpleNamespace(
        callspec=SimpleNamespace(params={"python_program_compiler": "pcc0"}),
    ))
    probe.test_two_root_cleanup_error_path_native_five_gc(
        compiler, request, archive, tmp_path, capfd,
    )
    assert calls == [
        ((probe.TWO_ROOT_PROGRAM, "TWO_ROOT_CLEANUP_EXCEPTION_OK\n", tmp_path / "gc0",
          compiler, "pcc0", archive, capfd),
         {"provenance_probe": "2", "backends": (0,),
          "emitted_ir_check": lifecycle.assert_compiler_two_root_error_edge}),
        ((lifecycle.TRACING_TWO_ROOT_PROGRAM, "TWO_ROOT_TRACING_CLEANUP_OK\n", tmp_path / "tracing",
          compiler, "pcc0", archive, capfd),
         {"provenance_probe": "2", "backends": (1, 2, 3, 4), "reference_arguments": ("cyclic-reference",),
          "emitted_ir_check": lifecycle.assert_compiler_two_root_error_edge}),
    ]
    assert slots == [(compiler, "pcc0", archive, tmp_path / "slots", capfd)]
    assert ir_programs == [probe.TWO_ROOT_PROGRAM, lifecycle.TRACING_TWO_ROOT_PROGRAM]
    for name in ("two-root-cleanup.ll", "two-root-tracing-cleanup.ll"):
        assert (tmp_path / name).read_text() == "host-only IR control\n"
    tree = ast.parse((ROOT / gate.NODES[4].split("::")[0]).read_text())
    native = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "test_two_root_cleanup_error_path_native_five_gc")
    assert [arg.arg for arg in native.args.args] == [
        "python_program_compiler", "request", "explicit_owned_runtime", "tmp_path", "capfd",
    ]
    assert ast.unparse(native.decorator_list[0]) == "pytest.mark.integration"
    fixture_mark = native.decorator_list[1]
    assert ast.unparse(fixture_mark.func) == "pytest.mark.parametrize"
    assert [ast.literal_eval(arg) for arg in fixture_mark.args] == [
        "python_program_compiler", ("pcc0", "pcc1"),
    ]
    assert [(keyword.arg, ast.literal_eval(keyword.value)) for keyword in fixture_mark.keywords] == [
        ("indirect", True),
    ]


def test_explicit_fixture_retains_single_thread_config_and_never_requests_pcc1(tmp_path, monkeypatch):
    from pcc.tools import runtime_archive_provenance

    archive = tmp_path / "libpy_runtime_pcc_py.a"
    archive.write_bytes(b"host-only archive placeholder")
    Path(str(archive) + ".provenance.json").write_text("{}")
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(archive))
    monkeypatch.setenv("PCC_WITH_THREADS", "0")
    monkeypatch.setenv("PCC_REFCOUNT_KIND", "atomic")
    monkeypatch.setattr(runtime_archive_provenance, "manifest_is_stale_for_current_codegen", lambda _: False)
    fixtures = []

    def getfixturevalue(name):
        fixtures.append(name)
        assert name == "pcc_runtime_archive"
        return archive

    request = SimpleNamespace(getfixturevalue=getfixturevalue)
    assert support.explicit_owned_runtime.__wrapped__(request, monkeypatch) == archive
    assert fixtures == ["pcc_runtime_archive"]
    import os

    assert os.environ["PCC_WITH_THREADS"] == "0"
    assert os.environ["PCC_REFCOUNT_KIND"] == "atomic"
    assert os.environ["PCC_TEST_NO_NATIVE_PROVISIONING"] == os.environ["PCC_NO_AUTO_PCC1"] == "1"
    assert os.environ["PATH"] == ""


def test_workflow_runs_post_stage1_without_building_or_widening_the_job(tmp_path):
    assert gate.source_identity is prior.source_identity
    text = (ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    other = text.split("  other-platform-native-wheel:\n", 1)[1].split("  pcc1-package-parity:\n", 1)[0]
    name = "Verify native object ownership and cleanup with the existing runtime"
    step = other.split("      - name: " + name + "\n", 1)[1].split("      - name:", 1)[0]
    assert other.index("id: platform_qualification") < other.index("id: platform_runtime") < other.index(name)
    assert other.index(name) < other.index("--phase pcc1")
    assert "!cancelled()" in step
    assert "steps.platform_qualification.outcome == 'success'" in step
    assert "steps.platform_qualification.outcome == 'failure'" in step
    assert "steps.platform_runtime.outputs.ready" not in step
    assert "PCC_RUNTIME_ARCHIVE: ${{ steps.platform_runtime.outputs.archive }}" in step
    assert "timeout-minutes: 6" in step and "timeout-minutes: 360" in other
    for key, value in {"PCC_TEST_NO_NATIVE_PROVISIONING": "1", "PCC_NO_AUTO_PCC1": "1",
                       "PCC_WITH_THREADS": "1", "PCC_REFCOUNT_KIND": "atomic",
                       "PCC_PYTEST_AUTO_CLEAN": "0", "UV_PYTHON_DOWNLOADS": "never"}.items():
        assert f'{key}: "{value}"' in step
    command = "uv run --frozen --no-sync python scripts/ci_native_object_regressions.py --out-dir build/worker-prior-gate"
    assert "run: " + command in step
    assert "download-artifact" not in step and "continue-on-error" not in step
    host_name = "Verify host Stage2 export-retention, AArch64 call-emission, and ELF assembly contracts"
    host_header = "      - name: " + host_name + "\n"
    assert other.count(host_header) == 1
    host_step = other.split(host_header, 1)[1].split("      - name:", 1)[0]
    host_commands = [line.removeprefix("        run: ") for line in host_step.splitlines()
                     if line.startswith("        run: ")]
    assert len(host_commands) == 1
    host_command, linux_condition = host_commands[0].split(" ${{ ", 1)
    assert host_command.split() == [
        "uv", "run", "--frozen", "--no-sync", "python", "-m", "pytest", "-x", "-q",
        "tests/python/test_stage2_export_capture.py",
        "tests/python/test_aarch64_call_target_reuse.py",
        "tests/python/test_x86_jump_threading.py",
        "tests/python/test_owned_elf_assembler_dispatch.py",
        "tests/python/test_elf_arm_branch_slot_defaults.py",
        "tests/python/test_gc_phase_attribution.py",
        "tests/python/test_gc_phase_counter_widths.py",
        "tests/test_ci_native_object_regressions.py",
        "tests/python/test_two_root_lifecycle_support.py",
        "tests/test_ci_native_ordering_gate.py",
        "tests/python/test_user_ordering_exhaustion.py",
    ]
    linux_only = (
        "tests/test_ci_stage1_artifact_export.py",
        "tests/python/test_gc4_copy_page_probe_native.py",
        "tests/python/test_gc_relocation_membership_lifecycle.py",
        "tests/python/test_gc_remembered_membership_lifecycle.py",
        "tests/python/test_gc_remembered_membership_native.py",
        "tests/python/test_owned_runtime_pass_prefix.py",
        "tests/python/test_pipeline_ir_split.py",
        "tests/python/test_pipeline_ir_frame_maps.py",
        "tests/python/test_x86_64_branch_syntax_cache.py",
        "tests/python/test_x86_64_instruction_encoding_cache.py",
        "tests/python/test_x86_64_instruction_encoding_cache_lowering.py",
        "tests/python/test_x86_64_operand_cache.py",
    )
    assert linux_condition == (
        "matrix.platform == 'linux-x86_64' && '" + " ".join(linux_only) + "' || '' }}")
    for path in (*[node.split("::")[0] for node in (*gate.NODES, *gate.WINDOWS_NODES)],
                 "scripts/ci_native_object_regressions.py", "tests/test_ci_native_object_regressions.py",
                 "tests/python/test_elf_arm_branch_slot_defaults.py",
                 "tests/python/test_gc_phase_attribution.py",
                 "tests/python/test_gc_phase_counter_widths.py",
                 "tests/elf_arm_branch_slot_reference.py",
                 "tests/elf_x86_packed_classification_reference.py",
                 *linux_only, "tests/python/test_freestanding_gc_relocation_copy.py",
                 "tests/python/fixtures/gc4_copy_page_probe_native.c"):
        assert text.count('      - "' + path + '"') == 2
    for suffix in ("json", "jsonl", "stdout", "stderr", "tsv", "xml"):
        assert "build/worker-prior-gate/**/*." + suffix in other
    assert gate.TIMEOUT_S == 300 and len(gate.NODES) == 5
    assert "[pcc0]" in gate.NODES[0] and all("pcc1" not in node for node in gate.NODES)
    command = prior.pytest_command(tmp_path, gate.NODES, integration=True)
    assert command[-7:] == ["-m", "integration", *gate.NODES]
    assert "-x" in command
    assert not any(option in command for option in ("-n", "-n0", "--dist", "-o", "--override-ini"))
    assert '-n 6 --dist=loadgroup' in (ROOT / "pyproject.toml").read_text()


def _win64_receipt(directory, runtime, run_id):
    import hashlib
    from pcc.backend.target_objects import encode_assembly_object
    from tests.python.test_windows_direct_scalar_call_args import (
        HOME_CALLBACK_ASM,
        MANAGED_CALLBACK_SOURCE,
        TARGET,
    )

    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "win64_home_callback.py"
    source.write_text(MANAGED_CALLBACK_SOURCE)
    helper = directory / "win64_home_callback.obj"
    helper.write_bytes(encode_assembly_object(HOME_CALLBACK_ASM, TARGET))
    binary = directory / "win64_home_callback.exe"
    payload = bytearray(70)
    payload[:2] = b"MZ"
    payload[60:64] = (64).to_bytes(4, "little")
    payload[64:] = b"PE\0\0\x64\x86"
    binary.write_bytes(payload)
    record = {
        "schema": "pcc.win64-home-callback.v1", "status": "PASS", "run_id": run_id,
        "compiler_mode": "host-pcc0", "backend": "self", "target": TARGET,
        "source_sha256": gate.sha(source),
        "helper_assembly_sha256": hashlib.sha256(HOME_CALLBACK_ASM.encode()).hexdigest(),
        "helper_sha256": gate.sha(helper), "runtime_archive": runtime["path"],
        "runtime_sha256": runtime["hashes"][""],
        "binary_path": str(binary), "binary_sha256": gate.sha(binary),
        "executions": [{"requested_gc_backend": gc, "command": [str(binary)],
                        "returncode": 0, "stdout": "WIN64_HOME_ROOT_CALLBACK_OK\n", "stderr": ""}
                       for gc in range(5)],
    }
    path = directory / "win64-home-callback.json"
    path.write_text(json.dumps(record))
    return path


def test_win64_native_extension_selects_only_windows_and_binds_its_inputs():
    expected = (prior.WIN64_DIRECT_SCALAR_TEST +
                "::test_native_win64_home_space_and_rooted_callback_five_collectors",)
    assert gate.WINDOWS_NODES == expected
    assert gate.selected_nodes("win32") == (*gate.NODES, *expected)
    assert gate.selected_nodes("linux") == gate.NODES
    assert gate.selected_nodes("darwin") == gate.NODES
    assert set(prior.WIN64_DIRECT_SCALAR_INPUTS) <= set(gate.TEST_INPUTS)
    command = prior.pytest_command(Path("host-only"), gate.selected_nodes("win32"), integration=True)
    assert command[-8:] == ["-m", "integration", *gate.NODES, *expected]
    assert "-x" in command
    assert not any(option in command for option in ("-n", "-n0", "--dist", "-o", "--override-ini"))


@pytest.mark.parametrize("stage_status", ("PASS", "FAIL"))
def test_win64_extension_reuses_admission_in_one_existing_gate_call(
    tmp_path, monkeypatch, admitted, stage_status,
):
    _source, runtime = admitted
    from tests.python.test_windows_direct_scalar_call_args import TARGET

    runtime["target"] = TARGET
    stage_path = tmp_path / "stage1.json"
    stage = json.loads(stage_path.read_text())
    stage.update(status=stage_status, runtime=runtime)
    stage_path.write_text(json.dumps(stage))
    nodes = (*gate.NODES, *gate.WINDOWS_NODES)
    monkeypatch.setattr(gate, "selected_nodes", lambda: nodes)
    monkeypatch.setattr(gate, "verify_native_image", lambda path: {"format": "pe", "libraries": ["kernel32.dll"]})
    calls = []

    def run(out, name, selected, env, **options):
        calls.append((selected, env, options))
        _receipts(out / name, runtime)
        _win64_receipt(out / name / "win64", runtime, env["PCC_NATIVE_OBJECT_GATE_RUN_ID"])

    monkeypatch.setattr(gate, "run_pytest", run)
    result = gate.run(tmp_path)
    assert len(calls) == 1
    selected, env, options = calls[0]
    assert selected == nodes and options == {"integration": True, "timeout": 300}
    assert env["PCC_RUNTIME_ARCHIVE"] == runtime["path"]
    assert env["PCC_NATIVE_OBJECT_GATE_RUN_ID"] == result["run_id"]
    assert env["PCC_TEST_NO_NATIVE_PROVISIONING"] == "1" and env["UV_PYTHON_DOWNLOADS"] == "never"
    assert result["status"] == "PASS" and result["nodes"] == list(nodes)
    assert result["tree_rss_limit_bytes"] == 17179869184
    assert result["observed_collector_witness"] is False
    assert len(result["programs"]) == 8
    assert sum(len(row["executions"]) for row in result["programs"].values()) == 35
    extra = result["programs"]["win64-home-callback"]
    assert extra["compiler_mode"] == "host-pcc0" and extra["observed_collector_witness"] is True
    assert [row["requested_gc_backend"] for row in extra["executions"]] == list(range(5))
    assert result["source"] == result["source_after"]
    assert result["runtime"] == result["runtime_after"] == runtime


@pytest.mark.parametrize("change", (
    "missing", "duplicate", "stale", "running", "schema", "pcc1", "llvm",
    "target", "runtime-target", "runtime-path", "runtime-digest", "source", "source-digest",
    "assembly-digest", "helper", "helper-digest", "binary-path", "binary-digest", "machine",
    "image-format", "missing-gc", "duplicate-gc", "boolean-gc", "failed-gc", "wrong-output",
    "stderr", "wrong-command",
))
def test_win64_receipt_rejects_missing_stale_or_mismatched_evidence(
    tmp_path, monkeypatch, admitted, change,
):
    _source, runtime = admitted
    from tests.python.test_windows_direct_scalar_call_args import TARGET

    runtime["target"] = TARGET
    monkeypatch.setattr(gate, "verify_native_image", lambda path: {"format": "pe", "libraries": ["kernel32.dll"]})
    directory = tmp_path / "receipts"
    path = _win64_receipt(directory / "win64", runtime, "current-run")
    record = json.loads(path.read_text())
    fields = {
        "stale": ("run_id", "old-run"), "running": ("status", "RUNNING"),
        "schema": ("schema", "other"), "pcc1": ("compiler_mode", "pcc1"),
        "llvm": ("backend", "llvm"),
        "target": ("target", "aarch64-pc-windows-msvc"),
        "runtime-path": ("runtime_archive", "other.a"), "runtime-digest": ("runtime_sha256", "stale"),
        "source-digest": ("source_sha256", "stale"),
        "assembly-digest": ("helper_assembly_sha256", "stale"),
        "helper-digest": ("helper_sha256", "stale"),
        "binary-path": ("binary_path", "other.exe"), "binary-digest": ("binary_sha256", "stale"),
    }
    if change in fields:
        key, value = fields[change]
        record[key] = value
    elif change == "runtime-target":
        runtime["target"] = "aarch64-pc-windows-msvc"
    elif change in ("source", "helper"):
        target = path.parent / ("win64_home_callback.py" if change == "source" else "win64_home_callback.obj")
        target.write_bytes(b"unexpected source or helper")
        record[change + "_sha256"] = gate.sha(target)
    elif change == "machine":
        binary = path.parent / "win64_home_callback.exe"
        raw = bytearray(binary.read_bytes())
        raw[68:70] = b"\x64\xaa"
        binary.write_bytes(raw)
        record["binary_sha256"] = gate.sha(binary)
    elif change == "image-format":
        monkeypatch.setattr(gate, "verify_native_image", lambda path: {"format": "static-elf"})
    elif change == "missing-gc":
        record["executions"].pop()
    elif change == "duplicate-gc":
        record["executions"][-1]["requested_gc_backend"] = 0
    elif change == "boolean-gc":
        record["executions"][1]["requested_gc_backend"] = True
    elif change == "failed-gc":
        record["executions"][-1]["returncode"] = 124
    elif change == "wrong-output":
        record["executions"][2]["stdout"] = "616\n"
    elif change == "stderr":
        record["executions"][2]["stderr"] = "diagnostic"
    elif change == "wrong-command":
        record["executions"][2]["command"] = [sys.executable]
    path.write_text(json.dumps(record))
    if change == "missing":
        path.unlink()
    elif change == "duplicate":
        _win64_receipt(directory / "duplicate", runtime, "current-run")
    with pytest.raises(AssertionError):
        gate.verify_win64_callback_receipt(directory, runtime, "current-run")


@pytest.mark.parametrize("failure", (None, "compile", "collector", "runtime-drift"))
def test_win64_native_probe_records_one_compile_and_fails_closed(
    tmp_path, monkeypatch, admitted, failure,
):
    _source, runtime = admitted
    from pcc.frontends.python import pipeline
    from tests.python import test_windows_direct_scalar_call_args as probe

    runtime["target"] = probe.TARGET
    monkeypatch.setenv("PCC_NATIVE_OBJECT_GATE_RUN_ID", "host-control-run")
    calls, executions = [], []
    expected = tmp_path / "expected"
    expected_path = _win64_receipt(expected, runtime, "host-control-run")
    expected_binary = (expected_path.parent / "win64_home_callback.exe").read_bytes()
    work = tmp_path / "probe"
    work.mkdir()

    def compile_program(source, binary, **options):
        calls.append((source, binary, options))
        if failure == "compile":
            raise RuntimeError("deliberate compile failure")
        Path(binary).write_bytes(expected_binary)

    def execute(command, *, env, timeout):
        backend = int(env["PCC_GC_BACKEND"])
        executions.append((command, env, timeout))
        assert command == [str(work / "win64_home_callback.exe")] and timeout == 10
        if failure == "runtime-drift" and backend == 4:
            Path(runtime["path"]).write_bytes(b"changed runtime")
        return subprocess.CompletedProcess(command, 124 if failure == "collector" and backend == 2 else 0,
                                           stdout="WIN64_HOME_ROOT_CALLBACK_OK\n", stderr="")

    monkeypatch.setattr(pipeline, "compile_python", compile_program)
    monkeypatch.setattr(probe, "run_process_group_timeout", execute)
    monkeypatch.setattr(gate, "verify_native_image", lambda path: {"format": "pe", "libraries": ["kernel32.dll"]})
    capfd = SimpleNamespace(readouterr=lambda: SimpleNamespace(out="", err=""))
    if failure is None:
        probe.test_native_win64_home_space_and_rooted_callback_five_collectors(work, Path(runtime["path"]), capfd)
        verified = gate.verify_win64_callback_receipt(work, runtime, "host-control-run")
        assert verified["status"] == "PASS" and verified["compiler_mode"] == "host-pcc0"
    else:
        with pytest.raises((AssertionError, RuntimeError)):
            probe.test_native_win64_home_space_and_rooted_callback_five_collectors(work, Path(runtime["path"]), capfd)
    assert len(calls) == 1
    source, binary, options = calls[0]
    assert options == {"backend": "self", "ir_scaffold_mode": "on",
                       "runtime_archive": runtime["path"], "object_inputs": (str(work / "win64_home_callback.obj"),),
                       "target_triple": probe.TARGET}
    record = json.loads((work / "win64-home-callback.json").read_text())
    assert record["status"] == ("PASS" if failure is None else "FAIL")
    assert len(executions) == (0 if failure == "compile" else 3 if failure == "collector" else 5)
    assert "assert result == 616" in probe.MANAGED_CALLBACK_SOURCE
    assert 'assert caller_only[0]["answer"] == 73' in probe.MANAGED_CALLBACK_SOURCE
    assert 'assert collector() == int(os.environ["PCC_GC_BACKEND"])' in probe.MANAGED_CALLBACK_SOURCE


@pytest.mark.parametrize("label", ("two-root-cleanup", "two-root-tracing"))
@pytest.mark.parametrize("change", (
    "stale-run", "missing-run", "binary-path", "requested-collectors", "boolean-collector",
    "reference-mode", "native-cycle", "missing-ir", "ir-digest", "extra-module",
    "missing-module", "duplicate-module", "swapped-ir", "wrong-cleanup", "wrong-map",
    "wrong-module-map", "duplicate-receipt", "duplicate-json-key", "unpaired", "swapped-source",
))
def test_two_root_split_receipts_fail_closed(tmp_path, monkeypatch, admitted, label, change):
    _source, runtime = admitted
    directory = tmp_path / "receipts"
    paths = _receipts(directory, runtime)
    path = next(path for path in paths if _receipt_label(path) == label)
    record = json.loads(path.read_text())
    module = path.parent / "executable-ir/self_backend_input_0.ll"
    if change == "stale-run":
        record["run_id"] = "old-run"
    elif change == "missing-run":
        record.pop("run_id")
    elif change == "binary-path":
        record["binary_path"] = str(path.parent.parent / "wrong.out")
    elif change == "requested-collectors":
        record["gc_backends"] = list(range(5))
    elif change == "boolean-collector":
        value = bool(record["gc_backends"][0])
        record["gc_backends"][0] = value
        record["executions"][0]["gc_backend"] = value
    elif change == "reference-mode":
        record["reference"]["command"] = [sys.executable, str(path.parent / "program.py")]
        if label == "two-root-cleanup":
            record["reference"]["command"].append("cyclic-reference")
    elif change == "native-cycle":
        record["executions"][0]["command"].append("cyclic-reference")
    elif change == "missing-ir":
        record.pop("executable_input_ir")
    elif change == "ir-digest":
        record["executable_input_ir"][module.name] = "wrong"
    elif change == "extra-module":
        module.with_name("unrecorded.ll").write_text("foreign module")
    elif change == "missing-module":
        module.unlink()
    elif change == "duplicate-module":
        extra = module.with_name("self_backend_input_1.ll")
        extra.write_bytes(module.read_bytes())
        record["executable_input_ir"][extra.name] = gate.sha(extra)
    elif change == "swapped-ir":
        other = next(other for other in paths if _receipt_label(other) in
                     {"two-root-cleanup", "two-root-tracing"} - {label})
        module.write_bytes((other.parent / "executable-ir" / module.name).read_bytes())
        record["executable_input_ir"][module.name] = gate.sha(module)
    elif change in ("wrong-cleanup", "wrong-map", "wrong-module-map"):
        text = module.read_text()
        if change == "wrong-cleanup":
            text = text.replace("@py_cleanup_two_roots_preserving_exception", "@missing_cleanup")
        else:
            text = text.replace("constant i32 1", "constant i32 2")
        module.write_text(text)
        record["executable_input_ir"][module.name] = gate.sha(module)
        if change == "wrong-module-map":
            extra = module.with_name("self_backend_input_1.ll")
            extra.write_text("@map = internal constant i32 1\n")
            record["executable_input_ir"][extra.name] = gate.sha(extra)
    elif change == "swapped-source":
        other_label = "two-root-tracing" if label == "two-root-cleanup" else "two-root-cleanup"
        source = path.parent / "program.py"
        source.write_text(gate.expected_programs()[other_label][0])
        record["source_sha256"] = gate.sha(source)
    path.write_text(json.dumps(record))
    if change == "duplicate-json-key":
        path.write_text(path.read_text().replace('{', '{"status": "PASS", ', 1))
    elif change == "duplicate-receipt":
        extra = directory / "duplicate"
        extra.mkdir()
        (extra / path.name).write_text(path.read_text())
    elif change == "unpaired":
        extra = directory / "unpaired"
        extra.mkdir()
        moved = path.parent.rename(extra / path.parent.name)
        # Rebind paths so the sibling-pair invariant, not a stale path alone,
        # must reject independently valid evidence from another test root.
        moved_record = json.loads((moved / path.name).read_text())
        moved_record["binary_path"] = str(moved / "program.out")
        moved_record["reference"]["command"][1] = str(moved / "program.py")
        for row in moved_record["executions"]:
            row["command"] = [str(moved / "program.out")]
        (moved / path.name).write_text(json.dumps(moved_record))
    with pytest.raises((AssertionError, KeyError, FileNotFoundError)):
        gate.verify_program_receipts(directory, runtime, RUN_ID)


@pytest.mark.parametrize("change", (
    "missing", "duplicate", "stale", "schema", "pcc1", "routed-pcc1", "llvm", "target",
    "source", "source-digest", "runtime-path", "runtime-digest", "binary-path", "binary-digest",
    "ir-digest", "object-digest", "object-producer", "first-slot", "second-slot", "tls",
    "ir-crlf", "object-with-rehashed-digest", "missing-gc", "duplicate-gc", "boolean-gc",
    "failed-gc", "wrong-output", "stderr", "native-command", "linkage", "linkage-digest",
))
def test_two_root_native_slot_postcondition_is_mandatory(tmp_path, monkeypatch, admitted, change):
    _source, runtime = admitted
    directory = tmp_path / "receipts"
    _receipts(directory, runtime)
    path = directory / "two-root/slots/slot-postcondition.json"
    record = json.loads(path.read_text())
    fields = {
        "stale": ("run_id", "old-run"), "schema": ("schema", "other"),
        "pcc1": ("compiler_fixture_parameter", "pcc1"), "routed-pcc1": ("compiler_mode", "pcc1-routed"),
        "llvm": ("backend", "llvm"), "target": ("target", "other-target"),
        "source-digest": ("source_sha256", "wrong"), "runtime-path": ("runtime_archive", "other.a"),
        "runtime-digest": ("runtime_sha256", "wrong"), "binary-path": ("binary_path", "other.out"),
        "binary-digest": ("binary_sha256", "wrong"), "ir-digest": ("probe_ir_sha256", "wrong"),
        "object-digest": ("probe_object_sha256", "wrong"), "object-producer": ("probe_object_producer", "llvm"),
    }
    if change in fields:
        key, value = fields[change]
        record[key] = value
    elif change == "source":
        source = path.parent / "slots.py"
        source.write_text("print('TWO_ROOT_SLOTS_OK')\n")
        record["source_sha256"] = gate.sha(source)
    elif change in ("first-slot", "second-slot", "tls", "ir-crlf"):
        probe_ir = path.parent / "slots.ll"
        data = probe_ir.read_bytes()
        if change == "ir-crlf":
            data = data.replace(b"\n", b"\r\n")
        else:
            original = {"first-slot": b"%first_empty = icmp eq ptr %first_after, null",
                        "second-slot": b"%second_empty = icmp eq ptr %second_after, null",
                        "tls": b"%same_exception = icmp eq ptr %pending, %selected"}[change]
            assert original in data
            data = data.replace(original, original.replace(b"icmp eq ptr", b"icmp ne ptr"))
        probe_ir.write_bytes(data)
        record["probe_ir_sha256"] = gate.sha(probe_ir)
    elif change == "object-with-rehashed-digest":
        helper = path.parent / "slots.o"
        helper.write_bytes(helper.read_bytes() + b"wrong object")
        record["probe_object_sha256"] = gate.sha(helper)
    elif change == "missing-gc":
        record["executions"].pop()
    elif change == "duplicate-gc":
        record["executions"][-1]["gc_backend"] = 0
    elif change == "boolean-gc":
        record["gc_backends"][1] = record["executions"][1]["gc_backend"] = True
    elif change == "failed-gc":
        record["executions"][2]["returncode"] = 1
    elif change == "wrong-output":
        record["executions"][2]["stdout"] = "wrong\n"
    elif change == "stderr":
        record["executions"][2]["stderr"] = "unexpected diagnostic"
    elif change == "native-command":
        record["executions"][2]["command"] = [sys.executable]
    elif change == "linkage":
        record["linkage"]["libpython"] = "on"
    elif change == "linkage-digest":
        record["linkage"]["binary_sha256"] = "wrong"
    path.write_text(json.dumps(record))
    if change == "missing":
        path.unlink()
    elif change == "duplicate":
        extra = directory / "duplicate"
        extra.mkdir()
        (extra / path.name).write_text(path.read_text())
    with pytest.raises((AssertionError, KeyError, FileNotFoundError)):
        gate.verify_program_receipts(directory, runtime, RUN_ID)


def test_two_root_encoded_markers_and_legacy_receipts_keep_distinct_contracts(tmp_path, admitted):
    _source, runtime = admitted
    directory = tmp_path / "receipts"
    paths = _receipts(directory, runtime)
    for path in paths:
        if _receipt_label(path) in ("two-root-cleanup", "two-root-tracing"):
            text = (path.parent / "executable-ir/self_backend_input_0.ll").read_text()
            assert "TWO_ROOT_" not in text and r"\54\57\4F\5F\52\4F\4F\54" in text
        else:
            record = json.loads(path.read_text())
            for key in ("run_id", "binary_path", "gc_backends"):
                record.pop(key)
            path.write_text(json.dumps(record))
    result = gate.verify_program_receipts(directory, runtime, RUN_ID)
    assert len(result) == 7
    assert result["two-root-cleanup"]["reference"]["command"][-1].endswith("program.py")
    assert result["two-root-tracing"]["reference"]["command"][-1] == "cyclic-reference"
