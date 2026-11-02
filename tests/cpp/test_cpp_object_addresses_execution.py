"""Actual same-original dot-call programs versus the explicit C++17 oracle."""
import pytest
from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_object_addresses import PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_object_address_originals_match_real_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)
