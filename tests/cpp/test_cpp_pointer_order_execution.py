"""Same-source C++17 owned/reference pointer ordering, not project acceptance."""

import os
import shutil
import subprocess

import pytest

from tests.cpp.sizeof_execution import execute_public_row_pair, execute_row_pair
from tests.cpp.test_cpp_pointer_order import INVALID_OPERANDS, PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_pointer_order_originals_execute_against_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize('opt', (0, 2))
def test_public_pointer_order_executes_at_both_optimization_levels(opt, tmp_path, monkeypatch):
    source = ('int main(){int a[4][3]{};int(*p)[3]=a;const int(*q)[3]=a+4;'
              'if(!(p<q)||!(p<=q)||!(q>p)||!(q>=p))return 1;'
              'if(p>=q||q<=p||p>q||q<p)return 2;return 42;}')
    execute_public_row_pair(source, opt, tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.parametrize('op', ('<', '<=', '>', '>='))
@pytest.mark.parametrize('name,parameters,left,right', INVALID_OPERANDS,
                         ids=[row[0] for row in INVALID_OPERANDS])
def test_invalid_order_operands_are_rejected_by_real_cpp17(op, name, parameters, left, right, tmp_path):
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit C++17 reference is required'
    source = tmp_path / (name + '.cpp')
    source.write_text('bool f(' + parameters + '){return ' + left + op + right + ';}')
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-fsyntax-only', str(source)],
                            capture_output=True, timeout=20)
    (tmp_path / 'reference.stdout').write_bytes(result.stdout)
    (tmp_path / 'reference.stderr').write_bytes(result.stderr)
    assert result.returncode != 0
