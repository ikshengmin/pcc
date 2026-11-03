"""Native early-completion/late-root regression.

Use the shared runtime fixture and normal 90 s / 1 GiB guard. The helper is
compiled through the owned C path and executed in both completion windows.
"""

import os
from pathlib import Path
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[2]

from tests.owned_c_execution import compile_owned_c_with_runtime


@pytest.fixture(scope="module")
def empty_gray_binary(tmp_path_factory, pcc_runtime_archive):
    source = Path(__file__).with_name("fixtures") / "empty_gray_late_roots_native.c"
    binary = tmp_path_factory.mktemp("empty_gray_late_roots") / "empty_gray"
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=pcc_runtime_archive,
        cpp_args=("-I", str(REPO / "pcc/runtime/src")),
    )
    return binary


@pytest.mark.integration
@pytest.mark.xdist_group("empty_gray_late_roots")
@pytest.mark.parametrize("last_gray", [0, 1], ids=["already_empty", "last_gray_cleared"])
def test_real_claim_remark_preserves_late_root_and_barrier_work(empty_gray_binary, last_gray):
    run = subprocess.run(
        [str(empty_gray_binary), str(last_gray)], capture_output=True, text=True, timeout=20,
        env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND="4",
                 PCC_GC4_TRACING_ONLY="0", PCC_GC_TRACING_ONLY="1"),
    )
    expected = (f"EMPTY_GRAY_WINDOW_OK entry={last_gray} backend=4 tracing=1 "
                "commits=1 late_root=black child=black stw=1\n")
    assert (run.returncode, run.stdout, run.stderr) == (0, expected, ""), (last_gray, run)
