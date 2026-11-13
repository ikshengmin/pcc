"""A thread's object magazine holds up to 32 KiB of cells per size class.

Each thread caches freed object cells per size class and refills an empty
class from the central free lists under the allocator lock, taking half its
limit at once. With the limit at min(32, 4096 // usable) a 64-byte class
refilled 16 cells per lock, so four allocating workers took the global
allocator lock every 16 allocations. min(256, 32768 // usable) takes 128
per lock for that class: pcc_perf prim p27 --against p26 measured four-worker
list -13.7..-19.3%, dict -17.9..-21.4%, str_concat gc2 -17.9% on GC1-GC3,
with one worker and the loop control unchanged.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from test_class_namespace_writer import load_functions


ALLOCATOR = Path(__file__).resolve().parents[2] / "pcc" / "runtime" / "py" / "freestanding_allocator.py"


@pytest.mark.parametrize("usable,limit", [
    (16, 256), (64, 256), (128, 256), (256, 128), (1024, 32),
    (4096, 8), (8192, 4), (32768, 1), (65536, 1),
])
def test_magazine_limit_is_32_kib_of_cells_capped_at_256(usable, limit):
    namespace = {}
    load_functions(ALLOCATOR, ("_object_magazine_limit",), namespace)
    assert namespace["_object_magazine_limit"](usable) == limit
