"""Native scheduling arithmetic of stock GC2 before a separate pacer."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc2_stock_debt_threshold_and_budget(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc2_debt_threshold_native.c"
    binary = tmp_path / "gc2_debt"
    compile_owned_c_with_runtime(source, binary, runtime_archive=explicit_owned_runtime)
    result = _record_execution(
        tmp_path, "gc2-debt", [str(binary)], dict(os.environ, PCC_GC_BACKEND="2"),
    )
    assert (result["returncode"], result["stdout"], result["stderr"]) == (
        0, "GC2_STOCK_DEBT_BASELINE_OK\n", "",
    ), result
