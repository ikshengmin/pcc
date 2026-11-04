"""Host-pcc0 owned native output versus same-source C++17 reference.

This is not native pcc1 qualification. The helper preserves both observations
when owned compilation fails. Placement keeps its original TU bytes but uses
real external <new> for the reference, a distinct effective include closure.
"""

import hashlib
import json
import os
import re

import pytest

from tests.cpp import sizeof_execution
from tests.cpp.test_cpp_conditional_expressions import (
    DEFERRED,
    INVALID,
    ORIGINAL_PLACEMENT,
    PROGRAMS,
)
from tests.cpp.test_cpp_constructor_direct_list_execution import syntax_reference
from tests.cpp.test_cpp_placement_new import compile_reference_with_new


def reference_syntax_with_receipt(tmp_path, name, source):
    result = syntax_reference(tmp_path, name, source)
    original = tmp_path / (name + '.cpp')
    expected = source.encode('utf-8')
    (tmp_path / 'reference.json').write_text(json.dumps({
        'argv': result.args, 'returncode': result.returncode,
        'source_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
        'expected_source_sha256': hashlib.sha256(expected).hexdigest(),
        'stdout_hex': result.stdout.hex(), 'stderr_hex': result.stderr.hex(),
    }, indent=2))
    assert original.read_bytes() == expected
    return result, original


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_conditional_host_pcc0_native_output_matches_cpp17(name, units, tmp_path, monkeypatch):
    sizeof_execution.execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize('name,units', ORIGINAL_PLACEMENT, ids=[row[0] for row in ORIGINAL_PLACEMENT])
def test_original_placement_conditional_matches_real_new_reference(name, units, tmp_path, monkeypatch):
    def reference_with_new(originals, directory, *, compiler):
        return compile_reference_with_new(originals, directory)

    # Change only this test's reference hook; the unchanged owned helper still
    # records its failure before attempting the reference with original bytes.
    monkeypatch.setattr(sizeof_execution, 'compile_reference_cpp', reference_with_new)
    sizeof_execution.execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_conditional_original_fails_cpp17_reference(name, source, code, tmp_path):
    result, original = reference_syntax_with_receipt(tmp_path, name, source)
    assert result.returncode == 1, result.stderr
    assert result.stdout == b''
    assert os.fsencode(original.name) in result.stderr
    assert re.search(rb':\d+:\d+: (?:fatal )?error:', result.stderr), result.stderr
    assert not any(marker in result.stderr.lower() for marker in (
        b'internal compiler error', b'please submit a full bug report',
        b'segmentation fault', b'stack dump', b'assertion failed',
    )), result.stderr


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_deferred_conditional_original_is_valid_cpp17(name, source, code, tmp_path):
    result, _ = reference_syntax_with_receipt(tmp_path, name, source)
    assert result.returncode == 0, result.stderr
