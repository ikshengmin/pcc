"""Same-original typed class-template aggregate native/reference obligations."""
import pytest
from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_class_templates import PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,units',PROGRAMS,ids=[row[0] for row in PROGRAMS])
def test_class_template_originals_match_real_cpp17(name,units,tmp_path,monkeypatch):
    execute_row_pair(tmp_path,monkeypatch,units)
