"""A GEP with a register index folds into its load/store, as LLVM selects it.

``%a = getelementptr T, ptr %p, i64 %i`` whose only use is the directly
following scalar load or store of ``%a`` emitted ``add`` (after ``lsl`` for a
scaled index) and then the access through ``[xA]``. When ``%p`` and ``%i``
are both in registers it is selected as the register-offset form
``ldr/str Rt, [Xp, Xi]``, or ``[Xp, Xi, lsl #log2(size)]`` when the element
size equals the access size: the GEP is not emitted. A stepped handler window
executed about 8.6k ``add`` + access pairs (docs/status.md, item 3 P0 notes),
for example the byte loops of memchr/memcmp/memset. A second use of the GEP,
an instruction between the two, a 32-bit index, an element size other than 1
or the access size, and atomic or volatile accesses keep the old form.
Encodings are clang 20's (labeled reference); the program executes each form.
"""
import re
import subprocess

import pytest

from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.arm64_encode import assemble_text
from pcc.backend.macho_exec import link_executable
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


ENCODINGS = {
    "  ldrb w0, [x1, x2]": 0x38626820,
    "  ldrb w19, [x20, x8]": 0x38686A93,
    "  ldr x0, [x1, x2]": 0xF8626820,
    "  ldr w3, [x4, x5]": 0xB8656883,
    "  ldrh w6, [x7, x8]": 0x786868E6,
    "  str x1, [x2, x3]": 0xF8236841,
    "  str w1, [x2, x3]": 0xB8236841,
    "  strb w1, [x2, x3]": 0x38236841,
    "  strh w1, [x2, x3]": 0x78236841,
    "  strb wzr, [x2, x3]": 0x3823685F,
    "  str xzr, [x19, x28]": 0xF83C6A7F,
    "  ldr x0, [x1, x2, lsl #3]": 0xF8627820,
    "  ldr w0, [x1, x2, lsl #2]": 0xB8627820,
    "  ldrh w0, [x1, x2, lsl #1]": 0x78627820,
    "  str x0, [x1, x2, lsl #3]": 0xF8227820,
    "  ldr x5, [x0, x0]": 0xF8606805,
    "  ldrb w0, [sp, x2]": 0x38626BE0,
}


def test_register_offset_accesses_encode_like_the_reference_assembler():
    for text, word in ENCODINGS.items():
        assert int.from_bytes(assemble_text(text).code[:4], "little") == word, text
    for bad in ("  ldr x0, [x1, x2, lsl #2]", "  ldrb w0, [x1, x2, lsl #1]",
                "  ldr x0, [x1, sp]", "  ldr x0, [x1, x2]!"):
        with pytest.raises(Exception):
            assemble_text(bad)


def test_register_offset_records_reencode_to_the_same_words():
    from pcc.backend import self_backend_aarch64_darwin_mem as mem
    from pcc.backend import self_backend_aarch64_semantics as sem
    from pcc.backend.self_backend_aarch64_semantic_encode import encode_semantic_instruction

    for text, word in ENCODINGS.items():
        match = re.fullmatch(r"  (\w+) (\w+), \[(\w+), (\w+)(?:, lsl #(\d))?\]", text)
        mnemonic, register, base, index, shift = match.groups()
        assert mem.emitted_register_offset_memory_line(
            mnemonic, register, base, index, int(shift or 0)) == text
        mem.begin_direct_instruction_capture(capture_semantics=True)
        try:
            line = mem.emitted_register_offset_memory_line(mnemonic, register, base, index, int(shift or 0))
            assert line == mem.DIRECT_INSTRUCTION_PLACEHOLDER
            records = mem.borrow_direct_instruction_records()
            owner = mem.borrow_direct_instruction_semantics()
            assert records.get4_unchecked(0).first == word, text
            header = owner.require_header(0)
            assert sem.OPCODE_NAMES[header.first] == mnemonic
            tokens = [owner.operand(0, i) for i in range(header.fourth)]
            address = tokens[1]
            assert (address.first, address.second, address.third) == (
                sem.TOKEN_ADDRESS, sem.ADDRESS_REGISTER, 3 if shift else 2), text
            assert encode_semantic_instruction(header.first, tokens).first == word, text
        finally:
            mem.end_direct_instruction_capture()


