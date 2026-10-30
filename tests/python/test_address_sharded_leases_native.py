"""Native public-token transfer, indexed overlap and reset exclusion gate."""
import os
from pathlib import Path
import subprocess

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime


@pytest.mark.integration
def test_native_address_shards_public_tokens_five_gc(tmp_path, threaded_pcc_runtime_archive):
    source = Path(__file__).resolve().parents[1] / "fixtures/native/address_sharded_leases.c"
    output = tmp_path / "address_sharded_leases"
    compile_owned_c_with_runtime(source, output, runtime_archive=threaded_pcc_runtime_archive)
    for backend in range(5):
        for different in (0, 1):
            for probe in ("0", "2"):
                result = subprocess.run(
                    [str(output), str(backend), str(different)], capture_output=True,
                    text=True, timeout=45, env=dict(os.environ, PATH="/nonexistent",
                                                   PCC_GC_REFCOUNT_PROVENANCE_PROBE=probe),
                )
                assert (result.returncode, result.stdout, result.stderr) == (
                    0, "ADDRESS_SHARDED_LEASES_OK\n", "",
                ), (backend, different, probe, result.returncode, result.stdout, result.stderr)
