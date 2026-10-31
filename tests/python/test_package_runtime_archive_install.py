"""Host-only installed-runtime receipt contracts, not native runtime proof.

The synthetic module inventory and data-only objects below model an installed
bundle. Real owned writers and provenance verification bind their bytes; they
do not implement, compile, or execute the production runtime.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from pcc.backend.ar_writer import write_archive
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.frontends.python import owned_runtime_build as owned
from pcc.frontends.python.pipeline_targets import host_target_triple
from pcc.tools import runtime_archive_provenance as provenance


def _forbidden(*_args, **_kwargs):
    raise AssertionError("receipt fixture attempted runtime provisioning or a subprocess")


@pytest.fixture(autouse=True)
def isolated_owned_runtime_request(monkeypatch):
    monkeypatch.setenv("PCC_RUNTIME_BUILD", "owned")
    monkeypatch.setenv("PCC_WITH_THREADS", "1")
    monkeypatch.setenv("PCC_REFCOUNT_KIND", "atomic")
    monkeypatch.delenv("PCC_SELF_BOOLEAN_REGISTER_LOWERING", raising=False)
    monkeypatch.delenv("PCC_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("PCC_RUNTIME_ARCHIVE", raising=False)
    # Keep the real admission guard and deep verifier. No fallback may build
    # a runtime or launch an external verifier/compiler in these host tests.
    monkeypatch.setattr(owned, "build_runtime_archive", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_wheel_stamp(archive: Path, target_id: str) -> None:
    manifest = Path(str(archive) + ".provenance.json")
    inventory = Path(str(archive) + ".capi_syms")
    Path(str(archive) + ".wheel").write_text(
        "pcc.runtime-wheel-artifact.v2\n"
        + "target="
        + target_id
        + "\narchive-sha256="
        + _sha256(archive)
        + "\nmanifest-sha256="
        + _sha256(manifest)
        + "\ncapi-inventory-sha256="
        + _sha256(inventory)
        + "\n",
        encoding="utf-8",
    )


def _write_synthetic_installed_bundle(root: Path, target_id: str) -> Path:
    archive = root / "libpy_runtime_pcc_py.a"
    (root / "py").mkdir(parents=True)
    (root / "Makefile").write_text(
        "# Synthetic verifier inventory, not the production runtime.\n"
        "PY_MODULES = synthetic_installed_fixture\n"
        "FREESTANDING_PY_MODULES =\n",
        encoding="utf-8",
    )
    target = host_target_triple()
    config = owned.runtime_build_config()
    objects = []
    for index, name in enumerate(owned.runtime_modules(str(root), target, config["threads"])):
        source = root / "py" / (name + ".py")
        source.write_text("# Synthetic receipt identity; not a runtime implementation.\n")
        symbol = "PyRuntime_InstalledAnchor" if index == 0 else "synthetic_installed_" + name
        ir = root / (name + ".ll")
        ir.write_text("@" + symbol + " = global i64 0\n", encoding="utf-8")
        obj = root / (name + ".o")
        obj.write_bytes(emit_owned_object(ir.read_text(encoding="utf-8"), target))
        provenance.write_pcc_python_receipt(
            object_path=obj, ir_path=ir, source_path=source, runtime_root=root,
            target_triple=target, object_emitter="pcc", runtime_build_config=config,
        )
        objects.append(obj)
    archive.write_bytes(write_archive([(obj.name, obj.read_bytes()) for obj in objects]))
    anchor = "_PyRuntime_InstalledAnchor" if "darwin" in target else "PyRuntime_InstalledAnchor"
    Path(str(archive) + ".capi_syms").write_text(anchor + "\n", encoding="ascii")
    provenance.assemble_runtime_archive_manifest(archive, objects, runtime_root=root)
    receipt = provenance.verify_runtime_archive_manifest(archive, runtime_root=root)
    assert owned._manifest_matches_config(receipt, str(root), target, config)
    assert not provenance.manifest_is_stale_for_current_codegen(receipt)
    Path(str(archive) + ".target").write_text(target_id + "\n", encoding="utf-8")
    _write_wheel_stamp(archive, target_id)
    return archive


def _select_installed_bundle(
    archive: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from pcc.frontends.python import pipeline

    monkeypatch.setenv("PCC_RUNTIME_CC", "pcc")
    monkeypatch.delenv("PCC_RUNTIME_ARCHIVE", raising=False)
    monkeypatch.setattr(pipeline, "_PY_RUNTIME_DIR", str(archive.parent))
    monkeypatch.setattr(pipeline, "_PY_RUNTIME_ARCHIVE_PCC_PY", str(archive))
    return pipeline


def test_installed_runtime_bundle_is_selected_without_host_provenance_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pcc.frontends.python import pipeline

    archive = _write_synthetic_installed_bundle(
        tmp_path / "site-packages" / "pcc" / "runtime",
        pipeline._runtime_archive_target_id(),
    )
    pipeline = _select_installed_bundle(archive, monkeypatch)

    def forbidden_host_verifier(*_args, **_kwargs):
        raise AssertionError("installed wheel receipt must not launch host Python")

    monkeypatch.setattr(
        pipeline,
        "_runtime_archive_provenance_valid",
        forbidden_host_verifier,
    )
    monkeypatch.setattr(
        pipeline,
        "_runtime_archive_codegen_stale",
        forbidden_host_verifier,
    )

    assert pipeline._runtime_archive_wheel_stamp_matches(str(archive))
    assert pipeline._ensure_runtime(False, needs_libpython=False) == str(archive)
    assert not (archive.parent / "build_owned").exists()


@pytest.mark.parametrize("damaged_suffix", ["", ".provenance.json", ".capi_syms"])
def test_installed_runtime_receipt_binds_archive_manifest_and_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damaged_suffix: str,
) -> None:
    from pcc.frontends.python import pipeline

    archive = _write_synthetic_installed_bundle(
        tmp_path / "runtime", pipeline._runtime_archive_target_id(),
    )
    damaged = Path(str(archive) + damaged_suffix)
    damaged.write_bytes(damaged.read_bytes() + b"tampered")
    selected = _select_installed_bundle(archive, monkeypatch)
    assert not selected._runtime_archive_wheel_stamp_matches(str(archive))
    # An explicit invalid archive must reject, not fall through to an automatic
    # rebuild. Assert the real verifier's error survives the public boundary.
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(archive))
    with pytest.raises(provenance.ProvenanceError) as rejected:
        provenance.verify_runtime_archive_manifest(archive, runtime_root=archive.parent)
    with pytest.raises(selected.PyPipelineError) as selected_error:
        selected._ensure_runtime(False, needs_libpython=False)
    assert str(selected_error.value) == str(rejected.value)
    assert not (archive.parent / "build_owned").exists()


@pytest.mark.parametrize("mismatch", ["target", "config"])
def test_bound_installed_runtime_receipt_still_checks_target_and_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatch: str,
) -> None:
    from pcc.frontends.python import pipeline

    target_id = pipeline._runtime_archive_target_id()
    archive = _write_synthetic_installed_bundle(tmp_path / "runtime", target_id)
    manifest = Path(str(archive) + ".provenance.json")
    receipt = json.loads(manifest.read_text(encoding="utf-8"))
    if mismatch == "target":
        receipt["target_triple"] = "wrong-target"
    else:
        for member in receipt["members"]:
            member["runtime_build_config"]["refcount"] = "biased"
    manifest.write_text(json.dumps(receipt), encoding="utf-8")
    _write_wheel_stamp(archive, target_id)
    selected = _select_installed_bundle(archive, monkeypatch)
    assert selected._runtime_archive_wheel_stamp_matches(str(archive))
    monkeypatch.setenv("PCC_RUNTIME_ARCHIVE", str(archive))
    with pytest.raises(selected.PyPipelineError, match="target/source configuration"):
        selected._ensure_runtime(False, needs_libpython=False)
    assert not (archive.parent / "build_owned").exists()


def test_installed_runtime_inventory_link_read_uses_same_hostless_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pcc.frontends.python import pipeline

    archive = _write_synthetic_installed_bundle(
        tmp_path / "site-packages" / "pcc" / "runtime",
        pipeline._runtime_archive_target_id(),
    )

    def forbidden_host_verifier(*_args, **_kwargs):
        raise AssertionError("link inventory must reuse the installed receipt")

    monkeypatch.setattr(
        pipeline,
        "_runtime_archive_provenance_valid",
        forbidden_host_verifier,
    )

    expected = Path(str(archive) + ".capi_syms").read_text(encoding="ascii").splitlines()
    assert pipeline._capi_export_anchor_symbols(str(archive)) == expected


def test_legacy_unbound_wheel_marker_is_not_a_hostless_completion_receipt(
    tmp_path: Path,
) -> None:
    from pcc.frontends.python import pipeline

    archive = tmp_path / "libpy_runtime_pcc_py.a"
    archive.write_bytes(b"archive")
    Path(str(archive) + ".wheel").write_text(
        "pcc.runtime-wheel-artifact.v1\n"
        + pipeline._runtime_archive_target_id()
        + "\nsha256:"
        + ("a" * 64)
        + "\n",
        encoding="utf-8",
    )

    assert not pipeline._runtime_archive_wheel_stamp_matches(str(archive))
