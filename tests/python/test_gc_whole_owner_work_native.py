"""Owned native whole-owner pacing on real managed lists and GC roots.

A controlled single-mutator mark phase, not collector concurrency, relocation,
full-GC, native-pcc1 or performance qualification. Archive build is separate.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc/runtime"
ORIGINAL_SOURCE = Path(__file__).parent / "fixtures/gc_whole_owner_work_native.c"
TARGET = "x86_64-unknown-linux-gnu"
SOURCE_SHA256 = "a6b545d9e5ebf3a4dd6ecf5e422b58b8cbece6654ba925da528cb51bc3c2b156"
REQUIRED_MEMBERS = (
    "freestanding_gc_common_mark_cycle.o",
    "freestanding_gc_object_slots.o",
    "freestanding_gc_public_collection.o",
    "freestanding_gc_incremental_concurrent_scheduler.o",
)


def _original_source():
    """Read the frozen original C input without a pending-test dependency."""
    return ORIGINAL_SOURCE.read_bytes()


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def test_whole_owner_fixture_preserves_original_c_input():
    source = _original_source()
    assert len(source) == 6144
    assert _sha256(source) == SOURCE_SHA256
    probe = test_whole_owner_budget_executes_real_owned_runtime
    assert probe.__globals__["_original_source"] is _original_source
    calls = (
        node for node in ast.walk(ast.parse(inspect.getsource(probe)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "_original_source"
    )
    assert len(list(calls)) == 1, "the owned driver must read the original fixture"

@pytest.mark.integration
@pytest.mark.pcc_gate(
    probe=lambda: sys.platform == "linux" and platform.machine() == "x86_64",
)
def test_whole_owner_budget_executes_real_owned_runtime(tmp_path, monkeypatch):
    """Use an explicit admitted archive, never a runtime provisioning fixture."""
    from pcc.backend.elf_x86_64 import parse_static_executable
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive

    assert sys.platform == "linux" and platform.machine() == "x86_64"
    assert os.environ.get("PCC_TEST_NO_NATIVE_PROVISIONING") == "1", (
        "run under the existing no-native-provisioning guard"
    )
    assert os.environ.get("PCC_REFCOUNT_KIND") == "atomic", (
        "the native contract requires an explicitly selected atomic runtime"
    )
    explicit = os.environ.get("PCC_RUNTIME_ARCHIVE")
    assert explicit, "supply a source-matching prebuilt PCC_RUNTIME_ARCHIVE"
    archive, manifest = _verified_test_runtime_archive(
        explicit, threads=True, runtime_root=RUNTIME,
    )
    assert manifest["target_triple"] == TARGET
    source_bytes = _original_source()
    assert len(source_bytes) == 6144 and _sha256(source_bytes) == SOURCE_SHA256
    archive_sha256 = _sha256(archive.read_bytes())
    members = {item["member"]: item for item in manifest["members"]}
    assert set(REQUIRED_MEMBERS) <= members.keys()

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

    source = tmp_path / "whole_owner_work.c"
    binary = tmp_path / "whole_owner_work"
    link_map = tmp_path / "whole_owner_work.map"
    source.write_bytes(source_bytes)
    receipt = {
        "status": "NOT_EXECUTED",
        "source_sha256": SOURCE_SHA256,
        "archive_sha256": archive_sha256,
        "runtime_root": str(RUNTIME),
        # Source/member/configuration and compiler hashes identify the dirty
        # runtime precisely; a Git HEAD alone would not describe its contents.
        "runtime_provenance": manifest,
        "driver_sha256": _sha256(Path(__file__).read_bytes()),
        "arms": [{"backend": value, "status": "UNRUN"} for value in range(5)],
        "scope": "host pcc0 owned link; native real managed lists, controlled mark phase only",
    }
    try:
        receipt["phase"] = "compile_and_link"
        with monkeypatch.context() as owned:
            def forbidden(*args, **kwargs):
                raise AssertionError("owned compile/link attempted an external process")

            owned.setattr(subprocess, "Popen", forbidden)
            evaluator = CEvaluator(backend="self", target_triple=TARGET)
            units = evaluator.compile_translation_units(
                [TranslationUnit(str(source), str(source), source_bytes.decode("utf-8"))],
                use_system_cpp=False, use_compile_cache=False,
                include_dirs=[str(ROOT / "utils/fake_libc_include")],
            )
            assert len(units) == 1
            (tmp_path / "whole_owner_work.ll").write_text(units[0][1], encoding="utf-8")
            evaluator.emit_executable(
                units, str(binary), freestanding_libc=True,
                link_args=["-Wl,-Map," + str(link_map)],
            )

        receipt["phase"] = "verify_owned_image"
        binary_bytes = binary.read_bytes()
        layout = parse_static_executable(binary_bytes)
        receipt["layout"] = layout
        receipt["binary_sha256"] = _sha256(binary_bytes)
        assert layout["entry"] > 0 and layout["tls_segments"] == 1
        lines = link_map.read_text(encoding="utf-8").splitlines()
        assert "# target " + TARGET in lines
        assert "# image sha256=" + receipt["binary_sha256"] in lines
        for name in REQUIRED_MEMBERS:
            assert (
                f"{archive}({name}) archive_sha256={archive_sha256} "
                f"member_sha256={members[name]['object_sha256']}"
            ) in lines
        assert _sha256(archive.read_bytes()) == archive_sha256

        receipt["phase"] = "native_execution"
        # One pytest item keeps all five arms sequential, including under the
        # unchanged default six-worker test scheduler. Stop on first failure.
        for backend in range(5):
            arm = receipt["arms"][backend]
            try:
                run = subprocess.run(
                    [str(binary), str(backend)], capture_output=True, timeout=10,
                    env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
                )
            except subprocess.TimeoutExpired as exc:
                arm.update(
                    status="TIMEOUT", timeout_s=10,
                    stdout_hex=(exc.stdout or b"").hex(),
                    stderr_hex=(exc.stderr or b"").hex(),
                )
                receipt["status"] = "TIMEOUT"
                raise
            arm.update(
                status="FAIL", returncode=run.returncode,
                stdout_hex=run.stdout.hex(), stderr_hex=run.stderr.hex(),
            )
            assert (run.returncode, run.stdout, run.stderr) == (
                0, b"gc-whole-owner-work-ok\n", b"",
            ), arm
            arm["status"] = "PASS"
        receipt["source_sha256_after"] = _sha256(source.read_bytes())
        receipt["fixture_sha256_after"] = _sha256(ORIGINAL_SOURCE.read_bytes())
        receipt["archive_sha256_after"] = _sha256(archive.read_bytes())
        assert receipt["source_sha256_after"] == SOURCE_SHA256
        assert receipt["fixture_sha256_after"] == SOURCE_SHA256
        assert receipt["archive_sha256_after"] == archive_sha256
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
    # Write only after the child has exited. The existing process-tree guard
    # remains the cleanup owner; this test adds no observer or watchdog.
    (tmp_path / "owned-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8",
    )
