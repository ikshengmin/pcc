"""A 32-bit load followed by its sign extension is one ``ldrsw``.

pcc-Python's ``load_i32`` returns a 64-bit integer, so the runtime's IR is
full of ``load i32`` + ``sext i32 to i64`` pairs that the self backend emitted
as ``ldr wN`` + ``sxtw xN, wN``: 9.7% of the self backend's excess executed
instructions over clang on the handler (docs/status.md, P0 exact ranking).
The typed peephole ``fold_sign_extending_load`` makes them one ``ldrsw`` (or
``ldursw`` for an unscaled offset). The encodings are checked against values
assembled by clang 20 once; the programs run with negative values.
"""
import re
import subprocess

import pytest

from pcc.backend.arm64_encode import assemble_text, encode_emitted_load_store_parts
from pcc.backend.macho_exec import link_executable
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


# (mnemonic, register, base, offset) -> word, as clang 20's assembler encodes them.
ENCODINGS = {
    ("ldrsw", "x1", "x9", 12): 0xB9800D21,
    ("ldrsw", "x2", "x3", 0): 0xB9800062,
    ("ldursw", "x1", "x29", -4): 0xB89FC3A1,
    ("ldrsw", "x20", "x21", 16380): 0xB9BFFEB4,
}


def test_sign_extending_loads_encode_like_the_reference_assembler():
    for (mnemonic, register, base, offset), word in ENCODINGS.items():
        assert encode_emitted_load_store_parts(mnemonic, register, base, offset) == word
        text = "  %s %s, [%s%s]" % (mnemonic, register, base, ", #%d" % offset if offset else "")
        assert int.from_bytes(assemble_text(text).code[:4], "little") == word, text
    with pytest.raises(Exception):
        encode_emitted_load_store_parts("ldrsw", "w1", "x9", 0)


SOURCE = '''target triple = "arm64-apple-darwin"
@cell = global i32 -7, align 4
@pair = global [2 x i64] [i64 0, i64 -42], align 8
define i64 @load_extend(ptr %p) {
entry:
  %v = load i32, ptr %p, align 4
  %s = sext i32 %v to i64
  ret i64 %s
}
define i64 @load_extend_offset(ptr %p) {
entry:
  %f = getelementptr i8, ptr %p, i64 12
  %v = load i32, ptr %f, align 4
  %s = sext i32 %v to i64
  ret i64 %s
}
define i1 @load_extend_negative(ptr %p) {
entry:
  %v = load i32, ptr %p, align 4
  %s = sext i32 %v to i64
  %n = icmp slt i64 %s, 0
  ret i1 %n
}
define i32 @main() {
entry:
  %a = call i64 @load_extend(ptr @cell)
  %ok_a = icmp eq i64 %a, -7
  %b = call i64 @load_extend_offset(ptr @pair)
  %ok_b = icmp eq i64 %b, -1
  %c = call i1 @load_extend_negative(ptr @cell)
  %ab = and i1 %ok_a, %ok_b
  %all = and i1 %ab, %c
  %status = select i1 %all, i32 0, i32 37
  ret i32 %status
}
'''


def body(assembly, name):
    return re.search(r"^_" + name + r":\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)", assembly, re.S | re.M).group(1)


def test_load_then_sext_emits_one_sign_extending_load(monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    assembly = emit_aarch64_darwin_asm(SOURCE, optimize=True)
    for name in ("load_extend", "load_extend_offset", "load_extend_negative"):
        emitted = body(assembly, name)
        assert "ldrsw" in emitted or "ldursw" in emitted, (name, emitted)
        assert "sxtw" not in emitted, (name, emitted)


# owned Darwin arm64 execution
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
@pytest.mark.parametrize("registers", ("0", "1"))
def test_sign_extending_loads_execute(tmp_path, monkeypatch, registers):
    from pcc.backend.native_object import NativeObject
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(SOURCE),
                                                    optimize=True, structured_instructions=True)
    sections, undefined = transport.assemble_sections()
    data = NativeObject.from_sections(sections, undefined=undefined).to_macho()
    output = tmp_path / "sign_extend"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
