"""Same-original C++17 friend-class observations with separate process receipts.

The owned adapter executes Linux x86_64 host-PCC ELF artifacts, not pcc1.
External G++ is an explicit reference, never a production fallback. The
existing row helper preserves returned output, but timeout/launch exceptions
do not produce complete process receipts. Failed/incomplete evidence stays so.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess

import pytest

from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_friend_classes import INVALID, PROGRAMS, UNIMPLEMENTED


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_friend_originals_execute_against_explicit_cpp17_reference(name, units, tmp_path, monkeypatch):
    (tmp_path / 'original-inputs.json').write_text(json.dumps([
        {'filename': filename, 'source_hex': source.encode('utf-8').hex(),
         'sha256': hashlib.sha256(source.encode('utf-8')).hexdigest()}
        for filename, source in units
    ], indent=2))
    execute_row_pair(tmp_path, monkeypatch, units)
    for filename, source in units:
        expected = source.encode('utf-8')
        assert (tmp_path / 'originals' / filename).read_bytes() == expected
        assert (tmp_path / 'reference' / filename).read_bytes() == expected


def reference_syntax(tmp_path, name, source):
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit C++17 reference is required'
    original = tmp_path / (name + '.cpp')
    original.write_bytes(source.encode('utf-8'))
    command = [compiler, '-std=c++17', '-pedantic-errors', '-fsyntax-only', str(original)]
    result = subprocess.run(command, capture_output=True, timeout=20)
    (tmp_path / 'reference.stdout.bin').write_bytes(result.stdout)
    (tmp_path / 'reference.stderr.bin').write_bytes(result.stderr)
    (tmp_path / 'reference.json').write_text(json.dumps({
        'argv': command, 'compiler': compiler, 'returncode': result.returncode,
        'source_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
        'stdout_hex': result.stdout.hex(), 'stderr_hex': result.stderr.hex(),
    }, indent=2))
    assert original.read_bytes() == source.encode('utf-8')
    return result, original


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_friend_originals_require_cpp17_source_diagnostics(name, source, code, tmp_path):
    result, original = reference_syntax(tmp_path, name, source)
    assert result.returncode == 1, result.stderr
    assert result.stdout == b''
    assert os.fsencode(original.name) in result.stderr
    assert re.search(rb':\d+:\d+: (?:fatal )?error:', result.stderr), result.stderr
    assert not any(marker in result.stderr.lower() for marker in (
        b'internal compiler error', b'please submit a full bug report',
        b'segmentation fault', b'stack dump', b'assertion failed',
    )), result.stderr


@pytest.mark.integration
@pytest.mark.parametrize('name,source', UNIMPLEMENTED, ids=[row[0] for row in UNIMPLEMENTED])
def test_unimplemented_friend_originals_are_reference_valid(name, source, tmp_path):
    result, _ = reference_syntax(tmp_path, name, source)
    assert result.returncode == 0, result.stderr
