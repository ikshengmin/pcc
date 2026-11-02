"""Same original DMI programs, host-owned execution versus explicit G++17."""
import pytest

from tests.cpp.test_cpp_class_execution import execute_and_record
from tests.cpp.test_cpp_default_member_initializers import DMI_CASES


@pytest.mark.integration
@pytest.mark.parametrize('name,source', DMI_CASES, ids=[row[0] for row in DMI_CASES])
def test_default_member_initializer_originals_match_reference(tmp_path, monkeypatch, name, source):
    execute_and_record(tmp_path, monkeypatch, ((name+'.cpp', source),), 42)


@pytest.mark.integration
def test_default_member_initializer_out_of_line_constructor_two_units(tmp_path, monkeypatch):
    header = 'struct S{int n=42;S();};'
    units = (('definition.cpp', header+'S::S(){}'),
             ('caller.cpp', header+'int cpp_test_entry(){S s;return s.n;}'))
    execute_and_record(tmp_path, monkeypatch, units, 42)
