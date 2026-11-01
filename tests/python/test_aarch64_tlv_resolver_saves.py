"""Darwin TLV resolver calls keep only the registers live after them.

Forming a thread-local address inside another instruction calls the TLV
resolver, which clobbers x0, x16, x17, LR and NZCV (AArch64 Darwin TLS ABI;
LLVM's CSR_Darwin_AArch64_TLS was the reference). The emitter saves all five
around the call; the optimized path keeps a save only when a backward
liveness dataflow over the function's blocks finds a later read. The rules
are checked on hand-written functions, and a program exercising arguments,
call operands, flags, loops and a second thread's own TLV copy is emitted by
the production object path, linked by pcc's owned linker and executed.
"""

import platform
import subprocess
import sys

import pytest

from pcc.backend.macho_exec import link_executable
from pcc.backend.owned_object_emit import emit_owned_object
from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm
from pcc.backend.self_backend_aarch64_darwin_addr import _materialize_thread_local_address
from pcc.backend.self_backend_aarch64_darwin_tlv import (
    elide_dead_tlv_resolver_saves,
    tlv_resolver_lines,
)


_NATIVE = pytest.mark.pcc_gate(probe=lambda: sys.platform == "darwin" and platform.machine() == "arm64")
_ALL = 31


def _emitted(reg: str, symbol: str = "_tls") -> list[str]:
    return tlv_resolver_lines(symbol, reg, _ALL, emitted=True)


def _kept(reg: str, preserve: int, symbol: str = "_tls") -> list[str]:
    return tlv_resolver_lines(symbol, reg, preserve)


@pytest.mark.parametrize("reg", ["x0", "x1", "x9", "x16", "x17"])
def test_recognized_text_is_the_emitters_sequence(reg):
    assert _materialize_thread_local_address("_tls", reg) == _emitted(reg)


def _function(*body: str) -> list[str]:
    return ["_probe:", "  paciasp", "  stp x29, x30, [sp, #-16]!", "  mov x29, sp", *body]


_EPILOGUE = ("  ldp x29, x30, [sp], #16", "  autiasp", "  ret")


def test_dead_registers_lose_their_saves():
    lines = _function(*_emitted("x9"), "  ldr x1, [x9]", "  mov x0, x1", *_EPILOGUE)
    assert elide_dead_tlv_resolver_saves(lines) == _function(
        "  adrp x0, _tls@PAGE", "  add x0, x0, _tls@PAGEOFF", "  ldr x16, [x0]", "  blr x16",
        "  mov x9, x0", "  ldr x1, [x9]", "  mov x0, x1", *_EPILOGUE,
    )


def test_argument_in_x0_survives():
    lines = _function(*_emitted("x9"), "  ldr x1, [x9]", "  add x0, x1, x0", *_EPILOGUE)
    assert elide_dead_tlv_resolver_saves(lines) == _function(
        "  sub sp, sp, #16", "  str x0, [sp]",
        "  adrp x0, _tls@PAGE", "  add x0, x0, _tls@PAGEOFF", "  ldr x16, [x0]", "  blr x16",
        "  mov x9, x0", "  ldr x0, [sp]", "  add sp, sp, #16",
        "  ldr x1, [x9]", "  add x0, x1, x0", *_EPILOGUE,
    )


def test_call_operand_in_x0_survives():
    # x0 holds the first argument while the second is formed.
    lines = _function("  mov x0, x19", *_emitted("x1"), "  bl _callee", *_EPILOGUE)
    assert _kept("x1", 1) == [
        "  sub sp, sp, #16", "  str x0, [sp]",
        "  adrp x0, _tls@PAGE", "  add x0, x0, _tls@PAGEOFF", "  ldr x16, [x0]", "  blr x16",
        "  mov x1, x0", "  ldr x0, [sp]", "  add sp, sp, #16",
    ]
    assert elide_dead_tlv_resolver_saves(lines) == _function("  mov x0, x19", *_kept("x1", 1), "  bl _callee", *_EPILOGUE)


