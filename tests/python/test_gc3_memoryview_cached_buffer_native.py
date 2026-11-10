"""Retained Py_buffer pointers across real bounded GC3 promotion and sweep.

Host pcc0 owns C parsing, self object emission and linking. Native execution
uses the explicit source-matched threaded archive, never a mock collector or
host C compiler. Current GC3 selects count-free ownership unconditionally;
provenance-probe variants are not mislabeled as a counted GC3 mode.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.process_timeout import run_process_group_timeout


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("fixtures") / "gc3_memoryview_cached_buffer.c"


@pytest.mark.integration
@pytest.mark.parametrize("writable", (0, 1), ids=("bytes", "bytearray"))
def test_gc3_retained_memoryview_buffer_native(tmp_path, writable):
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive

    selected = os.environ.get("PCC_RUNTIME_ARCHIVE")
    assert selected, "Supply a source-matched threaded PCC_RUNTIME_ARCHIVE"
    assert os.environ.get("PCC_TEST_NO_NATIVE_PROVISIONING") == "1", (
        "Use the existing no-native-provisioning guard"
    )
    archive, manifest = _verified_test_runtime_archive(
        selected, threads=True, runtime_root=ROOT / "pcc/runtime",
    )
    source_hash = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    binary = tmp_path / "gc3_memoryview_buffer"
    receipt = {
        "status": "NOT_EXECUTED",
        "scope": "host pcc0 / owned self / native GC3 count-free",
        "counted_gc3": "not supported by current public collector configuration",
        "source_sha256": source_hash,
        "runtime_sha256": archive_hash,
        "runtime_provenance": manifest,
        "writable": writable,
        "runs": [],
    }
    receipt_path = tmp_path / "gc3-memoryview-buffer.json"
    try:
        compile_owned_c_with_runtime(FIXTURE, binary, runtime_archive=archive)
        receipt["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
        for probe in ("0", "2"):
            environment = dict(
                os.environ,
                PATH="/nonexistent",
                PCC_GC_BACKEND="3",
                PCC_GC_REFCOUNT_PROVENANCE_PROBE=probe,
                PCC_THREADS="1",
                PCC_REFCOUNT_KIND="atomic",
                PCC_GC_MINOR_HEAP_SIZE="1048576",
                PCC_GC_MINOR_ALLOC_MAX="4096",
                PCC_LOG="",
            )
            result = run_process_group_timeout(
                [str(binary), str(writable)],
                env=environment,
                timeout=20,
                stderr_path=tmp_path / f"probe-{probe}.stderr",
            )
            (tmp_path / f"probe-{probe}.stdout").write_text(result.stdout)
            receipt["runs"].append({
                "provenance_probe": probe,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            })
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
            assert (result.returncode, result.stdout, result.stderr) == (
                0, "GC3_CACHED_MEMORYVIEW_BUFFER_OK\n", "",
            ), (writable, probe, receipt)
        assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == source_hash
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == archive_hash
        receipt["status"] = "PASS"
    except Exception as error:
        receipt["status"] = "FAIL"
        receipt["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
