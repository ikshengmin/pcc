"""Fail fast when an orchestrated run has reserved native build capacity."""

from __future__ import annotations

import os
from pathlib import Path


GUARD_NAME = ".pcc-test-no-native-provisioning"


def require_native_provisioning_allowed(repo_root: Path | None = None) -> None:
    """Reject automatic builds, without skipping or weakening any test.

    Explicitly selected prebuilt archives can still be verified and reused.
    A coordinator owns the guard file's lifecycle; an interrupted reservation
    stays fail-closed until that coordinator clears it.
    """
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[1]
    guard = root / "build" / GUARD_NAME
    disabled = os.environ.get("PCC_TEST_NO_NATIVE_PROVISIONING", "").strip().lower()
    if disabled in {"1", "true", "yes", "on"} or guard.exists():
        raise RuntimeError(
            "automatic native test provisioning is disabled; select an explicitly "
            "verified prebuilt runtime or wait for the active build reservation "
            f"to finish ({guard})"
        )
