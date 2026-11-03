"""Same-original reference-result/operator[] CI obligations, execution UNRUN."""

import json
import os
import shutil
import subprocess

import pytest

from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_template_subscript_results import INVALID, PROGRAMS


# N4659 [temp.res]/8: the dependent pointer cannot make a floating index valid
# in any specialization, and this original never instantiates the template.
# https://timsong-cpp.github.io/cppwp/n4659/temp.res#8
# Keep the eager PCC rejection and report IFNDR reference acceptance as such.
IFNDR = {
    'bad-nondependent-index': (
        'template<class T>struct Box{T*p;T get(){return p[0.5];}};',
        'cpp.invalid-subscript',
    ),
}


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_reference_subscript_originals_match_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_reference_subscript_original_is_rejected_by_reference(name, source, code, tmp_path,
                                                                      record_property):
    ifndr = name in IFNDR
    if ifndr:
        assert (source, code) == IFNDR[name]
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit C++17 negative reference is required'
    unit = tmp_path / (name+'.cpp')
    unit.write_text(source)
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-fsyntax-only', str(unit)],
                            capture_output=True, timeout=20)
    (tmp_path / 'reference.stdout.bin').write_bytes(result.stdout)
    (tmp_path / 'reference.stderr.bin').write_bytes(result.stderr)
    classification = 'IFNDR' if ifndr else 'diagnostic-required'
    outcome = {0: 'accepted', 1: 'rejected'}.get(result.returncode, 'abnormal-exit')
    (tmp_path / 'reference.result.json').write_text(json.dumps({
        'classification': classification,
        'rule': 'N4659 [temp.res]/8' if ifndr else 'diagnostic required',
        'command': result.args, 'returncode': result.returncode,
        'outcome': outcome,
        'stdout_hex': result.stdout.hex(), 'stderr_hex': result.stderr.hex(),
        'pcc_expected_diagnostic': code,
    }, indent=2))
    record_property('reference_classification', classification)
    record_property('reference_returncode', result.returncode)
    record_property('reference_outcome', outcome)
    if ifndr:
        # No diagnostic is required; an abnormal driver status still fails.
        assert result.returncode in (0, 1)
    else:
        assert result.returncode != 0
