"""Compiled bytearray append conversion must match the same CPython program."""

import os
from pathlib import Path
import subprocess
import sys

from pcc.frontends.python.pipeline import compile_python


_FIXTURES = Path(__file__).parent / "fixtures" / "bytearray_append"


def compare_with_cpython(tmp_path, pcc_runtime_archive, case):
    source = _FIXTURES / (case + ".py")
    expected = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=10, check=True,
    ).stdout
    output = tmp_path / case
    compile_python(str(source), str(output), backend="self", runtime_archive=str(pcc_runtime_archive))
    for backend in (0,):
        result = subprocess.run(
            [str(output)], capture_output=True, text=True, timeout=10,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
        )
        assert (result.returncode, result.stdout) == (0, expected), (backend, result.stderr)

def test_bytearray_append_index_conversion(tmp_path, pcc_runtime_archive):
    compare_with_cpython(tmp_path, pcc_runtime_archive, "conversion")
