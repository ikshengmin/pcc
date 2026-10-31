"""AArch64 frame slots beyond the unscaled x29 reach use one scaled SP access.

Between prologue and epilogue the self backend's frame is
``sp = x29 - total_frame``. A slot at ``[x29, #-off]`` with ``off > 255`` was an
address computation into x15 followed by the access (and, for a zero store, a
third instruction for the zero); it is now ``[sp, #total_frame - off]``, the
ARMv8 load/store unsigned-offset form (ARM ARM C6.2, LDR/STR immediate,
unsigned offset), with a zero stored from the zero register. The program is
emitted by the production object path, linked by pcc's owned linker and run.
"""

import platform
import re
import subprocess
import sys

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm


_WIDE_SLOTS = 80
_SEED = 5


def _program() -> str:
    lines = [
        'target triple = "arm64-apple-darwin"',
        "define void @escape(ptr %p) {",
        "entry:",
        "  ret void",
        "}",
        "define i64 @frame_sum(i64 %seed) {",
        "entry:",
    ]
    for index in range(_WIDE_SLOTS):
        lines.append(f"  %s{index} = alloca i64, align 8")
    lines.extend([
        "  %b = alloca i8, align 1",
        "  %h = alloca i16, align 2",
        "  %w = alloca i32, align 4",
        "  %p = alloca ptr, align 8",
        "  store ptr null, ptr %p, align 8",
        "  store i8 7, ptr %b, align 1",
        "  store i16 300, ptr %h, align 2",
        "  store i32 70000, ptr %w, align 4",
    ])
    for index in range(_WIDE_SLOTS):
        lines.append(f"  %v{index} = add i64 %seed, {index}")
        lines.append(f"  store i64 %v{index}, ptr %s{index}, align 8")
        lines.append(f"  call void @escape(ptr %s{index})")
    for name in ("b", "h", "w", "p"):
        lines.append(f"  call void @escape(ptr %{name})")
    lines.append("  %acc0 = add i64 0, 0")
    for index in range(_WIDE_SLOTS):
        lines.append(f"  %l{index} = load i64, ptr %s{index}, align 8")
        lines.append(f"  %acc{index + 1} = add i64 %acc{index}, %l{index}")
    last = f"%acc{_WIDE_SLOTS}"
    lines.extend([
        "  %lb = load i8, ptr %b, align 1",
        "  %lh = load i16, ptr %h, align 2",
        "  %lw = load i32, ptr %w, align 4",
        "  %lp = load ptr, ptr %p, align 8",
        "  %eb = zext i8 %lb to i64",
        "  %eh = zext i16 %lh to i64",
        "  %ew = zext i32 %lw to i64",
        "  %ep = ptrtoint ptr %lp to i64",
        f"  %t1 = add i64 {last}, %eb",
        "  %t2 = add i64 %t1, %eh",
        "  %t3 = add i64 %t2, %ew",
        "  %t4 = add i64 %t3, %ep",
        "  ret i64 %t4",
        "}",
        "define i32 @main() {",
        "entry:",
        f"  %sum = call i64 @frame_sum(i64 {_SEED})",
        f"  %ok = icmp eq i64 %sum, {_expected()}",
        "  br i1 %ok, label %good, label %bad",
        "good:",
        "  ret i32 0",
        "bad:",
        "  ret i32 3",
        "}",
    ])
    return "\n".join(lines) + "\n"


def _expected() -> int:
    return _WIDE_SLOTS * _SEED + sum(range(_WIDE_SLOTS)) + 7 + 300 + 70000


def _body(text: str, name: str) -> str:
    return text.split("_" + name + ":", 1)[1].split(".section", 1)[0].split("\n_main:", 1)[0]


@pytest.mark.parametrize("optimize", [False, True])
def test_far_slots_are_one_scaled_sp_access(optimize):
    body = _body(emit_aarch64_darwin_asm(_program(), optimize=optimize), "frame_sum")
    # The null pointer and the narrow constants sit past 255 bytes below x29.
    assert re.search(r"^  str xzr, \[sp, #\d+\]$", body, re.M), body
    assert re.search(r"^  strb w\d+, \[sp, #\d+\]$", body, re.M), body
    assert re.search(r"^  strh w\d+, \[sp, #\d+\]$", body, re.M), body
    assert re.search(r"^  ldrb w\d+, \[sp, #\d+\]$", body, re.M), body
    assert re.search(r"^  ldrh w\d+, \[sp, #\d+\]$", body, re.M), body
    assert re.search(r"^  ldr x\d+, \[sp, #\d+\]$", body, re.M), body
    # No slot access goes through a computed x15 address any more.
    assert not re.search(r"^  (?:ldur|stur|ldr|str)[bh]? [wx]\w+, \[x15\]$", body, re.M), body


@pytest.mark.pcc_gate(probe=lambda: sys.platform == "darwin" and platform.machine() == "arm64")
def test_far_slot_program_runs(tmp_path):
    data = emit_owned_object(_program(), "arm64-apple-darwin")
    executable = tmp_path / "far-slots"
    executable.write_bytes(link_executable([data], entry="_main"))
    executable.chmod(0o755)
    ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
