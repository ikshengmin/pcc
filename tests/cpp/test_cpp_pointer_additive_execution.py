"""Real original program pairs for builtin pointer offsets; no oracle replacement."""
import pytest

from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_pointer_additive import PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[item[0] for item in PROGRAMS])
def test_pointer_offset_originals_match_genuine_cpp17_reference(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)
