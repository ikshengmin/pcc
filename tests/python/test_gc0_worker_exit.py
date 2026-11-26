"""The default threaded full collector and explicit opt-out at normal process exit."""
from pathlib import Path
import subprocess

import pytest

from tests.owned_runtime_c_fixture import link_c_harness_bounded


@pytest.mark.parametrize(("mode", "status", "expected"), [
    ("default", 0, "exited during scan; worker joined"),
    ("exit", 19, "exited during scan; worker joined"),
    ("off", 0, "explicitly disabled"),
    ("single", 0, "one mutator scans without worker"),
])
def test_exit_during_old_generation_scan(tmp_path, threaded_pcc_runtime_archive, mode, status, expected):
    fixture = Path(__file__).with_name("fixtures") / "gc_worker_exit.c"
    binary = tmp_path / "worker_exit"
    linked = link_c_harness_bounded(fixture, binary, runtime_archive=threaded_pcc_runtime_archive)
    assert linked.returncode == 0, linked.stdout + linked.stderr
    run = subprocess.run([binary, mode], capture_output=True, text=True, timeout=30)
    assert (run.returncode, run.stdout.strip()) == (status, expected), run.stderr
