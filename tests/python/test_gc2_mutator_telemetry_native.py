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
            _assert_workload(tmp_path, binary, env, workers, repeat)
            _assert_workload(tmp_path, binary, env, workers, repeat, ("hold",))


def _assert_workload(tmp_path, binary, env, workers, repeat, mode=()):
    suffix = "-hold" if mode else ""
    result = _record_execution(
        tmp_path, f"w{workers}-{repeat}{suffix}", [str(binary), str(workers), *mode], env,
    )
    assert (result["returncode"], result["stderr"]) == (0, ""), result
    output = result["stdout"].splitlines()
    stages = ("before", "mid", "held", "polled", "joined", "drained") if mode else (
        "before", "mid", "joined", "drained",
    )
    assert len(output) == 1 + len(stages), result
    assert re.fullmatch(
        rf"GC2_MUTATOR_TELEMETRY_OK workers={workers} lists=48000 "
        r"assists=\d+ traces=[1-9]\d* sweeps=[1-9]\d* "
        r"work_stw=\d+ stw=[1-9]\d* max_stw_us=[1-9]\d*",
        output[0],
    ), result
    values = {}
    for stage, line in zip(stages, output[1:], strict=True):
        match = re.fullmatch(
            rf"{stage} epoch=(\d+) finishes=(\d+) sweeps=(\d+) "
            r"live_bytes=(\d+) mark=[01] gray=(\d+) flush=[01] finish=[01] sweep=[01] "
            r"published=[01] pending=[01] fallback=[01] debt=\d+ traces=\d+ assists=\d+ write_shades=\d+",
            line,
        )
        assert match, (stage, result)
        values[stage] = tuple(map(int, match.groups()))
    for index in (1, 2):
        series = [stage[index] for stage in values.values()]
        assert series == sorted(series), result
        assert series[-1] > series[0], result
    assert values["before"][3] > 0 and values["drained"][3] > 0, result
