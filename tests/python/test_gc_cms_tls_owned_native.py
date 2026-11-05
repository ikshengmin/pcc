"""Initial-thread CMS calls execute through the real owned Linux runtime.

This is host-pcc0 owned C/object/link and native startup evidence. Zero results
do not distinguish a raw-TLS load from a getter call. This smoke guards the
owned startup and call path, not that specific cross-object TLS defect. It does
not establish worker lifecycle, TLS isolation, collector correctness,
native-pcc1 qualification or performance.
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
INITIAL_THREAD_SOURCE = Path(__file__).parent / "fixtures/gc_cms_tls_boundary/initial_thread.c"
TARGET = "x86_64-unknown-linux-gnu"
SOURCE_SHA256 = "58d4cda31a05c2e01702bb285c2f8206581591b4d9f3753b4aadf752714f3773"
REQUIRED_MEMBERS = (
    "freestanding_gc_public_collection.o",
    "freestanding_gc_incremental_concurrent_scheduler.o",
)


def _initial_thread_source():
    """Read the frozen original C input without a pending-test dependency."""
    return INITIAL_THREAD_SOURCE.read_bytes()


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def test_initial_thread_fixture_preserves_original_c_input():
    source = _initial_thread_source()
    assert len(source) == 464
    assert _sha256(source) == SOURCE_SHA256
    probe = test_initial_thread_cms_getter_and_handoff_execute_owned_runtime
    assert probe.__globals__["_initial_thread_source"] is _initial_thread_source
    calls = (
        node for node in ast.walk(ast.parse(inspect.getsource(probe)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "_initial_thread_source"
    )
    assert len(list(calls)) == 1, "the owned driver must read the original fixture"

@pytest.mark.integration
@pytest.mark.pcc_gate(
    probe=lambda: sys.platform == "linux" and platform.machine() == "x86_64",
)
def test_initial_thread_cms_getter_and_handoff_execute_owned_runtime(tmp_path, monkeypatch):
    """Use an explicit admitted archive, never a runtime provisioning fixture."""
    from pcc.backend.elf_x86_64 import parse_static_executable
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    from tests.runtime_fixture_provenance import _verified_test_runtime_archive

    assert sys.platform == "linux" and platform.machine() == "x86_64"
    assert os.environ.get("PCC_TEST_NO_NATIVE_PROVISIONING") == "1", (
        "run under the existing no-native-provisioning guard"
    )
    explicit = os.environ.get("PCC_RUNTIME_ARCHIVE")
    assert explicit, "supply a source-matching prebuilt PCC_RUNTIME_ARCHIVE"
    archive, manifest = _verified_test_runtime_archive(
        explicit, threads=True, runtime_root=RUNTIME,
    )
    assert manifest["target_triple"] == TARGET
    source_bytes = _initial_thread_source()
    assert len(source_bytes) == 464 and _sha256(source_bytes) == SOURCE_SHA256
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

    source = tmp_path / "cms_tls_owned.c"
    binary = tmp_path / "cms_tls_owned"
    link_map = tmp_path / "cms_tls_owned.map"
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
        "scope": "host pcc0 owned link; native initial-thread zero getter/handoff only",
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
            (tmp_path / "cms_tls_owned.ll").write_text(units[0][1], encoding="utf-8")
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
        try:
            run = subprocess.run(
                [str(binary)], capture_output=True, timeout=10,
                env=dict(os.environ, PCC_GC_BACKEND="0"),
            )
        except subprocess.TimeoutExpired as exc:
            # subprocess.run has already killed and waited for its child.
            receipt.update(
                status="TIMEOUT", timeout_s=10,
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
        receipt["fixture_sha256_after"] = _sha256(INITIAL_THREAD_SOURCE.read_bytes())
        receipt["archive_sha256_after"] = _sha256(archive.read_bytes())
        assert receipt["source_sha256_after"] == SOURCE_SHA256
        assert receipt["fixture_sha256_after"] == SOURCE_SHA256
        assert receipt["archive_sha256_after"] == archive_sha256
        assert (run.returncode, run.stdout, run.stderr) == (0, b"cms-tls-link-ok\n", b"")
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