SOURCE = '''target triple = "arm64-apple-darwin"
define i64 @load_byte(ptr %p, i64 %i) {
entry:
  %a = getelementptr i8, ptr %p, i64 %i
  %v = load i8, ptr %a, align 1
  %w = zext i8 %v to i64
  ret i64 %w
}
define void @store_byte(ptr %p, i64 %i, i8 %v) {
entry:
  %a = getelementptr inbounds i8, ptr %p, i64 %i
  store i8 %v, ptr %a, align 1
  ret void
}
define i64 @load_word(ptr %p, i64 %i) {
entry:
  %a = getelementptr inbounds i64, ptr %p, i64 %i
  %v = load i64, ptr %a, align 8
  ret i64 %v
}
define void @store_word(ptr %p, i64 %i, i64 %v) {
entry:
  %a = getelementptr inbounds i64, ptr %p, i64 %i
  store i64 %v, ptr %a, align 8
  ret void
}
define ptr @load_pointer(ptr %p, i64 %i) {
entry:
  %a = getelementptr inbounds ptr, ptr %p, i64 %i
  %v = load ptr, ptr %a, align 8
  ret ptr %v
}
define i64 @load_half(ptr %p, i64 %i) {
entry:
  %a = getelementptr inbounds i16, ptr %p, i64 %i
  %v = load i16, ptr %a, align 2
  %w = zext i16 %v to i64
  ret i64 %w
}
define i64 @load_int(ptr %p, i64 %i) {
entry:
  %a = getelementptr inbounds i32, ptr %p, i64 %i
  %v = load i32, ptr %a, align 4
  %w = zext i32 %v to i64
  ret i64 %w
}
define i64 @byte_index_word(ptr %p, i64 %i) {
entry:
  %a = getelementptr i8, ptr %p, i64 %i
  %v = load i64, ptr %a, align 8
  ret i64 %v
}
define void @store_zero(ptr %p, i64 %i) {
entry:
  %a = getelementptr i8, ptr %p, i64 %i
  store i64 0, ptr %a, align 8
  ret void
}
define void @store_constant(ptr %p, i64 %i) {
entry:
  %a = getelementptr inbounds i64, ptr %p, i64 %i
  store i64 99, ptr %a, align 8
  ret void
}
define i64 @shared(ptr %p, i64 %i) {
entry:
  %a = getelementptr i8, ptr %p, i64 %i
  %v = load i8, ptr %a, align 1
  %w = zext i8 %v to i64
  %q = ptrtoint ptr %a to i64
  %s = add i64 %w, %q
  ret i64 %s
}
define i64 @not_adjacent(ptr %p, i64 %i, i64 %k) {
entry:
  %a = getelementptr i8, ptr %p, i64 %i
  %k2 = add i64 %k, 3
  %v = load i8, ptr %a, align 1
  %w = zext i8 %v to i64
  %s = add i64 %w, %k2
  ret i64 %s
}
define i64 @narrow_index(ptr %p, i32 %i) {
entry:
  %a = getelementptr i8, ptr %p, i32 %i
  %v = load i8, ptr %a, align 1
  %w = zext i8 %v to i64
  ret i64 %w
}
define i64 @scale_mismatch(ptr %p, i64 %i) {
entry:
  %a = getelementptr inbounds i64, ptr %p, i64 %i
  %v = load i32, ptr %a, align 4
  %w = zext i32 %v to i64
  ret i64 %w
}
define i64 @atomic_word(ptr %p, i64 %i) {
entry:
  %a = getelementptr inbounds i64, ptr %p, i64 %i
  %v = load atomic i64, ptr %a acquire, align 8
  ret i64 %v
}
define i64 @volatile_byte(ptr %p, i64 %i) {
entry:
  %a = getelementptr i8, ptr %p, i64 %i
  %v = load volatile i8, ptr %a, align 1
  %w = zext i8 %v to i64
  ret i64 %w
}
define void @fill(ptr %p, i64 %n) {
entry:
  br label %head
head:
  %i = phi i64 [ 0, %entry ], [ %next, %body ]
  %more = icmp slt i64 %i, %n
  br i1 %more, label %body, label %done
body:
  %m = mul i64 %i, 3
  %v = add i64 %m, 1
  %a = getelementptr inbounds i64, ptr %p, i64 %i
  store i64 %v, ptr %a, align 8
  %next = add i64 %i, 1
  br label %head
done:
  ret void
}
define i64 @sum(ptr %p, i64 %n) {
entry:
  br label %head
head:
  %i = phi i64 [ 0, %entry ], [ %next, %body ]
  %acc = phi i64 [ 0, %entry ], [ %acc2, %body ]
  %more = icmp slt i64 %i, %n
  br i1 %more, label %body, label %done
body:
  %a = getelementptr inbounds i64, ptr %p, i64 %i
  %v = load i64, ptr %a, align 8
  %acc2 = add i64 %acc, %v
  %next = add i64 %i, 1
  br label %head
done:
  ret i64 %acc
}
define void @fill_bytes(ptr %p, i64 %n) {
entry:
  br label %head
head:
  %i = phi i64 [ 0, %entry ], [ %next, %body ]
  %more = icmp slt i64 %i, %n
  br i1 %more, label %body, label %done
body:
  %t = trunc i64 %i to i8
  %v = xor i8 %t, 90
  %a = getelementptr inbounds i8, ptr %p, i64 %i
  store i8 %v, ptr %a, align 1
  %next = add i64 %i, 1
  br label %head
done:
  ret void
}
define i64 @find(ptr %p, i64 %n, i64 %value) {
entry:
  %want = and i64 %value, 255
  br label %head
head:
  %i = phi i64 [ 0, %entry ], [ %next, %step ]
  %more = icmp slt i64 %i, %n
  br i1 %more, label %body, label %missing
body:
  %a = getelementptr i8, ptr %p, i64 %i
  %b = load i8, ptr %a, align 1
  %w = zext i8 %b to i64
  %hit = icmp eq i64 %w, %want
  br i1 %hit, label %found, label %step
step:
  %next = add i64 %i, 1
  br label %head
found:
  ret i64 %i
missing:
  ret i64 -1
}
define i32 @main() {
entry:
  %words = alloca [8 x i64], align 8
  %bytes = alloca [32 x i8], align 8
  call void @fill(ptr %words, i64 8)
  %s1 = call i64 @sum(ptr %words, i64 8)
  call void @fill_bytes(ptr %bytes, i64 32)
  %f = call i64 @find(ptr %bytes, i64 32, i64 87)
  %b13 = call i64 @load_byte(ptr %bytes, i64 13)
  call void @store_byte(ptr %bytes, i64 13, i8 7)
  %b13b = call i64 @load_byte(ptr %bytes, i64 13)
  %w3 = call i64 @load_word(ptr %words, i64 3)
  call void @store_word(ptr %words, i64 3, i64 1000)
  %w3b = call i64 @load_word(ptr %words, i64 3)
  %h = call i64 @load_half(ptr %words, i64 8)
  %n = call i64 @load_int(ptr %words, i64 4)
  %bw = call i64 @byte_index_word(ptr %words, i64 16)
  call void @store_zero(ptr %words, i64 40)
  call void @store_constant(ptr %words, i64 6)
  %s2 = call i64 @sum(ptr %words, i64 8)
  %slot = alloca ptr, align 8
  store ptr %bytes, ptr %slot, align 8
  %pp = call ptr @load_pointer(ptr %slot, i64 0)
  %pb = call i64 @load_byte(ptr %pp, i64 2)
  %sh = call i64 @shared(ptr %bytes, i64 0)
  %base = ptrtoint ptr %bytes to i64
  %shx = sub i64 %sh, %base
  %na = call i64 @not_adjacent(ptr %bytes, i64 1, i64 4)
  %ni = call i64 @narrow_index(ptr %bytes, i32 3)
  %sm = call i64 @scale_mismatch(ptr %words, i64 1)
  %aw = call i64 @atomic_word(ptr %words, i64 1)
  %vb = call i64 @volatile_byte(ptr %bytes, i64 4)
  %c1 = icmp eq i64 %s1, 92
  %c2 = icmp eq i64 %f, 13
  %c3 = icmp eq i64 %b13, 87
  %c4 = icmp eq i64 %b13b, 7
  %c5 = icmp eq i64 %w3, 10
  %c6 = icmp eq i64 %w3b, 1000
  %c7 = icmp eq i64 %h, 7
  %c8 = icmp eq i64 %n, 7
  %c9 = icmp eq i64 %bw, 7
  %c10 = icmp eq i64 %s2, 1146
  %c11 = icmp eq i64 %pb, 88
  %c12 = icmp eq i64 %shx, 90
  %c13 = icmp eq i64 %na, 98
  %c14 = icmp eq i64 %ni, 89
  %c15 = icmp eq i64 %sm, 4
  %c16 = icmp eq i64 %aw, 4
  %c17 = icmp eq i64 %vb, 94
  %a1 = and i1 %c1, %c2
  %a2 = and i1 %a1, %c3
  %a3 = and i1 %a2, %c4
  %a4 = and i1 %a3, %c5
  %a5 = and i1 %a4, %c6
  %a6 = and i1 %a5, %c7
  %a7 = and i1 %a6, %c8
  %a8 = and i1 %a7, %c9
  %a9 = and i1 %a8, %c10
  %a10 = and i1 %a9, %c11
  %a11 = and i1 %a10, %c12
  %a12 = and i1 %a11, %c13
  %a13 = and i1 %a12, %c14
  %a14 = and i1 %a13, %c15
  %a15 = and i1 %a14, %c16
  %a16 = and i1 %a15, %c17
  %status = select i1 %a16, i32 0, i32 61
  ret i32 %status
}
'''


