"""Same-original owned-native/reference array-bound obligations, UNRUN.

Host pcc0 emits and executes native C++ programs here. This does not claim
that a native pcc1 frontend or a bootstrap fixed point has been validated.
"""

import json
import os
import shutil
import subprocess

import pytest

from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_template_array_bounds import INVALID, PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_template_array_bound_originals_match_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_template_array_bound_reference_rejection(name, source, code, tmp_path, record_property):
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit C++17 reference compiler is required'
    original = tmp_path / (name + '.cpp')
    original.write_text(source)
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-fsyntax-only', str(original)],
                            capture_output=True, timeout=20)
    (tmp_path / 'reference.stdout.bin').write_bytes(result.stdout)
    (tmp_path / 'reference.stderr.bin').write_bytes(result.stderr)
    # A nondependent floating bound invalidates every possible specialization:
    # [temp.res]/8 makes the unused primary IFNDR, unlike the instantiated rows.
    ifndr = name == 'nonintegral-dependent-expression'
    classification = 'IFNDR' if ifndr else 'diagnostic-required'
    (tmp_path / 'reference.json').write_text(json.dumps({
        'classification': classification, 'returncode': result.returncode,
        'command': result.args, 'pcc_expected_diagnostic': code,
    }, indent=2))
    record_property('reference_classification', classification)
    record_property('reference_returncode', result.returncode)
    if ifndr:
        assert result.returncode in (0, 1)
    else:
        assert result.returncode != 0
