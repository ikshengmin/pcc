"""Same-original defaulted-D1 native/reference obligations; all UNRUN."""

import struct
import subprocess

import pytest

from tests.cpp.owned_execution import (
    Execution,
    compile_host_cpp,
    write_host_executable,
)
from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_class_execution import record_execution
from tests.cpp.test_cpp_constructor_direct_list_execution import syntax_reference
from tests.cpp.test_cpp_defaulted_destructors import (
    DEFERRED,
    INVALID,
    PLACEMENT_PROGRAMS,
    PROGRAMS,
)
from tests.cpp.test_cpp_placement_new import compile_reference_with_new


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_defaulted_d1_originals_match_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PLACEMENT_PROGRAMS, ids=[row[0] for row in PLACEMENT_PROGRAMS])
def test_defaulted_d1_placement_matches_real_new_reference(name, units, tmp_path, monkeypatch):
    # The reference force-includes its real <new>; original TU bytes match,
    # effective include closures differ. No substitute allocation stub is used.
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned defaulted D1 build invoked an external tool')
        guard.setattr(subprocess, 'Popen', forbidden)
        artifact = compile_host_cpp(units)
        assert artifact == compile_host_cpp(units)
    owned = tmp_path / 'owned'
    write_host_executable(artifact, owned)
    reference = compile_reference_with_new(units, tmp_path / 'reference')
    expected = Execution(42, struct.pack('<i', 42), b'')
    record_execution(owned, expected)
    record_execution(reference, expected)


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_defaulted_d1_originals_fail_reference(name, source, code, tmp_path):
    assert syntax_reference(tmp_path, name, source).returncode != 0


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_deferred_defaulted_d1_originals_are_reference_valid(name, source, code, tmp_path):
    result = syntax_reference(tmp_path, name, source)
    assert result.returncode == 0, result.stderr
