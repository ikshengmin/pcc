"""fused-protocol: calls the fused collector (GC0) answers without work.

PCC_GC_FUSED_PROTOCOL=1 removes pin/unpin and store-barrier calls and
replaces address-lease calls by their 0 answer after code generation
(pcc/frontends/python/fused_protocol_pass.py). No block is split. A program
compiled this way runs only on the fused collector.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

from pcc.frontends.python.fused_protocol_pass import fused_protocol_text

MODULE = textwrap.dedent("""
    define ptr @probe(ptr %slot, ptr %value) {
    entry:
      call void (ptr) @pcc_gc_pin(ptr %value)
      %token = call i64 (ptr) @pcc_gc_foreign_lease_acquire(ptr %slot)
      %borrowed = call i64 (ptr) @pcc_gc_borrowed_lease_acquire(ptr %slot)
      call void (ptr, ptr, ptr) @pcc_gc_note_slot_write_barrier(ptr null, ptr %slot, ptr %value)
      call void (ptr, ptr) @pcc_gc_note_write_barrier(ptr %slot, ptr %value)
      %loaded = call ptr (ptr, ptr) @pcc_gc_load_ptr(ptr null, ptr %slot)
      %status = call i64 (ptr, i64) @pcc_gc_foreign_lease_release(ptr %slot, i64 %token)
      call void (ptr) @pcc_gc_unpin(ptr %value)
      call void (ptr, ptr) @pcc_gc_store_root(ptr %slot, ptr %value)
      ret ptr %loaded
    }
    """).lstrip()


def test_inert_calls_are_removed_or_answered_and_the_rest_stay():
    text, changed = fused_protocol_text(MODULE)
    assert changed
    lines = [line.strip() for line in text.splitlines()]
    assert lines == [
        "define ptr @probe(ptr %slot, ptr %value) {",
        "entry:",
        "%token = add i64 0, 0",
        "%borrowed = add i64 0, 0",
        "%loaded = call ptr (ptr, ptr) @pcc_gc_load_ptr(ptr null, ptr %slot)",
        "%status = add i64 0, 0",
        "call void (ptr, ptr) @pcc_gc_store_root(ptr %slot, ptr %value)",
        "ret ptr %loaded",
        "}",
    ]
    declarations = "declare void @pcc_gc_pin(ptr)\n"
    assert fused_protocol_text(declarations) == (declarations, False)


PROGRAM = textwrap.dedent("""
    class Point:
        def __init__(self, x, y):
            self.x = x
            self.y = y

        def total(self):
            return self.x + self.y


    def main():
        points = [Point(index, str(index)) for index in range(200)]
        names = {}
        total = 0
        for point in points:
            names[point.y] = point
            total += point.x
            try:
                total += point.total()
            except TypeError:
                total -= 1
        print(total, len(names), sorted(names)[:3], names["7"].x)


    main()
    """).lstrip()


def test_a_program_compiled_with_the_fused_protocol_matches_cpython(tmp_path, monkeypatch):
    from pcc.frontends.python.pipeline import compile_python

    monkeypatch.setenv("PCC_GC_FUSED_PROTOCOL", "1")
    source = tmp_path / "fused.py"
    executable = tmp_path / "fused.out"
    source.write_text(PROGRAM, encoding="utf-8")
    compile_python(str(source), str(executable), ir_scaffold_mode="on", backend="self")
    reference = subprocess.run([sys.executable, str(source)], capture_output=True,
                               text=True, timeout=30)
    assert reference.returncode == 0, reference.stderr
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60,
                            env=dict(os.environ, PCC_GC_BACKEND="0"))
    assert (result.returncode, result.stdout) == (0, reference.stdout), result.stderr
