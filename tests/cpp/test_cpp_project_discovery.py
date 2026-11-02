"""Generic independent C++ TU discovery, including the unchanged real project."""
import pytest

from pcc.driver.project import _collect_input_units, collect_project, collect_translation_units
from tests.cpp.project_cases import load_project, materialize_project


@pytest.mark.parametrize('suffix', ['.cc', '.cpp', '.cxx', '.c++', '.C'])
def test_cpp_directory_retains_translation_unit_identity(tmp_path, suffix):
    (tmp_path/('b'+suffix)).write_text('int main(){return 42;}')
    (tmp_path/('a'+suffix)).write_text('int helper(){return 1;}')
    (tmp_path/'ignored.h').write_text('not a translation unit')
    (tmp_path/'ignored.cpp').mkdir()
    (tmp_path/'ignored.c').mkdir()
    units, base = collect_translation_units(str(tmp_path))
    assert [u.name for u in units] == ['a'+suffix, 'b'+suffix]
    assert [u.source for u in units] == ['int helper(){return 1;}', 'int main(){return 42;}']
    assert all(u.path.endswith(u.name) for u in units)
    assert base == str(tmp_path)


def test_cpp_directory_refuses_source_merging(tmp_path):
    (tmp_path/'main.cpp').write_text('int main(){return 0;}')
    with pytest.raises(ValueError, match='require --separate-tus'):
        collect_project(str(tmp_path))


def test_mixed_directory_is_not_silently_filtered_to_one_language(tmp_path):
    (tmp_path/'main.cpp').write_text('int main(){return 0;}')
    (tmp_path/'helper.c').write_text('int helper(void){return 1;}')
    with pytest.raises(ValueError, match='mixed C/C\\+\\+ directory'):
        collect_translation_units(str(tmp_path))


def test_cpp_make_discovery_remains_explicit(tmp_path):
    (tmp_path/'main.cpp').write_text('int main(){return 0;}')
    with pytest.raises(ValueError, match='make-derived C\\+\\+'):
        _collect_input_units(str(tmp_path), sources_from_make='all')


@pytest.mark.parametrize('sources,diagnostic', [
    ({'a.cpp':'int helper(){return 1;}'}, 'No main'),
    ({'a.cpp':'int main(){return 0;}', 'b.cpp':'int main(){return 1;}'}, 'Multiple main'),
])
def test_cpp_directory_requires_exactly_one_semantic_entry(tmp_path, sources, diagnostic):
    for name,source in sources.items():
        (tmp_path/name).write_text(source)
    with pytest.raises(ValueError, match=diagnostic):
        collect_translation_units(str(tmp_path))


def test_original_tinyxml2_upstream_tus_are_selected_without_source_edits(tmp_path):
    fixture = load_project(variant='upstream-tests')
    paths = materialize_project(fixture, tmp_path/'source')
    units, base = _collect_input_units(str(tmp_path/'source'))
    assert [u.name for u in units] == ['tinyxml2.cpp', 'xmltest.cpp']
    assert [u.source for u in units] == [p.read_text() for p in paths]
    fixture.verify()


def test_cpp_directory_dependency_does_not_enter_c_main_discovery(tmp_path):
    project = tmp_path/'project'
    project.mkdir()
    (project/'main.cpp').write_text('int main(){return 0;}')
    dependency = tmp_path/'helper.c'
    dependency.write_text('int helper(void){return 1;}')
    with pytest.raises(ValueError, match='mixed C/C\\+\\+ project dependency'):
        collect_translation_units(str(project), dependencies=[str(dependency)])
