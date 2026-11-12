"""Same-source owned/reference execution of member classes of class templates (Linux x86-64 lane)."""
import pytest

from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_class_template_nested_classes import PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,source', PROGRAMS, ids=[item[0] for item in PROGRAMS])
def test_member_class_programs_execute_against_real_cpp17_reference(name, source, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, ((name + '.cpp', source),))
