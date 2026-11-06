"""A guarded, non-repeating prologue has the same scalar-slot lifetime as entry.

Python execution-model entry checks must remain before the body. Promotion
does not move those checks or admit slots allocated by repeated loop blocks.
"""

import os
from pathlib import Path
import platform
import re
import subprocess
import sys

import pytest

from pcc.frontends.python.pipeline import compile_python
from pcc.ir.optimization.mem2reg import mem2reg_text
from tests.owned_ir_validation import verify_ir_text


GUARDED = '''define i64 @total(i64 %n, i1 %allowed) {
guard:
  br i1 %allowed, label %prologue, label %denied
prologue:
  %sum = alloca i64
  %i = alloca i64
  store i64 0, ptr %sum
  store i64 0, ptr %i
  br label %head
head:
  %cur = load i64, ptr %i
  %more = icmp slt i64 %cur, %n
  br i1 %more, label %body, label %done
body:
  %acc = load i64, ptr %sum
  %next = add i64 %acc, %cur
  store i64 %next, ptr %sum
  %step = add i64 %cur, 1
  store i64 %step, ptr %i
  br label %head
done:
  %result = load i64, ptr %sum
  ret i64 %result
denied:
  ret i64 -1
}
'''


@pytest.mark.parametrize("duplicate_edge", [False, True])
def test_guarded_loop_slots_are_promoted(duplicate_edge):
    source = GUARDED
    if duplicate_edge:
        source = source.replace('label %prologue, label %denied',
                                'label %prologue, label %prologue')
    result, changed = mem2reg_text(source)
    assert changed
    verify_ir_text(result)
    assert "alloca" not in result
    assert " = load " not in result and "  store " not in result
    assert result.count(" = phi i64 ") == 2
    assert "br i1 %allowed" in result
    assert "ret i64 -1" in result


def test_single_execution_tail_is_promoted():
    # This was the previous blanket rejection of any non-entry allocation.
    source = '''define i64 @late() {
entry:
  br label %tail
tail:
  %slot = alloca i64, align 8
  store i64 9, ptr %slot, align 8
  %value = load i64, ptr %slot, align 8
  ret i64 %value
}
'''
    result, changed = mem2reg_text(source)
    assert changed and "alloca" not in result
    assert "ret i64 9" in result
    verify_ir_text(result)


def test_a_backedge_to_the_allocation_block_remains_unpromoted():
    source = '''define i64 @fresh(i1 %again) {
entry:
  br label %allocate
allocate:
  %slot = alloca i64
  store i64 9, ptr %slot
  %value = load i64, ptr %slot
  br i1 %again, label %allocate, label %done
done:
  ret i64 %value
}
'''
    result, changed = mem2reg_text(source)
    assert not changed and result == source


PROGRAM = '''import gc
from pcc import i64
def sum_up(limit: i64) -> i64:
    index: i64 = 0
    total: i64 = 0
    while index < limit:
        if index % 17 == 0:
            gc.collect()
        if index & 1:
            total += index
        else:
            total -= index
        index += 1
    return total
def main():
    print(sum_up(0), sum_up(1), sum_up(10), sum_up(1000))
main()
'''


def test_default_frontend_promotes_slots_after_the_activation_guard(tmp_path):
    source = tmp_path / "prologue.py"
    source.write_text(PROGRAM)
    output = tmp_path / "prologue.ll"
    compile_python(str(source), str(output), emit_llvm_only=True, backend="self")
    body = re.search(r'define[^\n]*@user_prologue_sum_up\([^\n]*\{\n(.*?)^}',
                     output.read_text(), re.M | re.S).group(1)
    assert "activation.entry" in body and "@py_recursion_enter(" in body
    assert "@py_recursion_leave(" in body
    assert "alloca i64" not in body
    assert " = phi i64 " in body


@pytest.mark.skipif(sys.platform != "darwin" or platform.machine() != "arm64",
                    reason="owned Darwin arm64 emitter execution")
def test_guard_success_failure_and_loop_paths_execute_before_and_after(tmp_path):
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm

    source = 'target triple = "arm64-apple-darwin"\n' + GUARDED + '''
define i32 @main() {
entry:
  %yes = call i64 @total(i64 10, i1 true)
  %no = call i64 @total(i64 10, i1 false)
  %empty = call i64 @total(i64 0, i1 true)
  %sum = add i64 %yes, %no
  %all = add i64 %sum, %empty
  %ok = icmp eq i64 %all, 44
  %status = select i1 %ok, i32 0, i32 1
  ret i32 %status
}
'''
    promoted, changed = mem2reg_text(source)
    assert changed
    for name, text in (("control", source), ("promoted", promoted)):
        assembly = tmp_path / (name + ".s")
        assembly.write_text(emit_aarch64_darwin_asm(text))
        binary = tmp_path / name
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[2] / "scripts/pcc_link_macho.py"),
             "--out", str(binary), "--asm", str(assembly)],
            capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")


@pytest.mark.integration
def test_guarded_frontend_program_matches_python_across_collectors(tmp_path):
    source = tmp_path / "prologue.py"
    source.write_text(PROGRAM)
    reference = subprocess.run([sys.executable, str(source)], capture_output=True,
                               text=True, timeout=10)
    assert reference.returncode == 0, reference.stderr
    binary = tmp_path / "prologue"
    result = subprocess.run(
        [sys.executable, "-m", "pcc", "--backend", "self", str(source), "-o", str(binary)],
        capture_output=True, text=True, timeout=120,
        env=dict(os.environ, PCC_NO_AUTO_PCC1="1"),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for collector in range(5):
        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10,
                             env=dict(os.environ, PCC_GC_BACKEND=str(collector)))
        assert (ran.returncode, ran.stdout, ran.stderr) == (0, reference.stdout, ""), collector
