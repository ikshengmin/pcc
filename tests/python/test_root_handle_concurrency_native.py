"""Execute the owned C probe with an explicitly supplied pcc_runtime_archive.

No runtime build fallback. Use the shared performance lock, six pytest workers,
and 90 s / 1 GiB tree guards. The native child itself has a 20-second timeout.
"""

import os
from pathlib import Path
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[2]

from tests.owned_c_execution import compile_owned_c_with_runtime


@pytest.fixture(scope="module")
def root_handle_binary(tmp_path_factory, pcc_runtime_archive):
    source = Path(__file__).with_name("fixtures") / "root_handle_concurrency_native.c"
    binary = tmp_path_factory.mktemp("root_handle_concurrency") / "root_handles"
    compile_owned_c_with_runtime(
        source, binary, runtime_archive=pcc_runtime_archive,
        cpp_args=("-I", str(REPO / "pcc/runtime/src")),
    )
    return binary


@pytest.mark.integration
@pytest.mark.xdist_group("root_handle_concurrency")
@pytest.mark.parametrize("backend,tracing", [(0, 0), (3, 0), (4, 0), (4, 1)])
def test_native_handle_revision_running_state_and_stw_rewrite(root_handle_binary, backend, tracing):
    run = subprocess.run(
        [str(root_handle_binary)], capture_output=True, text=True, timeout=20,
        env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend),
                 PCC_GC4_TRACING_ONLY="0", PCC_GC_TRACING_ONLY=str(tracing)),
    )
    expected = (f"ROOT_HANDLE_NATIVE_OK backend={backend} tracing={tracing} "
                "mutations=6152 roots=0 enumerated=4\n")
    assert (run.returncode, run.stdout, run.stderr) == (0, expected, ""), (backend, tracing, run)
