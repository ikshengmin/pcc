"""Same original inline-member units with owned and real g++ observations."""

import pytest

from tests.cpp.sizeof_execution import execute_public_row_pair, execute_row_pair
from tests.cpp.test_cpp_constructor_direct_list_execution import syntax_reference
from tests.cpp.test_cpp_explicit_inline_members import CASES, DEFERRED, INVALID, PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_explicit_inline_member_originals_match_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_explicit_inline_members_are_rejected_by_reference(name, source, code, tmp_path):
    assert syntax_reference(tmp_path, name, source).returncode != 0


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_deferred_explicit_inline_members_are_valid_reference(name, source, code, tmp_path):
    result = syntax_reference(tmp_path, name, source)
    assert result.returncode == 0, result.stderr


@pytest.mark.integration
@pytest.mark.parametrize('opt', [0, 2])
def test_public_explicit_inline_preserves_receiver_order(opt, tmp_path, monkeypatch):
    source = dict(CASES)['receiver-before-argument'] + 'int main(){return cpp_test_entry();}'
    execute_public_row_pair(source, opt, tmp_path, monkeypatch)
