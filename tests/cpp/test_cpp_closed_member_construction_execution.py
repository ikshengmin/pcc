"""Same-original native/reference obligations; all locally UNRUN."""

import pytest

from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_constructor_direct_list_execution import syntax_reference
from tests.cpp.test_cpp_closed_member_construction import (
    DECLARATION_CONTROLS,
    DEFERRED,
    INVALID,
    PROGRAMS,
    RETAINED_BOUNDARIES,
)


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_closed_member_constructor_originals_match_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_member_c1_d1_originals_fail_reference(name, source, code, tmp_path):
    assert syntax_reference(tmp_path, name, source).returncode != 0


@pytest.mark.integration
@pytest.mark.parametrize('name,source', DECLARATION_CONTROLS, ids=[row[0] for row in DECLARATION_CONTROLS])
def test_closed_member_constructor_declarations_are_reference_valid(name, source, tmp_path):
    result = syntax_reference(tmp_path, name, source)
    assert result.returncode == 0, result.stderr


@pytest.mark.integration
@pytest.mark.parametrize('name,source', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_unclosed_member_constructor_originals_remain_valid_cpp17(name, source, tmp_path):
    result = syntax_reference(tmp_path, name, source)
    assert result.returncode == 0, result.stderr


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', RETAINED_BOUNDARIES, ids=[row[0] for row in RETAINED_BOUNDARIES])
def test_other_member_lifetime_boundaries_remain_valid_cpp17(name, source, code, tmp_path):
    result = syntax_reference(tmp_path, name, source)
    assert result.returncode == 0, result.stderr
