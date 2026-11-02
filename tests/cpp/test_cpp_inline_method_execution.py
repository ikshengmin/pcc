"""Actual owned and genuine G++17 execution obligations, never token emulation."""
import pytest

from tests.cpp.sizeof_execution import execute_public_row_pair, execute_row_pair
from tests.cpp.test_cpp_inline_methods import PROGRAMS, SET_PROGRAM


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[item[0] for item in PROGRAMS])
def test_inline_method_originals_execute_against_real_cpp17_reference(name,units,tmp_path,monkeypatch):
    execute_row_pair(tmp_path,monkeypatch,units)


@pytest.mark.integration
def test_original_set_body_executes_through_owned_public_route(tmp_path,monkeypatch):
    # Same raw TU and original upstream assertion macro/body on both routes;
    # owned preprocessing is production, G++ remains a separately named oracle.
    execute_public_row_pair(SET_PROGRAM,0,tmp_path,monkeypatch)
