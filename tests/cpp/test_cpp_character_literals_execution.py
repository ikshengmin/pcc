"""Same-original C++17 reference obligations, including explicit IFNDR coverage."""

import json
import os
import shutil
import subprocess

import pytest

from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.owned_execution import TARGET
from tests.cpp.test_cpp_character_literals import INVALID, PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize("name,units", PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_character_originals_match_cpp17_reference(name, units, tmp_path, monkeypatch):
    assert TARGET == "x86_64-unknown-linux-gnu", "character corpus requires the matching fixed-target reference"
    execute_row_pair(tmp_path, monkeypatch, units)


# N4659 [temp.res]/8.1,8.3: this uninstantiated template has no valid
# specialization. It remains ill-formed, but the standard does not require a
# reference diagnostic. PCC's unchanged INVALID corpus still requires the exact
# early diagnostic and prohibits reaching object emission.
# https://timsong-cpp.github.io/cppwp/n4659/temp.res#8
IFNDR_NAME = "char-zero-pointer-in-template"
IFNDR_ROWS = tuple(row for row in INVALID if row[0] == IFNDR_NAME)


@pytest.mark.integration
@pytest.mark.parametrize("name,source,code", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_character_original_cpp17_diagnostic_contract(name, source, code, tmp_path):
    test_ifndr_reference_classification_is_exact()
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 negative reference is required"
    unit = tmp_path / (name + ".cpp")
    unit.write_text(source)
    result = subprocess.run([compiler, "-std=c++17", "-pedantic-errors", "-fsyntax-only", str(unit)],
                            capture_output=True, timeout=20)
    (tmp_path / "reference.stdout.bin").write_bytes(result.stdout)
    (tmp_path / "reference.stderr.bin").write_bytes(result.stderr)
    (tmp_path / "reference.contract.json").write_text(json.dumps({
        "case": name,
        "source_kind": "unchanged-original",
        "standard_status": "ill-formed-no-diagnostic-required" if name == IFNDR_NAME else "ill-formed-diagnostic-required",
        "returncode": result.returncode,
        "diagnostic_observed": bool(result.stderr),
    }, indent=2))
    if name == IFNDR_NAME:
        # Acceptance is recorded, never interpreted as source validity. A crash,
        # timeout or abnormal exit is not an allowed reference outcome.
        assert result.returncode in (0, 1), result.stderr
        if result.returncode == 1:
            assert result.stderr, "reference rejection must carry a diagnostic"
    else:
        assert result.returncode == 1, result.stderr
        assert result.stderr, "reference rejection must carry a diagnostic"


def test_ifndr_reference_classification_is_exact():
    assert len(IFNDR_ROWS) == 1
    assert IFNDR_ROWS[0] == (
        IFNDR_NAME,
        "int f(int*);template<class T>int g(T x){return f('\\0');}",
        "cpp.no-viable-overload",
    )


@pytest.mark.integration
@pytest.mark.parametrize("name,source,code", IFNDR_ROWS, ids=[row[0] for row in IFNDR_ROWS])
def test_invalid_character_instantiated_template_requires_cpp17_diagnostic(name, source, code, tmp_path):
    test_ifndr_reference_classification_is_exact()
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 negative reference is required"
    # Separate negative control, not a replacement for the unchanged original.
    # Explicit instantiation makes the invalid call require a diagnostic.
    unit = tmp_path / (name + "-instantiated-control.cpp")
    unit.write_text(source + "\ntemplate int g<int>(int);\n")
    result = subprocess.run([compiler, "-std=c++17", "-pedantic-errors", "-fsyntax-only", str(unit)],
                            capture_output=True, timeout=20)
    (tmp_path / "reference.stdout.bin").write_bytes(result.stdout)
    (tmp_path / "reference.stderr.bin").write_bytes(result.stderr)
    assert result.returncode == 1, result.stderr
    assert result.stderr, "instantiated invalid template must produce a diagnostic"
