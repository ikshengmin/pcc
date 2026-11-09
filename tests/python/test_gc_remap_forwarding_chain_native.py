"""Native ABI regression for first-pass full-chain heap remapping.

The public installer supplies the chain, not automatic relocation. This tests
real runtime entrypoints/allocations and never uses a model or ordinary read
barrier to establish success. It does not prove production retirement, freed
source storage, pcc1 compilation, bootstrap, or concurrent remap scheduling.
"""

import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.process_timeout import run_process_group_timeout


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def remap_chain_binary(tmp_path_factory, threaded_pcc_runtime_archive):
    binary = tmp_path_factory.mktemp("gc-remap-chain-native") / "remap_chain"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/gc_remap_forwarding_chain.c",
        binary,
        runtime_archive=Path(threaded_pcc_runtime_archive),
    )
    return binary


@pytest.mark.integration
@pytest.mark.parametrize("backend,edges", [
    (backend, edges)
    for backend in (3, 4)
    for edges in (0, 1, 2, 3, 16)
] + [(backend, 0) for backend in (0, 1, 2)])
def test_first_native_remap_reaches_terminal(remap_chain_binary, backend, edges):
    # pcc_gc_config_ensure fixes NoRC for every nonzero backend; there is no
    # separate supported count-mode environment switch. Select the backend
    # explicitly and let the native fixture reject an actual counted runtime
    # rather than silently accepting an inherited configuration mismatch.
    result = run_process_group_timeout(
        [str(remap_chain_binary), str(edges)],
        env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
        timeout=30,
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        0, "GC_REMAP_CHAIN_NATIVE_OK\n", "",
    ), (backend, edges, result)
