"""Same-original owned native/reference scalar-array obligations, UNRUN.

Host pcc0 emits native executables. Native pcc1 frontend, bootstrap and whole
TinyXML2 acceptance remain unqualified. Placement rows use real external
<new>, preserving original TU bytes but not identical include closures.
"""

import struct
import subprocess

import pytest

from tests.cpp.owned_execution import Execution, compile_host_cpp, write_host_executable
from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_class_execution import record_execution
from tests.cpp.test_cpp_constructor_direct_list_execution import syntax_reference
from tests.cpp.test_cpp_placement_new import compile_reference_with_new
from tests.cpp.test_cpp_scalar_array_members import GAPS, INVALID, PLACEMENT_PROGRAMS, PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_scalar_array_member_originals_match_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PLACEMENT_PROGRAMS, ids=[row[0] for row in PLACEMENT_PROGRAMS])
def test_scalar_array_placement_originals_match_real_new_reference(name, units, tmp_path, monkeypatch):
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned scalar-array placement invoked an external build tool')
        guard.setattr(subprocess, 'Popen', forbidden)
        artifact = compile_host_cpp(units)
        assert artifact == compile_host_cpp(units)
    binary = tmp_path / 'owned'
    write_host_executable(artifact, binary)
    oracle = compile_reference_with_new(units, tmp_path / 'reference')
    expected = Execution(42, struct.pack('<i', 42), b'')
    record_execution(binary, expected)
    record_execution(oracle, expected)


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_scalar_array_member_originals_fail_reference(name, source, code, tmp_path):
    assert syntax_reference(tmp_path, name, source).returncode != 0


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', GAPS, ids=[row[0] for row in GAPS])
def test_remaining_scalar_array_member_gaps_are_cpp17_valid(name, source, code, tmp_path):
    result = syntax_reference(tmp_path, name, source)
    assert result.returncode == 0, result.stderr
