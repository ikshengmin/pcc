"""Same-original owned native output/reference obligations; all locally UNRUN."""

import os
import shutil
import subprocess

import pytest

from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_constructor_direct_lists import DEFERRED, INVALID, PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize("name,units", PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_direct_list_constructor_originals_match_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


def syntax_reference(tmp_path, name, source):
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 reference is required"
    unit = tmp_path / (name + ".cpp")
    unit.write_text(source)
    result = subprocess.run([compiler, "-std=c++17", "-pedantic-errors", "-fsyntax-only", str(unit)],
                            capture_output=True, timeout=20)
    (tmp_path / "reference.stdout.bin").write_bytes(result.stdout)
    (tmp_path / "reference.stderr.bin").write_bytes(result.stderr)
    return result


@pytest.mark.integration
@pytest.mark.parametrize("name,source,code", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_direct_list_constructor_original_is_rejected_by_reference(name, source, code, tmp_path):
    assert syntax_reference(tmp_path, name, source).returncode != 0


@pytest.mark.integration
@pytest.mark.parametrize("name,source,code", DEFERRED, ids=[row[0] for row in DEFERRED])
def test_deferred_direct_list_constructor_original_is_valid_reference_only(name, source, code, tmp_path):
    result = syntax_reference(tmp_path, name, source)
    assert result.returncode == 0, result.stderr
