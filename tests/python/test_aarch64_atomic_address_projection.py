"""Address identities reuse registers without changing atomic observations."""
import platform
import re
import subprocess
import sys

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


def test_zero_byte_address_has_no_scratch_copy_chain(monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    source = '''target triple = "arm64-apple-darwin"
define i64 @read_cell(ptr %cell) {
entry:
  %same = getelementptr i8, ptr %cell, i64 0
  %value = load atomic i64, ptr %same acquire, align 8
  ret i64 %value
}
'''
    assembly = emit_aarch64_darwin_asm(source, optimize=False)
    body = assembly.split("_read_cell:", 1)[1].split(".section", 1)[0]
    assert re.search(r"ldar x[0-9]+, \[x0\]", body), body
    assert not re.search(r"\bmov x(?:9|11),", body), body


def _program(width, thread_local=False):
    stride = width // 8
    storage = f"@cells = global [2 x i{width}] [i{width} 11, i{width} 17], align 8"
    neighbor = f"call i{width} @offset(ptr %base, i64 {stride})"
    if thread_local:
        # Darwin's owned TLS surface supports scalar cells. Resolve a second
        # cell while the first address is live, exercising that hidden call.
        storage = (f"@cells = thread_local global i{width} 11, align 8\n"
                   f"@neighbor = thread_local global i{width} 17, align 8")
        neighbor = f"load atomic i{width}, ptr @neighbor acquire, align {stride}"
    return f'''target triple = "arm64-apple-darwin"
{storage}
define void @mutate() {{
entry:
  store atomic i{width} 31, ptr @cells release, align {stride}
  ret void
}}
define i{width} @observe(ptr %cell) {{
entry:
  %same = getelementptr i8, ptr %cell, i64 0
  %first = load atomic i{width}, ptr %same acquire, align {stride}
  call void @mutate()
  %old = atomicrmw add ptr %same, i{width} 5 acq_rel
  %pair = cmpxchg ptr %same, i{width} 36, i{width} 41 acq_rel acquire
  %seen = extractvalue {{ i{width}, i1 }} %pair, 0
  %latest = load atomic i{width}, ptr %cell seq_cst, align {stride}
  %a = add i{width} %first, %old
  %b = add i{width} %a, %seen
  %out = add i{width} %b, %latest
  ret i{width} %out
}}
define i{width} @donated_address(ptr %cell) {{
entry:
  %same = getelementptr i8, ptr %cell, i64 0
  %pair = cmpxchg ptr %same, i{width} 41, i{width} 99 acq_rel acquire
  %old = extractvalue {{ i{width}, i1 }} %pair, 0
  ret i{width} %old
}}
define i{width} @offset(ptr %cell, i64 %distance) {{
entry:
  %next = getelementptr i8, ptr %cell, i64 %distance
  %same = getelementptr i8, ptr %next, i64 0
  %v = load atomic i{width}, ptr %same acquire, align {stride}
  ret i{width} %v
}}
define i32 @main() {{
entry:
  %base = getelementptr i8, ptr @cells, i64 0
  %v = call i{width} @observe(ptr %base)
  %old = call i{width} @donated_address(ptr %base)
  %neighbor = {neighbor}
  %current = load atomic i{width}, ptr %base acquire, align {stride}
  %a = icmp eq i{width} %v, 119
  %b = icmp eq i{width} %old, 41
  %c = icmp eq i{width} %neighbor, 17
  %d = icmp eq i{width} %current, 99
  %e = and i1 %a, %b
  %f = and i1 %c, %d
  %ok = and i1 %e, %f
  %status = select i1 %ok, i32 0, i32 77
  ret i32 %status
}}
'''


@pytest.mark.skipif(sys.platform != "darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 execution")
@pytest.mark.parametrize("width", (8, 16, 32, 64))
@pytest.mark.parametrize("callee_saved", ("0", "1"))
@pytest.mark.parametrize("thread_local", (False, True))
def test_address_alias_calls_offsets_and_cas_donation_execute(tmp_path, monkeypatch, width, callee_saved, thread_local):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", callee_saved)
    output = tmp_path / "address"
    data = emit_owned_object(_program(width, thread_local), "arm64-apple-darwin")
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
