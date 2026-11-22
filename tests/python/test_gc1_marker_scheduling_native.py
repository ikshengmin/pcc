"""Use an owned threaded runtime to record current GC marker startup."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
@pytest.mark.parametrize("backend", range(5), ids=[f"gc{i}" for i in range(5)])
def test_current_gc_marker_startup_and_root_lifetime(tmp_path, explicit_owned_runtime, backend):
    source = Path(__file__).with_name("fixtures") / "gc1_marker_scheduling_native.c"
    binary = tmp_path / "gc_marker_baseline"
    compile_owned_c_with_runtime(source, binary, runtime_archive=explicit_owned_runtime)
    result = _record_execution(
        tmp_path, "marker-baseline", [str(binary)], dict(os.environ, PCC_GC_BACKEND=str(backend)),
    )
    expected = f"GC1_MARKER_BASELINE_OK starts={int(backend != 0)}\n"
    assert (result["returncode"], result["stdout"], result["stderr"]) == (0, expected, ""), result
