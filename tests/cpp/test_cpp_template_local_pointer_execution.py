"""Same-original native-emitted/reference local-pointer obligations, UNRUN.

Host pcc0 builds native executables. These are not native pcc1 frontend,
allocation runtime, complete TinyXML2 or bootstrap fixed-point claims.
"""

import pytest

from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_constructor_direct_list_execution import syntax_reference
from tests.cpp.test_cpp_template_local_pointers import GAPS, INVALID, PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_template_local_pointer_originals_match_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_template_local_pointer_originals_fail_reference(name, source, code, tmp_path):
    # Every negative includes a concrete instantiation, rather than treating
    # acceptance of an uninstantiated dependent primary as reference failure.
    assert syntax_reference(tmp_path, name, source).returncode != 0


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', GAPS, ids=[row[0] for row in GAPS])
def test_remaining_template_local_pointer_gaps_are_cpp17_valid(name, source, code, tmp_path):
    result = syntax_reference(tmp_path, name, source)
    assert result.returncode == 0, result.stderr
