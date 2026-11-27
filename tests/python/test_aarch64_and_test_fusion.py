"""A masked test before a branch is one ``tst``, as LLVM's selection does.

``%a = and iN %x, C`` whose only use is the block's fused
``icmp eq/ne iN %a, 0`` (or ``%a, C`` for a single-bit C) emitted
``and`` + ``cbz``/``cbnz`` or ``and`` + ``cmp`` + ``b.cond``. When the
``and`` directly precedes the compare and C is a logical immediate, it is
selected as ``tst x, #C`` with the branch on Z: the ``and`` is not emitted
and needs no register. A stepped handler window executed about 14.6k such
pairs (docs/status.md, item 3 P0 notes). Other uses of the ``and``, a
non-encodable mask, an instruction between the two, or another constant
keep the old form. Encodings are clang 20's; the program takes each branch.
"""
import re
import subprocess

import pytest

from pcc.backend.arm64_encode import assemble_text
from pcc.backend.macho_exec import link_executable
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


ENCODINGS = {
    "  tst x8, #0xffffff": 0xF2405D1F,
    "  tst w1, #0x1": 0x7200003F,
    "  tst x19, #0x20000000000000": 0xF24B027F,
    "  tst x0, #0x7": 0xF240081F,
    "  tst w2, #0xff00": 0x72181C5F,
}


def test_tst_immediates_encode_like_the_reference_assembler():
    for text, word in ENCODINGS.items():
        assert int.from_bytes(assemble_text(text).code[:4], "little") == word, text
        decimal = re.sub(r"#(0x[0-9a-f]+)", lambda m: "#" + str(int(m.group(1), 16)), text)
        assert int.from_bytes(assemble_text(decimal).code[:4], "little") == word, decimal
    with pytest.raises(Exception):
        assemble_text("  tst x0, #0x12345")


def _branchy(name, ty, mask, compare, predicate="eq", between=""):
    return f'''define i64 @{name}({ty} %x) {{
entry:
  %a = and {ty} %x, {mask}
{between}  %c = icmp {predicate} {ty} %a, {compare}
  br i1 %c, label %yes, label %no
yes:
  ret i64 11
no:
  ret i64 22
}}
'''


SOURCE = ('target triple = "arm64-apple-darwin"\n'
          + _branchy("zero64", "i64", "4096", "0")
          + _branchy("nonzero64", "i64", "16777215", "0", "ne")
          + _branchy("bit_set", "i64", "1", "1")
          + _branchy("bit_clear", "i64", "1", "1", "ne")
          + _branchy("zero32", "i32", "65280", "0")
          + _branchy("odd_mask", "i64", "74565", "0")
          + _branchy("other_constant", "i64", "6", "2")
          + _branchy("not_adjacent", "i64", "8", "0", between="  %d = add i64 %x, 1\n")
          + '''define i64 @shared(i64 %x) {
entry:
  %a = and i64 %x, 8
  %c = icmp eq i64 %a, 0
  br i1 %c, label %yes, label %no
yes:
  ret i64 %a
no:
  ret i64 22
}
define i32 @main() {
entry:
  %r1 = call i64 @zero64(i64 8192)
  %r2 = call i64 @zero64(i64 4096)
  %r3 = call i64 @nonzero64(i64 16777216)
  %r4 = call i64 @nonzero64(i64 16777217)
  %r5 = call i64 @bit_set(i64 3)
  %r6 = call i64 @bit_set(i64 2)
  %r7 = call i64 @bit_clear(i64 3)
  %r8 = call i32 @zero32_wrap(i32 255)
  %r9 = call i32 @zero32_wrap(i32 256)
  %s1 = add i64 %r1, %r2
  %s2 = add i64 %s1, %r3
  %s3 = add i64 %s2, %r4
  %s4 = add i64 %s3, %r5
  %s5 = add i64 %s4, %r6
  %s6 = add i64 %s5, %r7
  %ok_a = icmp eq i64 %s6, 121
  %ok_b = icmp eq i32 %r8, 11
  %ok_c = icmp eq i32 %r9, 22
  %ab = and i1 %ok_a, %ok_b
  %all = and i1 %ab, %ok_c
  %status = select i1 %all, i32 0, i32 59
  ret i32 %status
}
define i32 @zero32_wrap(i32 %x) {
entry:
  %r = call i64 @zero32(i32 %x)
  %t = trunc i64 %r to i32
  ret i32 %t
}
''')


def _body(assembly: str, name: str) -> str:
    return re.search(r"^_" + name + r":\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)",
                     assembly, re.S | re.M).group(1)


def test_single_use_masked_tests_become_tst(monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    assembly = emit_aarch64_darwin_asm(SOURCE, optimize=True)
    for name, mask in (("zero64", 4096), ("nonzero64", 0xFFFFFF), ("bit_set", 1),
                       ("bit_clear", 1), ("zero32", 0xFF00)):
        body = _body(assembly, name)
        assert re.search(r"\btst [wx]\d+, #" + str(mask) + r"\b", body), (name, body)
        assert not re.search(r"\band [wx]\d+,", body), (name, body)
        assert not re.search(r"\b(cbz|cbnz|cmp)\b", body), (name, body)
    for name in ("odd_mask", "other_constant", "not_adjacent", "shared"):
        body = _body(assembly, name)
        assert "tst " not in body and re.search(r"\band [wx]\d+,", body), (name, body)


# owned Darwin arm64 execution
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
@pytest.mark.parametrize("registers", ("0", "1"))
def test_fused_tests_take_each_branch(tmp_path, monkeypatch, registers):
    from pcc.backend.native_object import NativeObject
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(SOURCE),
                                                    optimize=True, structured_instructions=True)
    sections, undefined = transport.assemble_sections()
    data = NativeObject.from_sections(sections, undefined=undefined).to_macho()
    output = tmp_path / "tst"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
