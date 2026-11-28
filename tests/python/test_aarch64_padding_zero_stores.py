"""Frame alignment holes are left as they are.

The prologue cleared reserved alignment bytes (and the odd callee-saved
pad) so that a conservative stack scan of the removed tracing collectors
could not retain an earlier activation's pointer through a frame hole. The
one collector roots compiled frames by their precise stack maps, and the
conservative stack roots and raw stack capture behind
``pcc_thread_conservative_roots_enabled`` are never enabled
(``pcc_thread_enable_conservative_roots`` has no caller), so the clears
were about 2000 stores per handler request that nothing read
(docs/status.md, item 3). The program runs and reads its slots back.
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


def test_alignment_holes_are_not_cleared():
    lines = _prologue(emit_aarch64_darwin_asm(SOURCE, optimize=True), "flags")
    zeroes = [line for line in lines
              if re.match(r"stu?r[bh]? [wx]zr, \[(x29|sp)", line)]
    assert zeroes == [], lines


def test_nothing_enables_the_conservative_stack_roots():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "pcc"
    call = re.compile(r"(?<!def )\bpcc_thread_enable_conservative_roots\(\)")
    callers = [path for path in root.rglob("*.py")
               if call.search(path.read_text(encoding="utf-8"))]
    assert callers == [], callers


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
