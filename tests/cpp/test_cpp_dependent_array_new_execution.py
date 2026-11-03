"""Native/reference observers for unused array-new bodies only, UNRUN.

No positive row executes new[], delete[], or an allocation replacement. Actual
uses retain fail-before-IR capability checks and syntax-only reference records.
"""

import pytest

from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_constructor_direct_list_execution import syntax_reference
from tests.cpp.test_cpp_dependent_array_new import INVALID, PROGRAMS, SYNTAX_GAPS, USED_GAPS


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_unused_array_new_originals_match_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_array_new_originals_fail_reference(name, source, code, tmp_path):
    assert syntax_reference(tmp_path, name, source).returncode != 0


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', USED_GAPS + SYNTAX_GAPS,
                         ids=[row[0] for row in USED_GAPS + SYNTAX_GAPS])
def test_unowned_array_new_originals_remain_cpp17_valid(name, source, code, tmp_path):
    result = syntax_reference(tmp_path, name, source)
    assert result.returncode == 0, result.stderr
