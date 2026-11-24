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


# The former expressions of the pass: the reference its scanner must equal.
# Native pcc1 runs the pass and its owned regex engine rejects them.
_VALUE = r"(?:null|%[\w.$-]+|@[\w.$-]+)"
_REFERENCE_DROP = r"^\s*call void \((?:ptr|ptr, ptr|ptr, ptr, ptr)\) " + (
    r"@(?:pcc_gc_pin|pcc_gc_unpin|pcc_gc_note_slot_write_barrier|pcc_gc_note_write_barrier)\("
    rf"ptr {_VALUE}(?:, ptr {_VALUE}){{0,2}}\)\s*$"
)
_REFERENCE_ZERO = r"^(\s*)(%[\w.$-]+) = call i64 \((?:ptr|ptr, i64)\) " + (
    r"@(?:pcc_gc_foreign_lease_acquire|pcc_gc_borrowed_lease_acquire|pcc_gc_foreign_lease_release)\("
    rf"ptr {_VALUE}(?:, i64 (?:-?\d+|%[\w.$-]+))?\)\s*$"
)


def _reference_text(text):
    import re

    drop, zero = re.compile(_REFERENCE_DROP), re.compile(_REFERENCE_ZERO)
    if "@pcc_gc_" not in text:
        return text, False
    output, changed, inside = [], False, False
    for line in text.split("\n"):
        if not inside:
            inside = line.startswith("define ") and line.rstrip().endswith("{")
            output.append(line)
            continue
        if line == "}":
            inside = False
            output.append(line)
            continue
        if "@pcc_gc_" in line:
            if drop.match(line):
                changed = True
                continue
            match = zero.match(line)
            if match:
                output.append(f"{match.group(1)}{match.group(2)} = add i64 0, 0")
                changed = True
                continue
        output.append(line)
    return ("\n".join(output), True) if changed else (text, False)


EDGE_LINES = [
    "  call void (ptr) @pcc_gc_pin(ptr %x.1)",
    "call void (ptr) @pcc_gc_pin(ptr null)\t ",
    "  call void (ptr) @pcc_gc_pin(ptr @g$x-1)",
    "  call void (ptr) @pcc_gc_pin(ptr %a, ptr %b)",
    "  call void (ptr) @pcc_gc_pin(ptr %a, ptr %b, ptr %c)",
    "  call void (ptr) @pcc_gc_pin(ptr %a, ptr %b, ptr %c, ptr %d)",
    "  call void (ptr, ptr, ptr) @pcc_gc_note_slot_write_barrier(ptr null, ptr %s, ptr %v)",
    "  call void (ptr, ptr) @pcc_gc_note_write_barrier(ptr %s, ptr %v)",
    "  call void (ptr) @pcc_gc_pinned(ptr %x)",
    "  call void (ptr) @pcc_gc_pin(ptr %)",
    "  call void (ptr) @pcc_gc_pin(ptr nullx)",
    "  call void (ptr) @pcc_gc_pin(ptr %x) ; tail",
    "  call void (ptr) @pcc_gc_pin(ptr %\"quoted\")",
    "  call void (i64) @pcc_gc_pin(ptr %x)",
    "  call void (ptr, ptr, ptr, ptr) @pcc_gc_unpin(ptr %x)",
    "  call fastcc void (ptr) @pcc_gc_pin(ptr %x)",
    "  call void (ptr) @pcc_gc_unpin(ptr %ü.é)",
    "  %t.1 = call i64 (ptr) @pcc_gc_foreign_lease_acquire(ptr %root)",
    "\t%t = call i64 (ptr) @pcc_gc_borrowed_lease_acquire(ptr @root)",
    "  %s.2 = call i64 (ptr, i64) @pcc_gc_foreign_lease_release(ptr %root, i64 %t.1)",
    "  %s.3 = call i64 (ptr, i64) @pcc_gc_foreign_lease_release(ptr %root, i64 -42)",
    "  %s.4 = call i64 (ptr, i64) @pcc_gc_foreign_lease_release(ptr %root, i64 -)",
    "  %s.5 = call i64 (ptr, i64) @pcc_gc_foreign_lease_release(ptr %root, i64 %)",
    "  %s.6 = call i64 (ptr, i64) @pcc_gc_foreign_lease_release(ptr %root, i64 ١٢)",
    "  %s.7 = call i64 (ptr) @pcc_gc_foreign_lease_release(ptr %root, i64 7)",
    "  %s.8 = call i64 (ptr, i64) @pcc_gc_foreign_lease_release(ptr %root)",
    "  %s.9 = call i64 (ptr, i64) @pcc_gc_foreign_lease_release(ptr %root, ptr %x)",
    "  %s.10= call i64 (ptr) @pcc_gc_foreign_lease_acquire(ptr %root)",
    "  %s.11 = call i32 (ptr) @pcc_gc_foreign_lease_acquire(ptr %root)",
    "  %s.12 = call i64 (ptr) @pcc_gc_foreign_lease_acquire(ptr %root)  ",
    "  @s = call i64 (ptr) @pcc_gc_foreign_lease_acquire(ptr %root)",
    "  %loaded = call ptr (ptr, ptr) @pcc_gc_load_ptr(ptr null, ptr %slot)",
    "  call void (ptr, ptr) @pcc_gc_store_root(ptr %slot, ptr %value)",
    "",
]


