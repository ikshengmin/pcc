"""Module/attribute name keys stay cached when C-string addresses collide.

The name-key cache is indexed by a C string's address. It was direct-mapped:
two pooled literals whose addresses shared a slot left the later one
uncached, so every lookup of that name built, hashed and freed a key, and
throughput followed the image layout (a runtime inlining change made one
primitive allocate 1.5x as many objects). Names now probe a bounded run of
slots. Strings 4096 bytes apart share a home slot by construction.
"""
from __future__ import annotations

import os
import subprocess

from pcc.frontends.python.pipeline import compile_python


PROGRAM = '''from pcc.extern import c_rawptr, extern
from pcc.unsafe import int_to_ptr, load_i64, load_i8, malloc, ptr_add, ptr_is_null, ptr_to_int, store_i8

cached_name_key = extern("pcc_cached_name_key", (c_rawptr,), c_rawptr)

NAMES = ["collide_alpha", "collide_beta", "collide_gamma"]


def put(base, offset: int, text: str) -> None:
    index: int = 0
    data = text.encode("ascii")
    while index < len(data):
        store_i8(base, offset + index, data[index])
        index += 1
    store_i8(base, offset + index, 0)


def key_matches(key, text: str) -> bool:
    data = text.encode("ascii")
    if load_i64(key, 16) != len(data):
        return False
    index: int = 0
    while index < len(data):
        if load_i8(key, 40 + index) != data[index]:
            return False
        index += 1
    return True


def main() -> None:
    raw = malloc(4096 * len(NAMES) + 64)
    base = int_to_ptr((ptr_to_int(raw) + 63) & ~63)
    first = []
    index: int = 0
    while index < len(NAMES):
        text = ptr_add(base, index * 4096)
        put(base, index * 4096, NAMES[index])
        key = cached_name_key(text)
        if ptr_is_null(key):
            print("uncached", NAMES[index])
            return
        if not key_matches(key, NAMES[index]):
            print("wrong key", NAMES[index])
            return
        first.append(ptr_to_int(key))
        index += 1
    index = 0
    while index < len(NAMES):
        again = cached_name_key(ptr_add(base, index * 4096))
        if ptr_to_int(again) != first[index]:
            print("not reused", NAMES[index])
            return
        index += 1
    print("NAME_CACHE_OK")


main()
'''


def test_colliding_name_addresses_are_all_cached(tmp_path, pcc_runtime_archive):
    source = tmp_path / "name_cache_collisions.py"
    source.write_text(PROGRAM)
    binary = tmp_path / "name_cache_collisions"
    compile_python(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                   runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20,
                             env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)))
        assert (run.returncode, run.stdout, run.stderr) == (0, "NAME_CACHE_OK\n", ""), backend
