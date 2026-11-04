"""Same-original default-merge observations; all nodes remain UNRUN.

Owned artifacts use the host-Python adapter and owned ELF backend/linker. This
is not a native pcc1, public CLI, project, libc, virtual dispatch or TinyXML2 qualification.
Reference compiler invocations are explicit and never an owned fallback.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_default_argument_merge import INVALID, PROGRAMS


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def command_receipt(argv, result, cwd=None):
    command = [os.fspath(arg) for arg in argv]
    executable = shutil.which(command[0])
    assert executable is not None, 'invoked executable disappeared before receipt'
    return {
        'argv': command, 'cwd': os.fspath(cwd) if cwd is not None else None,
        'command_sha256': sha256(json.dumps(command, ensure_ascii=False).encode('utf-8')),
        'executable': executable,
        'executable_sha256': sha256(Path(executable).read_bytes()),
        'returncode': result.returncode,
        'stdout_hex': result.stdout.hex(), 'stderr_hex': result.stderr.hex(),
    }


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_merge_originals_match_cpp17_with_exact_receipts(name, units, tmp_path, monkeypatch):
    original_run = subprocess.run
    receipts = []

    def observed_run(argv, **kwargs):
        result = original_run(argv, **kwargs)
        receipt = command_receipt(argv, result, kwargs.get('cwd'))
        receipts.append(receipt)
        index = len(receipts) - 1
        (tmp_path / ('command-%d.stdout.bin' % index)).write_bytes(result.stdout)
        (tmp_path / ('command-%d.stderr.bin' % index)).write_bytes(result.stderr)
        (tmp_path / 'commands.json').write_text(json.dumps(receipts, indent=2))
        return result

    (tmp_path / 'original-inputs.json').write_text(json.dumps([
        {'filename': filename, 'utf8_hex': source.encode('utf-8').hex(),
         'sha256': sha256(source.encode('utf-8'))} for filename, source in units
    ], indent=2))
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, 'run', observed_run)
        # This helper independently attempts the reference even after an owned
        # failure, records binary hashes and exact stdout/stderr, and requires
        # deterministic repeated owned builds plus exit 42 / 2a000000 / empty.
        execute_row_pair(tmp_path, monkeypatch, units)
    for filename, source in units:
        expected = source.encode('utf-8')
        assert (tmp_path / 'originals' / filename).read_bytes() == expected
        assert (tmp_path / 'reference' / filename).read_bytes() == expected
    assert len(receipts) == 3  # owned execution, reference build, reference execution
    assert receipts[0]['stdout_hex'] == receipts[2]['stdout_hex'] == '2a000000'
    assert receipts[0]['stderr_hex'] == receipts[2]['stderr_hex'] == ''
    assert receipts[0]['returncode'] == receipts[2]['returncode'] == 42
    assert receipts[1]['returncode'] == 0


def reference_syntax(tmp_path, name, source):
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit C++17 reference is required'
    unit = tmp_path / (name + '.cpp')
    unit.write_bytes(source.encode('utf-8'))
    argv = [compiler, '-std=c++17', '-pedantic-errors', '-fsyntax-only', str(unit)]
    result = subprocess.run(argv, capture_output=True, timeout=20)
    receipt = command_receipt(argv, result)
    receipt['source_sha256'] = sha256(unit.read_bytes())
    receipt['source_utf8_hex'] = unit.read_bytes().hex()
    (tmp_path / 'reference.json').write_text(json.dumps(receipt, indent=2))
    (tmp_path / 'reference.stdout.bin').write_bytes(result.stdout)
    (tmp_path / 'reference.stderr.bin').write_bytes(result.stderr)
    assert unit.read_bytes() == source.encode('utf-8')
    return result, unit


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code,message', INVALID,
                         ids=[row[0] for row in INVALID])
def test_invalid_merge_sources_have_real_reference_source_errors(name, source, code, message, tmp_path):
    result, unit = reference_syntax(tmp_path, name, source)
    assert result.returncode == 1, result.stderr
    assert result.stdout == b''
    assert re.search(rb':\d+:\d+: (?:fatal )?error:', result.stderr), result.stderr
    assert os.fsencode(unit.name) in result.stderr
    lowered = result.stderr.lower()
    assert not any(marker in lowered for marker in (
        b'internal compiler error', b'please submit a full bug report',
        b'segmentation fault', b'stack dump', b'assertion failed',
    )), result.stderr
