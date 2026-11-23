"""Slot calls skip address leases where every lease is a no-op.

On collectors 0-3, once the configuration is known and without the refcount
provenance probe, pcc_gc_foreign_lease_acquire and
pcc_gc_borrowed_lease_acquire return 0 before reading their slot, and
pcc_gc_foreign_lease_release returns 0 for that token: no collector of those
moves an object, and none can replace them once an object exists. The
runtime publishes that admission as pcc_gc_pins_inert's sibling
pcc_gc_leases_inert, and a slot call (argument leases and the owned-result
publication lease) reads it inline instead of making both calls. GC4 and
an unconfigured runtime still call.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

PROGRAM = textwrap.dedent("""
    class Point:
        def __init__(self, x: int, y: int) -> None:
            self.x = x
            self.y = y

        def total(self) -> int:
            return self.x + self.y

        def scaled(self, factor: int) -> "Point":
            return Point(self.x * factor, self.y * factor)


    def main() -> None:
        point = Point(1, 2)
        total = 0
        index = 0
        while index < 2000:
            total += point.total()
            other = point.scaled(index % 7)
            total += other.x - other.y
            index += 1
        names = [str(index) for index in range(50)]
        print(total, len(names), names[-1].zfill(4), "-".join(names[:5]))


    if __name__ == "__main__":
        main()
    """).lstrip()


def _function_body(ir_text: str, name: str) -> str:
    start = ir_text.index("define external ptr @" + name + "(")
    return ir_text[start:ir_text.index("\n}\n", start)]


def test_slot_call_leases_read_the_inert_word_before_calling(tmp_path):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "leases.py"
    source.write_text(PROGRAM, encoding="utf-8")
    ir_path = tmp_path / "leases.ll"
    compile_python(str(source), str(ir_path), emit_llvm_only=True,
                   ir_scaffold_mode="on", backend="self")
    body = _function_body(ir_path.read_text(encoding="utf-8"), "user_leases_Point_total")
    acquires = re.findall(
        r"%call\.slot\.[\w.]+ = call i64 \(ptr\) @pcc_gc_(?:foreign|borrowed)_lease_acquire", body)
    releases = re.findall(
        r"%call\.slot\.[\w.]+ = call i64 \(ptr, i64\) @pcc_gc_foreign_lease_release", body)
    gates = re.findall(r"= load i32, ptr @pcc_gc_leases_inert", body)
    skips = re.findall(r"= icmp eq i64 %call\.slot\.[\w.]*lease[\w.]*, 0", body)
    # Two attribute reads: an argument lease and a publication lease each,
    # every acquire behind the inert word and every release behind a 0 test.
    assert len(acquires) >= 4
    assert len(gates) == len(acquires)
    assert len(skips) == len(releases) >= 4


_INERT_PROBE = r"""
#include "py_runtime.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern int32_t pcc_gc_leases_inert;

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    int first = atoi(argv[1]), second = atoi(argv[2]);
    int before = pcc_gc_leases_inert;
    if (pcc_gc_set_backend(first) != 0) return 3;
    int selected = pcc_gc_leases_inert;
    if (second >= 0 && pcc_gc_set_backend(second) != 0) return 4;
    int switched = pcc_gc_leases_inert;
    PyObject *obj = py_list_new(0);
    if (obj == NULL) return 5;
    printf("%d %d %d\n", before, selected, switched);
    return 0;
}
"""


def test_the_inert_word_follows_the_selected_collector(tmp_path, pcc_runtime_archive):
    runtime = Path(__file__).resolve().parents[2] / "pcc" / "runtime"
    source = tmp_path / "inert.c"
    executable = tmp_path / "inert"
    source.write_text(_INERT_PROBE, encoding="utf-8")
    build = subprocess.run(
        ["clang", "-std=c11", f"-I{runtime / 'include'}", f"-I{runtime / 'src'}",
         str(source), str(pcc_runtime_archive), "-pthread", "-o", str(executable)],
        capture_output=True, text=True, timeout=60,
    )
    assert build.returncode == 0, build.stderr
    cases = {(0, -1): "0 1 1", (3, -1): "0 1 1", (4, -1): "0 0 0",
             (0, 4): "0 1 0", (4, 1): "0 0 1"}
    for (first, second), expected in cases.items():
        result = subprocess.run([str(executable), str(first), str(second)],
                                capture_output=True, text=True, timeout=30,
                                env=dict(os.environ, PCC_GC_REFCOUNT_PROVENANCE_PROBE="0"))
        assert result.returncode == 0, (first, second, result.stderr)
        assert result.stdout == expected + "\n", (first, second, result.stdout)


def test_attribute_and_method_calls_match_cpython_on_every_collector(tmp_path):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "leases.py"
    executable = tmp_path / "leases.out"
    source.write_text(PROGRAM, encoding="utf-8")
    compile_python(str(source), str(executable), ir_scaffold_mode="on", backend="self")
    reference = subprocess.run([sys.executable, str(source)], capture_output=True,
                               text=True, timeout=30)
    assert reference.returncode == 0, reference.stderr
    for backend in range(5):
        result = subprocess.run([str(executable)], capture_output=True, text=True,
                                timeout=60, env=dict(os.environ, PCC_GC_BACKEND=str(backend)))
        assert result.returncode == 0, (backend, result.stderr)
        assert result.stdout == reference.stdout, backend
