"""InstCombine removes a negated branch condition, as LLVM's does.

pcc-Python lowers ``if not f(x):`` to ``%n = xor i1 %c, true`` followed by
``br i1 %n``, and the self backend materialized the negation (``mov``,
``mov #1``, ``eor``, a byte spill) before testing it again
(``_list_is_sane``, ``_ptr_can_have_header``: docs/status.md, item 3).
Upstream (InstCombineBranch / visitBranchInst, InstCombineCompares):
``br (xor X, true), T, F`` is ``br X, F, T``, and ``xor (icmp P a, b),
true`` is ``icmp !P a, b`` when the compare has no other use. A negation
with another use keeps its value. The program branches on every predicate.
"""
import subprocess

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.ir.optimization.instcombine import instcombine_text


def _combined(text: str) -> str:
    return instcombine_text(text)[0]


def test_a_branch_on_a_negation_swaps_its_successors():
    text = ("define i64 @f(i1 %c) {\nentry:\n  %n = xor i1 %c, true\n"
            "  br i1 %n, label %yes, label %no\nyes:\n  ret i64 1\nno:\n  ret i64 0\n}\n")
    out = _combined(text)
    assert "br i1 %c, label %no, label %yes" in out and "xor" not in out, out


@pytest.mark.parametrize("pred,inverse", (
    ("eq", "ne"), ("ne", "eq"), ("slt", "sge"), ("sge", "slt"), ("sgt", "sle"),
    ("sle", "sgt"), ("ult", "uge"), ("uge", "ult"), ("ugt", "ule"), ("ule", "ugt"),
))
def test_a_negated_compare_is_the_inverse_compare(pred, inverse):
    text = (f"define i1 @f(i64 %a, i64 %b) {{\nentry:\n  %c = icmp {pred} i64 %a, %b\n"
            "  %n = xor i1 %c, 1\n  ret i1 %n\n}\n")
    out = _combined(text)
    assert f"%n = icmp {inverse} i64 %a, %b" in out and "xor" not in out, out


def test_a_negation_with_another_use_keeps_its_value():
    text = ("define i64 @f(i1 %c) {\nentry:\n  %n = xor i1 %c, true\n"
            "  %w = zext i1 %n to i64\n  br i1 %n, label %yes, label %no\n"
            "yes:\n  ret i64 %w\nno:\n  ret i64 0\n}\n")
    out = _combined(text)
    assert "%n = xor i1 %c, true" in out and "br i1 %n, label %yes, label %no" in out, out


def test_a_compare_with_another_use_is_not_inverted():
    text = ("define i64 @f(i64 %a) {\nentry:\n  %c = icmp slt i64 %a, 4\n"
            "  %n = xor i1 %c, true\n  %w = zext i1 %c to i64\n  %v = zext i1 %n to i64\n"
            "  %s = add i64 %w, %v\n  ret i64 %s\n}\n")
    out = _combined(text)
    assert "%c = icmp slt i64 %a, 4" in out and "xor i1 %c" in out, out


SOURCE = '''target triple = "arm64-apple-darwin"
define i64 @classify(i64 %a, i64 %b) {
entry:
  %lt = icmp slt i64 %a, %b
  %nlt = xor i1 %lt, true
  br i1 %nlt, label %not_less, label %less
less:
  %ult = icmp ult i64 %a, %b
  %nult = xor i1 %ult, 1
  br i1 %nult, label %wrapped, label %plain
wrapped:
  ret i64 3
plain:
  ret i64 1
not_less:
  %eq = icmp eq i64 %a, %b
  %neq = xor i1 %eq, true
  %r = select i1 %neq, i64 2, i64 0
  ret i64 %r
}
define i32 @main() {
entry:
  %a = call i64 @classify(i64 -1, i64 2)
  %b = call i64 @classify(i64 1, i64 2)
  %c = call i64 @classify(i64 2, i64 2)
  %d = call i64 @classify(i64 5, i64 2)
  %ok_a = icmp eq i64 %a, 3
  %ok_b = icmp eq i64 %b, 1
  %ok_c = icmp eq i64 %c, 0
  %ok_d = icmp eq i64 %d, 2
  %ab = and i1 %ok_a, %ok_b
  %cd = and i1 %ok_c, %ok_d
  %all = and i1 %ab, %cd
  %status = select i1 %all, i32 0, i32 59
  ret i32 %status
}
'''


# owned Darwin arm64 execution, before and after the rewrite
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
@pytest.mark.parametrize("combine", (False, True))
def test_inverted_conditions_run(tmp_path, combine):
    text = _combined(SOURCE) if combine else SOURCE
    if combine:
        assert "xor" not in text, text
    output = tmp_path / "inverted"
    output.write_bytes(link_executable([emit_owned_object(text, "arm64-apple-darwin")],
                                       entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
