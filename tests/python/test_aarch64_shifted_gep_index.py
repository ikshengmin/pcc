"""A constant left shift feeding a GEP index folds into the address.

``%s = shl i64 %i, k`` whose only use is the directly following
``getelementptr T, ptr %b, i64 %s`` emitted ``lsl`` and then the GEP's
``add`` (and, for a GEP feeding a load or store, the access through the
sum). LLVM selects the shift into the address: ``ldr x0, [x1, x2, lsl #3]``
when the GEP feeds an access of 2^k bytes, and ``add x0, x1, x2, lsl #n``
(n = k + log2 of the element size) otherwise. The stepped handler window
executed about 5k more ``lsl`` than LLVM's code generation, mostly these
(docs/status.md, item 3 P0 notes). A global base is materialized into x9 for
the folded access. A second use of the shift, a shift amount that differs
from the access size, and an instruction between the two keep the ``lsl``.
The program executes every form natively under both allocators.
"""
import re
import subprocess

import pytest

from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.macho_exec import link_executable
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


SOURCE = '''target triple = "arm64-apple-darwin"
@table = global [8 x i64] [i64 10, i64 11, i64 12, i64 13, i64 14, i64 15, i64 16, i64 17], align 8
define i64 @load_scaled(ptr %b, i64 %i) {
entry:
  %s = shl i64 %i, 3
  %p = getelementptr i8, ptr %b, i64 %s
  %v = load i64, ptr %p, align 8
  ret i64 %v
}
define void @store_scaled(ptr %b, i64 %i, i32 %x) {
entry:
  %s = shl i64 %i, 2
  %p = getelementptr inbounds i8, ptr %b, i64 %s
  store i32 %x, ptr %p, align 4
  ret void
}
define ptr @address(ptr %b, i64 %i) {
entry:
  %s = shl i64 %i, 3
  %p = getelementptr i8, ptr %b, i64 %s
  ret ptr %p
}
define ptr @address_of_words(ptr %b, i64 %i) {
entry:
  %s = shl i64 %i, 2
  %p = getelementptr i64, ptr %b, i64 %s
  ret ptr %p
}
define i64 @global_table(i64 %i) {
entry:
  %m = and i64 %i, 7
  %s = shl i64 %m, 3
  %p = getelementptr i8, ptr @table, i64 %s
  %v = load i64, ptr %p, align 8
  ret i64 %v
}
define i64 @size_mismatch(ptr %b, i64 %i) {
entry:
  %s = shl i64 %i, 2
  %p = getelementptr i8, ptr %b, i64 %s
  %v = load i64, ptr %p, align 8
  ret i64 %v
}
define i64 @shared(ptr %b, i64 %i) {
entry:
  %s = shl i64 %i, 3
  %p = getelementptr i8, ptr %b, i64 %s
  %v = load i64, ptr %p, align 8
  %r = add i64 %v, %s
  ret i64 %r
}
define i32 @main() {
entry:
  %words = alloca [8 x i64], align 8
  %w0 = getelementptr [8 x i64], ptr %words, i64 0, i64 0
  store i64 100, ptr %w0, align 8
  %w1 = getelementptr [8 x i64], ptr %words, i64 0, i64 1
  store i64 101, ptr %w1, align 8
  %w2 = getelementptr [8 x i64], ptr %words, i64 0, i64 2
  store i64 102, ptr %w2, align 8
  %w3 = getelementptr [8 x i64], ptr %words, i64 0, i64 3
  store i64 103, ptr %w3, align 8
  %w4 = getelementptr [8 x i64], ptr %words, i64 0, i64 4
  store i64 104, ptr %w4, align 8
  %v1 = call i64 @load_scaled(ptr %words, i64 3)
  call void @store_scaled(ptr %words, i64 1, i32 7)
  %v2 = load i64, ptr %w0, align 8
  %a1 = call ptr @address(ptr %words, i64 2)
  %ok_a1 = icmp eq ptr %a1, %w2
  %a2 = call ptr @address_of_words(ptr %words, i64 1)
  %ok_a2 = icmp eq ptr %a2, %w4
  %v3 = call i64 @global_table(i64 13)
  %v4 = call i64 @size_mismatch(ptr %words, i64 4)
  %v5 = call i64 @shared(ptr %words, i64 1)
  %c1 = icmp eq i64 %v1, 103
  %c2 = icmp eq i64 %v2, 30064771172
  %c3 = icmp eq i64 %v3, 15
  %c4 = icmp eq i64 %v4, 102
  %c5 = icmp eq i64 %v5, 109
  %b1 = and i1 %c1, %c2
  %b2 = and i1 %b1, %ok_a1
  %b3 = and i1 %b2, %ok_a2
  %b4 = and i1 %b3, %c3
  %b5 = and i1 %b4, %c4
  %b6 = and i1 %b5, %c5
  %status = select i1 %b6, i32 0, i32 79
  ret i32 %status
}
'''


def _body(assembly: str, name: str) -> str:
    return re.search(r"^_" + name + r":\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)",
                     assembly, re.S | re.M).group(1)


def test_shifts_feeding_gep_indices_fold_into_the_address(monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    assembly = emit_aarch64_darwin_asm(SOURCE, optimize=True)
    folded = {
        "load_scaled": r"ldr x\d+, \[x\d+, x\d+, lsl #3\]",
        "store_scaled": r"str w\d+, \[x\d+, x\d+, lsl #2\]",
        "address": r"add x\d+, x\d+, x\d+, lsl #3",
        "address_of_words": r"add x\d+, x\d+, x\d+, lsl #5",
        "global_table": r"ldr x\d+, \[x9, x\d+, lsl #3\]",
    }
    for name, pattern in folded.items():
        body = _body(assembly, name)
        assert re.search(r"\b" + pattern, body), (name, body)
        assert not re.search(r"\blsl x\d+, x\d+, #", body), (name, body)
    for name in ("size_mismatch", "shared"):
        body = _body(assembly, name)
        assert re.search(r"\blsl x\d+, x\d+, #", body), (name, body)


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
def test_shifted_gep_programs_run(tmp_path, monkeypatch, registers):
    from pcc.backend.native_object import NativeObject
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(SOURCE),
                                                    optimize=True, structured_instructions=True)
    sections, undefined = transport.assemble_sections()
    data = NativeObject.from_sections(sections, undefined=undefined).to_macho()
    output = tmp_path / "shifted_gep"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
