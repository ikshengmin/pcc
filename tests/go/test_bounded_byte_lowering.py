"""Lower proven byte writes without granting a general Go slice carrier."""
import os

import pytest

from pcc.frontends.go import DEFAULT_TARGET
from pcc.frontends.go.managed_objects import FixedByteExtent, byte_backing_plan
from pcc.frontends.go.slice_proof import ByteAppendSummary, byte_append_summary
from pcc.ir.compat import ir
from scripts.go_std_closure import FIXTURE
from tests.go.owned_execution import Execution, compile_reference, run
from tests.go.test_execution import forbid_external_compiler


def test_capacity_and_word_width_reject_before_writing_ir():
    from pcc.frontends.go.slice_lowering import allocate_fixed_byte_span, append_fixed_byte_words

    module = ir.Module()
    function = ir.Function(module, ir.FunctionType(ir.VoidType(), ()), name="probe")
    builder = ir.IRBuilder(function.append_basic_block("entry"))
    span = allocate_fixed_byte_span(builder, FixedByteExtent(0, 4, byte_backing_plan()))
    summary = ByteAppendSummary((0, 8, 16, 24))
    unchanged = str(module)
    with pytest.raises(ValueError, match="capacity"):
        append_fixed_byte_words(builder, span, summary, (ir.Constant(ir.IntType(32), 1),) * 2)
    assert str(module) == unchanged
    with pytest.raises(ValueError, match="i32"):
        append_fixed_byte_words(builder, span, summary, (ir.Constant(ir.IntType(64), 1),))
    assert str(module) == unchanged
    other = ir.Function(module, ir.FunctionType(ir.VoidType(), ()), name="other")
    other_builder = ir.IRBuilder(other.append_basic_block("entry"))
    with pytest.raises(ValueError, match="function lifetime"):
        append_fixed_byte_words(other_builder, span, summary, (ir.Constant(ir.IntType(32), 1),))


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
def test_original_append_bytes_raw_native_result_matches_reference(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.x86_64_asm_driver import assemble_file
    from pcc.frontends.go.slice_lowering import allocate_fixed_byte_span, append_fixed_byte_words
    from pcc.frontends.go.syntax import GoParser

    original = (FIXTURE / "src/internal/cpu/cpu_x86.go").read_text()
    helper = original[original.index("func appendBytes("):]
    program = GoParser().parse(original, "cpu_x86.go", require_main=False)
    summary = byte_append_summary(program, "appendBytes")
    assert summary is not None
    module = ir.Module()
    module.triple = DEFAULT_TARGET
    byte, word = ir.IntType(8), ir.IntType(32)
    buffer = ir.ArrayType(byte, 48)
    function = ir.Function(module, ir.FunctionType(ir.VoidType(), (buffer.as_pointer(),) + (word,) * 4),
                           name="probe")
    builder = ir.IRBuilder(function.append_basic_block("entry"))
    span = allocate_fixed_byte_span(builder, FixedByteExtent(0, 48, byte_backing_plan()))
    backing = span.storage
    for length in (16, 32, 48):
        span = append_fixed_byte_words(builder, span, summary, tuple(function.args[1:]))
        assert span.storage is backing and span.extent.length == length
    builder.store(builder.load(span.storage), function.args[0])
    builder.ret_void()
    start = """.intel_syntax noprefix
.text
.globl _start
_start:
  sub rsp, 48
  mov rdi, rsp
  mov esi, 0x04030201
  mov edx, 0x88776655
  mov ecx, 0xddccbbaa
  mov r8d, 0xffeeddcc
  call probe
  mov eax, 1
  mov edi, 1
  mov rsi, rsp
  mov edx, 48
  syscall
  mov eax, 60
  xor edi, edi
  syscall
"""
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        body = parse_relocatable(emit_owned_object(str(module), DEFAULT_TARGET))
        image = link_static_executable([assemble_file(start), body], entry="_start")
    owned = tmp_path / "owned"
    owned.write_bytes(image)
    owned.chmod(0o700)
    source = "package main\nimport \"os\"\n" + helper + """
func main() {
  b:=make([]byte,0,48)
  for i:=0;i<3;i++ { b=appendBytes(b,0x04030201,0x88776655,0xddccbbaa,0xffeeddcc) }
  if n,e:=os.Stdout.Write(b); n!=48||e!=nil { panic("write") }
}
"""
    reference = compile_reference((("control.go", source),), tmp_path / "reference",
                                  compiler=os.environ["PCC_GO_REFERENCE"],
                                  cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    expect = bytes.fromhex("0102030455667788aabbccddccddeeff") * 3
    assert run(owned) == run(reference) == Execution(0, expect, b"")
