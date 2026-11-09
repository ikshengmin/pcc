"""Real native pending-stop publication and borrowed-root remap witness.

Contract: docs/project-intent.md five-GC roots; docs/compiler-contract.md
owned runtime execution. This does not claim relocation at a runtime poll,
NoRC finalizer timing equivalence, or stop latency under graph contention.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.owned_runtime_c_fixture import link_c_harness_bounded
from tests.python.process_timeout import run_process_group_timeout

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).with_name("fixtures") / "generator_frame_clear_pending_stop.c"


@pytest.mark.integration
def test_native_clear_services_pending_stop_with_published_borrowed_frame(tmp_path, pcc_runtime_archive):
    binary = tmp_path / "frame-clear-pending-stop"
    compiled = link_c_harness_bounded(
        SOURCE, binary, runtime_archive=pcc_runtime_archive,
        extra_include_dirs=(str(ROOT / "pcc/runtime/src"),), timeout=60,
    )
    assert compiled.returncode == 0, (compiled.stdout, compiled.stderr)
    receipt = {"source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
               "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "runs": []}
    for backend, forwarded in ((3, 0), (4, 0), (4, 1)):
        command = [str(binary)] + (["forwarded"] if forwarded else [])
        result = run_process_group_timeout(
            command, timeout=20,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_LOG="",
                     PCC_GC_REFCOUNT_PROVENANCE_PROBE="0", PCC_GC_MINOR_ALLOC_MAX="4096"),
            stderr_path=tmp_path / f"gc{backend}-forwarded{forwarded}.stderr",
        )
        (tmp_path / f"gc{backend}-forwarded{forwarded}.stdout").write_text(result.stdout)
        receipt["runs"].append({"backend": backend, "forwarded": forwarded,
                                "returncode": result.returncode,
                                "stdout": result.stdout, "stderr": result.stderr})
        (tmp_path / "pending-stop.json").write_text(json.dumps(receipt, indent=2) + "\n")
        assert (result.returncode, result.stdout, result.stderr) == (
            0, f"FRAME_CLEAR_PENDING_STOP_OK gc={backend} forwarded={forwarded}\n", "",
        ), (backend, forwarded)
