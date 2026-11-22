"""Host capabilities that ``pcc_gate(probe=...)`` reads at collection.

A test that needs one is deselected on a host without it (the no-skip
doctrine); reaching the operation anyway is a failure, not a skip.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
import tempfile
from pathlib import Path


@functools.cache
def can_create_symlinks() -> bool:
    """Whether this process may create a symbolic link (Windows may refuse)."""
    with tempfile.TemporaryDirectory() as directory:
        link = Path(directory) / "link"
        try:
            link.symlink_to(Path(directory) / "missing")
        except (OSError, NotImplementedError):
            return False
        return True


@functools.cache
def gnu_gxx() -> str | None:
    """Path of a GNU g++, or None.

    A reference that a test names as GCC must be GCC: macOS's /usr/bin/g++ is
    an Apple clang shim, which disagrees with GCC on some valid programs.
    """
    compiler = shutil.which("g++")
    if compiler is None:
        return None
    try:
        result = subprocess.run([compiler, "--version"], capture_output=True,
                                text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    banner = result.stdout
    if (result.returncode != 0 or "Free Software Foundation" not in banner
            or "clang" in banner.lower()):
        return None
    return compiler
