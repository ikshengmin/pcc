"""The automatic full collector budgets reference slots, not just objects."""
from pathlib import Path
import subprocess

import pytest

from tests.owned_runtime_c_fixture import link_c_harness_bounded


@pytest.mark.parametrize("shape", ["mutation", "wide-gray"])
def test_large_old_list_is_scanned_in_multiple_stops(
    tmp_path: Path, pcc_runtime_archive: Path, shape: str,
) -> None:
    fixture = Path(__file__).with_name("fixtures") / "gc_full_high_fanout.c"
    binary = tmp_path / "gc_full_high_fanout"
    build = link_c_harness_bounded(fixture, binary, runtime_archive=pcc_runtime_archive)
    assert build.returncode == 0, build.stdout + build.stderr
    args = [str(binary)] if shape == "mutation" else [str(binary), shape]
    run = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)


@pytest.mark.parametrize("layout", ["flat", "grouped", "mixed", "sliced", "racing-young", "disabled"])
def test_large_unreachable_component_survives_handover_until_exact_reclaim(
    tmp_path: Path, pcc_runtime_archive: Path, layout: str,
) -> None:
    fixture = Path(__file__).with_name("fixtures") / "gc_full_unreachable_scaling.c"
    binary = tmp_path / "gc_full_unreachable_scaling"
    build = link_c_harness_bounded(fixture, binary, runtime_archive=pcc_runtime_archive)
    assert build.returncode == 0, build.stdout + build.stderr
    args = [str(binary), "20000"]
    if layout == "mixed":
        args = [str(binary), layout]
    elif layout != "flat":
        args.append(layout)
    run = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
