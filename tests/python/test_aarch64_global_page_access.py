"""A scalar access to a link-unit-local global is ``adrp`` + PAGEOFF access.

A global this module defines, or an ``external dso_local`` declaration
(LLVM's "resolved within this linkage unit"), is addressed directly. A
scalar load or store of it at offset 0 emitted ``adrp x9, g@PAGE``,
``add x9, x9, g@PAGEOFF`` and the access through ``[x9]``; LLVM folds the
page offset into the access: ``ldr w0, [x9, g@PAGEOFF]`` with an
ARM64_RELOC_PAGEOFF12 the linker scales by the access size. The stepped
handler window executed about 17k such triples per 600000 instructions
(docs/status.md, item 3 P0 notes), mostly the safepoint polls on every
pcc-Python loop backedge. An external global without ``dso_local`` keeps
its GOT load, an acquire load keeps ``ldapr`` through the address, and a
nonzero offset keeps the add. Words and relocations are clang 20's; two
objects are linked by pcc's linker and executed.
"""
import re
import subprocess

import pytest

from pcc.backend.arm64_encode import assemble_text
from pcc.backend.macho_exec import link_executable
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


ENCODINGS = {
    "  ldr w0, [x9, _g@PAGEOFF]": 0xB9400120,
    "  ldr x1, [x9, _g@PAGEOFF]": 0xF9400121,
    "  ldrb w2, [x9, _g@PAGEOFF]": 0x39400122,
    "  ldrh w3, [x9, _g@PAGEOFF]": 0x79400123,
    "  str w4, [x9, _g@PAGEOFF]": 0xB9000124,
    "  str x5, [x9, _g@PAGEOFF]": 0xF9000125,
    "  strb w6, [x9, _g@PAGEOFF]": 0x39000126,
    "  strh w7, [x9, _g@PAGEOFF]": 0x79000127,
    "  str wzr, [x9, _g@PAGEOFF]": 0xB900013F,
}


def test_pageoff_accesses_encode_like_the_reference_assembler():
    from pcc.backend import macho_spec as spec
    for text, word in ENCODINGS.items():
        assembled = assemble_text(text + "\n")
        assert int.from_bytes(assembled.code[:4], "little") == word, text
        (relocation,) = assembled.relocations
        assert (relocation.symbol, relocation.type, relocation.pcrel) == (
            "_g", spec.ARM64_RELOC_PAGEOFF12, False), text


def test_pageoff_records_reencode_to_the_same_words():
    from pcc.backend import self_backend_aarch64_darwin_mem as mem
    from pcc.backend.arm64_encode import STRUCTURED_RELOCATION_PAGEOFF12
    from pcc.backend.self_backend_aarch64_semantic_encode import encode_semantic_instruction

    for text, word in ENCODINGS.items():
        mnemonic, register = re.fullmatch(r"  (\w+) (\w+), \[x9, _g@PAGEOFF\]", text).groups()
        assert mem.emitted_pageoff_memory_line(mnemonic, register, "x9", "_g") == text
        mem.begin_direct_instruction_capture(capture_semantics=True)
        try:
            assert mem.emitted_pageoff_memory_line(mnemonic, register, "x9", "_g") == \
                mem.DIRECT_INSTRUCTION_PLACEHOLDER
            records = mem.borrow_direct_instruction_records()
            owner = mem.borrow_direct_instruction_semantics()
            record = records.get4_unchecked(0)
            assert (record.first, record.third) == (word, STRUCTURED_RELOCATION_PAGEOFF12), text
            header = owner.require_header(0)
            tokens = [owner.operand(0, i) for i in range(header.fourth)]
            encoded = encode_semantic_instruction(header.first, tokens)
            assert (encoded.first, encoded.third, encoded.fourth) == (
                word, STRUCTURED_RELOCATION_PAGEOFF12, record.fourth), text
        finally:
            mem.end_direct_instruction_capture()


