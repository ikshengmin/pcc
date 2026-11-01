"""Normal-exit owned Linux execution vs identical-input G++17 reference.

Complete D1 objects only; no native pcc1, EH/unwind, deleting D0, base D2,
inline/COMDAT or complete TinyXML2 project qualification.
"""
import pytest

from tests.cpp.test_cpp_class_execution import execute_and_record
from tests.cpp.test_cpp_destructors import DESTRUCTOR_CASES


@pytest.mark.integration
@pytest.mark.parametrize('name,source', DESTRUCTOR_CASES, ids=[row[0] for row in DESTRUCTOR_CASES])
def test_destructor_original_programs_match_reference(tmp_path, monkeypatch, name, source):
    execute_and_record(tmp_path, monkeypatch, ((name+'.cpp', source),), 42)


@pytest.mark.integration
def test_complete_object_destructor_links_across_translation_units(tmp_path, monkeypatch):
    header = 'struct S{int*p;~S();};'
    units = (('definition.cpp', header+'S::~S(){*p=42;}'),
             ('entry.cpp', header+'int cpp_test_entry(){int n=0;{S s{&n};}return n;}'))
    execute_and_record(tmp_path, monkeypatch, units, 42)


@pytest.mark.integration
@pytest.mark.parametrize('original', (
    "struct Guard { ~Guard(); };",
    "namespace A {struct S {~S();};}",
))
def test_original_unused_destructor_declarations_execute_with_reference(tmp_path, monkeypatch, original):
    units = (("original-declaration.cpp", original),
             ("entry.cpp", "int cpp_test_entry(){return 42;}"))
    execute_and_record(tmp_path, monkeypatch, units, 42)
