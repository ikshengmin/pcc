"""Unchanged C++17 program pairs for declaration-bound ordinary defaults."""
import pytest
from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_default_arguments import PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[item[0] for item in PROGRAMS])
def test_default_argument_originals_match_real_cpp17(name,units,tmp_path,monkeypatch):
    execute_row_pair(tmp_path,monkeypatch,units)
