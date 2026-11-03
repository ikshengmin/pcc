"""Owned C emission and execution with an explicitly supplied current archive.

The session runtime fixture supplies an explicitly selected or admitted archive.
Run under the repository's shared lock and 90 s / 1 GiB tree-RSS guard.
"""

import os
from pathlib import Path
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[2]

from tests.owned_c_execution import compile_owned_c_with_runtime


@pytest.fixture(scope="module")
def scan_budget_binary(tmp_path_factory, pcc_runtime_archive):
    archive = pcc_runtime_archive
    source = Path(__file__).with_name("fixtures") / "gc_tracing_scan_budget_native.c"
    binary = tmp_path_factory.mktemp("gc_tracing_scan_budget") / "scan_budget"
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=Path(archive),
        cpp_args=("-I", str(REPO / "pcc/runtime/src")),
    )
    return binary


@pytest.mark.integration
@pytest.mark.xdist_group("gc_tracing_scan_budget")
@pytest.mark.parametrize("backend,tracing", [(1, 0), (2, 0), (3, 0), (4, 0), (4, 1)])
def test_real_objects_advance_one_node_and_stw_retains_progress(scan_budget_binary, backend, tracing):
    run = subprocess.run(
        [str(scan_budget_binary)], capture_output=True, text=True, timeout=20,
        env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend),
                 PCC_GC4_TRACING_ONLY="0", PCC_GC_TRACING_ONLY=str(tracing)),
    )
    expected = f"SCAN_BUDGET_NATIVE_OK backend={backend} tracing={tracing}\n"
    assert (run.returncode, run.stdout, run.stderr) == (0, expected, ""), (backend, tracing, run)
