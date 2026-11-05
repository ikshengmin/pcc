"""Same-original class-reference result observations; all locally UNRUN."""

import pytest

from tests.cpp.sizeof_execution import execute_public_row_pair, execute_row_pair
from tests.cpp.test_cpp_class_reference_results import DEFERRED, INVALID, PROGRAMS
from tests.cpp.test_cpp_constructor_direct_list_execution import syntax_reference


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_class_reference_results_match_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize('opt', (0, 2))
def test_public_class_reference_result_aliases_original(opt, tmp_path, monkeypatch):
    source = ('int calls=0;struct S{int n;};S&borrow(S*p){++calls;return *p;}'
              'int main(){S s{17};borrow(&s).n+=25;if(calls!=1)return 1;return s.n;}')
    execute_public_row_pair(source, opt, tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_class_reference_results_fail_reference(name, source, code, tmp_path):
    assert syntax_reference(tmp_path, name, source).returncode != 0


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_deferred_class_reference_results_remain_valid_cpp17(name, source, code, tmp_path):
    result = syntax_reference(tmp_path, name, source)
    assert result.returncode == 0, result.stderr