USER = '''target triple = "arm64-apple-darwin"
@word = global i32 5, align 4
@byte = global i8 9, align 1
@half = global i16 300, align 2
@table = global [4 x i64] [i64 1, i64 2, i64 3, i64 4], align 8
@shared = external dso_local global i64, align 8
@flag = external dso_local global i32, align 4
@remote = external global i32, align 4
define i64 @load_word() {
entry:
  %v = load i32, ptr @word, align 4
  %w = zext i32 %v to i64
  ret i64 %w
}
define i64 @load_byte_gep() {
entry:
  %a = getelementptr i8, ptr @byte, i64 0
  %v = load i8, ptr %a, align 1
  %w = zext i8 %v to i64
  ret i64 %w
}
define i64 @load_half() {
entry:
  %v = load i16, ptr @half, align 2
  %w = zext i16 %v to i64
  ret i64 %w
}
define i64 @load_shared() {
entry:
  %v = load i64, ptr @shared, align 8
  ret i64 %v
}
define void @store_shared(i64 %x) {
entry:
  store i64 %x, ptr @shared, align 8
  ret void
}
define void @store_word_zero() {
entry:
  store i32 0, ptr @word, align 4
  ret void
}
define i64 @poll() {
entry:
  %v = load atomic i32, ptr @flag monotonic, align 4
  %w = zext i32 %v to i64
  ret i64 %w
}
define i64 @acquire() {
entry:
  %v = load atomic i32, ptr @flag acquire, align 4
  %w = zext i32 %v to i64
  ret i64 %w
}
define i64 @load_remote() {
entry:
  %v = load i32, ptr @remote, align 4
  %w = zext i32 %v to i64
  ret i64 %w
}
define i64 @load_table_two() {
entry:
  %a = getelementptr i8, ptr @table, i64 16
  %v = load i64, ptr %a, align 8
  ret i64 %v
}
define i32 @main() {
entry:
  %a = call i64 @load_word()
  %b = call i64 @load_byte_gep()
  %c = call i64 @load_half()
  %d = call i64 @load_shared()
  call void @store_shared(i64 40)
  %e = call i64 @load_shared()
  call void @store_word_zero()
  %f = call i64 @load_word()
  %g = call i64 @poll()
  %h = call i64 @acquire()
  %i = call i64 @load_remote()
  %j = call i64 @load_table_two()
  %c1 = icmp eq i64 %a, 5
  %c2 = icmp eq i64 %b, 9
  %c3 = icmp eq i64 %c, 300
  %c4 = icmp eq i64 %d, 7
  %c5 = icmp eq i64 %e, 40
  %c6 = icmp eq i64 %f, 0
  %c7 = icmp eq i64 %g, 11
  %c8 = icmp eq i64 %h, 11
  %c9 = icmp eq i64 %i, 13
  %c10 = icmp eq i64 %j, 3
  %b1 = and i1 %c1, %c2
  %b2 = and i1 %b1, %c3
  %b3 = and i1 %b2, %c4
  %b4 = and i1 %b3, %c5
  %b5 = and i1 %b4, %c6
  %b6 = and i1 %b5, %c7
  %b7 = and i1 %b6, %c8
  %b8 = and i1 %b7, %c9
  %b9 = and i1 %b8, %c10
  %status = select i1 %b9, i32 0, i32 97
  ret i32 %status
}
'''

PROVIDER = '''target triple = "arm64-apple-darwin"
@shared = global i64 7, align 8
@flag = global i32 11, align 4
@remote = global i32 13, align 4
'''


def _body(assembly: str, name: str) -> str:
    return re.search(r"^_" + name + r":\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)",
                     assembly, re.S | re.M).group(1)


def test_local_global_accesses_fold_the_page_offset(monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    assembly = emit_aarch64_darwin_asm(USER, optimize=True)
    folded = {
        "load_word": r"ldr w\d+, \[x9, _word@PAGEOFF\]",
        "load_byte_gep": r"ldrb w\d+, \[x9, _byte@PAGEOFF\]",
        "load_half": r"ldrh w\d+, \[x9, _half@PAGEOFF\]",
        "load_shared": r"ldr x\d+, \[x9, _shared@PAGEOFF\]",
        "store_shared": r"str x\d+, \[x9, _shared@PAGEOFF\]",
        "store_word_zero": r"str wzr, \[x9, _word@PAGEOFF\]",
        "poll": r"ldr w\d+, \[x9, _flag@PAGEOFF\]",
    }
    for name, pattern in folded.items():
        body = _body(assembly, name)
        assert re.search(r"\badrp x9, _\w+@PAGE$", body, re.M), (name, body)
        assert re.search(r"\b" + pattern, body), (name, body)
        assert not re.search(r"\badd x9, x9, _\w+@PAGEOFF", body), (name, body)
    acquire = _body(assembly, "acquire")
    assert re.search(r"\badd x\d+, x\d+, _flag@PAGEOFF", acquire) and "ldapr w" in acquire, acquire
    remote = _body(assembly, "load_remote")
    assert "_remote@GOTPAGEOFF" in remote and "_remote@PAGEOFF" not in remote, remote
    table = _body(assembly, "load_table_two")
    assert re.search(r"\badd x9, x9, _table@PAGEOFF", table), table


def _object(source, registers, monkeypatch):
    from pcc.backend.native_object import NativeObject
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(source),
                                                    optimize=True, structured_instructions=True)
    sections, undefined = transport.assemble_sections()
    return NativeObject.from_sections(sections, undefined=undefined).to_macho()


@pytest.mark.parametrize("registers", ("0", "1"))
def test_typed_and_text_emission_agree(monkeypatch, registers):
    from pcc.backend.arm64_asm_driver import assemble_file
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    expected = assemble_file(emit_aarch64_darwin_asm(USER, optimize=True))
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(USER),
                                                    optimize=True, structured_instructions=True)
    assert transport.assemble_sections() == expected


# owned Darwin arm64 execution, two objects linked by pcc's linker
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
@pytest.mark.parametrize("registers", ("0", "1"))
def test_page_offset_accesses_run(tmp_path, monkeypatch, registers):
    user = _object(USER, registers, monkeypatch)
    provider = _object(PROVIDER, registers, monkeypatch)
    output = tmp_path / "pages"
    output.write_bytes(link_executable([user, provider], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
