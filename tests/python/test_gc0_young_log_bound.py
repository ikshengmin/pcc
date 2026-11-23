"""Young tracking remains bounded even when no net growth requests a collection."""
from pathlib import Path
import subprocess

import pytest

from tests.owned_runtime_c_fixture import link_c_harness_bounded


@pytest.mark.parametrize("scenario", ["churn", "thread-exit"])
def test_young_log_remains_bounded_preserving_young_collection(
    tmp_path: Path, pcc_runtime_archive: Path, scenario: str,
) -> None:
    fixture = Path(__file__).with_name("fixtures") / "young_log_bound.c"
    binary = tmp_path / "young_log_bound"
    build = link_c_harness_bounded(fixture, binary, runtime_archive=pcc_runtime_archive)
    assert build.returncode == 0, build.stdout + build.stderr
    args = [str(binary)] if scenario == "churn" else [str(binary), scenario]
    run = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