def test_flags_read_after_the_call_survive():
    lines = _function("  cmp x1, #0", *_emitted("x9"), "  b.eq L_probe_zero", "  mov x0, #1", *_EPILOGUE,
                      "L_probe_zero:", "  mov x0, #2", *_EPILOGUE)
    assert elide_dead_tlv_resolver_saves(lines) == _function(
        "  cmp x1, #0", *_kept("x9", 16), "  b.eq L_probe_zero", "  mov x0, #1", *_EPILOGUE,
        "L_probe_zero:", "  mov x0, #2", *_EPILOGUE,
    )
    assert "  mrs x17, nzcv" in _kept("x9", 16) and "  str x0, [sp]" not in _kept("x9", 16)


def test_frameless_return_keeps_lr():
    lines = ["_leaf:", *_emitted("x9"), "  ldr x0, [x9]", "  ret"]
    out = elide_dead_tlv_resolver_saves(lines)
    assert out == ["_leaf:", *_kept("x9", 8), "  ldr x0, [x9]", "  ret"]
    assert "  str x30, [sp]" in out


def test_scratch_value_across_the_call_survives():
    lines = _function("  mov x16, x2", *_emitted("x9"), "  str x16, [x9]", "  mov x0, #0", *_EPILOGUE)
    out = elide_dead_tlv_resolver_saves(lines)
    assert out == _function("  mov x16, x2", *_kept("x9", 2), "  str x16, [x9]", "  mov x0, #0", *_EPILOGUE)


def test_back_edge_reads_keep_their_register():
    lines = _function(
        "L_probe_head:", "  add x2, x17, #1", *_emitted("x9"), "  str x2, [x9]",
        "  cbnz x2, L_probe_head", "  mov x0, #0", *_EPILOGUE,
    )
    assert elide_dead_tlv_resolver_saves(lines) == _function(
        "L_probe_head:", "  add x2, x17, #1", *_kept("x9", 4), "  str x2, [x9]",
        "  cbnz x2, L_probe_head", "  mov x0, #0", *_EPILOGUE,
    )
    assert "  str x17, [sp]" in _kept("x9", 4)


@pytest.mark.parametrize("after", [
    ["  svc #0x80"],                       # unknown instruction: reads everything
    ["  b L_elsewhere"],                   # a label outside the function's code
    ["  br x2"],                           # indirect branch
    [".section __DATA,__data", ".quad 0"],  # code ends without a terminator
])
def test_unmodeled_continuations_keep_every_save(after):
    lines = _function(*_emitted("x9"), *after)
    assert elide_dead_tlv_resolver_saves(lines) == _function(*_kept("x9", _ALL), *after)
    assert _kept("x9", _ALL) == _emitted("x9")


def test_tail_call_keeps_argument_and_lr():
    lines = ["_tail:", *_emitted("x9"), "  b _other"]
    assert elide_dead_tlv_resolver_saves(lines) == ["_tail:", *_kept("x9", 1 | 8), "  b _other"]


def test_adjacent_calls_do_not_keep_each_other_alive():
    lines = _function(*_emitted("x9"), *_emitted("x10", "_other"), "  str x10, [x9]", "  mov x0, #0", *_EPILOGUE)
    assert elide_dead_tlv_resolver_saves(lines) == _function(
        *_kept("x9", 0), *_kept("x10", 0, "_other"), "  str x10, [x9]", "  mov x0, #0", *_EPILOGUE,
    )


