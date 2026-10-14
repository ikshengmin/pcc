"""Host-only controls for the post-Stage1 native object gate; no native build."""

from __future__ import annotations

import ast
import copy
import json
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


def _receipts(directory, runtime):
    paths = []
    for label, (program, output) in gate.expected_programs().items():
        work = directory / label
        work.mkdir(parents=True)
        source = work / "program.py"
        source.write_text(program, encoding="utf-8")
        binary = work / "program.out"
        binary.write_bytes(b"host-only receipt control; never executed")
        oracle = {"command": [sys.executable, str(source)],
                  "returncode": 0, "stdout": output, "stderr": ""}
        record = {
            "status": "PASS", "compiler_fixture_parameter": "pcc0", "compiler_mode": "pcc0",
            "backend": "self", "libpython": "off", "source_sha256": gate.sha(source),
            "runtime_archive": runtime["path"], "runtime_sha256": runtime["hashes"][""],
            "binary_sha256": gate.sha(binary), "reference": oracle,
            "executions": [{**oracle, "command": [str(binary)], "gc_backend": gc}
                           for gc in range(5)],
        }
        path = work / "ownership-regression.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        paths.append(path)
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
    return source, runtime


@pytest.mark.parametrize("stage_status", ("PASS", "FAIL"))
def test_gate_reuses_terminal_runtime_and_records_ten_executions(tmp_path, monkeypatch, admitted, stage_status):
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
    assert set(result["programs"]) == {"object-identity", "super-result"}
    assert sum(len(row["executions"]) for row in result["programs"].values()) == 10
    for row in result["programs"].values():
        assert [item["requested_gc_backend"] for item in row["executions"]] == list(range(5))
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
    "missing-receipt", "failed-receipt", "pcc1", "routed-pcc1", "llvm", "libpython",
    "source-digest", "runtime-path", "runtime-digest", "binary-digest", "oracle",
    "missing-gc", "duplicate-gc", "failed-gc", "wrong-output", "stderr", "wrong-command",
    "foreign-program", "duplicate-program",
))
def test_native_evidence_requires_exact_source_and_all_five_successes(tmp_path, monkeypatch, admitted, change):
    _source, runtime = admitted
    directory = tmp_path / "receipts"
    paths = _receipts(directory, runtime)
    path = paths[0]
    record = json.loads(path.read_text())
    fields = {"failed-receipt": ("status", "FAIL"), "pcc1": ("compiler_fixture_parameter", "pcc1"),
              "routed-pcc1": ("compiler_mode", "pcc1-routed"), "llvm": ("backend", "llvm"),
              "libpython": ("libpython", "on"), "source-digest": ("source_sha256", "wrong"),
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
        record["executions"][-1]["gc_backend"] = 0
    elif change == "failed-gc":
        record["executions"][2]["returncode"] = 124
    elif change == "wrong-output":
        record["executions"][2]["stdout"] = "wrong"
    elif change == "stderr":
        record["executions"][2]["stderr"] = "unexpected diagnostic"
    elif change == "wrong-command":
        record["executions"][2]["command"] = [sys.executable]
    elif change == "foreign-program":
        (path.parent / "program.py").write_text("print('unrelated')")
    elif change == "duplicate-program":
        other = paths[1].parent / "program.py"
        other.write_text((path.parent / "program.py").read_text())
    path.write_text(json.dumps(record))
    if change == "missing-receipt":
        path.unlink()
    with pytest.raises(AssertionError):
        gate.verify_program_receipts(directory, runtime)


@pytest.mark.parametrize("machine", (62, 183))
@pytest.mark.parametrize("program_header", (1, 2, 3))
def test_elf_admission_checks_program_headers_without_external_tools(tmp_path, machine, program_header):
    from pcc.backend.elf_x86_64 import ElfError

    payload = bytearray(128)
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    struct.pack_into("<16sHHIQQQIHHHHHH", payload, 0,
                     ident, 2, machine, 1, 0x400000, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    struct.pack_into("<IIQQQQQQ", payload, 64,
                     program_header, 5, 0, 0x400000, 0x400000, 128, 128, 4096)
    binary = tmp_path / "program.out"
    binary.write_bytes(payload)
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
        assert options == {"backend": "self", "libpython_mode": "off",
                           "ir_scaffold_mode": "on", "runtime_archive": str(archive)}
        Path(binary).write_bytes(b"host-only executable placeholder")

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
    name = "Verify object identity and super results with the existing runtime"
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
                       "PCC_WITH_THREADS": "0", "PCC_REFCOUNT_KIND": "atomic",
                       "PCC_PYTEST_AUTO_CLEAN": "0", "UV_PYTHON_DOWNLOADS": "never"}.items():
        assert f'{key}: "{value}"' in step
    command = "uv run --frozen --no-sync --python 3.13 python scripts/ci_native_object_regressions.py --out-dir build/worker-prior-gate"
    assert "run: " + command in step
    assert "download-artifact" not in step and "continue-on-error" not in step
    assert "tests/test_ci_native_object_regressions.py\n" in other
    for path in (*[node.split("::")[0] for node in gate.NODES],
                 "scripts/ci_native_object_regressions.py", "tests/test_ci_native_object_regressions.py"):
        assert text.count('      - "' + path + '"') == 2
    for suffix in ("json", "jsonl", "stdout", "stderr", "tsv", "xml"):
        assert "build/worker-prior-gate/**/*." + suffix in other
    assert gate.TIMEOUT_S == 300 and len(gate.NODES) == 2
    assert "[pcc0]" in gate.NODES[0] and all("pcc1" not in node for node in gate.NODES)
    command = prior.pytest_command(tmp_path, gate.NODES, integration=True)
    assert command[-4:] == ["-m", "integration", *gate.NODES]
    assert "-x" in command
    assert not any(option in command for option in ("-n", "-n0", "--dist", "-o", "--override-ini"))
    assert '-n 6 --dist=loadgroup' in (ROOT / "pyproject.toml").read_text()
