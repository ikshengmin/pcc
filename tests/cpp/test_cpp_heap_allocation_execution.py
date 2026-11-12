"""Same-source owned/reference execution of allocating new/delete (Linux x86-64 lane)."""
import pytest

from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_heap_allocation import PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,source', PROGRAMS, ids=[item[0] for item in PROGRAMS])
def test_heap_allocation_programs_execute_against_real_cpp17_reference(name, source, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, ((name + '.cpp', source),))
