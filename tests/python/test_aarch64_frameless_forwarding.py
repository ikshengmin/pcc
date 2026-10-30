"""Only unchanged scalar sibling-call inputs can keep a forwarding leaf frame-free."""
import platform
import re
import subprocess
import sys

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


SOURCE = '''target triple = "arm64-apple-darwin"
@pending = global i32 0, align 4
@left = global i64 7, align 8
@right = global i64 13, align 8
define i64 @callee(ptr %owner, ptr %slot) {
entry:
  %a = load i64, ptr %owner
  %b = load i64, ptr %slot
  %sum = add i64 %a, %b
  %result = add i64 %sum, 1
  ret i64 %result
}
define i64 @wrapper(ptr %owner, ptr %slot) {
entry:
  %pending = load atomic i32, ptr @pending acquire, align 4
  %ready = icmp eq i32 %pending, 0
  br i1 %ready, label %fast, label %slow
fast:
  %value = load i64, ptr %slot
  ret i64 %value
slow:
  %result = call i64 @callee(ptr %owner, ptr %slot)
  ret i64 %result
}
define i32 @main() {
entry:
  %fast = call i64 @wrapper(ptr @left, ptr @right)
  store atomic i32 1, ptr @pending release, align 4
  %slow = call i64 @wrapper(ptr @left, ptr @right)
  %a = icmp eq i64 %fast, 13
  %b = icmp eq i64 %slow, 21
  %ok = and i1 %a, %b
  %status = select i1 %ok, i32 0, i32 79
  ret i32 %status
}
'''


def body(assembly):
    return re.search(r"_wrapper:\n(.*?)(?=\n\.p2align 2|\n\.section|\Z)", assembly, re.S).group(1)


def test_unchanged_tail_forwarding_has_no_saved_return_or_frame(monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    emitted = body(emit_aarch64_darwin_asm(SOURCE, optimize=True))
    assert "bti c" in emitted and "  b _callee" in emitted
    assert "paciasp" not in emitted and "autiasp" not in emitted
    assert "x29" not in emitted and "sp" not in emitted
    assert "ldar " in emitted


@pytest.mark.parametrize("arguments", ("ptr %slot, ptr %owner", "ptr @left, ptr %slot"))
def test_permuted_or_replaced_inputs_retain_protected_tail_frame(arguments, monkeypatch):
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", "1")
    source = SOURCE.replace("@callee(ptr %owner, ptr %slot)\n", "@callee("+arguments+")\n")
    emitted = body(emit_aarch64_darwin_asm(source, optimize=True))
    assert "paciasp" in emitted and "autiasp" in emitted
    assert "  b _callee" in emitted


@pytest.mark.skipif(sys.platform != "darwin" or platform.machine() != "arm64",
                    reason="owned Darwin arm64 execution")
@pytest.mark.parametrize("registers", ("0", "1"))
@pytest.mark.parametrize("optimize", (False, True))
def test_forwarding_fast_and_slow_paths_execute(tmp_path, monkeypatch, registers, optimize):
    from pcc.backend.native_object import NativeObject
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_indexed_transport
    from pcc.backend.self_backend_parse import parse_self_backend_module
    monkeypatch.setenv("PCC_SELF_CALLEE_SAVED_REGISTERS", registers)
    transport = emit_aarch64_darwin_indexed_transport(parse_self_backend_module(SOURCE),
                                                    optimize=optimize, structured_instructions=True)
    sections, undefined = transport.assemble_sections()
    data = NativeObject.from_sections(sections, undefined=undefined).to_macho()
    output = tmp_path / "forward"
    output.write_bytes(link_executable([data], entry="_main"));output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
