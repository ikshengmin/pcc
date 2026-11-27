"""InstCombine drops a truncation of an extension, as LLVM's visitTrunc does.

pcc-Python reads a byte as ``sext i8 (load i8) to i64`` and writes one as
``trunc i64 to i8``, so every byte copy in the runtime (memmove's and
memcpy's tails, string building) carried ``trunc (sext i8 x to i64) to
i8``: the self backend emitted ``ldrb`` + ``sxtb`` + a copy + ``and #0xff``
for a byte LLVM moves with ``ldrb`` + ``strb``. Upstream
(InstCombineCasts.cpp): ``trunc (ext iN x to iW) to iM`` is ``x`` when M
equals N, ``trunc iN x to iM`` when M is narrower and the same extension
of x to iM when it is wider. The program copies bytes with both signs
through each form.
"""
import subprocess

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.ir.optimization.instcombine import instcombine_text


def _fn(body: str, params: str, ret: str) -> str:
    return f"define {ret} @f({params}) {{\nentry:\n{body}}}\n"


def _combined(text: str) -> str:
    return instcombine_text(text)[0]


@pytest.mark.parametrize("ext", ("sext", "zext"))
@pytest.mark.parametrize("narrow", ("i8", "i16", "i32"))
def test_truncating_back_to_the_source_width_is_the_source(ext, narrow):
    text = _fn(f"  %w = {ext} {narrow} %x to i64\n  %t = trunc i64 %w to {narrow}\n"
               f"  ret {narrow} %t\n", f"{narrow} %x", narrow)
    out = _combined(text)
    assert f"ret {narrow} %x" in out, out
    assert "trunc" not in out and ext not in out, out


@pytest.mark.parametrize("ext", ("sext", "zext"))
def test_narrower_truncation_truncates_the_source(ext):
    text = _fn(f"  %w = {ext} i32 %x to i64\n  %t = trunc i64 %w to i8\n  ret i8 %t\n",
               "i32 %x", "i8")
    out = _combined(text)
    assert "%t = trunc i32 %x to i8" in out and ext not in out, out


@pytest.mark.parametrize("ext", ("sext", "zext"))
def test_wider_truncation_extends_the_source(ext):
    text = _fn(f"  %w = {ext} i8 %x to i64\n  %t = trunc i64 %w to i32\n  ret i32 %t\n",
               "i8 %x", "i32")
    out = _combined(text)
    assert f"%t = {ext} i8 %x to i32" in out and "trunc" not in out, out


@pytest.mark.parametrize("mask,order", (("255", "left"), ("255", "right"), ("-1", "left"),
                                        ("4095", "left")))
def test_a_mask_keeping_every_truncated_bit_is_dropped(mask, order):
    operands = f"%x, {mask}" if order == "left" else f"{mask}, %x"
    text = _fn(f"  %m = and i64 {operands}\n  %t = trunc i64 %m to i8\n  ret i8 %t\n",
               "i64 %x", "i8")
    out = _combined(text)
    assert "trunc i64 %x to i8" in out and " and " not in out, out


def test_a_mask_clearing_a_truncated_bit_stays():
    text = _fn("  %m = and i64 %x, 127\n  %t = trunc i64 %m to i8\n  ret i8 %t\n",
               "i64 %x", "i8")
    out = _combined(text)
    assert "and i64 %x, 127" in out and "trunc i64 %m to i8" in out, out


def test_a_shared_extension_stays_for_its_other_uses():
    text = _fn("  %w = sext i8 %x to i64\n  %t = trunc i64 %w to i8\n"
               "  %y = add i64 %w, 1\n  store i8 %t, ptr %p, align 1\n  ret i64 %y\n",
               "i8 %x, ptr %p", "i64")
    out = _combined(text)
    assert "store i8 %x, ptr %p" in out and "sext i8 %x to i64" in out, out


SOURCE = '''target triple = "arm64-apple-darwin"
@source = global [4 x i8] [i8 -128, i8 127, i8 -1, i8 5], align 1
@wide = global i32 -1000, align 4
define void @copy_bytes(ptr %dst, ptr %src, i64 %n) {
entry:
  br label %head
head:
  %i = phi i64 [0, %entry], [%next, %body]
  %more = icmp slt i64 %i, %n
  br i1 %more, label %body, label %done
body:
  %from = getelementptr i8, ptr %src, i64 %i
  %to = getelementptr i8, ptr %dst, i64 %i
  %v = load i8, ptr %from, align 1
  %w = sext i8 %v to i64
  %t = trunc i64 %w to i8
  store i8 %t, ptr %to, align 1
  %next = add i64 %i, 1
  br label %head
done:
  ret void
}
define i32 @narrow_word(ptr %p) {
entry:
  %v = load i32, ptr %p, align 4
  %w = zext i32 %v to i64
  %t = trunc i64 %w to i8
  %e = sext i8 %t to i32
  ret i32 %e
}
define i32 @widen_byte(ptr %p) {
entry:
  %v = load i8, ptr %p, align 1
  %w = sext i8 %v to i64
  %t = trunc i64 %w to i32
  ret i32 %t
}
define void @fill(ptr %dst, i64 %value, i64 %n) {
entry:
  %byte = and i64 %value, 255
  br label %head
head:
  %i = phi i64 [0, %entry], [%next, %body]
  %more = icmp slt i64 %i, %n
  br i1 %more, label %body, label %done
body:
  %to = getelementptr i8, ptr %dst, i64 %i
  %t = trunc i64 %byte to i8
  store i8 %t, ptr %to, align 1
  %next = add i64 %i, 1
  br label %head
done:
  ret void
}
define i32 @main() {
entry:
  %copy = alloca [4 x i8], align 1
  %filled = alloca [3 x i8], align 1
  call void @fill(ptr %filled, i64 4660, i64 3)
  %f2 = getelementptr i8, ptr %filled, i64 2
  %fill_byte = load i8, ptr %f2, align 1
  %ok_fill = icmp eq i8 %fill_byte, 52
  call void @copy_bytes(ptr %copy, ptr @source, i64 4)
  %c0 = getelementptr i8, ptr %copy, i64 0
  %c3 = getelementptr i8, ptr %copy, i64 3
  %first = load i8, ptr %c0, align 1
  %last = load i8, ptr %c3, align 1
  %n = call i32 @narrow_word(ptr @wide)
  %b = call i32 @widen_byte(ptr @source)
  %ok0 = icmp eq i8 %first, -128
  %ok1 = icmp eq i8 %last, 5
  %ok2 = icmp eq i32 %n, 24
  %ok3 = icmp eq i32 %b, -128
  %a01 = and i1 %ok0, %ok1
  %a012 = and i1 %a01, %ok2
  %a0123 = and i1 %a012, %ok3
  %all = and i1 %a0123, %ok_fill
  %status = select i1 %all, i32 0, i32 53
  ret i32 %status
}
'''


# owned Darwin arm64 execution, before and after the rewrite
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
@pytest.mark.parametrize("combine", (False, True))
def test_truncated_extensions_run(tmp_path, combine):
    text = _combined(SOURCE) if combine else SOURCE
    if combine:
        assert "store i8 %v, ptr %to" in text, text
        assert "trunc i32 %v to i8" in text and "sext i8 %v to i32" in text, text
        assert "trunc i64 %value to i8" in text, text
    output = tmp_path / "trunc_ext"
    output.write_bytes(link_executable([emit_owned_object(text, "arm64-apple-darwin")],
                                       entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