def _body(assembly: str, name: str) -> str:
    return re.search(r"^_" + name + r":\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)",
                     assembly, re.S | re.M).group(1)


FOLDED = {
    "load_byte": r"ldrb w\d+, \[x\d+, x\d+\]",
    "store_byte": r"strb w\d+, \[x\d+, x\d+\]",
    "load_word": r"ldr x\d+, \[x\d+, x\d+, lsl #3\]",
    "store_word": r"str x\d+, \[x\d+, x\d+, lsl #3\]",
    "load_pointer": r"ldr x\d+, \[x\d+, x\d+, lsl #3\]",
    "load_half": r"ldrh w\d+, \[x\d+, x\d+, lsl #1\]",
    "load_int": r"ldr w\d+, \[x\d+, x\d+, lsl #2\]",
    "byte_index_word": r"ldr x\d+, \[x\d+, x\d+\]",
    "store_zero": r"str xzr, \[x\d+, x\d+\]",
    "store_constant": r"str x\d+, \[x\d+, x\d+, lsl #3\]",
    "sum": r"ldr x\d+, \[x\d+, x\d+, lsl #3\]",
    "fill": r"str x\d+, \[x\d+, x\d+, lsl #3\]",
    "fill_bytes": r"strb w\d+, \[x\d+, x\d+\]",
    "find": r"ldrb w\d+, \[x\d+, x\d+\]",
}


