"""Frame alignment holes are cleared with the widest aligned stores.

The prologue clears reserved alignment bytes so raw stack capture cannot
retain an earlier activation's pointer through a frame hole. It cleared
every hole that was not 8-byte aligned one byte at a time: a 4-byte hole
beside an i32 slot took four ``sturb`` on every call, about 5k executed
stores more than LLVM's code generation in a stepped handler window
(docs/status.md, item 3 P0 notes). Each store is now the widest naturally
aligned one (8, 4, 2, then 1 byte) that stays inside the hole; the same
bytes are cleared. The program runs and reads its slots back.
"""
import re
import subprocess

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


SOURCE = '''target triple = "arm64-apple-darwin"
define void @consume(ptr %a, ptr %b, ptr %c) {
entry:
  %va = load i32, ptr %a
  %vb = load i32, ptr %b
  %vc = load i16, ptr %c
  %wc = sext i16 %vc to i32
  %s = add i32 %va, %vb
  %t = add i32 %s, %wc
  store i32 %t, ptr %a
  ret void
}
define i32 @flags(i32 %x) {
entry:
  %a = alloca i32, align 4
  %b = alloca i32, align 4
  %c = alloca i16, align 2
  store i32 %x, ptr %a
  store i32 7, ptr %b
  store i16 3, ptr %c
  call void @consume(ptr %a, ptr %b, ptr %c)
  %v = load i32, ptr %a
  ret i32 %v
}
define i32 @main() {
entry:
  %r = call i32 @flags(i32 30)
  %ok = icmp eq i32 %r, 40
  %status = select i1 %ok, i32 0, i32 43
  ret i32 %status
}
'''


def _prologue(assembly: str, name: str) -> list[str]:
    body = re.search(r"^_" + name + r":\n(.*?)(?=^L_pcc_smap_)", assembly, re.S | re.M).group(1)
    return [line.strip() for line in body.splitlines() if line.strip()]


def test_alignment_holes_use_the_widest_aligned_zero_stores():
    lines = _prologue(emit_aarch64_darwin_asm(SOURCE, optimize=True), "flags")
    zeroes = [line for line in lines if re.match(r"stu?r[bh]? [wx]zr, \[x29", line)]
    assert zeroes, lines
    assert any(line.startswith("stur wzr, [x29, #-") for line in zeroes), zeroes
    for line in zeroes:
        width = {"sturb": 1, "sturh": 2}.get(line.split()[0], 8 if "xzr" in line else 4)
        offset = int(line.rsplit("#-", 1)[1].rstrip("]"))
        assert offset % width == 0, line  # naturally aligned below x29
    # No hole is cleared byte by byte where a wider aligned store fits.
    for first, second in zip(zeroes, zeroes[1:]):
        assert not (first.startswith("sturb") and second.startswith("sturb")), zeroes


# owned Darwin arm64 execution
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
def test_cleared_frame_runs(tmp_path):
    from pcc.backend.native_object import NativeObject
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(SOURCE),
                                                    optimize=True, structured_instructions=True)
    sections, undefined = transport.assemble_sections()
    data = NativeObject.from_sections(sections, undefined=undefined).to_macho()
    output = tmp_path / "padding"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
