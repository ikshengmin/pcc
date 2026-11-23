"""The automatic full collector budgets reference slots, not just objects."""
from pathlib import Path
import subprocess

from tests.owned_runtime_c_fixture import link_c_harness_bounded


def test_large_old_list_is_scanned_in_multiple_stops_with_mutation(
    tmp_path: Path, pcc_runtime_archive: Path,
) -> None:
    fixture = Path(__file__).with_name("fixtures") / "gc_full_high_fanout.c"
    binary = tmp_path / "gc_full_high_fanout"
    build = link_c_harness_bounded(fixture, binary, runtime_archive=pcc_runtime_archive)
    assert build.returncode == 0, build.stdout + build.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
