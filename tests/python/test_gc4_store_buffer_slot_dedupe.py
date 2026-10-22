"""GC4 keeps one store-buffer entry per recorded slot.

An old list whose slots keep receiving young values used to gain one entry per
store, each enqueue walking every pending entry: 25,600 stores into 2,048 slots
left 25,967 pending entries. The drain promotes whatever a recorded slot holds
when it runs, so a slot already pending needs no second entry.
"""
import os
from pathlib import Path
import subprocess

PROGRAM = '''from pcc.extern import c_int64, extern

pcc_gc_backend4_store_buffer_high_water = extern("pcc_gc_backend4_store_buffer_high_water", (), c_int64)
pcc_gc_backend4_store_buffer_duplicate_skips = extern("pcc_gc_backend4_store_buffer_duplicate_skips", (), c_int64)


def main():
    ring = []
    i = 0
    while i < 2048:
        ring.append([i, i + 1, i + 2])
        i = i + 1
    r = 0
    while r < 400:
        j = 0
        while j < 64:
            ring[(r * 64 + j) % 2048] = [r, j, 3]
            j = j + 1
        r = r + 1
    high = pcc_gc_backend4_store_buffer_high_water()
    print("bounded", high <= 2048, "deduplicated", pcc_gc_backend4_store_buffer_duplicate_skips() > 0, "sum", ring[0][0] + ring[2047][1])


main()
'''


def test_gc4_store_buffer_holds_one_entry_per_slot(tmp_path: Path, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "slot_dedupe.py"
    source.write_text(PROGRAM, encoding="utf-8")
    binary = tmp_path / "slot_dedupe"
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    ran = subprocess.run([str(binary)], env=dict(os.environ, PCC_GC_BACKEND="4"),
                         capture_output=True, text=True, timeout=120)
    assert ran.returncode == 0, ran.stdout + ran.stderr[-2000:]
    assert ran.stdout == "bounded True deduplicated True sum 447\n"
