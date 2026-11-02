"""Same-original native/C++17 reference obligations; all locally UNRUN."""

import pytest

from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_constructor_direct_list_execution import syntax_reference
from tests.cpp.test_cpp_aggregate_destructor_access import (
    DECLARATION_CONTROLS,
    DEFERRED,
    INVALID,
    PROGRAMS,
)


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_aggregate_access_originals_match_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize('name,source', INVALID, ids=[row[0] for row in INVALID])
def test_aggregate_access_invalid_originals_fail_reference(name, source, tmp_path):
    assert syntax_reference(tmp_path, name, source).returncode != 0


@pytest.mark.integration
@pytest.mark.parametrize('name,source', DECLARATION_CONTROLS, ids=[row[0] for row in DECLARATION_CONTROLS])
def test_aggregate_access_prototype_controls_are_reference_valid(name, source, tmp_path):
    result = syntax_reference(tmp_path, name, source)
    assert result.returncode == 0, result.stderr


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_aggregate_access_deferred_originals_are_reference_valid(name, source, code, tmp_path):
    result = syntax_reference(tmp_path, name, source)
    assert result.returncode == 0, result.stderr
