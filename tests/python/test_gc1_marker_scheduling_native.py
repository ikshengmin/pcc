"""Use an owned threaded runtime to record current GC marker startup."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.fixture
def marker_binary(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc1_marker_scheduling_native.c"
    binary = tmp_path / "gc_marker_baseline"
    compile_owned_c_with_runtime(source, binary, runtime_archive=explicit_owned_runtime)
    return binary


@pytest.mark.integration
@pytest.mark.parametrize("backend", range(5), ids=[f"gc{i}" for i in range(5)])
def test_current_gc_marker_startup_and_root_lifetime(tmp_path, marker_binary, backend):
    result = _record_execution(
        tmp_path, "marker-baseline", [str(marker_binary)],
        dict(os.environ, PCC_GC_BACKEND=str(backend)),
    )
    expected = f"GC1_MARKER_BASELINE_OK starts={int(backend >= 2)}\n"
    assert (result["returncode"], result["stdout"], result["stderr"]) == (0, expected, ""), result



@pytest.mark.integration
def test_gc1_automatic_progress_reaches_mark_and_sweep_without_explicit_collect(
    tmp_path, marker_binary,
):
    env = dict(os.environ, PCC_GC_BACKEND="1", PCC_GC_DEBT_THRESHOLD="131072")
    result = _record_execution(tmp_path, "automatic", [str(marker_binary), "0"], env)
    assert (result["returncode"], result["stdout"], result["stderr"]) == (
        0, "GC1_AUTOMATIC_PROGRESS_OK starts=0\n", "",
    ), result
    # The exact same live workload rejects a claim that candidate GC1
    # made this progress with a marker thread. Status 26 is checked only
    # after a real mark commit and candidate sweep have been observed.
    control = _record_execution(tmp_path, "worker-negative", [str(marker_binary), "1"], env)
    assert (control["returncode"], control["stdout"], control["stderr"]) == (
        26, "", "",
    ), control
