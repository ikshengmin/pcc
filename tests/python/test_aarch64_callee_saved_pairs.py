"""AAPCS64 6.1.1/6.4.5: preserve registers, aligned SP and frame records."""

import platform
import struct
import subprocess
from types import SimpleNamespace

import pytest

from pcc.backend.self_backend_aarch64_darwin_regalloc import (
    callee_saved_area_size,
    emit_callee_saved_frame,
    emit_callee_saved_loads,
    emit_callee_saved_stores,
)
from pcc.backend.self_backend_aarch64_darwin_terminators import (
    emit_epilogue,
    emit_tail_epilogue,
)
from pcc.backend.self_backend_target_passes import pair_adjacent_aarch64_64bit_memory_ops
from tests.owned_c_execution import compile_and_run_owned_c


_FRAMES = [(count, total) for count in (1, 2, 5, 10)
           for total in (16, 64, 496, 512, 528, 4096)
           if total >= (count * 8 + 15) // 16 * 16]


def _frame(count, total, *, extra=0, dynamic=False):
    function = SimpleNamespace(
        aarch64_callee_saved=list(range(19, 19 + count)),
        platform_frame_extra=extra,
        aarch64_dynamic_stack=dynamic,
        aarch64_frameless=False,
    )
    function.frame_size = total - callee_saved_area_size(function) - extra
    assert function.frame_size >= 0
    return function


@pytest.mark.parametrize("count,total", _FRAMES)
@pytest.mark.parametrize("load", (False, True))
def test_frame_writeback_encodes_boundaries_and_keeps_capture_order(count, total, load):
    from pcc.backend.arm64_encode import assemble_text
    from pcc.backend import self_backend_aarch64_darwin_mem as memory

    function = _frame(count, total)
    lines = emit_callee_saved_frame(function, load)
    writeback = total <= (504 if load else 512)
    pair = lines[-1] if load else lines[0]
    second = "xzr" if count == 1 else "x20"
    if writeback:
        expected = (f"  ldp x19, {second}, [sp], #{total}" if load
                    else f"  stp x19, {second}, [sp, #-{total}]!")
        assert pair == expected
    else:
        assert not any(line.startswith(("  ldp ", "  stp ")) for line in lines)
    # Compare the direct structured stream against independently parsed text.
    # Capturing the writeback word before the remaining restores is incorrect
    # even if the returned placeholder list appears in the intended order.
    expected_code = assemble_text("\n".join(lines)).code
    memory.begin_direct_instruction_capture()
    try:
        captured_lines = emit_callee_saved_frame(function, load)
        records = memory.borrow_direct_instruction_records()
        words = [records.get_unchecked(index) for index in range(0, len(records), 4)]
        assert len(captured_lines) == len(words)
        assert struct.pack("<" + "I" * len(words), *words) == expected_code
    finally:
        memory.end_direct_instruction_capture()


@pytest.mark.parametrize("count,extra,dynamic", [
    (0, 0, False), (5, 192, False), (5, 0, True),
])
def test_special_frames_keep_separate_adjustments(count, extra, dynamic):
    from pcc.backend.self_backend_aarch64_darwin_regs import emit_stack_adjust

    function = _frame(count, 256, extra=extra, dynamic=dynamic)
    assert emit_callee_saved_frame(function, False) == (
        emit_stack_adjust(-256) + emit_callee_saved_stores(function)
    )
    assert emit_callee_saved_frame(function, True) == (
        emit_callee_saved_loads(function) + emit_stack_adjust(256)
    )


@pytest.mark.skipif(platform.system() != "Darwin" or platform.machine() != "arm64",
                    reason="native Darwin arm64 frame execution")
def test_frame_boundaries_restore_all_registers_on_return_and_tail_call(tmp_path, monkeypatch):
    from pcc.backend.macho_exec import link_executable
    from pcc.backend.native_object import decode_native_object
    from pcc.backend.target_objects import encode_assembly_object

    # The caller's independently generated saves protect the process ABI;
    # each callee overwrites every register that its real emitter saves.
    monkeypatch.delenv("PCC_SELF_BRANCH_PROTECTION", raising=False)
    lines = [".section __TEXT,__text,regular,pure_instructions", ".globl _main", "_main:",
             "  stp x29, x30, [sp, #-96]!", "  mov x29, sp"]
    for first in range(19, 29, 2):
        lines.append(f"  stp x{first}, x{first + 1}, [sp, #{16 + (first - 19) * 8}]")
    for register in range(19, 29):
        lines.append(f"  mov x{register}, #{1000 + register}")
    callees = []
    for tail in (False, True):
        for count, total in _FRAMES:
            name = f"_frame_{count}_{total}_{int(tail)}"
            function = _frame(count, total)
            lines.extend([f"  bl {name}", "  mov x9, sp", "  cmp x9, x29",
                          "  b.ne L_failed", "  cmp x0, #42", "  b.ne L_failed"])
            for register in range(19, 29):
                lines.extend([f"  cmp x{register}, #{1000 + register}", "  b.ne L_failed"])
            callees.extend([name + ":", "  paciasp",
                            "  stp x29, x30, [sp, #-16]!", "  mov x29, sp"])
            callees.extend(emit_callee_saved_frame(function, False))
            for register in function.aarch64_callee_saved:
                callees.append(f"  mov x{register}, #7")
            # Write the final local slot when present: it must not alias the
            # callee-save pair or either word of the caller's frame record.
            if function.frame_size:
                callees.append("  stur xzr, [x29, #-8]")
            callees.append("  mov x0, #42")
            callees.extend(emit_tail_epilogue(function, "_tail_target") if tail
                           else emit_epilogue(function))
    lines.extend(["  mov x0, #0", "  b L_done", "L_failed:",
                  "  mov x0, #1", "L_done:"])
    for first in range(19, 29, 2):
        lines.append(f"  ldp x{first}, x{first + 1}, [sp, #{16 + (first - 19) * 8}]")
    lines.extend(["  ldp x29, x30, [sp], #96", "  ret", *callees,
                  "_tail_target:", "  bti c", "  mov x0, #42", "  ret"])
    obj = decode_native_object(encode_assembly_object("\n".join(lines), "arm64-apple-darwin"))
    output = tmp_path / "frame-boundaries"
    output.write_bytes(link_executable([obj]))
    output.chmod(0o755)
    result = subprocess.run([str(output)], capture_output=True, text=True, timeout=15)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


@pytest.mark.parametrize("extra", [0, 192, 504, 512])
@pytest.mark.parametrize("count", [0, 1, 2, 5, 10])
def test_target_pairing_preserves_every_callee_slot(extra, count):
    function = SimpleNamespace(
        aarch64_callee_saved=list(range(19, 19 + count)),
        platform_frame_extra=extra,
    )
    for load, emit in [(False, emit_callee_saved_stores), (True, emit_callee_saved_loads)]:
        lines = pair_adjacent_aarch64_64bit_memory_ops(emit(function))
        offset = extra
        expected_register = 19
        for line in lines:
            address = "[sp]" if offset == 0 else f"[sp, #{offset}]"
            if line.startswith(("  ldp ", "  stp ")):
                mnemonic = "ldp" if load else "stp"
                assert line == (f"  {mnemonic} x{expected_register}, "
                                f"x{expected_register + 1}, {address}")
                expected_register += 2
                offset += 16
            else:
                mnemonic = "ldr" if load else "str"
                assert line == f"  {mnemonic} x{expected_register}, {address}"
                expected_register += 1
                offset += 8
        assert expected_register == 19 + count
        paired = count // 2 if extra <= 192 else int(extra == 504 and count >= 2)
        assert len(lines) == count - paired


@pytest.mark.skipif(platform.machine().lower() not in ("arm64", "aarch64"),
                    reason="executes AArch64 callee saves")
def test_paired_saves_execute_recursive_and_register_pressure_calls():
    result = compile_and_run_owned_c(r'''
        #include <stdio.h>
        long mix(long x) { return x * 3 + 7; }
        long fib(long n) {
            if (n < 2) return n;
            return fib(n - 1) + fib(n - 2);
        }
        long pressure(long a, long b, long c, long d, long e, long f,
                      long g, long h) {
            long first = mix(a + b);
            long second = mix(c + d);
            return first + second + mix(e + f) + mix(g + h)
                   + a + b + c + d + e + f + g + h;
        }
        int main(void) {
            long total = 0;
            for (long i = 1; i <= 100; ++i) {
                long actual = pressure(i, i+1, i+2, i+3, i+4, i+5, i+6, i+7);
                if (actual != 32 * i + 140) return 1;
                total += actual;
            }
            if (fib(12) != 144 || total != 175600) return 2;
            puts("PAIRED_CALLEE_SAVES_OK");
            return 0;
        }
    ''')
    assert (result.returncode, result.stdout, result.stderr) == (
        0, "PAIRED_CALLEE_SAVES_OK\n", "",
    )
