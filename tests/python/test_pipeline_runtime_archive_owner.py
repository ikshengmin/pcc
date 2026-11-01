"""Focused facade contracts for runtime archive policy extraction."""

from __future__ import annotations

from pathlib import Path
import json

import pytest

from pcc.frontends.python import pipeline
from pcc.frontends.python import pipeline_runtime_archive as runtime_archive


def test_runtime_archive_path_and_mode_helpers_have_one_owner():
    archive = "/tmp/libpy_runtime_pcc_py.a"
    assert pipeline._runtime_archive_target_stamp(archive) == runtime_archive.target_stamp(
        archive
    )
    assert (
        pipeline._runtime_archive_provenance_manifest(archive)
        == runtime_archive.provenance_manifest(archive)
    )
    assert pipeline._runtime_archive_capi_inventory(archive) == runtime_archive.capi_inventory(
        archive
    )
    # The C runtime selectors are retired along with the C archives.
    assert not hasattr(pipeline, "_runtime_cc_mode")
    assert not hasattr(pipeline, "_runtime_high_mode")
    assert not hasattr(runtime_archive, "cc_mode")


def test_non_production_archive_bundle_policy_is_basename_scoped():
    assert runtime_archive.requires_provenance("/tmp/libpy_runtime_pcc_py.a")
    assert not runtime_archive.requires_provenance("/tmp/foreign.a")
    assert not hasattr(runtime_archive, "requires_c_bundle_validation")


def test_codegen_freshness_checker_runs_the_owned_verifier(
    tmp_path: Path,
    monkeypatch,
):
    archive = tmp_path / "libpy_runtime_pcc_py.a"
    manifest = Path(str(archive) + ".provenance.json")
    manifest.write_text(json.dumps({"source": "test"}), encoding="utf-8")
    calls = []
    from pcc.tools import runtime_archive_provenance as provenance

    def check(records):
        calls.append(records)
        return False

    monkeypatch.setattr(provenance, "manifest_is_stale_for_current_codegen", check)
    monkeypatch.setattr(runtime_archive.subprocess, "run", lambda *a, **k: pytest.fail("host delegation"))

    assert not runtime_archive.provenance_codegen_stale(
        str(archive),
        pcc_source_root=lambda: "/source/root",
        host_python_command=lambda: "/host/python",
    )
    assert calls == [{"source": "test"}]

    monkeypatch.setattr(
        provenance,
        "manifest_is_stale_for_current_codegen",
        lambda *_args, **_kwargs: True,
    )
    assert runtime_archive.provenance_codegen_stale(
        str(archive),
        pcc_source_root=lambda: "/source/root",
        host_python_command=lambda: "/host/python",
    )
