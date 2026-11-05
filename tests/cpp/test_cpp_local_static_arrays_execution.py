"""Same-original static array execution/reference obligations, locally UNRUN."""

import pytest

from tests.cpp.sizeof_execution import execute_public_row_pair, execute_row_pair
from tests.cpp.test_cpp_constructor_direct_list_execution import syntax_reference
from tests.cpp.test_cpp_local_static_arrays import DEFERRED, INVALID, PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_static_arrays_match_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize('opt', (0, 2))
def test_public_static_array_persistence(opt, tmp_path, monkeypatch):
    source = ('int next(){static int a[]={16,24};return ++a[0]+ ++a[1];}'
              'int main(){if(next()!=42)return 1;return next()-2;}')
    execute_public_row_pair(source, opt, tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_static_arrays_fail_reference(name, source, code, tmp_path):
    assert syntax_reference(tmp_path, name, source).returncode != 0


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_deferred_static_arrays_remain_valid_cpp17(name, source, code, tmp_path):
    result = syntax_reference(tmp_path, name, source)
    assert result.returncode == 0, result.stderr
