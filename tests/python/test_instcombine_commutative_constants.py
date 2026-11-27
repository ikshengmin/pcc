"""InstCombine moves the constant of a commutative operation to the right.

LLVM canonicalizes ``add/mul/and/or/xor C, x`` to ``op x, C``
(InstCombinerImpl::SimplifyAssociativeOrCommutative, by operand
complexity: constants lowest). The pcc runtime's IR keeps source order,
so ``add i64 104, %x`` reached the self backend, whose immediate forms read
only a constant right operand: it emitted ``mov x9, #0x68`` and a register
``add`` (about 2.2k per 600000 stepped instructions; docs/status.md, item 3
P0 notes). Target-neutral. Other rewrites still see the swapped line, two
constants still fold, and ``sub``/``shl`` keep their order.
"""
import subprocess

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.ir.optimization.instcombine import instcombine_text


def _fn(body: str, params: str = "i64 %x", ret: str = "i64") -> str:
    return f"define {ret} @f({params}) {{\nentry:\n{body}}}\n"


def _combined(text: str) -> str:
    return instcombine_text(text)[0]


@pytest.mark.parametrize("op,ty,constant", [
    ("add", "i64", "104"), ("and", "i32", "255"), ("or", "i64", "-8"),
    ("xor", "i8", "1"), ("mul", "i64", "12"), ("add nsw", "i32", "7"),
])
def test_constant_left_operands_move_right(op, ty, constant):
    text = _fn(f"  %r = {op} {ty} {constant}, %x\n  ret {ty} %r\n", f"{ty} %x", ty)
    assert f"%r = {op} {ty} %x, {constant}" in _combined(text)


def test_order_sensitive_and_constant_operations_keep_their_order():
    for line in ("%r = sub i64 5, %x", "%r = shl i64 1, %x", "%r = lshr i64 64, %x"):
        assert line in _combined(_fn(f"  {line}\n  ret i64 %r\n"))
    assert "%r = add i64 %x, %y" in _combined(_fn("  %r = add i64 %x, %y\n  ret i64 %r\n", "i64 %x, i64 %y"))
    folded = _combined(_fn("  %r = add i64 2, 3\n  ret i64 %r\n"))
    assert "ret i64 5" in folded


def test_swapped_operations_still_meet_other_rewrites():
    # mul 2^k, x -> shl x, k still applies after the swap.
    assert "%r = shl i64 %x, 3" in _combined(_fn("  %r = mul i64 8, %x\n  ret i64 %r\n"))
    # add C2, (sub x, C1) -> add x, C2-C1.
    text = _fn("  %s = sub i64 %x, 3\n  %r = add i64 10, %s\n  ret i64 %r\n")
    assert "add i64 %x, 7" in _combined(text)


PROGRAM = '''target triple = "arm64-apple-darwin"
define i64 @f(i64 %x) {
entry:
  %a = add i64 104, %x
  %b = and i64 255, %a
  %c = xor i64 1, %b
  %d = or i64 4096, %c
  %e = mul i64 3, %d
  ret i64 %e
}
define i32 @main() {
entry:
  %r = call i64 @f(i64 300)
  %ok = icmp eq i64 %r, 12735
  %s = select i1 %ok, i32 0, i32 53
  ret i32 %s
}
'''


@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
def test_canonicalized_program_runs(tmp_path):
    from pcc.ir.optimization.driver import optimize_ir
    ir = optimize_ir(PROGRAM, "instcombine")
    assert "add i64 %x, 104" in ir
    executable = tmp_path / "commutative"
    executable.write_bytes(link_executable([emit_owned_object(ir, "arm64-apple-darwin")], entry="_main"))
    executable.chmod(0o755)
    ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
