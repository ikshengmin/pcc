"""Coordinate external Go oracle builds, never PCC compilation or execution."""
from contextlib import nullcontext
from pathlib import Path

from scripts.file_lock import exclusive_file_lock


def reference_build_slot(selected_cache):
    """Only one external builder may populate a given shared GOCACHE at a time.

    Go serializes publication of a compiled package, not the expensive builds
    preceding publication. Separate xdist processes can otherwise compile the
    same cold packages concurrently. Do not guess whether a particular package
    is warm: still invoke Go for every oracle, with its original inputs/flags.
    """
    if not selected_cache:
        return nullcontext()  # caller owns a private per-test cache
    cache = Path(selected_cache)
    if not cache.is_absolute() or (cache.exists() and not cache.is_dir()):
        return nullcontext()  # preserve Go's own diagnostic for an invalid cache
    return exclusive_file_lock(cache / '.pcc-reference-build.lock')
