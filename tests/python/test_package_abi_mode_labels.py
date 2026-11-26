"""PKG-P0-ABI-MODE-LABELS regression coverage.

Every package import/linkage result must carry explicit execution-mode labels.
A supported cpython-compat extension without a direct libpython edge may pass
compatibility but never earns a native package claim. Direct libpython edges
are rejected in every mode; the retired legacy libpython label cannot grant
CPython-ABI compatibility. Identical CPython-ABI artifacts remain rejected
under pcc-native mode with PCC-PKG-004.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pcc.driver.cli_bootstrap as cb
from pcc.package.linkage import linkage_report
from pcc.package.schema import capability_profile, wheel_tag_fields


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "AGENTS.md").is_file():
            return parent
    raise RuntimeError("could not locate repo root (AGENTS.md not found walking up)")


REPO_ROOT = _repo_root()


def _make_cpython_abi_wheel(tmp_path: Path, *, direct_libpython=True) -> Path:
    """Build a realistic CPython binary wheel fixture in-process.

    The wheel name and the bundled extension member both declare a CPython
    extension ABI (``cp313`` / ``cpython-313``). The direct libpython edge is
    independently controlled, so an extension ABI alone cannot be mistaken
    for a forbidden direct edge. No subprocess / shell out.
    """

    wheel = tmp_path / "foo-1.0-cp313-cp313-macosx.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("foo/__init__.py", "VALUE = 1\n")
        native = "\x7fELF\x00extension fixture"
        if direct_libpython:
            native += "...linked against /usr/local/lib/libpython3.13.dylib\x00"
        zf.writestr("foo/_foo.cpython-313-darwin.so", native)
        zf.writestr("foo-1.0.dist-info/METADATA", "Name: foo\nVersion: 1.0\n")
    return wheel


def _assert_no_native_claim(report, execution_mode):
    assert report["execution_mode"] == execution_mode
    assert report["native_package_claim"] is False
    assert report["capability_profile"]["native_package_claim"] is False
    assert report["capability_profile"]["execution_mode"] == execution_mode
    assert report["scans"], "expected at least one artifact scan"
    for scan in report["scans"]:
        assert scan["execution_mode"] == execution_mode
        assert scan["native_package_claim"] is False


def test_direct_libpython_cpython_abi_fixture_rejected_without_native_claim(tmp_path):
    wheel = _make_cpython_abi_wheel(tmp_path)
    for abi_mode in ("libpython", "cpython-compat", "pcc-native"):
        report = linkage_report(artifacts=[str(wheel)], abi_mode=abi_mode)
        assert report["ok"] is False
        assert report["links_libpython"] is True
        assert report["link_libpython_edges"]
        assert report["uses_cpython_extension_abi"] is True
        assert "PCC-PKG-003" in {d["code"] for d in report["diagnostics"]}
        mode = "unsupported" if abi_mode == "libpython" else abi_mode
        _assert_no_native_claim(report, mode)


def test_cpython_abi_without_direct_libpython_only_compat_can_pass(tmp_path):
    wheel = _make_cpython_abi_wheel(tmp_path, direct_libpython=False)
    for abi_mode in ("libpython", "cpython-compat", "pcc-native"):
        report = linkage_report(artifacts=[str(wheel)], abi_mode=abi_mode)
        assert report["ok"] is (abi_mode == "cpython-compat")
        assert report["links_libpython"] is False
        assert report["link_libpython_edges"] == []
        assert report["uses_cpython_extension_abi"] is True
        assert "PCC-PKG-004" in {d["code"] for d in report["diagnostics"]}
        mode = "unsupported" if abi_mode == "libpython" else abi_mode
        _assert_no_native_claim(report, mode)


def test_libpython_off_cpython_abi_fixture_rejects_with_pcc_pkg_004(tmp_path):
    wheel = _make_cpython_abi_wheel(tmp_path)

    # pcc-native (no-libpython) abi: the identical CPython wheel is rejected.
    report = linkage_report(artifacts=[str(wheel)], abi_mode="pcc-native")

    assert report["ok"] is False
    assert report["execution_mode"] == "pcc-native"
    codes = {diag.get("code") for diag in report["diagnostics"]}
    assert "PCC-PKG-004" in codes
    # A rejected CPython-ABI artifact never earns a native package claim.
    assert report["native_package_claim"] is False
    assert report["uses_cpython_extension_abi"] is True


def test_empty_linkage_scan_never_claims_native_package_support():
    report = linkage_report(abi_mode="pcc-native")

    assert report["ok"] is True
    assert report["execution_mode"] == "pcc-native"
    assert report["scans"] == []
    assert report["native_package_claim"] is False


def test_pcc1_native_linkage_json_empty_scan_never_claims_native_package_support():
    report = json.loads(cb._native_linkage_json([], [], [], "pcc-native"))

    assert report["ok"] is True
    assert report["execution_mode"] == "pcc-native"
    assert report["scans"] == []
    assert report["native_package_claim"] is False
    assert report["capability_profile"] == capability_profile(
        "pcc-native", False, False, False
    )


def test_host_and_pcc1_share_wheel_tag_and_capability_contract():
    wheel = "demo-1.2-cp313-cp313-linux_x86_64.whl"
    assert cb._native_wheel_tag_fields(wheel) == wheel_tag_fields(wheel)

    host = linkage_report(abi_mode="pcc-native")
    native = json.loads(cb._native_linkage_json([], [], [], "pcc-native"))
    assert native["capability_profile"] == host["capability_profile"]

    install_source = (REPO_ROOT / "pcc" / "package" / "install.py").read_text(
        encoding="utf-8"
    )
    metadata_source = (REPO_ROOT / "pcc" / "package" / "metadata.py").read_text(
        encoding="utf-8"
    )
    bootstrap_source = (REPO_ROOT / "pcc" / "driver" / "cli_bootstrap.py").read_text(
        encoding="utf-8"
    )
    assert "fields = wheel_tag_fields(name)" in install_source
    assert "fields = wheel_tag_fields(str(path))" in metadata_source
    assert "return wheel_tag_fields(path)" in bootstrap_source
