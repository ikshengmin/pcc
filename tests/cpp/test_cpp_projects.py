"""Real project corpus: reference success is not PCC/native qualification."""

import json
import os
from pathlib import Path
import shutil

import pytest

from tests.cpp.owned_execution import assert_exact, run_executable
from tests.cpp.project_cases import (
    compile_project_native,
    compile_project_oracle,
    load_project,
    materialize_project,
    native_project_environment,
)
from tests.integration.test_owned_platform_native import native_compiler


def test_cpp_project_configuration_selects_versioned_inputs_and_expected_outcome():
    fixture = load_project()
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
    from tests.cpp.project_cases import PROJECT

    (tmp_path / "source/pcc-fixture.json").write_bytes(
        (PROJECT / "pcc-fixture.json").read_bytes()
    )
    (tmp_path / "source/tinyxml2.cpp").unlink()
    with pytest.raises(AssertionError, match="missing project input"):
        load_project(tmp_path / "source")


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


@pytest.mark.integration
def test_cpp_tinyxml2_explicit_cpp17_reference_oracle(tmp_path):
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 oracle unavailable; set PCC_CPP_ORACLE"
    fixture = load_project()
    binary = compile_project_oracle(fixture, tmp_path, compiler=compiler)
    assert_exact(run_executable(binary), fixture.expected)
    assert_exact(run_executable(binary), fixture.expected)


@pytest.mark.integration
def test_cpp_tinyxml2_native_linux_windows_project_exact_outcome(
    native_compiler, tmp_path
):
    # Deliberately NOT pcc_gate/skip/xfail. An absent/unsupported native C++ route
    # leaves project integration incomplete even if every external oracle passes.
    fixture = load_project()
    binary = compile_project_native(native_compiler, fixture, tmp_path)
    first = run_executable(binary)
    second = run_executable(binary)
    assert_exact(first, fixture.expected)
    assert_exact(second, fixture.expected)
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
