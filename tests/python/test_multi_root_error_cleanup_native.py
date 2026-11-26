"""Three to five operand owners share one runtime cleanup on an error edge.

When a call's operand raises, the operands already evaluated are released
before the exception propagates. Up to two plain owners took a runtime
helper; three or more were released inline (save the exception to a slot,
clear each owner, restore the exception, about 12 IR lines per edge, 7% of
Stage1's IR lines). py_cleanup_{three,four,five}_roots_preserving_exception
run the same program. The finalizer order matches CPython's, which
releases the latest operand first (Python Language Reference 3.3.1
"__del__", 6.3.4 "Calls").
"""
from __future__ import annotations

import re
import sys

from tests.python.process_timeout import run_process_group_timeout


PROGRAM = '''
class Canary:
    def __init__(self, name):
        self.name = name

    def __del__(self):
        print("del", self.name)


def boom(tag):
    raise ValueError(tag)


def consume(*values):
    return len(values)


def three():
    return consume(Canary("a3"), Canary("b3"), boom("three"))


def four():
    return consume(Canary("a4"), Canary("b4"), Canary("c4"), boom("four"))


def five():
    return consume(Canary("a5"), Canary("b5"), Canary("c5"), Canary("d5"), boom("five"))


def keys(source):
    return dict(source)


for probe in (three, four, five):
    try:
        probe()
    except ValueError as error:
        print("caught", error)
try:
    keys(5)
except TypeError:
    print("caught keys")
print("end")
'''


def test_three_to_five_operand_owners_release_like_cpython(tmp_path, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "multi_root.py"
    source.write_text(PROGRAM)
    reference = run_process_group_timeout([sys.executable, "-E", "-B", str(source)], timeout=30)
    assert reference.returncode == 0 and not reference.stderr, reference.stderr
    ir_path = tmp_path / "multi_root.ll"
    compile_python(str(source), str(ir_path), backend="self", ir_scaffold_mode="on",
                   emit_llvm_only=True)
    helpers = set(re.findall(r"@py_cleanup_(\w+)_roots_preserving_exception\(", ir_path.read_text()))
    assert {"three", "four", "five"} <= helpers, helpers
    binary = tmp_path / "multi_root"
    compile_python(str(source), str(binary), backend="self", ir_scaffold_mode="on",
                   runtime_archive=str(pcc_runtime_archive))
    ran = run_process_group_timeout([str(binary)], timeout=30)
    assert (ran.returncode, ran.stderr) == (0, ""), ran.stderr[-2000:]
    assert ran.stdout == reference.stdout
