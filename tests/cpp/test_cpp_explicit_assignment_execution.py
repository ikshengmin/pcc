"""Same-original explicit assignment execution/reference obligations, UNRUN."""

import pytest

from tests.cpp.sizeof_execution import execute_public_row_pair, execute_row_pair
from tests.cpp.test_cpp_explicit_assignment import DEFERRED, INVALID, PROGRAMS
from tests.cpp.test_cpp_constructor_direct_list_execution import syntax_reference


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_explicit_assignment_matches_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize('opt', (0, 2))
def test_public_explicit_assignment_mutates_original(opt, tmp_path, monkeypatch):
    source = ('struct S{int n;S&operator=(const S&other){n=other.n;return *this;}};'
              'int main(){S a{17};S b{42};S&r=a.operator=(b);if(&r!=&a)return 1;return a.n;}')
    execute_public_row_pair(source, opt, tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_explicit_assignment_fails_reference(name, source, code, tmp_path):
    assert syntax_reference(tmp_path, name, source).returncode != 0


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_deferred_explicit_assignment_remains_valid_cpp17(name, source, code, tmp_path):
    result = syntax_reference(tmp_path, name, source)
    assert result.returncode == 0, result.stderr
