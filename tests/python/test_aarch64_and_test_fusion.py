"""A masked test before a branch is one ``tst``, as LLVM's selection does.

``%a = and iN %x, C`` whose only use is the block's fused
``icmp eq/ne iN %a, 0`` (or ``%a, C`` for a single-bit C) emitted
``and`` + ``cbz``/``cbnz`` or ``and`` + ``cmp`` + ``b.cond``. When the
``and`` directly precedes the compare and C is a logical immediate, it is
selected as ``tst x, #C`` with the branch on Z: the ``and`` is not emitted
and needs no register. A stepped handler window executed about 14.6k such
pairs (docs/status.md, item 3 P0 notes). Other uses of the ``and``, a
non-encodable mask, an instruction between the two, or another constant
keep the old form. A single-bit test is one ``tbz``/``tbnz`` on the tested
register (14-bit reach; a farther target is relaxed to the opposite test
over ``b``). Encodings are clang 20's; the program takes each branch.
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


TEST_BRANCHES = (
    # clang 20: offsets from the start of this text; back is 0, fwd is 0x18.
    "back:\n  nop\n  tbz x1, #0, fwd\n  tbnz x19, #53, fwd\n  tbz w2, #31, back\n"
    "  tbnz x0, #63, back\n  nop\nfwd:\n  ret\n",
    [0x360000A1, 0xB7A80093, 0x36FFFFA2, 0xB7FFFF80],
)


def test_test_branches_encode_and_relax_like_the_reference_assembler():
    text, words = TEST_BRANCHES
    code = assemble_text(text).code
    assert [int.from_bytes(code[i:i + 4], "little") for i in range(4, 20, 4)] == words
    far = "  tbz x3, #4, Lfar\n" + "  nop\n" * 9000 + "Lfar:\n  ret\n"
    code = assemble_text(far).code
    first, second = (int.from_bytes(code[i:i + 4], "little") for i in (0, 4))
    assert first == 0x37200043  # tbnz w3, #4, over the next word
    assert second == 0x14000000 | 9001  # b Lfar
    near = "  tbz x3, #4, Lnear\n" + "  nop\n" * 100 + "Lnear:\n  ret\n"
    assert int.from_bytes(assemble_text(near).code[:4], "little") == 0x36200CA3


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
    for name, mask in (("nonzero64", 0xFFFFFF), ("zero32", 0xFF00)):
        body = _body(assembly, name)
        assert re.search(r"\btst [wx]\d+, #" + str(mask) + r"\b", body), (name, body)
        assert not re.search(r"\band [wx]\d+,", body), (name, body)
        assert not re.search(r"\b(cbz|cbnz|cmp)\b", body), (name, body)
    # (x & 4096) == 0 jumps to "no" when bit 12 is set; (x & 1) == 1 when
    # bit 0 is clear; (x & 1) != 1 when it is set.
    for name, branch in (("zero64", "tbnz x0, #12, L_zero64_no"), ("bit_set", "tbz x0, #0, L_bit_set_no"),
                         ("bit_clear", "tbnz x0, #0, L_bit_clear_no")):
        body = _body(assembly, name)
        assert branch in body, (name, body)
        assert not re.search(r"\b(and|tst|cbz|cbnz|cmp)\b", body), (name, body)
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


def _far_source(steps: int) -> tuple[str, int]:
    body = []
    previous = "%x"
    expected = 8
    for index in range(steps):
        body.append(f"  %v{index} = {'add' if index % 2 else 'xor'} i64 {previous}, {index + 3}")
        expected = (expected + index + 3) if index % 2 else (expected ^ (index + 3))
        previous = f"%v{index}"
    text = f'''target triple = "arm64-apple-darwin"
define i64 @far(i64 %x) {{
entry:
  %a = and i64 %x, 8
  %c = icmp eq i64 %a, 0
  br i1 %c, label %yes, label %no
no:
{chr(10).join(body)}
  ret i64 {previous}
yes:
  ret i64 7
}}
define i32 @main() {{
entry:
  %y = call i64 @far(i64 0)
  %n = call i64 @far(i64 8)
  %ok_y = icmp eq i64 %y, 7
  %ok_n = icmp eq i64 %n, {expected}
  %ok = and i1 %ok_y, %ok_n
  %status = select i1 %ok, i32 0, i32 73
  ret i32 %status
}}
'''
    return text, expected


# owned Darwin arm64 execution of a relaxed native tbz (target beyond 32 KiB)
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
def test_relaxed_native_test_branch_runs(tmp_path, monkeypatch):
    from pcc.backend.macho_exec import link_executable
    from pcc.backend.native_object import NativeObject
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    text, _expected = _far_source(7000)
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(text),
                                                    optimize=True, structured_instructions=True)
    sections, undefined = transport.assemble_sections()
    data = NativeObject.from_sections(sections, undefined=undefined).to_macho()
    words = [int.from_bytes(data[i:i + 4], "little") for i in range(0, len(data) - 3, 4)]
    # tbz x?, #3, yes crosses the long block: relaxed to tbnz x?, #3 over
    # the next word (imm14 = 2), then b yes.
    relaxed = [i for i, w in enumerate(words) if (w & 0xFFFFFFE0) == 0x37180040]
    assert relaxed and (words[relaxed[0] + 1] & 0xFC000000) == 0x14000000, "no relaxed tbnz x?, #3"
    output = tmp_path / "far"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=20)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
