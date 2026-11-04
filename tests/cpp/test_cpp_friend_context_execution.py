"""Independent same-source observers for declaration-bound friend access.

Linux x86_64 host-PCC owned ELF and a separate explicit C++17 reference only.
The existing helpers retain normal returned outputs; timeout/launch errors
still leave incomplete process receipts. No pcc1 or project claim follows.
"""

import hashlib
import json
import os
import re

import pytest

from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_friend_class_execution import reference_syntax
from tests.cpp.test_cpp_friend_context_boundaries import INVALID, PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_friend_context_originals_match_cpp17(name, units, tmp_path, monkeypatch):
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


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_friend_context_originals_have_cpp17_source_diagnostics(name, source, code, tmp_path):
    result, original = reference_syntax(tmp_path, name, source)
    assert result.returncode == 1, result.stderr
    assert result.stdout == b''
    assert os.fsencode(original.name) in result.stderr
    assert re.search(rb':\d+:\d+: (?:fatal )?error:', result.stderr), result.stderr
    assert not any(marker in result.stderr.lower() for marker in (
        b'internal compiler error', b'please submit a full bug report',
        b'segmentation fault', b'stack dump', b'assertion failed',
    )), result.stderr
