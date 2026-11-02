"""Same-input owned native output and genuine C++17 reference obligations. UNRUN."""

import pytest

from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_constructor_direct_list_execution import syntax_reference
from tests.cpp.test_cpp_explicit_constructors import DEFERRED, INVALID, PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize("name,units", PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_explicit_constructor_originals_match_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize("name,source,code", INVALID, ids=[row[0] for row in INVALID])
def test_invalid_explicit_constructor_original_is_rejected_by_reference(name, source, code, tmp_path):
    assert syntax_reference(tmp_path, name, source).returncode != 0


@pytest.mark.integration
@pytest.mark.parametrize("name,source,code", DEFERRED, ids=[row[0] for row in DEFERRED])
def test_deferred_explicit_constructor_original_is_valid_reference_only(name, source, code, tmp_path):
    result = syntax_reference(tmp_path, name, source)
    assert result.returncode == 0, result.stderr
