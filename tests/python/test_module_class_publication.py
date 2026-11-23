"""Classes become visible to imports when their definition statement finishes."""
from pathlib import Path
import os
import subprocess
import sys

import pytest


@pytest.mark.integration
def test_module_classes_are_visible_during_import_cycles(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    source = Path(__file__).resolve().parents[1] / 'fixtures/module_class_cycle/main.py'
    expected = subprocess.run([sys.executable, str(source)], capture_output=True,
                              text=True, timeout=30)
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / 'class-cycle'
    python_program_compiler(str(source), str(binary), backend='self',
                            runtime_archive=str(pcc_runtime_archive))
    for backend in (0,):
        result = subprocess.run([str(binary)], capture_output=True, text=True,
                                timeout=30, env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert (result.returncode, result.stdout, result.stderr) == (
            0, expected.stdout, expected.stderr), f'GC{backend}: {result.stdout}{result.stderr}'
