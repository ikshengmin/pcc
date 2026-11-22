"""Open a real stopped-world cut while a GC2 list visitor is in flight."""
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc2_list_scan_publishes_before_stop_only_with_protection(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc2_list_scan_stop_native.c"
    binary = tmp_path / "gc2_list_scan_stop"
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=explicit_owned_runtime,
        cpp_args=("-I", str(Path(__file__).resolve().parents[2] / "pcc/runtime/src")),
    )
    env = dict(os.environ, PCC_GC_BACKEND="2")
    for repeat in range(3):
        for locked in (0, 1):
            result = _record_execution(
                tmp_path, f"list-stop-{locked}-{repeat}", [str(binary), str(locked)], env,
            )
            assert (result["returncode"], result["stderr"]) == (0, ""), result
            assert result["stdout"] == (
                f"GC2_LIST_SCAN_STOP_OK locked={locked} credit_at_cut={locked}\n"
            ), result
