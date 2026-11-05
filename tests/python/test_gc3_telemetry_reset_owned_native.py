"""Run the unchanged five GC3 reset scenarios through the owned C linker.

This is a sequential, no-worker remembered-work regression. The original C
input checks pending state, identity, values, promotion, re-enqueue and stable
empty queue state. It does not prove a real worker join, concurrent safety,
sole-root survival, reclamation, native-pcc1 ownership or performance.

The separate host-CC probe and its failures are not replaced by this driver.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime"
SOURCE = Path(__file__).parent / "fixtures/gc3_telemetry_reset/remembered_work.c"
SOURCE_SHA256 = "047499f6be09ced1c3a9ea4e0a59c30b447b6fda59a3f340cca26ee3d46af894"
SCENARIOS = ((0, 0), (0, 1), (0, 2), (1, 0), (1, 1))
HEADERS = (
    "pcc/runtime/src/py_internal.h",
    "pcc/runtime/include/py_runtime.h",
    "utils/fake_libc_include/stdint.h",
    "utils/fake_libc_include/stddef.h",
    "utils/fake_libc_include/stdio.h",
    "utils/fake_libc_include/stdlib.h",
    "utils/fake_libc_include/_fake_defines.h",
    "utils/fake_libc_include/_fake_typedefs.h",
    "utils/fake_libc_include/__pcc_builtin_va_list.h",
)


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _header_hashes():
    return {name: _sha256((ROOT / name).read_bytes()) for name in HEADERS}


def test_owned_gc3_probe_preserves_original_source_and_five_scenarios():
    source = SOURCE.read_bytes()
    assert len(source) == 7366 and _sha256(source) == SOURCE_SHA256
    assert SCENARIOS == ((0, 0), (0, 1), (0, 2), (1, 0), (1, 1))


@pytest.mark.integration
@pytest.mark.xdist_group("gc3_owned_reset_compile")
@pytest.mark.pcc_gate(
    probe=lambda: sys.platform == "linux" and platform.machine() == "x86_64",
)
@pytest.mark.parametrize("overflow,resets", SCENARIOS)
def test_owned_gc3_reset_preserves_remembered_work(tmp_path, monkeypatch, overflow, resets):
    from pcc.backend.elf_x86_64 import parse_static_executable
    from tests.owned_runtime_c_fixture import link_c_harness_bounded
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive

    assert sys.platform == "linux" and platform.machine() == "x86_64"
    assert (overflow, resets) in SCENARIOS
    assert os.environ.get("PCC_TEST_NO_NATIVE_PROVISIONING") == "1", (
        "run with the existing no-native-provisioning guard"
    )
    explicit = os.environ.get("PCC_RUNTIME_ARCHIVE")
    assert explicit, "supply a source-matching prebuilt PCC_RUNTIME_ARCHIVE"
    archive, manifest = _verified_test_runtime_archive(
        explicit, threads=True, runtime_root=RUNTIME,
    )
    assert manifest["target_triple"] == "x86_64-unknown-linux-gnu"
    assert all(
        row.get("object_emitter") == "pcc-self-backend-object-writer"
        and row.get("uses_host_cc") is False
        for row in manifest["members"]
    )
    source_bytes = SOURCE.read_bytes()
    assert len(source_bytes) == 7366 and _sha256(source_bytes) == SOURCE_SHA256
    archive_sha256 = _sha256(archive.read_bytes())
    headers = _header_hashes()
    for name, value in {
        "PCC_RUNTIME_ARCHIVE": str(archive),
        "PCC_RUNTIME_DIR": str(RUNTIME),
        "PCC_RUNTIME_CC": "pcc",
        "PCC_RUNTIME_HIGH": "py",
        "PCC_WITH_THREADS": "1",
        "PCC_SELF_LINK": "pcc",
        "PCC_SELF_OBJ": "pcc",
    }.items():
        monkeypatch.setenv(name, value)

    source = tmp_path / "gc3_reset.c"
    binary = tmp_path / "gc3_reset_owned"
    source.write_bytes(source_bytes)
    receipt = {
        "status": "NOT_EXECUTED",
        "scenario": {"overflow": overflow, "resets": resets},
        "source_sha256": SOURCE_SHA256,
        "archive_sha256": archive_sha256,
        "runtime_provenance": manifest,
        "header_sha256": headers,
        "driver_sha256": _sha256(Path(__file__).read_bytes()),
        "owned_helper_sha256": _sha256((ROOT / "tests/owned_runtime_c_fixture.py").read_bytes()),
        "scope": "host pcc0 owned native; sequential no-worker GC3 remembered-work only",
    }
    try:
        receipt["phase"] = "compile_and_link"
        # Reuse the existing process-group supervisor, including cleanup on
        # timeout. Its host-pcc0 child re-admits this archive and forbids
        # subprocess.Popen during C parsing/emission/linking. No host compiler,
        # new watchdog, source rewrite or archive subset is introduced.
        built = link_c_harness_bounded(
            source, binary, runtime_archive=archive,
            extra_include_dirs=(RUNTIME / "src",), timeout=30,
        )
        receipt.update(
            compile_returncode=built.returncode,
            compile_stdout=built.stdout,
            compile_stderr=built.stderr,
        )
        compile_stderr = Path(str(binary) + ".compile.stderr")
        if compile_stderr.exists():
            receipt["compile_stderr_file_hex"] = compile_stderr.read_bytes().hex()
        assert built.returncode == 0, built.stdout + built.stderr

        receipt["phase"] = "verify_owned_image"
        image = binary.read_bytes()
        receipt["binary_sha256"] = _sha256(image)
        layout = parse_static_executable(image)
        receipt["layout"] = layout
        assert layout["entry"] > 0 and layout["tls_segments"] == 1
        assert _sha256(archive.read_bytes()) == archive_sha256
        assert _header_hashes() == headers
        assert source.read_bytes() == source_bytes

        receipt["phase"] = "native_execution"
        env = os.environ.copy()
        env.update({
            "PCC_GC_BACKEND": "3",
            "PCC_GC_MINOR_HEAP_SIZE": "1048576",
            "PCC_GC_MINOR_ALLOC_MAX": "16",
        })
        try:
            run = subprocess.run(
                [str(binary), str(overflow), str(resets)],
                capture_output=True, timeout=20, env=env,
            )
        except subprocess.TimeoutExpired as exc:
            # subprocess.run has already killed and waited for this child.
            receipt.update(
                status="TIMEOUT", timeout_s=20,
                stdout_hex=(exc.stdout or b"").hex(),
                stderr_hex=(exc.stderr or b"").hex(),
            )
            raise
        receipt.update(
            returncode=run.returncode,
            stdout=run.stdout.decode("utf-8", errors="replace"),
            stderr=run.stderr.decode("utf-8", errors="replace"),
            stdout_hex=run.stdout.hex(), stderr_hex=run.stderr.hex(),
        )
        receipt["source_sha256_after"] = _sha256(source.read_bytes())
        receipt["fixture_sha256_after"] = _sha256(SOURCE.read_bytes())
        receipt["archive_sha256_after"] = _sha256(archive.read_bytes())
        assert receipt["source_sha256_after"] == SOURCE_SHA256
        assert receipt["fixture_sha256_after"] == SOURCE_SHA256
        assert receipt["archive_sha256_after"] == archive_sha256
        assert _header_hashes() == headers
        # Preserve the original five-case result requirements. Stderr remains
        # raw evidence rather than adding or dropping a success condition.
        assert run.returncode == 0, receipt
        assert run.stdout == b"gc3-reset-remembered-native-ok\n", receipt
        receipt["status"] = "PASS"
    except Exception as exc:
        if receipt["status"] != "TIMEOUT":
            receipt["status"] = "FAIL"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        try:
            (tmp_path / "owned-receipt.json").write_text(
                json.dumps(receipt, indent=2) + "\n", encoding="utf-8",
            )
        except OSError as write_error:
            exc.add_note(f"Could not save owned receipt: {write_error}")
        raise
    (tmp_path / "owned-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8",
    )
