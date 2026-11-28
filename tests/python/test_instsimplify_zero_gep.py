"""instsimplify replaces an all-zero getelementptr with its base pointer.

LLVM's SimplifyGEPInst answers ``gep T, ptr X, 0...`` with X. The runtime's
final IR kept about 100000 such GEPs of globals and 64000 of local
pointers: each of the former became an adrp/add into a scratch register,
a copy into the GEP value's register and a load through it, where a load
of the global itself is one adrp and one page-offset load. A local base
replaces every use. A global base replaces only a GEP whose single use is
a load or store address (rematerializing a global address at several uses
costs more than holding it), and a thread-local base is never replaced
(each direct use of a TLS global is its own dyld TLV call).
"""
from __future__ import annotations

import re
import subprocess

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm
from pcc.ir.optimization.instsimplify import simplify_module_text


HEADER = 'target triple = "arm64-apple-darwin"\n'
SOURCE = HEADER + '''@counter = global i64 40, align 8
@pair = global [2 x i64] [i64 5, i64 6], align 8
@other = global i64 2, align 8
@tls = thread_local global i64 9, align 8
define i64 @probe(ptr %p, i1 %c) {
entry:
  %local = getelementptr i8, ptr %p, i64 0
  %v = load i64, ptr %local, align 8
  %g = getelementptr i8, ptr @counter, i64 0
  %w = load i64, ptr %g, align 8
  %decay = getelementptr inbounds [2 x i64], ptr @pair, i32 0, i32 0
  %x = load i64, ptr %decay, align 8
  %twice = getelementptr i8, ptr @other, i64 0
  %y = load i64, ptr %twice, align 8
  store i64 3, ptr %twice, align 8
  %t = getelementptr i8, ptr @tls, i64 0
  %z = load i64, ptr %t, align 8
  %held = getelementptr i8, ptr @other, i64 0
  store ptr %held, ptr %p, align 8
  br i1 %c, label %a, label %b
a:
  br label %b
b:
  %q = phi ptr [ %local, %entry ], [ %p, %a ]
  %s1 = add i64 %v, %w
  %s2 = add i64 %s1, %x
  %s3 = add i64 %s2, %y
  %s4 = add i64 %s3, %z
  %back = load ptr, ptr %q, align 8
  %u = load i64, ptr %back, align 8
  %s5 = add i64 %s4, %u
  ret i64 %s5
}
'''


def _body(text):
    return re.search(r"define i64 @probe\(.*?^}\n", text, re.S | re.M).group(0)


def test_zero_geps_fold_where_the_base_is_cheap():
    text, changed = simplify_module_text(SOURCE)
    assert changed
    body = _body(text)
    # Local base: every use, the phi included.
    assert "%local" not in body
    assert "load i64, ptr %p, align 8" in body and "phi ptr [ %p, %entry ]" in body
    # One load of a global (byte offset or array decay): the global itself.
    assert "load i64, ptr @counter, align 8" in body and "%g = " not in body
    assert "load i64, ptr @pair, align 8" in body and "%decay = " not in body
    # Two uses, a stored value and a thread-local base keep their GEP.
    assert "%twice = getelementptr i8, ptr @other, i64 0" in body
    assert "%held = getelementptr i8, ptr @other, i64 0" in body
    assert "%t = getelementptr i8, ptr @tls, i64 0" in body


def test_a_folded_global_load_uses_the_page_offset_form():
    text, _changed = simplify_module_text(SOURCE)
    assembly = emit_aarch64_darwin_asm(text, optimize=True)
    body = assembly.split("_probe:", 1)[1].split(".section", 1)[0]
    assert re.search(r"ldr x\d+, \[x9, _counter@PAGEOFF\]", body), body


@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
def test_folded_program_computes_what_the_original_computes(tmp_path):
    main = '''
define i32 @main() {
entry:
  %cell = alloca i64, align 8
  store i64 100, ptr %cell, align 8
  %a = call i64 @probe(ptr %cell, i1 true)
  store i64 7, ptr %cell, align 8
  %b = call i64 @probe(ptr %cell, i1 false)
  %sum = add i64 %a, %b
  %folded = trunc i64 %sum to i32
  %status = and i32 %folded, 127
  ret i32 %status
}
'''
    results = []
    for index, text in enumerate((SOURCE + main, simplify_module_text(SOURCE + main)[0])):
        output = tmp_path / ("probe" + str(index))
        output.write_bytes(link_executable([emit_owned_object(text, "arm64-apple-darwin")],
                                           entry="_main"))
        output.chmod(0o755)
        results.append(subprocess.run([str(output)], capture_output=True, timeout=10).returncode)
    assert results[0] == results[1] and results[0] != 0


