"""Existing target pairing preserves callee slots and native execution."""

import platform
from types import SimpleNamespace

import pytest

from pcc.backend.self_backend_aarch64_darwin_regalloc import (
    emit_callee_saved_loads,
    emit_callee_saved_stores,
)
from pcc.backend.self_backend_target_passes import pair_adjacent_aarch64_64bit_memory_ops
from tests.owned_c_execution import compile_and_run_owned_c


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
