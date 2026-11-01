"""Adjacent frame-slot accesses below x29 pair into STP/LDP.

Arm ARM C6.2 (STP/LDP, signed offset): one instruction transfers two 64-bit
registers at a multiple of 8 between -512 and 504. Frame slots below x29 use
STUR/LDUR, which the pairing pass now accepts in ascending order; descending
zero stores (clearing root slots walks down from x29) pair too, since both
slots receive zero. The program dirties the stack, clears five slots in a
fresh frame and lets the callee check them; it is emitted by the production
object path, linked by pcc's owned linker and executed.
"""

import platform
import subprocess
import sys

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


_NATIVE = pytest.mark.pcc_gate(probe=lambda: sys.platform == "darwin" and platform.machine() == "arm64")

_PROGRAM = '''target triple = "arm64-apple-darwin"

define i64 @dirty(i64 %seed) {
entry:
  %junk = alloca [16 x i64], align 8
  br label %loop
loop:
  %i = phi i64 [0, %entry], [%next, %loop]
  %slot = getelementptr [16 x i64], ptr %junk, i64 0, i64 %i
  %value = add i64 %seed, %i
  store volatile i64 %value, ptr %slot, align 8
  %next = add i64 %i, 1
  %done = icmp eq i64 %next, 16
  br i1 %done, label %exit, label %loop
exit:
  %third = getelementptr [16 x i64], ptr %junk, i64 0, i64 3
  %seen = load volatile i64, ptr %third, align 8
  ret i64 %seen
}

define void @touch(ptr %a, ptr %b, ptr %c, ptr %d, ptr %e) {
entry:
  %va = load i64, ptr %a, align 8
  %vb = load i64, ptr %b, align 8
  %vc = load i64, ptr %c, align 8
  %vd = load i64, ptr %d, align 8
  %ve = load i64, ptr %e, align 8
  %o1 = or i64 %va, %vb
  %o2 = or i64 %o1, %vc
  %o3 = or i64 %o2, %vd
  %o4 = or i64 %o3, %ve
  %stale = icmp ne i64 %o4, 0
  br i1 %stale, label %bad, label %good
bad:
  store i64 999, ptr %c, align 8
  ret void
good:
  store i64 40, ptr %c, align 8
  ret void
}

define i64 @clear_slots(i64 %n) {
entry:
  %s0 = alloca ptr, align 8
  %s1 = alloca ptr, align 8
  %s2 = alloca ptr, align 8
  %s3 = alloca ptr, align 8
  %s4 = alloca ptr, align 8
  store ptr null, ptr %s0, align 8
  store ptr null, ptr %s1, align 8
  store ptr null, ptr %s2, align 8
  store ptr null, ptr %s3, align 8
  store ptr null, ptr %s4, align 8
  call void @touch(ptr %s0, ptr %s1, ptr %s2, ptr %s3, ptr %s4)
  %v = load ptr, ptr %s2, align 8
  %i = ptrtoint ptr %v to i64
  %r = add i64 %i, %n
  ret i64 %r
}

define i32 @main() {
entry:
  %seen = call i64 @dirty(i64 77)
  %r = call i64 @clear_slots(i64 2)
  %ok_seen = icmp eq i64 %seen, 80
  %ok_r = icmp eq i64 %r, 42
  %ok = and i1 %ok_seen, %ok_r
  br i1 %ok, label %good, label %bad
good:
  ret i32 0
bad:
  ret i32 3
}
'''


def _body(text: str, name: str) -> str:
    out = []
    for line in text.split("\n_" + name + ":\n", 1)[1].splitlines():
        if line.startswith(".section") or (line.endswith(":") and not line.startswith((" ", ".", "L_"))):
            break
        out.append(line)
    return "\n".join(out)


def test_slot_clearing_pairs_descending_zero_stores():
    body = _body(emit_aarch64_darwin_asm(_PROGRAM, optimize=True), "clear_slots")
    assert body.count("stp xzr, xzr, [x29, #-") == 2
    assert body.count("stur xzr, [x29, #-") == 1
    unpaired = _body(emit_aarch64_darwin_asm(_PROGRAM, optimize=False), "clear_slots")
    assert unpaired.count("stur xzr, [x29, #-") == 5


@_NATIVE
def test_cleared_slots_are_zero_in_a_dirty_frame(tmp_path):
    data = emit_owned_object(_PROGRAM, "arm64-apple-darwin")
    executable = tmp_path / "slot-pairs"
    executable.write_bytes(link_executable([data], entry="_main"))
    executable.chmod(0o755)
    ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
