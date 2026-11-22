"""Qualify the default-off bridge in actual automatic GC2 mark and sweep."""
import os
from pathlib import Path
import re

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc2_actual_list_bridge_preserves_automatic_collection(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc2_actual_list_bridge_native.c"
    binary = tmp_path / "gc2_actual_list_bridge"
    compile_owned_c_with_runtime(source, binary, runtime_archive=explicit_owned_runtime)
    env = dict(os.environ, PCC_GC_BACKEND="2", PCC_GC_DEBT_THRESHOLD="131072")
    for repeat in range(3):
        for workers in (1, 4):
            for enabled in (0, 1) if repeat % 2 == 0 else (1, 0):
                result = _record_execution(
                    tmp_path, f"bridge-{workers}-{enabled}-{repeat}",
                    [str(binary), str(workers), str(enabled)], env,
                )
                assert (result["returncode"], result["stderr"]) == (0, ""), result
                count = r"[1-9]\d*" if enabled else "0"
                assert re.fullmatch(
                    rf"GC2_ACTUAL_LIST_BRIDGE_OK workers={workers} enabled={enabled} "
                    rf"visits={count} fallbacks={count} commits=[1-9]\d*\n", result["stdout"],
                ), result
