"""Same-original native/G++ obligations, not class copy or temporary support."""
import pytest
from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_lvalue_references import PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,units',PROGRAMS,ids=[row[0] for row in PROGRAMS])
def test_lvalue_reference_originals_match_cpp17(name,units,tmp_path,monkeypatch):
    execute_row_pair(tmp_path,monkeypatch,units)
