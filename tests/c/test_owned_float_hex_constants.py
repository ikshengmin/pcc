"""LLVM float tokens are widened binary64 spellings, never raw float bits."""

import platform
import subprocess
import sys

import pytest

from pcc.backend.elf_x86_64 import link_static_executable
from pcc.backend.owned_elf_link import assemble
from pcc.backend.self_backend_ir import TypeDesc
from pcc.backend.self_backend_parse import aggregate_literal_to_bytes
from pcc.backend.self_backend_x86_64_linux import emit_x86_64_linux_asm
from pcc.backend.wide_float import encode_float_bits


_FLOAT_CONSTANTS = [
    pytest.param("0x0000000000000000", 0x00000000, id="positive-zero"),
    pytest.param("0x8000000000000000", 0x80000000, id="negative-zero"),
    pytest.param("0x3FF0000000000000", 0x3F800000, id="one"),
    pytest.param("0x3FF8000000000000", 0x3FC00000, id="one-and-a-half"),
    pytest.param("0x7FF0000000000000", 0x7F800000, id="positive-infinity"),
    pytest.param("0xFFF0000000000000", 0xFF800000, id="negative-infinity"),
    pytest.param("0x7FF8000000000000", 0x7FC00000, id="quiet-nan"),
    pytest.param("0xFFF8000000000000", 0xFFC00000, id="negative-quiet-nan"),
    pytest.param("0x7FF82468A0000000", 0x7FC12345, id="quiet-nan-payload"),
    pytest.param("0xFFF82468A0000000", 0xFFC12345, id="negative-nan-payload"),
    pytest.param("0x7FF42468A0000000", 0x7FA12345, id="signaling-nan-payload"),
    pytest.param("0xFFF42468A0000000", 0xFFA12345, id="negative-signaling-nan"),
    pytest.param("0x36A0000000000000", 0x00000001, id="smallest-subnormal"),
]


@pytest.mark.parametrize("token,bits", _FLOAT_CONSTANTS)
def test_float_hex_aggregate_literals_use_widened_binary64_tokens(token, bits):
    float_type = TypeDesc("fp", 32)
    vector_type = TypeDesc("array", count=2, elem=float_type)
    expected = bits.to_bytes(4, "little")
    assert encode_float_bits(token, 32) == bits
    assert aggregate_literal_to_bytes(float_type, token) == expected
    assert aggregate_literal_to_bytes(
        vector_type, f"<float {token}, float {token}>"
    ) == expected + expected


@pytest.mark.pcc_gate(
    probe=lambda: sys.platform.startswith("linux")
    and platform.machine() in ("x86_64", "amd64")
)
@pytest.mark.parametrize("token,bits", _FLOAT_CONSTANTS)
def test_owned_x86_float_hex_scalar_aggregate_and_vector_lane_execute(
    tmp_path, monkeypatch, token, bits
):
    target = "x86_64-unknown-linux-gnu"
    ir_text = f'''
target triple = "{target}"

@global_float = global float {token}
@global_vector = global <2 x float> <float {token}, float {token}>

define i32 @probe() {{
entry:
  %scalar = alloca float
  %aggregate = alloca <2 x float>
  %lane_slot = alloca <2 x float>
  store float {token}, ptr %scalar
  %loaded_aggregate = load <2 x float>, ptr @global_vector
  store <2 x float> %loaded_aggregate, ptr %aggregate
  %shuffled = shufflevector <2 x float> <float {token}, float {token}>, <2 x float> zeroinitializer, <2 x i32> zeroinitializer
  store <2 x float> %shuffled, ptr %lane_slot
  %lane_address = getelementptr i8, ptr %lane_slot, i64 4
  %scalar_bits = load i32, ptr %scalar
  %aggregate_bits = load i32, ptr %aggregate
  %lane_bits = load i32, ptr %lane_address
  %global_bits = load i32, ptr @global_float
  %scalar_ok = icmp eq i32 %scalar_bits, {bits}
  %aggregate_ok = icmp eq i32 %aggregate_bits, {bits}
  %lane_ok = icmp eq i32 %lane_bits, {bits}
  %global_ok = icmp eq i32 %global_bits, {bits}
  %first_pair = and i1 %scalar_ok, %aggregate_ok
  %second_pair = and i1 %lane_ok, %global_ok
  %all_ok = and i1 %first_pair, %second_pair
  %failed = xor i1 %all_ok, true
  %result = zext i1 %failed to i32
  ret i32 %result
}}
'''
    startup = '''.intel_syntax noprefix
.text
.globl _start
_start:
  call probe
  mov rdi, rax
  mov rax, 60
  syscall
'''

    def forbid_compiler_process(*args, **kwargs):
        raise AssertionError("float regression invoked an external compiler")

    with monkeypatch.context() as guard:
        guard.setattr(subprocess, "Popen", forbid_compiler_process)
        assembly = emit_x86_64_linux_asm(ir_text)
        image = link_static_executable(
            [assemble(assembly, target), assemble(startup, target)]
        )
    executable = tmp_path / "float_constants"
    executable.write_bytes(image)
    executable.chmod(0o755)
    result = subprocess.run(
        [str(executable)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
