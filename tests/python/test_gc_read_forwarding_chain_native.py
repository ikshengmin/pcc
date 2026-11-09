"""Native public-ABI chains, not automatic evacuation or retirement.

Host PCC's owned C/self compiler links a provenance-verified runtime archive.
This is neither pcc1 compilation nor bootstrap qualification. Every subprocess
starts with a fresh chain and makes one selected read before raw assertions.
"""

import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.process_timeout import run_process_group_timeout


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def read_chain_binary(tmp_path_factory, threaded_pcc_runtime_archive):
    binary = tmp_path_factory.mktemp("gc-read-chain-native") / "read_chain"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/gc_read_forwarding_chain.c",
        binary,
        runtime_archive=Path(threaded_pcc_runtime_archive),
    )
    return binary


@pytest.mark.integration
@pytest.mark.parametrize("backend,edges,kind", [
    (backend, edges, kind)
    for backend in (3, 4)
    for edges in (0, 1, 2, 3, 16)
    for kind in range(4)
] + [
    (4, edges, 4) for edges in (0, 1, 2, 3, 16)
] + [
    (backend, 0, kind) for backend in (0, 1, 2) for kind in range(5)
] + [(3, 0, 4)])
def test_first_native_read_reaches_terminal(read_chain_binary, backend, edges, kind):
    # The effective config selects NoRC for all nonzero backends. Do not
    # pretend a second environment variable creates a counted moving mode.
    # GC3 cannot publicly forward ordinary user instances, hence its weakref
    # case is an identity control; GC4 tests genuine weakrefable instances.
    result = run_process_group_timeout(
        [str(read_chain_binary), str(edges), str(kind)],
        env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
        timeout=30,
    )
    assert (result.returncode, result.stdout, result.stderr) == (
        0, "GC_READ_CHAIN_NATIVE_OK\n", "",
    ), (backend, edges, kind, result)
