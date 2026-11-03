"""Same-original declaration/retained-route CI obligations, execution UNRUN."""

import pytest

from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_template_copy_declarations import PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_copy_declaration_originals_match_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)
