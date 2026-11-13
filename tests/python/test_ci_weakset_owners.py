"""CI-only explicit owner coverage; the peer regression corpus stays intact."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import ci_weakset_owners as coverage
from tests.python import test_weakset_weakref_order_native as peer


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_CI_STAGE1_MANIFEST")
@pytest.mark.parametrize("owner", ["pcc0", "pcc1"])
@pytest.mark.parametrize("program_name", ["weakset_order", "dynamic_set"])
def test_ci_weakset_explicit_owners(tmp_path, pcc_runtime_archive, owner, program_name):
    if program_name == "weakset_order":
        program = peer.PROGRAM
        cases = [(backend, mode, peer._expected(backend, mode))
                 for backend in range(5) for mode in ((), ("cyclic",))]
    else:
        program = peer.DYNAMIC_SET_PROGRAM
        cases = [(backend, (), peer.EXPECTED_DYNAMIC_SET) for backend in range(5)]
    coverage.run_case(tmp_path, pcc_runtime_archive, owner, program_name, program, cases)


def test_ci_weakset_host_command_is_a_fresh_cpython_not_fixture_label():
    command = coverage.compiler_command("pcc0", Path("native-pcc1"), Path("source.py"),
                                        Path("binary"), Path("runtime.a"), Path("owner.json"))
    assert command[0] == coverage.sys.executable
    assert command[1:3] == ["-c", coverage.HOST_COMPILE]
    assert "from pcc.frontends.python.pipeline import compile_python" in command[2]
    assert "compile_python.__code__.co_filename" in command[2]
    assert "pytest" not in command
    assert "native-pcc1" not in command


def test_ci_weakset_native_command_uses_admitted_executable():
    command = coverage.compiler_command("pcc1", Path("native-pcc1"), Path("source.py"),
                                        Path("binary"), Path("runtime.a"), Path("owner.json"))
    assert command == ["native-pcc1", "--backend", "self", "--ir-scaffold", "on",
                       "source.py", "-o", "binary"]
    with pytest.raises(ValueError, match="unknown compiler owner"):
        coverage.compiler_command("label-only", None, None, None, None, None)


@pytest.mark.parametrize("owner", ["pcc0", "pcc1"])
def test_ci_weakset_environment_removes_routes_without_mutating_parent(owner):
    original = {"PCC_TEST_COMPILER": "native", "PCC_PLATFORM_TEST_COMPILER": "native",
                "PCC_CURRENT_PCC1": "native", "PCC_REQUIRE_CURRENT_PCC1": "1",
                "PCC_TEST_COMPILER_LOG": "log", "PCC_TEST_COMPILER_STRICT": "1",
                "PCC_TEST_COMPILER_ROUTED": "1",
                "PCC_PYTHON_IR_PASSES": "off", "KEEP": "value"}
    env = coverage.compiler_environment(original, Path("runtime.a"), owner)
    assert env["KEEP"] == "value"
    assert original["PCC_TEST_COMPILER"] == "native"
    for name in original:
        if name != "KEEP":
            assert name not in env
    assert env["PCC_RUNTIME_ARCHIVE"] == "runtime.a"
    assert env["PCC_NO_AUTO_PCC1"] == "1"
    assert env["PCC_HOST_PYTHON"] == (
        coverage.sys.executable if owner == "pcc0" else "/usr/bin/false")
    assert env["PCC_HOST_PCC"] == env["PCC_RUNTIME_CC"] == "/usr/bin/false"
    assert env["PCC_DEBUG_WORKER_RERAISE"] == "1"


def test_ci_weakset_environment_rejects_an_unknown_owner():
    with pytest.raises(ValueError, match="unknown compiler owner"):
        coverage.compiler_environment({}, Path("runtime.a"), "label-only")


@pytest.mark.parametrize("backend", range(5))
def test_ci_weakset_requires_observed_collector_and_semantic_output(backend):
    correct = SimpleNamespace(returncode=0, stdout=f"semantic\nPCC_OBSERVED_GC {backend}\n", stderr="")
    coverage.check_output(correct, "semantic\n", backend)
    for stdout in ("semantic\n", f"semantic\nPCC_OBSERVED_GC {(backend + 1) % 5}\n",
                   f"wrong\nPCC_OBSERVED_GC {backend}\n"):
        with pytest.raises(AssertionError):
            coverage.check_output(SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
                                  "semantic\n", backend)
    for code, stderr in ((1, ""), (0, "warning")):
        with pytest.raises(AssertionError):
            coverage.check_output(SimpleNamespace(returncode=code, stdout=correct.stdout, stderr=stderr),
                                  "semantic\n", backend)


def test_ci_weakset_collector_query_follows_unchanged_peer_body():
    assert coverage.COLLECTOR_TRAILER == (
        '\nfrom pcc.unsafe import gc_backend_current\n'
        'print("PCC_OBSERVED_GC", gc_backend_current())\n')
    for program in (peer.PROGRAM, peer.DYNAMIC_SET_PROGRAM):
        assert (program + coverage.COLLECTOR_TRAILER).startswith(program)


def test_ci_weakset_reference_owner_enables_existing_opt_in():
    source = Path(coverage.ROOT / "scripts/ci_pytest_markers.py").read_text()
    assert 'PCC_RUN_CPYTHON_REFERENCE="1"' in source


def test_ci_weakset_workflow_keeps_trigger_and_failure_evidence():
    source = (coverage.ROOT / ".github/workflows/pcc1-package-parity.yml").read_text()
    for path in ("scripts/ci_weakset_owners.py", "tests/**"):
        assert source.count('      - "' + path + '"') == 2
    assert "            build/pytest-markers/**/weakset-owners/**\n" in source


def test_ci_weakset_windows_timeout_retains_bytes_and_streamed_stderr(tmp_path):
    stderr = tmp_path / "compile.stderr"
    stderr.write_text("durable compiler diagnostics\n")
    error = coverage.subprocess.TimeoutExpired(["compiler"], 400, output=b"partial\xff\n")
    coverage.save_timeout_output(tmp_path, error)
    assert (tmp_path / "compile.stdout").read_text() == "partial\ufffd\n"
    assert stderr.read_text() == "durable compiler diagnostics\n"
    error = coverage.subprocess.TimeoutExpired(["compiler"], 400, output="partial\n", stderr=b"error\n")
    coverage.save_timeout_output(tmp_path, error)
    assert (tmp_path / "compile.stdout").read_text() == "partial\n"
    assert stderr.read_text() == "error\n"
    assert coverage.timeout_fields(error) == {
        "returncode": None, "outcome": "timeout", "timeout_s": 400,
        "stdout": "partial\n", "stderr": "error\n",
    }


@pytest.mark.parametrize("owner", ["pcc0", "pcc1"])
@pytest.mark.parametrize("valid_output", [True, False, "timeout", "compile-timeout"])
def test_ci_weakset_receipt_records_execution_or_semantic_failure(
    tmp_path, monkeypatch, owner, valid_output,
):
    from tests.python import process_timeout

    monkeypatch.setattr(coverage, "ROOT", tmp_path)
    compiler = tmp_path / "native-pcc1"
    compiler.write_bytes(b"admitted compiler")
    runtime = tmp_path / "runtime.a"
    runtime.write_bytes(b"admitted runtime")
    pipeline = tmp_path / "pcc/frontends/python/pipeline.py"
    pipeline.parent.mkdir(parents=True)
    pipeline.write_text("# admitted host pipeline\n")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"source": {}, "producer": {}, "platform": "test"}))
    monkeypatch.setenv("PCC_CI_STAGE1_MANIFEST", str(manifest))
    monkeypatch.setenv("PCC_MARKER_REPORT_DIR", str(tmp_path / "reports"))
    admissions = []

    def admit(root, path, archive):
        admissions.append((root, path, archive))
        return compiler

    monkeypatch.setattr(coverage, "admitted_stage1", admit)
    binary = tmp_path / ("program.exe" if coverage.sys.platform == "win32" else "program")
    directory = tmp_path / "reports/weakset-owners" / (owner + "-program")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == str(binary):
            if valid_output == "timeout":
                raise coverage.subprocess.TimeoutExpired(command, 20, output=b"partial\n", stderr=b"error\n")
            observed = 0 if valid_output else 1
            return SimpleNamespace(returncode=0, stdout=f"semantic\nPCC_OBSERVED_GC {observed}\n", stderr="")
        if valid_output == "compile-timeout":
            (directory / "compile.stderr").write_text("streamed diagnostics\n")
            raise coverage.subprocess.TimeoutExpired(command, 400, output=b"partial compile\n")
        binary.write_bytes(b"emitted executable")
        if owner == "pcc0":
            (directory / "host-implementation.json").write_text(json.dumps({
                "implementation_sha256": coverage.digest(pipeline),
            }))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(process_timeout, "run_process_group_timeout", run)
    monkeypatch.setattr(process_timeout, "run_process_tree_timeout", run)
    args = (tmp_path, runtime, owner, "program", "print('semantic')\n", [(0, (), "semantic\n")])
    if valid_output in ("timeout", "compile-timeout"):
        with pytest.raises(coverage.subprocess.TimeoutExpired):
            coverage.run_case(*args)
    elif valid_output:
        coverage.run_case(*args)
    else:
        with pytest.raises(AssertionError):
            coverage.run_case(*args)
    receipt = json.loads((directory / "receipt.json").read_text())
    assert receipt["status"] == ("PASS" if valid_output is True else "FAIL")
    assert len(calls) == (1 if valid_output == "compile-timeout" else 2)
    assert calls[0][0][0] == (coverage.sys.executable if owner == "pcc0" else str(compiler))
    assert calls[0][1]["env"]["PCC_HOST_PYTHON"] == (
        coverage.sys.executable if owner == "pcc0" else "/usr/bin/false")
    if valid_output == "compile-timeout":
        assert receipt["runs"] == []
        assert len(admissions) == 1
        assert (directory / "compile.stdout").read_text() == "partial compile\n"
        assert (directory / "compile.stderr").read_text() == "streamed diagnostics\n"
        return
    assert calls[1][1]["env"]["PCC_GC_BACKEND"] == "0"
    assert receipt["runs"][0]["requested_gc"] == 0
    assert ("observed_gc" in receipt["runs"][0]) is (valid_output is True)
    assert len(admissions) == (2 if valid_output is True else 1)
    if valid_output == "timeout":
        assert receipt["runs"][0]["outcome"] == "timeout"
        assert receipt["runs"][0]["stdout"] == "partial\n"
        assert receipt["runs"][0]["stderr"] == "error\n"
        assert (directory / "compile.stdout").read_text() == ""