def test_scanner_accepts_exactly_the_former_expressions_on_ascii():
    """ASCII lines: identical to the former expressions. Non-ASCII ones keep
    their inert call, so host pcc0 and native pcc1 decide alike."""
    ascii_lines = [line for line in EDGE_LINES if line.isascii()]
    body = "define void @edges() {\n" + "\n".join(ascii_lines) + "\n}\n"
    assert fused_protocol_text(body) == _reference_text(body)
    for line in EDGE_LINES:
        module = "define void @one() {\n" + line + "\n}\n"
        if line.isascii():
            assert fused_protocol_text(module) == _reference_text(module), line
        else:
            assert fused_protocol_text(module) == (module, False), line
            assert _reference_text(module)[1], line  # the former \w took it


def test_scanner_equals_the_former_expressions_on_the_pass_inputs(tmp_path, monkeypatch):
    """The actual pre-pass IR of a program and of runtime modules.

    The pipeline imports the pass when it runs it, so a wrapper on the module
    attribute sees every input the compile gives it and checks both answers.
    """
    import pcc.frontends.python.fused_protocol_pass as fused
    from pcc.frontends.python.pipeline import compile_python
    from pathlib import Path

    scanner = fused.fused_protocol_text
    inputs = []

    def checked(text):
        answer = scanner(text)
        assert answer == _reference_text(text)
        inputs.append(text)
        return answer

    monkeypatch.setattr(fused, "fused_protocol_text", checked)
    program = tmp_path / "program.py"
    program.write_text(PROGRAM, encoding="utf-8")
    sources = [program, Path("pcc/runtime/py/py_weakref.py"),
               Path("pcc/runtime/py/py_tuple.py")]
    for index, source in enumerate(sources):
        compile_python(str(source), str(tmp_path / f"module{index}.ll"), emit_llvm_only=True,
                       python_library=source != program, backend="self")
    seen = "\n".join(inputs)
    assert len(inputs) >= len(sources)
    assert seen.count("@pcc_gc_pin(") > 1 and seen.count("@pcc_gc_foreign_lease_acquire(") > 1


def test_the_scanner_runs_natively_where_the_expressions_did_not(tmp_path):
    """The pass's own source, compiled by pcc, answers as under CPython.

    A native program with the former module-level re.compile stopped with
    "pattern outside the native regex subset", the error native pcc1 met.
    """
    from pcc.frontends.python.pipeline import compile_python
    from pathlib import Path

    source = Path("pcc/frontends/python/fused_protocol_pass.py").read_text(encoding="utf-8")
    assert "import re" not in source
    program = tmp_path / "native_scan.py"
    program.write_text(
        source + "\n\nLINES = " + repr(EDGE_LINES) + "\n"
        + "for line in LINES:\n"
        + "    text = 'define void @one() {\\n' + line + '\\n}\\n'\n"
        + "    print(repr(fused_protocol_text(text)))\n",
        encoding="utf-8",
    )
    executable = tmp_path / "native_scan.out"
    compile_python(str(program), str(executable), ir_scaffold_mode="on", backend="self")
    reference = subprocess.run([sys.executable, str(program)], capture_output=True,
                               text=True, timeout=30)
    assert reference.returncode == 0, reference.stderr
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=60)
    assert (result.returncode, result.stdout) == (0, reference.stdout), result.stderr
