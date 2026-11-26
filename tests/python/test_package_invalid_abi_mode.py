"""Unsupported package modes cannot publish acceptance or reuse old receipts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from pcc.driver import cli_bootstrap as cb
from pcc.package.install import install_package
from pcc.package.linkage import linkage_report
from pcc.package.schema import capability_profile


@pytest.mark.parametrize("abi", ("libpython", "auto", "unknown", ""))
@pytest.mark.parametrize("shape", ("empty", "clean", "direct"))
def test_invalid_abi_rejected_by_both_linkage_report_owners(tmp_path, abi, shape):
    artifacts = []
    if shape != "empty":
        artifact = tmp_path / "demo.so"
        artifact.write_text("clean artifact" if shape == "clean" else "-lpython3.13")
        artifacts.append(str(artifact))
    host = linkage_report(artifacts=artifacts, abi_mode=abi)
    native = json.loads(cb._native_linkage_json(artifacts, [], [], abi))
    for report in (host, native):
        assert report["ok"] is False
        assert report["execution_mode"] == "unsupported"
        assert report["native_package_claim"] is False
        assert report["no_libpython_runtime"] is False
        assert report["capability_profile"] == {
            "execution_mode": "unsupported", "native_package_claim": False,
            "no_libpython_runtime": False,
        }
        codes = [d["code"] for d in report["diagnostics"]]
        assert "PCC-PKG-ABI-MODE-INVALID" in codes
        assert report["links_libpython"] is (shape == "direct")
        if shape == "direct":
            assert codes[0] == "PCC-PKG-003"
        for scan in report["scans"]:
            assert scan["execution_mode"] == "unsupported"
            assert scan["native_package_claim"] is False
    assert capability_profile(abi, True, False, False) == host["capability_profile"]


@pytest.mark.parametrize("abi", ("pcc-native", "cpython-compat"))
def test_supported_clean_linkage_control(tmp_path, abi):
    artifact = tmp_path / "demo.so"
    artifact.write_text("clean artifact")
    for report in (linkage_report(artifacts=[str(artifact)], abi_mode=abi),
                   json.loads(cb._native_linkage_json([str(artifact)], [], [], abi))):
        assert report["ok"] is True
        assert report["execution_mode"] == abi
        assert report["native_package_claim"] is (abi == "pcc-native")
        assert report["scans"][0]["native_package_claim"] is (abi == "pcc-native")
        assert report["diagnostics"] == []


def _wheel(tmp_path, direct=False):
    wheel = tmp_path / "demo-0.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("demo/__init__.py", "VALUE = 42\n")
        z.writestr("demo/demo.so", "-lpython3.13" if direct else "clean artifact")
        z.writestr("demo-0.1.dist-info/METADATA", "Name: demo\nVersion: 0.1\n")
    return wheel


def _install(owner, wheel, site, cache, abi):
    if owner == "host":
        return install_package(str(wheel), target_dir=site, cache_dir=cache, abi=abi)
    return json.loads(cb._native_install_manifest_json(str(wheel), str(site), str(cache), [], abi))


def _old_receipt(site, wheel, abi):
    root = site / "demo"
    root.mkdir(parents=True)
    (root / "sentinel.py").write_text("old valid payload must not be touched\n")
    manifest = root / "pcc-package.json"
    manifest.write_text(json.dumps({
        "name": "demo", "artifact_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "abi_mode": abi, "install_success": True, "ok": True,
        "installed_path": str(root), "installed_payloads": [str(root)],
        "install_action": "installed",
    }))
    return manifest, manifest.read_bytes()


@pytest.mark.parametrize("owner", ("host", "native-source"))
@pytest.mark.parametrize("abi", ("libpython", "auto"))
@pytest.mark.parametrize("direct", (False, True))
def test_invalid_install_cannot_reuse_old_receipt_or_publish_cache(tmp_path, monkeypatch, owner, abi, direct):
    # Only the native owned digest intrinsic needs a CPython-host stand-in;
    # both real installer source paths, scanners, receipt and cache logic run.
    monkeypatch.setattr(cb.os, "_pcc_sha256_file_hex",
                        lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest(), raising=False)
    wheel = _wheel(tmp_path, direct)
    site, cache = tmp_path / "site", tmp_path / "cache"
    manifest, old = _old_receipt(site, wheel, abi)
    report = _install(owner, wheel, site, cache, abi)
    assert report["ok"] is False
    assert report["install_success"] is False
    assert report["native_package_claim"] is False
    assert report["linkage_native_package_claim"] is False
    assert report["capability_profile"]["native_package_claim"] is False
    assert report["import_attempted"] is False and report["import_success"] is None
    assert report["links_libpython"] is direct
    codes = [d["code"] for d in report["diagnostics"]]
    assert "PCC-PKG-ABI-MODE-INVALID" in codes
    if direct:
        assert codes[0] == "PCC-PKG-003"
    assert Path(report["manifest_path"]).is_file()
    assert Path(report["manifest_path"]) != manifest
    assert manifest.read_bytes() == old
    assert (manifest.parent / "sentinel.py").exists()
    assert not (manifest.parent / "__init__.py").exists()
    assert not cache.exists()


@pytest.mark.parametrize("owner", ("host", "native-source"))
def test_invalid_unresolved_install_never_resolves_or_builds(tmp_path, monkeypatch, owner):
    def forbidden(*args, **kwargs):
        pytest.fail("unsupported ABI attempted artifact acquisition or build")
    import pcc.package.install as install_module
    monkeypatch.setattr(install_module, "_resolve_spec_with_origin", forbidden)
    monkeypatch.setattr(cb, "_native_resolve_install_source_result", forbidden)
    report = _install(owner, "nonexistent-package", tmp_path / "site", tmp_path / "cache", "libpython")
    assert report["ok"] is False
    assert report["native_package_claim"] is False
    assert "PCC-PKG-ABI-MODE-INVALID" in {d["code"] for d in report["diagnostics"]}
    assert not (tmp_path / "site").exists()
    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize("owner", ("host", "native-source"))
def test_invalid_install_cannot_use_old_cache_by_package_name(tmp_path, monkeypatch, owner):
    wheel = _wheel(tmp_path)
    cache = tmp_path / "cache"
    old_manifest, old = _old_receipt(cache, wheel, "libpython")
    copied = old_manifest.parent / wheel.name
    copied.write_bytes(wheel.read_bytes())
    want = copied.read_bytes()
    site = tmp_path / "site"
    report = _install(owner, "demo", site, cache, "libpython")
    assert report["ok"] is False and report["native_package_claim"] is False
    assert "PCC-PKG-ABI-MODE-INVALID" in {d["code"] for d in report["diagnostics"]}
    assert not site.exists()
    assert old_manifest.read_bytes() == old
    assert copied.read_bytes() == want


@pytest.mark.parametrize("owner", ("host", "native-source"))
@pytest.mark.parametrize("abi", ("pcc-native", "cpython-compat"))
def test_supported_clean_install_and_reinstall_controls(tmp_path, monkeypatch, owner, abi):
    monkeypatch.setattr(cb.os, "_pcc_sha256_file_hex",
                        lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest(), raising=False)
    wheel = _wheel(tmp_path)
    site, cache = tmp_path / "site", tmp_path / "cache"
    first = _install(owner, wheel, site, cache, abi)
    assert first["ok"] is True and first["install_success"] is True, first
    assert first["install_action"] == "installed"
    assert first["native_package_claim"] is False
    assert first["linkage_native_package_claim"] is (abi == "pcc-native")
    assert (cache / "demo" / "pcc-package.json").exists()
    second = _install(owner, wheel, site, cache, abi)
    assert second["ok"] is True and second["install_action"] == "already-satisfied", second
    assert not (site / ".pcc-invalid-abi").exists()
