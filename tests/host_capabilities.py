"""Host capabilities that ``pcc_gate(probe=...)`` reads at collection.

A test that needs one is deselected on a host without it (the no-skip
doctrine); reaching the operation anyway is a failure, not a skip.
"""

from __future__ import annotations

import functools
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
