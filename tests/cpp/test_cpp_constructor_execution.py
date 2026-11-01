"""Host-owned native output and explicit same-input G++17 constructor pairs.

No native pcc1, base-C2, destruction, inline-ODR or full TinyXML2 claim.
"""
import pytest

from tests.cpp.test_cpp_class_execution import execute_and_record
from tests.cpp.test_cpp_constructors import CONSTRUCTOR_CASES


@pytest.mark.integration
@pytest.mark.parametrize('name,source', CONSTRUCTOR_CASES, ids=[row[0] for row in CONSTRUCTOR_CASES])
def test_constructor_complete_object_matches_reference(tmp_path, monkeypatch, name, source):
    execute_and_record(tmp_path, monkeypatch, ((name+'.cpp', source),), 42)


@pytest.mark.integration
def test_constructor_out_of_line_complete_object_links_across_translation_units(tmp_path, monkeypatch):
    header = 'struct S{int value;S(int);};'
    units = (('definition.cpp', header+'S::S(int n):value(n){}'),
             ('use.cpp', header+'int cpp_test_entry(){S s(42);return s.value;}'))
    execute_and_record(tmp_path, monkeypatch, units, 42)
