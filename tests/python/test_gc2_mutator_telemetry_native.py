"""Observe scheduling telemetry on stock GC2 at fixed total allocation work."""
import os
from pathlib import Path
import re

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.owned_regression_support import _record_execution, explicit_owned_runtime


@pytest.mark.integration
def test_gc2_one_and_four_mutators_publish_scheduler_telemetry(tmp_path, explicit_owned_runtime):
    source = Path(__file__).with_name("fixtures") / "gc2_mutator_telemetry_native.c"
    binary = tmp_path / "gc2_mutator_telemetry"
    compile_owned_c_with_runtime(source, binary, runtime_archive=explicit_owned_runtime)
    env = dict(os.environ, PCC_GC_BACKEND="2", PCC_GC_DEBT_THRESHOLD="131072")
    for repeat in range(3):
        for workers in (1, 4) if repeat % 2 == 0 else (4, 1):
            result = _record_execution(
                tmp_path, f"w{workers}-{repeat}", [str(binary), str(workers)], env,
            )
            assert (result["returncode"], result["stderr"]) == (0, ""), result
            assert re.fullmatch(
                rf"GC2_MUTATOR_TELEMETRY_OK workers={workers} lists=48000 "
                r"assists=\d+ traces=[1-9]\d* sweeps=[1-9]\d* "
                r"work_stw=\d+ stw=[1-9]\d* max_stw_us=[1-9]\d*\n",
                result["stdout"],
            ), result