_PROGRAM = '''target triple = "arm64-apple-darwin"
@tls_a = thread_local global i64 7, align 8
@tls_b = thread_local global i32 3, align 4

declare i32 @pthread_create(ptr, ptr, ptr, ptr)
declare i32 @pthread_join(ptr, ptr)

define i64 @add_tls(i64 %x) {
entry:
  %a = load i64, ptr @tls_a, align 8
  %s = add i64 %a, %x
  store i64 %s, ptr @tls_a, align 8
  ret i64 %s
}

define i64 @pair(i64 %x, ptr %p) {
entry:
  %v = load i64, ptr %p, align 8
  %r = add i64 %v, %x
  ret i64 %r
}

define i64 @call_with_tls(i64 %x) {
entry:
  %r = call i64 @pair(i64 %x, ptr @tls_a)
  ret i64 %r
}

define i64 @select_tls(i64 %x, i64 %y) {
entry:
  %c = icmp slt i64 %x, %y
  %b = load i32, ptr @tls_b, align 4
  %bw = sext i32 %b to i64
  %r = select i1 %c, i64 %bw, i64 %y
  ret i64 %r
}

define i64 @loop_tls(i64 %n) {
entry:
  br label %head
head:
  %i = phi i64 [0, %entry], [%next, %body]
  %acc = phi i64 [0, %entry], [%acc2, %body]
  %done = icmp sge i64 %i, %n
  br i1 %done, label %exit, label %body
body:
  %t = load i64, ptr @tls_a, align 8
  %acc2 = add i64 %acc, %t
  %t2 = add i64 %t, 1
  store i64 %t2, ptr @tls_a, align 8
  %next = add i64 %i, 1
  br label %head
exit:
  ret i64 %acc
}

define ptr @worker(ptr %out) {
entry:
  %initial = load i64, ptr @tls_a, align 8
  store i64 100, ptr @tls_a, align 8
  %sum = call i64 @loop_tls(i64 5)
  %both = mul i64 %initial, 1000
  %r = add i64 %both, %sum
  store i64 %r, ptr %out, align 8
  ret ptr null
}

define i32 @main() {
entry:
  %thread = alloca ptr, align 8
  %out = alloca i64, align 8
  %r1 = call i64 @add_tls(i64 5)
  %r2 = call i64 @call_with_tls(i64 30)
  %r3 = call i64 @select_tls(i64 1, i64 9)
  %r4 = call i64 @select_tls(i64 9, i64 1)
  %r5 = call i64 @loop_tls(i64 4)
  %created = call i32 @pthread_create(ptr %thread, ptr null, ptr @worker, ptr %out)
  %handle = load ptr, ptr %thread, align 8
  %joined = call i32 @pthread_join(ptr %handle, ptr null)
  %w = load i64, ptr %out, align 8
  %mine = load i64, ptr @tls_a, align 8
  %c1 = icmp eq i64 %r1, 12
  %c2 = icmp eq i64 %r2, 42
  %c3 = icmp eq i64 %r3, 3
  %c4 = icmp eq i64 %r4, 1
  %c5 = icmp eq i64 %r5, 54
  %c6 = icmp eq i64 %w, 7510
  %c7 = icmp eq i64 %mine, 16
  %c8 = icmp eq i32 %created, 0
  %c9 = icmp eq i32 %joined, 0
  %a1 = and i1 %c1, %c2
  %a2 = and i1 %a1, %c3
  %a3 = and i1 %a2, %c4
  %a4 = and i1 %a3, %c5
  %a5 = and i1 %a4, %c6
  %a6 = and i1 %a5, %c7
  %a7 = and i1 %a6, %c8
  %a8 = and i1 %a7, %c9
  br i1 %a8, label %good, label %bad
good:
  ret i32 0
bad:
  ret i32 3
}
'''


def _body(text: str, name: str) -> str:
    """Lines of one function, up to the next function label or section."""
    out = []
    for line in text.split("\n_" + name + ":\n", 1)[1].splitlines():
        if line.startswith(".section") or (line.endswith(":") and not line.startswith((" ", ".", "L_"))):
            break
        out.append(line)
    return "\n".join(out)


def test_production_text_keeps_only_live_saves():
    asm = emit_aarch64_darwin_asm(_PROGRAM, optimize=True)
    # The argument in x0 is live across the address formation for the load.
    assert _body(asm, "add_tls").count("blr x16") == 2
    assert "str x0, [sp]" in _body(asm, "add_tls").split("blr x16", 1)[0]
    # The first call operand is in x0 while the TLS operand is formed.
    pair_args = _body(asm, "call_with_tls").split("bl _pair", 1)[0]
    assert "str x0, [sp]" in pair_args and "ldr x0, [sp]" in pair_args
    # No function here reads NZCV after a resolver call.
    assert "mrs x17, nzcv" not in asm
    unoptimized = emit_aarch64_darwin_asm(_PROGRAM, optimize=False)
    assert unoptimized.count("blr x16") == asm.count("blr x16")
    assert unoptimized.count("mrs x17, nzcv") == asm.count("blr x16")


@_NATIVE
def test_tlv_program_runs(tmp_path):
    data = emit_owned_object(_PROGRAM, "arm64-apple-darwin")
    executable = tmp_path / "tlv-saves"
    executable.write_bytes(link_executable([data], entry="_main"))
    executable.chmod(0o755)
    ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
