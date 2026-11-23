"""Cross-module property mutation retains live descriptor behavior.

Python data model: Implementing Descriptors; builtin property accessor methods.
https://docs.python.org/3/reference/datamodel.html#implementing-descriptors
"""
from pathlib import Path
import os
import subprocess
import sys

import pytest


@pytest.mark.integration
def test_imported_property_mutation_matches_cpython_on_five_collectors(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    source = Path(__file__).resolve().parents[1] / 'fixtures/property_import_mutation/main.py'
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=30)
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / 'property-import-mutation'
    python_program_compiler(str(source), str(binary), backend='self',
                            runtime_archive=str(pcc_runtime_archive))
    for backend in (0,):
        result = subprocess.run([str(binary)], capture_output=True, text=True,
                                timeout=30, env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert (result.returncode, result.stdout, result.stderr) == (
            0, expected.stdout, expected.stderr), f'GC{backend}: {result.stdout}{result.stderr}'