@pytest.mark.parametrize("line, expected", [
    ("%r = and i1 false, %x", "false"),
    ("%r = and i1 %x, false", "false"),
    ("%r = or i1 false, %x", "%x"),
    ("%r = or i1 %x, false", "%x"),
    ("%r = and i1 true, %x", "%x"),
    ("%r = or i1 %x, true", "true"),
    ("%r = xor i1 false, %x", "%x"),
    ("%r = and i1 true, false", "false"),
    ("%r = or i1 false, true", "true"),
])
def test_boolean_literal_operands_simplify(line, expected):
    # The runtime's division guards left `and i1 false, %negone` and
    # `or i1 false, %overflow` in the final IR: the identities knew 0 but
    # not the i1 literal spelling false.
    text = ("@out = global i1 false\ndefine void @probe(i1 %x) {\nentry:\n  " + line + "\n"
            "  store i1 %r, ptr @out\n  ret void\n}\n")
    simplified, changed = simplify_module_text(text)
    assert changed
    assert "%r = " not in simplified
    assert "store i1 " + expected + ", ptr @out" in simplified, simplified


def test_a_gep_whose_base_simplifies_to_a_constant_stays():
    # %base becomes null in the same pass; the GEP must not turn the atomic
    # load's address into a constant null operand the backend refuses.
    text = HEADER + '''define i64 @probe(ptr %p) {
entry:
  %base = select i1 true, ptr null, ptr %p
  %slot = getelementptr i8, ptr %base, i64 0
  %v = load atomic i64, ptr %slot monotonic, align 8
  ret i64 %v
}
'''
    simplified, _changed = simplify_module_text(text)
    assert "load atomic i64, ptr null" not in simplified, simplified
    emit_aarch64_darwin_asm(simplified, optimize=True)


def _optional_key_program(target, chained=False):
    # A key-only sort call supplies no optional second key. The callee reads
    # the second key only on the branch where it is supplied. Inlining a
    # defined call must preserve a legal address representation until the
    # optimizer proves that branch dead, rather than require null-memory
    # operands from the backend.
    source = 'target triple = "' + target + '"\n' + '''define internal i64 @choose_key(ptr %first, ptr %second) {
entry:
  %present = icmp ne ptr %second, null
  br i1 %present, label %pair, label %single
pair:
  %slot = getelementptr i8, ptr %second, i64 0
  %other = load i64, ptr %slot, align 8
  ret i64 %other
single:
  %first_slot = getelementptr i8, ptr %first, i64 0
  %key = load i64, ptr %first_slot, align 8
  ret i64 %key
}
define i64 @key_only(ptr %first) {
entry:
  %key = call i64 @choose_key(ptr %first, ptr null)
  ret i64 %key
}
'''
    if chained:
        source = source.replace("%slot = getelementptr i8, ptr %second, i64 0",
                                "%base = getelementptr i8, ptr %second, i64 0\n  "
                                "%slot = getelementptr i8, ptr %base, i64 0")
    return source


def _inline_optional_key(target, chained):
    from pcc.ir.optimization.inline import inline_module

    simplified, _ = simplify_module_text(_optional_key_program(target, chained))
    inlined, changed = inline_module(simplified, include_definitions=True, definition_limit=96)
    assert changed
    assert not re.search(r"\bload[^\n]*, ptr null(?:[,\s]|$)", inlined)
    return inlined


@pytest.mark.parametrize("target", ("x86_64-unknown-linux-gnu", "x86_64-pc-windows-msvc"))
@pytest.mark.parametrize("chained", (False, True))
def test_optional_key_address_keeps_legal_form_after_inlining(target, chained):
    assert emit_owned_object(_inline_optional_key(target, chained), target)


def test_unclassified_memory_address_is_not_an_unconditional_access():
    # An aggregate store does not fit the scalar address matcher. It is not
    # permission to erase the explicit dynamic address in the first block.
    source = HEADER + '''define void @probe(ptr %p) {
entry:
  %slot = getelementptr i8, ptr %p, i64 0
  store { i64, i64 } { i64 1, i64 2 }, ptr %slot, align 8
  ret void
}
'''
    text, _ = simplify_module_text(source)
    assert "%slot = getelementptr i8, ptr %p, i64 0" in text


@pytest.mark.pcc_gate(platforms=("linux",), machines=("x86_64", "amd64"))
def test_defined_key_only_program_returns_the_supplied_key(tmp_path):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.x86_64_asm_driver import assemble_file

    target = "x86_64-unknown-linux-gnu"
    entry = assemble_file(""".intel_syntax noprefix
.text
.globl _start
_start:
  sub rsp, 16
  mov qword ptr [rsp], 42
  mov rdi, rsp
  call key_only
  mov edi, eax
  mov eax, 60
  syscall
""")
    # Execute only the defined key-only branch, with a real supplied key.
    # No invalid memory operation is executed as part of the witness.
    for name, text in (("original", _optional_key_program(target)),
                       ("folded", _inline_optional_key(target, True))):
        output = tmp_path / name
        payload = emit_owned_object(text, target)
        output.write_bytes(link_static_executable([entry, parse_relocatable(payload)]))
        output.chmod(0o755)
        assert subprocess.run([str(output)], capture_output=True, timeout=10).returncode == 42
