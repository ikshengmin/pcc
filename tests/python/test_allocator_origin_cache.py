"""The free-path origin hint does not replace managed-pointer provenance."""

import os
from pathlib import Path
import subprocess

from tests.owned_c_execution import compile_owned_c_with_runtime

ROOT = Path(__file__).resolve().parents[2]


def test_object_origin_preserves_unmarked_fallback_and_raw_pointer_rejection(
    tmp_path, pcc_runtime_archive,
):
    binary = tmp_path / "object_origin"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/allocator_object_origin.c", binary,
        runtime_archive=Path(pcc_runtime_archive),
    )
    run = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=30,
        env=dict(os.environ, PATH="/nonexistent"),
    )
    assert (run.returncode, run.stdout, run.stderr) == (
        0, "OBJECT_ORIGIN_CACHE_OK\n", "",
    )
