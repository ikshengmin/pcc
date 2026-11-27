"""InstCombine narrows masked and compared extensions, as LLVM's does.

pcc-Python reads bytes and 32-bit words sign-extended to i64, so the
runtime's IR is full of ``and (sext i8 x), 255`` (a byte read as unsigned)
and ``icmp eq (sext i32 x), C`` (a flag word tested against a constant).
The self backend emitted ``ldrb`` + ``sxtb`` + ``and`` and ``ldar w`` +
``sxtw`` + ``cbz``: about 6k executed extensions more than LLVM's code
generation in a stepped handler window (docs/status.md, item 3 P0 notes).
Upstream (InstCombineAndOrXor.cpp, InstCombineCompares.cpp):

- ``and (sext iN x to iM), 2^N-1`` is ``zext iN x to iM``;
- ``icmp eq/ne (sext iN x to iM), C`` is ``icmp eq/ne iN x, C`` when C is a
  signed N-bit value, and ``(zext iN x)`` likewise when ``0 <= C < 2^N``.

Both rewrites are target-neutral. The program runs with boundary values.
"""
import subprocess

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.ir.optimization.instcombine import instcombine_text


def _fn(body: str, params: str = "i8 %x", ret: str = "i64") -> str:
    return f"define {ret} @f({params}) {{\nentry:\n{body}}}\n"


def _combined(text: str) -> str:
    return instcombine_text(text)[0]


@pytest.mark.parametrize("narrow,mask,order", [
    ("i8", "255", "sext-first"), ("i8", "255", "mask-first"),
    ("i16", "65535", "sext-first"), ("i32", "4294967295", "sext-first"),
])
def test_masked_sign_extension_is_a_zero_extension(narrow, mask, order):
    operands = f"%s, {mask}" if order == "sext-first" else f"{mask}, %s"
    text = _fn(f"  %s = sext {narrow} %x to i64\n  %m = and i64 {operands}\n  ret i64 %m\n",
               params=f"{narrow} %x")
    out = _combined(text)
    assert f"zext {narrow} %x to i64" in out, out
    assert "sext" not in out and " and " not in out, out


@pytest.mark.parametrize("mask", ["127", "511", "-1"])
def test_other_masks_keep_the_sign_extension(mask):
    text = _fn(f"  %s = sext i8 %x to i64\n  %m = and i64 %s, {mask}\n  ret i64 %m\n")
    out = _combined(text)
    assert "zext" not in out and "sext i8 %x to i64" in out, out


@pytest.mark.parametrize("ext,narrow,constant,expected", [
    ("sext", "i32", "0", "0"), ("sext", "i32", "-1", "-1"),
    ("sext", "i8", "-128", "-128"), ("sext", "i16", "32767", "32767"),
    ("zext", "i8", "200", "-56"), ("zext", "i32", "0", "0"),
])
@pytest.mark.parametrize("predicate", ["eq", "ne"])
def test_extension_compared_with_a_fitting_constant_compares_narrow(ext, narrow, constant, expected, predicate):
    text = _fn(f"  %s = {ext} {narrow} %x to i64\n  %c = icmp {predicate} i64 %s, {constant}\n"
               f"  %r = zext i1 %c to i64\n  ret i64 %r\n", params=f"{narrow} %x")
    out = _combined(text)
    assert f"icmp {predicate} {narrow} %x, {expected}" in out, out
    assert f"{ext} {narrow}" not in out, out


@pytest.mark.parametrize("ext,narrow,constant", [
    ("sext", "i32", "2147483648"), ("sext", "i8", "128"), ("zext", "i8", "256"),
    ("zext", "i8", "-1"),
])
def test_constants_outside_the_narrow_range_are_left_alone(ext, narrow, constant):
    text = _fn(f"  %s = {ext} {narrow} %x to i64\n  %c = icmp eq i64 %s, {constant}\n"
               f"  %r = zext i1 %c to i64\n  ret i64 %r\n", params=f"{narrow} %x")
    out = _combined(text)
    assert f"icmp eq i64 %s, {constant}" in out, out


def test_a_shared_extension_stays_for_its_other_uses():
    text = _fn("  %s = sext i32 %x to i64\n  %c = icmp eq i64 %s, 0\n"
               "  %r = select i1 %c, i64 7, i64 %s\n  ret i64 %r\n", params="i32 %x")
    out = _combined(text)
    assert "icmp eq i32 %x, 0" in out and "sext i32 %x to i64" in out, out


SOURCE = '''target triple = "arm64-apple-darwin"
@bytes = global [2 x i8] [i8 -128, i8 127], align 1
@word = global i32 -1, align 4
define i64 @unsigned_byte(ptr %p) {
entry:
  %v = load i8, ptr %p, align 1
  %s = sext i8 %v to i64
  %m = and i64 %s, 255
  ret i64 %m
}
define i64 @word_is(ptr %p, i64 %k) {
entry:
  %v = load atomic i32, ptr %p acquire, align 4
  %s = sext i32 %v to i64
  %c = icmp eq i64 %s, -1
  br i1 %c, label %yes, label %no
yes:
  ret i64 %k
no:
  ret i64 0
}
define i32 @main() {
entry:
  %hi = getelementptr i8, ptr @bytes, i64 0
  %lo = getelementptr i8, ptr @bytes, i64 1
  %a = call i64 @unsigned_byte(ptr %hi)
  %b = call i64 @unsigned_byte(ptr %lo)
  %c = call i64 @word_is(ptr @word, i64 5)
  %ok_a = icmp eq i64 %a, 128
  %ok_b = icmp eq i64 %b, 127
  %ok_c = icmp eq i64 %c, 5
  %ab = and i1 %ok_a, %ok_b
  %all = and i1 %ab, %ok_c
  %status = select i1 %all, i32 0, i32 47
  ret i32 %status
}
'''


# owned Darwin arm64 execution, before and after the rewrite
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
@pytest.mark.parametrize("combine", [False, True])
def test_narrowed_program_runs(tmp_path, combine):
    text = _combined(SOURCE) if combine else SOURCE
    if combine:
        assert "zext i8 %v to i64" in text and "icmp eq i32 %v, -1" in text, text
    output = tmp_path / "narrow"
    output.write_bytes(link_executable([emit_owned_object(text, "arm64-apple-darwin")],
                                       entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
