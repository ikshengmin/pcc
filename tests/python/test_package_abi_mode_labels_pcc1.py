"""PKG-P0-ABI-MODE-LABELS current pcc1 binary gate.

The native CLI must agree with the in-process linkage report: direct
libpython edges never pass, retired/unknown ABI labels are unsupported,
and supported cpython-compat may admit an extension ABI without a direct
libpython edge but can never earn a native package claim. The same CPython
extension remains rejected in pcc-native mode.

The exact current pcc1 binary is exercised with PCC_HOST_PYTHON=/usr/bin/false
so no host Python fallback can satisfy this gate.
"""

from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path

from pcc1_gate import find_current_pcc1, skip_or_fail_no_current_pcc1


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "AGENTS.md").is_file():
            return parent
    raise RuntimeError("could not locate repo root (AGENTS.md not found walking up)")


REPO_ROOT = _repo_root()


def _make_cpython_abi_wheel(tmp_path: Path, *, direct_libpython=True) -> Path:
    """Build a realistic CPython binary wheel fixture in-process.

    The bundled extension member name declares a CPython extension ABI
    (``cpython-313``); the direct libpython edge is independently controlled.
    The native archive scanner inspects the member name as well as the wheel
    metadata. Built in-process; no subprocess / shell out.
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


def _run_pcc1_linkage(pcc1: Path, abi_mode: str, artifact: Path):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    # Prove the native linkage path runs with no host CPython fallback.
    env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pcc.package.linkage",
            "--abi=" + abi_mode,
            "--artifact",
            str(artifact),
            "--json",
        ],
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert proc.stdout, (
        "pcc1 linkage produced no stdout (abi=" + abi_mode + "); stderr=" + proc.stderr
    )
    return proc, json.loads(proc.stdout)


def _run_pcc1_linkage_empty(pcc1: Path, abi_mode: str):
    env = os.environ.copy()
    env.pop("LC_ALL", None)
    # Prove the native linkage path runs with no host CPython fallback.
    env["PCC_HOST_PYTHON"] = "/usr/bin/false"
    proc = subprocess.run(
        [
            str(pcc1),
            "-m",
            "pcc.package.linkage",
            "--abi=" + abi_mode,
            "--json",
        ],
        text=True,
        capture_output=True,
        timeout=60,
        env=env,
    )
    assert proc.stdout, (
        "pcc1 empty linkage produced no stdout (abi="
        + abi_mode
        + "); stderr="
        + proc.stderr
    )
    return proc, json.loads(proc.stdout)


def _assert_native_no_claim(report, mode):
    assert report["execution_mode"] == mode
    assert report["native_package_claim"] is False
    assert report["capability_profile"]["execution_mode"] == mode
    assert report["capability_profile"]["native_package_claim"] is False
    for scan in report["scans"]:
        assert scan["execution_mode"] == mode
        assert scan["native_package_claim"] is False


def test_pcc1_linkage_retired_libpython_never_claims_compatibility(tmp_path):
    pcc1 = find_current_pcc1(REPO_ROOT)
    if pcc1 is None:
        skip_or_fail_no_current_pcc1(
            "no current pcc1 binary with native linkage ABI-mode labels"
        )
    wheel = _make_cpython_abi_wheel(tmp_path)
    for abi in ("libpython", "cpython-compat"):
        proc, report = _run_pcc1_linkage(pcc1, abi, wheel)
        assert proc.returncode == 2
        assert report["ok"] is False
        assert report["links_libpython"] is True
        assert report["scans"]
        codes = {d["code"] for d in report["diagnostics"]}
        assert "PCC-PKG-003" in codes
        if abi == "libpython":
            assert "PCC-PKG-ABI-MODE-INVALID" in codes
        _assert_native_no_claim(report, "unsupported" if abi == "libpython" else abi)

    wheel = _make_cpython_abi_wheel(tmp_path, direct_libpython=False)
    proc, report = _run_pcc1_linkage(pcc1, "cpython-compat", wheel)
    assert proc.returncode == 0 and report["ok"] is True
    assert report["links_libpython"] is False and report["uses_cpython_extension_abi"] is True
    assert report["scans"]
    _assert_native_no_claim(report, "cpython-compat")


def test_pcc1_linkage_invalid_mode_clean_or_empty_never_claims_native(tmp_path):
    pcc1 = find_current_pcc1(REPO_ROOT)
    if pcc1 is None:
        skip_or_fail_no_current_pcc1(
            "no current pcc1 binary with native linkage ABI-mode labels"
        )
    artifact = tmp_path / "clean.so"
    artifact.write_text("clean artifact", encoding="utf-8")
    for abi in ("libpython", "auto", "unknown"):
        for proc, report in (_run_pcc1_linkage(pcc1, abi, artifact),
                             _run_pcc1_linkage_empty(pcc1, abi)):
            assert proc.returncode == 2 and report["ok"] is False
            assert report["links_libpython"] is False
            assert report["no_libpython_runtime"] is False
            assert "PCC-PKG-ABI-MODE-INVALID" in {d["code"] for d in report["diagnostics"]}
            _assert_native_no_claim(report, "unsupported")


def test_pcc1_linkage_pcc_native_cpython_abi_rejected_no_native_claim(tmp_path):
    pcc1 = find_current_pcc1(REPO_ROOT)
    if pcc1 is None:
        skip_or_fail_no_current_pcc1(
            "no current pcc1 binary with native linkage ABI-mode labels"
        )
    wheel = _make_cpython_abi_wheel(tmp_path)

    proc, report = _run_pcc1_linkage(pcc1, "pcc-native", wheel)

    # pcc-native (no-libpython) abi: the identical CPython wheel is rejected.
    assert proc.returncode == 2
    assert report["ok"] is False
    assert report["execution_mode"] == "pcc-native"
    # The CPython-ABI marker on the wheel member fired PCC-PKG-004...
    assert report["uses_cpython_extension_abi"] is True
    codes = {diag.get("code") for diag in report["diagnostics"]}
    assert "PCC-PKG-004" in codes
    # ...so a rejected CPython-ABI artifact never earns a native package claim.
    assert report["native_package_claim"] is False
    assert report["capability_profile"]["execution_mode"] == "pcc-native"

    # Per-scan results agree: pcc-native mode, no native claim.
    assert report["scans"], "expected at least one artifact scan"
    for scan in report["scans"]:
        assert scan["execution_mode"] == "pcc-native"
        assert scan["native_package_claim"] is False


def test_pcc1_linkage_empty_scan_never_claims_native_package_support():
    pcc1 = find_current_pcc1(REPO_ROOT)
    if pcc1 is None:
        skip_or_fail_no_current_pcc1(
            "no current pcc1 binary with native linkage empty-scan claim labels"
        )

    proc, report = _run_pcc1_linkage_empty(pcc1, "pcc-native")

    assert proc.returncode == 0
    assert report["ok"] is True
    assert report["execution_mode"] == "pcc-native"
    assert report["scans"] == []
    assert report["native_package_claim"] is False
