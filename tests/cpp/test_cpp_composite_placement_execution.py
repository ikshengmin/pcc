"""Same-original native/reference composite lifetime obligations; locally UNRUN."""

import struct
import subprocess

import pytest

from tests.cpp.owned_execution import (
    Execution,
    compile_host_cpp,
    write_host_executable,
)
from tests.cpp.test_cpp_class_execution import record_execution
from tests.cpp.test_cpp_composite_placement import (
    DEFERRED,
    INVALID,
    PRESERVED_ORIGINALS,
    PROGRAMS,
)
from tests.cpp.test_cpp_constructor_direct_list_execution import syntax_reference
from tests.cpp.test_cpp_placement_new import DECLARATION, compile_reference_with_new


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_composite_placement_originals_execute_against_cpp17(name, units, tmp_path, monkeypatch):
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned composite placement invoked an external build tool')
        guard.setattr(subprocess, 'Popen', forbidden)
        artifact = compile_host_cpp(units)
        assert artifact == compile_host_cpp(units)
    owned = tmp_path / 'owned'
    write_host_executable(artifact, owned)
    oracle = compile_reference_with_new(units, tmp_path / 'reference')
    expected = Execution(42, struct.pack('<i', 42), b'')
    record_execution(owned, expected)
    record_execution(oracle, expected)


@pytest.mark.integration
@pytest.mark.parametrize('name,source', PRESERVED_ORIGINALS, ids=[row[0] for row in PRESERVED_ORIGINALS])
def test_exact_formerly_deferred_placement_originals_remain_cpp17_valid(name, source, tmp_path):
    result = syntax_reference(tmp_path, name, source)
    assert result.returncode == 0, result.stderr


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_deferred_composite_placement_originals_are_reference_valid(name, source, code, tmp_path):
    result = syntax_reference(tmp_path, name, DECLARATION + source)
    assert result.returncode == 0, result.stderr


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_composite_placement_originals_fail_reference(name, source, code, tmp_path):
    assert syntax_reference(tmp_path, name, DECLARATION + source).returncode != 0
