"""Operands of simple calls may stay in caller-saved registers.

A value whose last use is an operand of a direct, non-variadic call with
only scalar register arguments no longer has to live in x19-x28: the call's
argument setup reads every operand before writing any argument register
(``emit_scalar_register_arguments``, a parallel move that parks one source
of a cycle in x16). Register copies around calls were the largest class of
the self backend's excess executed instructions over clang (docs/status.md,
P0 exact ranking: "args before call", "into argument register" and "result
after call" were 48% of the register copies).

The programs swap, rotate and duplicate arguments that arrive in x0-x2, mix
in constants and values that cross a call, and run natively.
"""
import subprocess

import pytest

from pcc.backend import self_backend_aarch64_darwin_regalloc as regalloc
from pcc.backend.arm64_asm_driver import assemble_file
from pcc.backend.self_backend_aarch64_darwin import (
    emit_aarch64_darwin_asm,
    emit_aarch64_darwin_indexed_module,
    emit_aarch64_darwin_indexed_transport,
)
from pcc.backend.self_backend_parse import parse_self_backend_module


SOURCE = '''target triple = "arm64-apple-darwin"
define i64 @sub2(i64 %a, i64 %b) {
entry:
  %r = sub i64 %a, %b
  ret i64 %r
}
define i64 @mix3(i64 %a, i64 %b, i64 %c) {
entry:
  %t = mul i64 %a, 100
  %u = mul i64 %b, 10
  %v = add i64 %t, %u
  %r = add i64 %v, %c
  ret i64 %r
}
define i64 @swap(i64 %a, i64 %b) {
entry:
  %r = call i64 @sub2(i64 %b, i64 %a)
  ret i64 %r
}
define i64 @rotate(i64 %a, i64 %b, i64 %c) {
entry:
  %r = call i64 @mix3(i64 %b, i64 %c, i64 %a)
  ret i64 %r
}
define i64 @twice(i64 %a, i64 %b) {
entry:
  %r = call i64 @mix3(i64 %b, i64 %b, i64 %a)
  ret i64 %r
}
define i64 @constant(i64 %a, i64 %b) {
entry:
  %r = call i64 @mix3(i64 7, i64 %a, i64 %b)
  ret i64 %r
}
define i64 @survivor(i64 %a, i64 %b) {
entry:
  %x = call i64 @sub2(i64 %b, i64 %a)
  %y = call i64 @mix3(i64 %a, i64 %b, i64 %x)
  ret i64 %y
}
define i32 @main() {
entry:
  %s = call i64 @swap(i64 3, i64 10)
  %r = call i64 @rotate(i64 1, i64 2, i64 3)
  %t = call i64 @twice(i64 4, i64 5)
  %c = call i64 @constant(i64 8, i64 9)
  %v = call i64 @survivor(i64 2, i64 9)
  %ok1 = icmp eq i64 %s, 7
  %ok2 = icmp eq i64 %r, 231
  %ok3 = icmp eq i64 %t, 554
  %ok4 = icmp eq i64 %c, 789
  %ok5 = icmp eq i64 %v, 297
  %a1 = and i1 %ok1, %ok2
  %a2 = and i1 %a1, %ok3
  %a3 = and i1 %a2, %ok4
  %a4 = and i1 %a3, %ok5
  %status = select i1 %a4, i32 0, i32 45
  ret i32 %status
}
'''


def _body(assembly, name):
    lines = assembly.split("\n")
    start = lines.index("_" + name + ":")
    end = start + 1
    while end < len(lines) and not lines[end].startswith(("_", ".p2align", ".section")):
        end += 1
    return lines[start:end]


def test_simple_call_predicate_admits_scalar_direct_calls_only():
    from pcc.backend.self_backend_kernel import get_indexed_function_kernel
    module = parse_self_backend_module(SOURCE)
    function = next(fn for fn in module.functions if fn.name == "swap")
    kernel = get_indexed_function_kernel(function)
    assert regalloc.call_operands_may_use_caller_registers(kernel, 0)


def test_operands_ending_at_a_simple_call_keep_their_argument_registers():
    assembly = emit_aarch64_darwin_asm(SOURCE, optimize=True)
    swap = _body(assembly, "swap")
    # No callee-saved copy: the swap is one parallel move through x16.
    assert not any("x19" in line for line in swap), swap
    assert "  mov x16, x0" in swap or "  mov x16, x1" in swap, swap
    rotate = _body(assembly, "rotate")
    assert not any("x19" in line for line in rotate), rotate
    # A value used after the first call still crosses it in x19-x28.
    survivor = _body(assembly, "survivor")
    assert any("x19" in line for line in survivor), survivor


def test_native_bytes_equal_the_assembled_text():
    assembly = emit_aarch64_darwin_indexed_module(parse_self_backend_module(SOURCE), optimize=True)
    transport = emit_aarch64_darwin_indexed_transport(
        parse_self_backend_module(SOURCE), optimize=True, structured_instructions=True,
    )
    assert transport.assemble_sections() == assemble_file(assembly)


# owned Darwin arm64 execution
@pytest.mark.pcc_gate(platforms=("darwin",), machines=("arm64",))
@pytest.mark.parametrize("optimize", (False, True))
def test_call_operand_programs_execute(tmp_path, optimize):
    from pcc.backend.macho_exec import link_executable
    from pcc.backend.native_object import NativeObject
    transport = emit_aarch64_darwin_indexed_transport(
        parse_self_backend_module(SOURCE), optimize=optimize, structured_instructions=True,
    )
    sections, undefined = transport.assemble_sections()
    data = NativeObject.from_sections(sections, undefined=undefined).to_macho()
    output = tmp_path / "operands"
    output.write_bytes(link_executable([data], entry="_main"))
    output.chmod(0o755)
    ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=10)
    assert (ran.returncode, ran.stdout, ran.stderr) == (0, "", "")
