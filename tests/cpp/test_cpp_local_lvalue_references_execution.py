"""Local alias same-source owned/reference obligations; locally UNRUN."""

import pytest

from tests.cpp.sizeof_execution import execute_public_row_pair, execute_row_pair
from tests.cpp.test_cpp_constructor_direct_list_execution import syntax_reference
from tests.cpp.test_cpp_local_lvalue_references import DEFERRED, INVALID, PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_local_reference_originals_match_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize('opt', (0, 2))
def test_public_local_reference_aliases_at_both_optimization_levels(opt, tmp_path, monkeypatch):
    source = ('int main(){int a[2]={17,1};int i=0;int&r=a[i++];r+=25;'
              'if(i!=1||a[1]!=1)return 1;return a[0];}')
    execute_public_row_pair(source, opt, tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_local_reference_originals_fail_reference(name, source, code, tmp_path):
    assert syntax_reference(tmp_path, name, source).returncode != 0


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_deferred_local_reference_originals_remain_valid_cpp17(name, source, code, tmp_path):
    result = syntax_reference(tmp_path, name, source)
    assert result.returncode == 0, result.stderr
