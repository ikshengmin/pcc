"""Same-original native-output/reference obligations for [lex.ccon]. UNRUN."""

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


@pytest.mark.integration
@pytest.mark.parametrize("name,source,code", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_character_original_is_rejected_by_cpp17(name, source, code, tmp_path):
    compiler = shutil.which(os.environ.get("PCC_CPP_ORACLE", "g++"))
    assert compiler, "explicit C++17 negative reference is required"
    unit = tmp_path / (name + ".cpp")
    unit.write_text(source)
    result = subprocess.run([compiler, "-std=c++17", "-pedantic-errors", "-fsyntax-only", str(unit)],
                            capture_output=True, timeout=20)
    (tmp_path / "reference.stdout.bin").write_bytes(result.stdout)
    (tmp_path / "reference.stderr.bin").write_bytes(result.stderr)
    assert result.returncode != 0
