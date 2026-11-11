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
import re
import struct
import subprocess
import sys

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.macho_spec import parse_object
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
    slot_offsets = (8, 16, 24, 32, 40)
    paired_stores = [
        "stp xzr, xzr, [x29, #-16]",  # slots -16 and -8
        "stp xzr, xzr, [x29, #-32]",  # slots -32 and -24
        "stur xzr, [x29, #-40]",
    ]
    for optimize in (True, False):
        body = _body(emit_aarch64_darwin_asm(_PROGRAM, optimize=optimize), "clear_slots")
        initialization, call, _ = body.partition("  bl _touch\n")
        assert call  # All five actual slot addresses must be passed after clearing.
        for argument, offset in enumerate(slot_offsets):
            assert f"  sub x{argument}, x29, #{offset}\n" in initialization
        zero_stores = [line.strip() for line in initialization.splitlines()
                       if re.fullmatch(r"\s+(?:stp xzr, xzr|stur xzr), \[x29, #-\d+\]", line)]
        # The 48-byte aligned frame has five 8-byte slots. Its remaining
        # alignment word is cleared in the prologue, independently of the
        # body-store pairing pass, so conservative capture sees no stale root.
        assert zero_stores[:1] == ["stur xzr, [x29, #-48]"]
        expected = paired_stores if optimize else [
            f"stur xzr, [x29, #-{offset}]" for offset in slot_offsets
        ]
        assert zero_stores[1:] == expected

    # Cross-emitted production object bytes must retain the same stores;
    # assembly text alone does not exercise the structured instruction route.
    data = emit_owned_object(_PROGRAM, "arm64-apple-darwin")
    obj = parse_object(data)
    sections = obj.sections()
    text = next(section for section in sections if section["sectname_str"] == "__text")
    symbols = {symbol["name"]: symbol["n_value"] - text["addr"]
               for symbol in obj.symbols() if symbol["n_sect"] == sections.index(text) + 1}
    start = symbols["_clear_slots"]
    end = min(offset for offset in symbols.values() if offset > start)
    words = [word for word, in struct.iter_unpack(
        "<I", data[text["offset"] + start:text["offset"] + end],
    )]
    first_call = next(index for index, word in enumerate(words) if word & 0xFC000000 == 0x94000000)
    stores = []
    for word in words[:first_call]:
        # ARM STP (64-bit, signed offset) and STUR (64-bit): xzr source(s),
        # x29 base, with the signed displacement decoded from the object.
        if word & 0xFFC07FFF == 0xA9007FBF:
            immediate = (word >> 15) & 0x7F
            stores.append(("stp", (immediate - 128 if immediate >= 64 else immediate) * 8))
        elif word & 0xFFE00FFF == 0xF80003BF:
            immediate = (word >> 12) & 0x1FF
            stores.append(("stur", immediate - 512 if immediate >= 256 else immediate))
    assert stores == [("stur", -48), ("stp", -16), ("stp", -32), ("stur", -40)]


@_NATIVE
def test_cleared_slots_are_zero_in_a_dirty_frame(tmp_path):
    data = emit_owned_object(_PROGRAM, "arm64-apple-darwin")
    executable = tmp_path / "slot-pairs"
    executable.write_bytes(link_executable([data], entry="_main"))
    executable.chmod(0o755)
    ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
