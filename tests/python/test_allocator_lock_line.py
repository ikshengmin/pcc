"""The central allocator lock under contention, on a cache line of its own.

Every magazine refill and drain takes this lock. Its word used to share a
128-byte line with the first entries of the provenance span cache, which
every pointer check reads, so each acquire and release (and each spinning
exchange) invalidated that line on every other core.
"""

import os
from pathlib import Path
import subprocess

from pcc.backend.macho_spec import parse_object
from tests.owned_c_execution import compile_owned_c_with_runtime

ROOT = Path(__file__).resolve().parents[2]
CACHE_LINE = 128


def _build(tmp_path, archive):
    binary = tmp_path / "allocator_lock"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/allocator_lock_contention.c", binary,
        runtime_archive=Path(archive),
    )
    return binary


def test_contended_allocator_lock_excludes_and_keeps_accounting(
    tmp_path, threaded_pcc_runtime_archive,
):
    binary = _build(tmp_path, threaded_pcc_runtime_archive)
    for backend in range(5):
        run = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=60,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
        )
        assert (run.returncode, run.stdout, run.stderr) == (
            0, "ALLOCATOR_LOCK_OK\n", "",
        ), backend


def test_allocator_lock_word_has_a_cache_line_of_its_own(
    tmp_path, threaded_pcc_runtime_archive,
):
    binary = _build(tmp_path, threaded_pcc_runtime_archive)
    rows = parse_object(binary.read_bytes()).symbols()
    # Section-defined, non-debug symbols with their linked addresses.
    symbols = sorted(
        (row["n_value"], row["name"]) for row in rows
        if (row["n_type"] & 0xE0) == 0 and (row["n_type"] & 0x0E) == 0x0E
    )
    addresses = {name: value for value, name in symbols}
    lock = addresses["_pcc_allocator_lock"]
    before = addresses["_pcc_allocator_lock_isolation_before"]
    after = addresses["_pcc_allocator_lock_isolation_after"]
    line = lock - lock % CACHE_LINE
    assert before <= line and after + CACHE_LINE >= line + CACHE_LINE
    neighbours = [
        name for value, name in symbols
        if before < value < after + CACHE_LINE
        and name not in ("_pcc_allocator_lock", "_pcc_allocator_lock_isolation_after")
    ]
    assert neighbours == []
