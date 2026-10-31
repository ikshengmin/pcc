"""Real project corpus: reference success is not PCC/native qualification."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.cpp.owned_execution import Execution, assert_exact
from tests.cpp.project_cases import (
    PROJECT,
    assert_project_outcome,
    compile_project_native,
    compile_project_oracle,
    load_project,
    materialize_project,
    native_project_environment,
    run_project,
    verify_materialized_project,
)
from tests.integration.test_owned_platform_native import native_compiler


def test_cpp_project_configuration_selects_versioned_inputs_and_expected_outcome():
    fixture = load_project()
    assert fixture.variant == "smoke"
    assert fixture.revision == "9148bdf719e997d1f474be6bcc7943881046dba1"
    assert fixture.standard == "c++17"
    assert fixture.translation_units == ("tinyxml2.cpp",)
    assert fixture.driver.name == "tinyxml2_smoke.cpp"
    assert (
        fixture.expected.returncode,
        fixture.expected.stdout,
        fixture.expected.stderr,
    ) == (
        0,
        b"tinyxml2:42:hello&world\n",
        b"",
    )
    assert {path.name for path in fixture.files} == {
        "tinyxml2.cpp",
        "tinyxml2.h",
        "LICENSE.txt",
    }


def test_cpp_project_fixture_copy_retains_exact_upstream_bytes(tmp_path):
    fixture = load_project()
    destination = tmp_path / "source"
    units = materialize_project(fixture, destination)
    assert [path.name for path in units] == ["tinyxml2.cpp", "pcc_test_main.cpp"]
    for path in fixture.files:
        assert (destination / path.name).read_bytes() == path.read_bytes()


def test_cpp_project_fixture_rejects_missing_source(tmp_path):
    fixture = load_project()
    materialize_project(fixture, tmp_path / "source")
    (tmp_path / "source/pcc-fixture.json").write_bytes(
        (PROJECT / "pcc-fixture.json").read_bytes()
    )
    (tmp_path / "source/tinyxml2.cpp").unlink()
    with pytest.raises(AssertionError, match="missing project input"):
        load_project(tmp_path / "source")


def test_cpp_project_upstream_selection_keeps_original_main_and_all_resources():
    fixture = load_project(variant="upstream-tests")
    assert fixture.variant == "upstream-tests"
    assert fixture.translation_units == ("tinyxml2.cpp", "xmltest.cpp")
    assert fixture.driver is None
    assert fixture.driver_sha256 is None
    assert fixture.preprocessor_definitions == ("_FILE_OFFSET_BITS=64",)
    assert fixture.expected.stdout is None
    assert fixture.expected.stderr is None
    configuration = json.loads((PROJECT / "pcc-fixture.json").read_text())
    assert set(dict(fixture.input_sha256)) == set(configuration["sha256"])
    resources = {
        path.relative_to(PROJECT).as_posix()
        for path in (PROJECT / "resources").rglob("*")
        if path.is_file()
    }
    assert resources == {
        "resources/dream.xml",
        "resources/empty.xml",
        "resources/out/readme.txt",
        "resources/utf8test.xml",
        "resources/utf8testverify.xml",
        "resources/xmltest-4636783552757760.xml",
        "resources/xmltest-5330.xml",
        "resources/xmltest-5662204197076992.xml",
        "resources/xmltest-5720541257269248.xml",
    }
    assert resources.issubset(dict(fixture.input_sha256))


def test_cpp_project_upstream_copy_preserves_nested_binary_inputs(tmp_path):
    fixture = load_project(variant="upstream-tests")
    destination = tmp_path / "source"
    units = materialize_project(fixture, destination)
    assert [path.name for path in units] == ["tinyxml2.cpp", "xmltest.cpp"]
    assert not (destination / "pcc_test_main.cpp").exists()
    for name, _digest in fixture.input_sha256:
        assert (destination / name).read_bytes() == (PROJECT / name).read_bytes()
    # The two CMakeLists.txt files have different upstream contents; a basename
    # flattening copy would silently lose one of them.
    assert (destination / "CMakeLists.txt").read_bytes() != (
        destination / "test/CMakeLists.txt"
    ).read_bytes()
    (destination / "resources/out/generated.xml").write_bytes(b"test output")
    verify_materialized_project(fixture, destination)
    fixture.verify()
    assert not (PROJECT / "resources/out/generated.xml").exists()


def _copied_configuration(tmp_path, *, variant="upstream-tests"):
    fixture = load_project(variant=variant)
    destination = tmp_path / "source"
    materialize_project(fixture, destination)
    manifest = destination / "pcc-fixture.json"
    manifest.write_bytes((PROJECT / "pcc-fixture.json").read_bytes())
    return destination, manifest


def test_cpp_project_upstream_rejects_resource_tampering(tmp_path):
    destination, _manifest = _copied_configuration(tmp_path)
    (destination / "resources/empty.xml").write_bytes(b"not empty")
    with pytest.raises(AssertionError, match="input SHA256 mismatch"):
        load_project(destination, variant="upstream-tests")


@pytest.mark.parametrize("changed", ["manifest", "resource"])
def test_cpp_project_rechecks_manifest_and_input_after_selection(tmp_path, changed):
    destination, manifest = _copied_configuration(tmp_path)
    fixture = load_project(destination, variant="upstream-tests")
    if changed == "manifest":
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        diagnostic = "manifest changed"
    else:
        (destination / "resources/empty.xml").write_bytes(b"changed")
        diagnostic = "input SHA256 mismatch"
    with pytest.raises(AssertionError, match=diagnostic):
        materialize_project(fixture, tmp_path / "must-not-copy")
    assert not (tmp_path / "must-not-copy").exists()


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "resources/../escape",
        "resources//empty.xml",
        "resources/./empty.xml",
        "C:/escape",
        "resources\\escape",
        "",
    ],
)
def test_cpp_project_rejects_unsafe_nested_paths(tmp_path, name):
    configuration = json.loads((PROJECT / "pcc-fixture.json").read_text())
    configuration["fixtures"]["upstream-tests"]["inputs"].append(name)
    configuration["sha256"][name] = "0" * 64
    (tmp_path / "pcc-fixture.json").write_text(json.dumps(configuration))
    with pytest.raises(ValueError, match="canonical relative paths"):
        load_project(tmp_path, variant="upstream-tests")


@pytest.mark.parametrize("digest", [None, "", "0" * 63, "G" * 64])
def test_cpp_project_rejects_missing_or_invalid_pins(tmp_path, digest):
    configuration = json.loads((PROJECT / "pcc-fixture.json").read_text())
    if digest is None:
        del configuration["sha256"]["resources/empty.xml"]
    else:
        configuration["sha256"]["resources/empty.xml"] = digest
    (tmp_path / "pcc-fixture.json").write_text(json.dumps(configuration))
    with pytest.raises(ValueError, match="SHA256"):
        load_project(tmp_path, variant="upstream-tests")


@pytest.mark.parametrize(
    "outcome",
    [
        Execution(1, b"Pass 10, Fail 0\n", b""),
        Execution(0, b"", b""),
        Execution(0, b"Test file loaded. ErrorID=0\n", b""),
        Execution(0, b"Pass 0, Fail 0\n", b""),
        Execution(0, b"Pass 10, Fail 1\n", b""),
        Execution(0, b"Pass 10, Fail 01\n", b""),
        Execution(0, b"Pass 10, Fail 0\nPass 10, Fail 0\n", b""),
        Execution(0, b"Pass 10, Fail 1\nPass 10, Fail 0\n", b""),
    ],
)
def test_cpp_project_upstream_summary_cannot_hide_failure_or_unrun_tests(outcome):
    with pytest.raises(AssertionError):
        assert_project_outcome(outcome, load_project(variant="upstream-tests"))


def test_cpp_project_upstream_matches_summary_not_performance_clock_bytes():
    fixture = load_project(variant="upstream-tests")
    first = Execution(0, b"Parsing dream.xml: 1.000\n\nPass 10, Fail 0\n", b"")
    second = Execution(0, b"Parsing dream.xml: 2.000\r\n\r\nPass 10, Fail 0\r\n", b"")
    assert first != second
    assert assert_project_outcome(first, fixture) == assert_project_outcome(
        second, fixture
    ) == {"matcher": "upstream-summary", "passed": "10", "failed": "0"}


def test_cpp_project_upstream_does_not_invent_empty_stderr_contract():
    fixture = load_project(variant="upstream-tests")
    actual = Execution(0, b"Pass 10, Fail 0\n", b"upstream diagnostic\n")
    assert assert_project_outcome(actual, fixture)["failed"] == "0"
    with pytest.raises(AssertionError, match="stderr mismatch"):
        assert_project_outcome(
            Execution(0, b"tinyxml2:42:hello&world\n", actual.stderr), load_project()
        )


def test_cpp_project_runner_uses_clean_cwd_no_arguments_and_raw_receipts(
    tmp_path, monkeypatch
):
    fixture = load_project(variant="upstream-tests")
    binary = tmp_path / "mock-xmltest"
    binary.write_bytes(b"mock, never executed")
    calls = []

    def fake_run(command, *, cwd, capture_output, text, timeout):
        calls.append(cwd)
        assert command == [str(binary)]
        assert capture_output and not text and timeout == 60
        assert (cwd / "xmltest.cpp").read_bytes() == (
            PROJECT / "xmltest.cpp"
        ).read_bytes()
        assert (cwd / "resources/out/readme.txt").is_file()
        assert not (cwd / "resources/out/generated.xml").exists()
        (cwd / "resources/out/generated.xml").write_bytes(b"output")
        return subprocess.CompletedProcess(
            command, 0, b"timing\nPass 10, Fail 0\n", b"upstream diagnostic\n"
        )

    monkeypatch.setattr("tests.cpp.project_cases.subprocess.run", fake_run)
    for name in ("first", "second"):
        root = tmp_path / name
        actual = run_project(fixture, binary, root, owner="external-reference")
        assert (root / "stdout").read_bytes() == actual.stdout
        assert (root / "stderr").read_bytes() == actual.stderr
        receipt = json.loads((root / "execution.json").read_text())
        assert receipt["status"] == "passed-upstream-tests"
        assert receipt["owner"] == "external-reference"
        assert receipt["variant"] == "upstream-tests"
        assert receipt["binary_unchanged"]
        assert receipt["outcome"]["passed"] == "10"
    assert calls == [tmp_path / "first/source", tmp_path / "second/source"]
    fixture.verify()


@pytest.mark.parametrize("failure", ["timeout", "exit", "summary"])
def test_cpp_project_runner_keeps_failure_and_partial_output_receipts(
    tmp_path, monkeypatch, failure
):
    fixture = load_project(variant="upstream-tests")
    binary = tmp_path / "mock-xmltest"
    binary.write_bytes(b"mock, never executed")

    def fake_run(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(
                command, 60, output=b"partial", stderr=b"error"
            )
        return subprocess.CompletedProcess(
            command, 1 if failure == "exit" else 0, b"partial", b""
        )

    monkeypatch.setattr("tests.cpp.project_cases.subprocess.run", fake_run)
    root = tmp_path / "failed"
    error = subprocess.TimeoutExpired if failure == "timeout" else AssertionError
    with pytest.raises(error):
        run_project(fixture, binary, root, owner="native-pcc1")
    receipt = json.loads((root / "execution.json").read_text())
    assert receipt["status"] == (
        "timeout" if failure == "timeout" else "executed-not-accepted"
    )
    assert receipt["owner"] == "native-pcc1"
    assert (root / "stdout").read_bytes() == b"partial"
    assert "error" in receipt


def test_cpp_project_reference_compiles_upstream_tus_not_smoke_driver(
    tmp_path, monkeypatch
):
    fixture = load_project(variant="upstream-tests")
    compiler = tmp_path / "mock-reference-c++"
    compiler.write_bytes(b"mock compiler, never executed")

    def fake_run(command, **kwargs):
        invocation = Path(command[0])
        assert invocation.is_absolute() and invocation.name == compiler.name
        assert invocation.samefile(compiler)
        assert "-std=c++17" in command and "-D_FILE_OFFSET_BITS=64" in command
        assert [Path(arg).name for arg in command if arg.endswith(".cpp")] == [
            "tinyxml2.cpp",
            "xmltest.cpp",
        ]
        Path(command[-1]).write_bytes(b"mock reference image, never executed")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr("tests.cpp.project_cases.subprocess.run", fake_run)
    # Make absolute before changing child cwd; keep the original driver basename.
    compile_project_oracle(fixture, tmp_path, compiler=os.path.relpath(compiler))
    receipt = json.loads((tmp_path / "reference-compile-receipt.json").read_text())
    assert receipt["owner"] == "external-reference"
    assert receipt["status"] == "compiled-not-executed"
    assert receipt["compiler_unchanged"]


@pytest.mark.parametrize("retargeted", [False, True])
def test_cpp_project_reference_preserves_cpp_driver_name_and_checks_target(
    tmp_path, monkeypatch, retargeted
):
    fixture = load_project(variant="upstream-tests")
    invocation = tmp_path / "mock-clang++"
    target = tmp_path / "mock-clang"
    replacement = tmp_path / "mock-other-clang"
    for path in (invocation, target, replacement):
        path.write_bytes(b"identical mock compiler bytes, never executed")
    # Model symlink resolution without requiring Windows symlink privileges.
    # The launch spelling and its resolved executable have different basenames.
    resolved_target = [target]
    original_resolve = Path.resolve

    def fake_resolve(path, *args, **kwargs):
        if path == invocation:
            return resolved_target[0]
        return original_resolve(path, *args, **kwargs)

    def fake_run(command, **kwargs):
        assert command[0] == str(invocation)
        assert Path(command[0]).name.endswith("++")
        Path(command[-1]).write_bytes(b"mock output")
        if retargeted:
            resolved_target[0] = replacement
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    monkeypatch.setattr("tests.cpp.project_cases.subprocess.run", fake_run)
    if retargeted:
        with pytest.raises(AssertionError, match="reference compiler changed"):
            compile_project_oracle(fixture, tmp_path, compiler=str(invocation))
    else:
        compile_project_oracle(fixture, tmp_path, compiler=str(invocation))
    receipt = json.loads((tmp_path / "reference-compile-receipt.json").read_text())
    assert receipt["compiler"] == str(invocation)
    assert receipt["compiler_resolved_target"] == str(target)
    assert receipt["compiler_unchanged"] is not retargeted
    assert receipt["status"] == (
        "input-changed" if retargeted else "compiled-not-executed"
    )


def test_cpp_project_runner_cannot_publish_pass_after_executable_changes(
    tmp_path, monkeypatch
):
    fixture = load_project(variant="upstream-tests")
    binary = tmp_path / "mock-xmltest"
    binary.write_bytes(b"original mock")

    def fake_run(command, **kwargs):
        binary.write_bytes(b"changed mock")
        return subprocess.CompletedProcess(command, 0, b"Pass 10, Fail 0\n", b"")

    monkeypatch.setattr("tests.cpp.project_cases.subprocess.run", fake_run)
    root = tmp_path / "changed"
    with pytest.raises(AssertionError, match="executable changed"):
        run_project(fixture, binary, root, owner="external-reference")
    receipt = json.loads((root / "execution.json").read_text())
    assert receipt["status"] == "input-changed"
    assert receipt["binary_unchanged"] is False


@pytest.mark.parametrize("phase", ["compile", "run"])
def test_cpp_project_cannot_accept_modified_materialized_input(
    tmp_path, monkeypatch, phase
):
    fixture = load_project(variant="upstream-tests")
    binary = tmp_path / "mock-compiler-or-runner"
    binary.write_bytes(b"mock, never executed")

    def fake_run(command, *, cwd, **kwargs):
        source = cwd if phase == "run" else cwd / "source"
        (source / "resources/empty.xml").write_bytes(b"changed")
        if phase == "compile":
            Path(command[-1]).write_bytes(b"mock compiled output")
        return subprocess.CompletedProcess(command, 0, b"Pass 10, Fail 0\n", b"")

    monkeypatch.setattr("tests.cpp.project_cases.subprocess.run", fake_run)
    with pytest.raises(AssertionError, match="materialized project input SHA256"):
        if phase == "compile":
            compile_project_oracle(fixture, tmp_path, compiler=str(binary))
        else:
            run_project(fixture, binary, tmp_path / "run", owner="external-reference")
    receipt_path = (
        tmp_path / "reference-compile-receipt.json"
        if phase == "compile"
        else tmp_path / "run/execution.json"
    )
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "input-changed"
    fixture.verify()


def test_cpp_project_native_environment_has_no_implicit_external_route(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CXX", "/some/g++")
    monkeypatch.setenv("PCC_HOST_PYTHON", "/some/python")
    environment = native_project_environment(tmp_path)
    assert list(Path(environment["PATH"]).iterdir()) == []
    assert environment["CXX"] == environment["CC"] == environment["PCC_HOST_PYTHON"]
    assert not Path(environment["CXX"]).exists()
    assert environment["PCC_SELF_OBJ"] == environment["PCC_SELF_LINK"] == "pcc"
    assert environment["PCC_NO_AUTO_PCC1"] == "1"


def test_cpp_project_native_missing_compiler_fails_before_output(tmp_path):
    with pytest.raises(AssertionError, match="native compiler missing"):
        compile_project_native(tmp_path / "missing-pcc1", load_project(), tmp_path)
    assert not (tmp_path / "native-project").exists()
    assert not (tmp_path / "source").exists()


def test_cpp_project_native_rejects_host_wrapper(tmp_path):
    wrapper = tmp_path / "fake-pcc1"
    wrapper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="not a shell/Python wrapper"):
        compile_project_native(wrapper, load_project(), tmp_path)
    assert not (tmp_path / "source").exists()


def test_cpp_project_native_upstream_command_keeps_owned_route(tmp_path, monkeypatch):
    # Contract-only mocks: no native compiler/runtime build or executable run.
    compiler = tmp_path / "mock-pcc1"
    compiler.write_bytes(b"\x7fELFmock compiler")
    archive = tmp_path / "mock-runtime.a"
    archive.write_bytes(b"mock runtime")
    monkeypatch.setattr("tests.cpp.project_cases.sys.platform", "linux")
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(archive))
    monkeypatch.setattr("scripts.bootstrap_platform.dependency_receipt", lambda _: {})
    monkeypatch.setattr(
        "tests.runtime_fixture_provenance._verified_test_runtime_archive",
        lambda _: (archive, {}),
    )

    def fake_run(command, *, cwd, env, log_path, timeout, rss_limit, native):
        assert command[0] == str(compiler)
        assert command[1:6] == [
            "--backend", "self", "--separate-tus", "--no-cache", "-O0"
        ]
        assert "--cpp-arg=-std=c++17" in command
        assert "--cpp-arg=-D_FILE_OFFSET_BITS=64" in command
        assert native and timeout == 300 and rss_limit == 4 * 1024**3
        assert list(Path(env["PATH"]).iterdir()) == []
        source = cwd / "source"
        assert (source / "xmltest.cpp").is_file()
        assert (source / "resources/empty.xml").is_file()
        assert not (source / "pcc_test_main.cpp").exists()
        Path(command[-1]).write_bytes(b"\x7fELFmock output")
        return {"mock": True}

    monkeypatch.setattr("scripts.platform_process_watchdog.run", fake_run)
    fixture = load_project(variant="upstream-tests")
    compile_project_native(compiler, fixture, tmp_path)
    receipt = json.loads((tmp_path / "native-compile-receipt.json").read_text())
    assert receipt["owner"] == "native-pcc1"
    assert receipt["variant"] == "upstream-tests"
    assert receipt["status"] == "compiled-not-executed"
    assert receipt["compiler_unchanged"] and receipt["runtime_unchanged"]


@pytest.mark.integration
def test_cpp_tinyxml2_explicit_cpp17_reference_oracle(tmp_path):
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 oracle unavailable; set PCC_CPP_ORACLE"
    fixture = load_project()
    binary = compile_project_oracle(fixture, tmp_path, compiler=compiler)
    first = run_project(
        fixture, binary, tmp_path / "first-run", owner="external-reference"
    )
    second = run_project(
        fixture, binary, tmp_path / "second-run", owner="external-reference"
    )
    assert_exact(first, second)


@pytest.mark.integration
def test_cpp_tinyxml2_native_linux_windows_project_exact_outcome(
    native_compiler, tmp_path
):
    # Deliberately NOT pcc_gate/skip/xfail. An absent/unsupported native C++ route
    # leaves project integration incomplete even if every external oracle passes.
    fixture = load_project()
    binary = compile_project_native(native_compiler, fixture, tmp_path)
    first = run_project(fixture, binary, tmp_path / "first-run", owner="native-pcc1")
    second = run_project(fixture, binary, tmp_path / "second-run", owner="native-pcc1")
    assert_exact(first, second)
    (tmp_path / "native-execution.json").write_text(
        json.dumps(
            {
                "owner": "native-pcc1",
                "status": "executed-exactly",
                "returncode": first.returncode,
                "stdout_hex": first.stdout.hex(),
                "stderr_hex": first.stderr.hex(),
                "repeat_exact": first == second,
            },
            indent=2,
        )
        + "\n"
    )


@pytest.mark.integration
def test_cpp_tinyxml2_upstream_tests_explicit_cpp17_reference_oracle(tmp_path):
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 oracle unavailable; set PCC_CPP_ORACLE"
    fixture = load_project(variant="upstream-tests")
    binary = compile_project_oracle(fixture, tmp_path, compiler=compiler)
    first = run_project(
        fixture, binary, tmp_path / "first-run", owner="external-reference"
    )
    second = run_project(
        fixture, binary, tmp_path / "second-run", owner="external-reference"
    )
    assert assert_project_outcome(first, fixture) == assert_project_outcome(
        second, fixture
    )


@pytest.mark.integration
def test_cpp_tinyxml2_upstream_tests_native_linux_windows_project(
    native_compiler, tmp_path
):
    # Whole upstream test runner, not a filtered case or the smoke driver.
    # Missing native capability is a failure, not skip/xfail/reference fallback.
    fixture = load_project(variant="upstream-tests")
    binary = compile_project_native(native_compiler, fixture, tmp_path)
    first = run_project(fixture, binary, tmp_path / "first-run", owner="native-pcc1")
    second = run_project(fixture, binary, tmp_path / "second-run", owner="native-pcc1")
    assert assert_project_outcome(first, fixture) == assert_project_outcome(
        second, fixture
    )
