"""Original pointer const_cast native/reference pairs; no readonly-object writes."""
import pytest
from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_const_casts import PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,units',PROGRAMS,ids=[row[0] for row in PROGRAMS])
def test_const_cast_originals_match_real_cpp17(name,units,tmp_path,monkeypatch):
    execute_row_pair(tmp_path,monkeypatch,units)
