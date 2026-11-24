"""Native scheduler roots retain their owners through explicit collection."""
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
def test_scheduler_root_lifetime(tmp_path, marker_binary):
    result = _record_execution(
        tmp_path, "scheduler-root-lifetime", [str(marker_binary)],
        dict(os.environ, PATH="/nonexistent"),
    )
    assert (result["returncode"], result["stdout"], result["stderr"]) == (
        0, "SCHEDULER_ROOT_LIFETIME_OK\n", "",
    ), result