def test_single_use_register_index_geps_fold_into_the_access(monkeypatch):
    # The default allocator gives arguments registers. The block-local
    # PCC_SELF_CALLEE_SAVED_REGISTERS=0 allocator leaves them in slots, so
    # these accesses keep the add there; the programs below run both modes.
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    assembly = emit_aarch64_darwin_asm(SOURCE, optimize=True)
    for name, pattern in FOLDED.items():
        body = _body(assembly, name)
        assert re.search(r"\b" + pattern, body), (name, body)
        if name in ("sum", "fill", "fill_bytes", "find"):
            continue  # loop arithmetic legitimately uses add/lsl
        # No address arithmetic remains for the single access.
        assert not re.search(r"\badd x\d+, x\d+, [xw]\d+", body), (name, body)
        assert not re.search(r"\blsl x\d+, x\d+, #", body), (name, body)
    for name in ("shared", "not_adjacent", "narrow_index", "scale_mismatch", "atomic_word", "volatile_byte"):
        body = _body(assembly, name)
        assert not re.search(r"\[x\d+, [xw]\d+", body), (name, body)
        assert re.search(r"\badd x\d+, x\d+, [xw]\d+", body), (name, body)


@pytest.mark.parametrize("registers", ("0", "1"))
def test_typed_and_text_emission_agree(monkeypatch, registers):
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    expected = assemble_file(emit_aarch64_darwin_asm(SOURCE, optimize=True))
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(SOURCE),
                                                    optimize=True, structured_instructions=True)
    assert transport.assemble_sections() == expected


# owned Darwin arm64 execution
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
@pytest.mark.parametrize("registers", ("0", "1"))
def test_register_offset_accesses_run(tmp_path, monkeypatch, registers):
    from pcc.backend.native_object import NativeObject
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(SOURCE),
                                                    optimize=True, structured_instructions=True)
    sections, undefined = transport.assemble_sections()
    data = NativeObject.from_sections(sections, undefined=undefined).to_macho()
    output = tmp_path / "regoff"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
