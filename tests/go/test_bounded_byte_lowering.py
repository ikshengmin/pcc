"""Lower proven byte writes without granting a general Go slice carrier."""
import os
import struct

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


def test_fixed_byte_view_rejects_an_unrelated_failure_block_before_emission():
    from pcc.frontends.go.slice_lowering import allocate_fixed_byte_span, fixed_byte_view, read_fixed_byte

    module = ir.Module()
    same = ir.Function(module, ir.FunctionType(ir.VoidType(), ()), name="same")
    other = ir.Function(module, ir.FunctionType(ir.VoidType(), ()), name="other")
    builder = ir.IRBuilder(same.append_basic_block("entry"))
    outside = other.append_basic_block("failure")
    span = allocate_fixed_byte_span(builder, FixedByteExtent(0, 4, byte_backing_plan()))
    view = fixed_byte_view(builder, span)
    before = str(module)
    with pytest.raises(ValueError, match="function"):
        read_fixed_byte(builder, view, ir.Constant(ir.IntType(64), 0), outside)
    assert str(module) == before


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
def test_dynamic_fixed_byte_view_and_index_check_match_native_go(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.x86_64_asm_driver import assemble_file
    from pcc.frontends.go.slice_lowering import (
        allocate_fixed_byte_span, append_fixed_byte_words, fixed_byte_view, read_fixed_byte, slice_fixed_bytes,
    )

    module = ir.Module()
    module.triple = DEFAULT_TARGET
    i32, i64 = ir.IntType(32), ir.IntType(64)
    fn = ir.Function(module, ir.FunctionType(i64, (i64, i64, i64)), name="probe")
    builder = ir.IRBuilder(fn.append_basic_block("entry"))
    failure = fn.append_basic_block("bounds_failure")
    span = allocate_fixed_byte_span(builder, FixedByteExtent(0, 8, byte_backing_plan()))
    span = append_fixed_byte_words(builder, span, ByteAppendSummary((0, 8, 16, 24)),
                                   (ir.Constant(i32, 0x04030201), ir.Constant(i32, 0x08070605)))
    view = slice_fixed_bytes(builder, fixed_byte_view(builder, span), fn.args[0], fn.args[1], failure)
    observed = read_fixed_byte(builder, view, fn.args[2], failure)
    builder.ret(builder.zext(observed, i64))
    builder.position_at_end(failure)
    builder.ret(ir.Constant(i64, -1))
    cases = ((2, 7, 0), (5, 8, 2), (0, 8, 8), (-1, 4, 1), (3, 2, 0), (0, 9, 0), (0, 6, -1))
    start = """.intel_syntax noprefix
.text
.globl _start
_start:
  sub rsp, 64
"""
    for index, (low, high, item) in enumerate(cases):
        start += (f"  mov rdi, {low}\n  mov rsi, {high}\n  mov rdx, {item}\n"
                  f"  call probe\n  mov QWORD PTR [rsp + {index * 8}], rax\n")
    start += f"""  mov eax, 1
  mov edi, 1
  mov rsi, rsp
  mov edx, {len(cases) * 8}
  syscall
  mov eax, 60
  xor edi, edi
  syscall
"""
    with monkeypatch.context() as denied:
        forbid_external_compiler(denied)
        object_ = parse_relocatable(emit_owned_object(str(module), DEFAULT_TARGET))
        image = link_static_executable([assemble_file(start), object_], entry="_start")
    owned = tmp_path / "owned"
    owned.write_bytes(image)
    owned.chmod(0o700)
    values = ",".join(f"{{{low},{high},{item}}}" for low, high, item in cases)
    source = """package main
import ("os"; "encoding/binary")
func probe(low,high,item int)int64 {
  data:=[8]byte{1,2,3,4,5,6,7,8}
  if low<0 || high<low || high>len(data) { return -1 }
  view:=data[low:high]
  if item<0 || item>=len(view) { return -1 }
  return int64(view[item])
}
func main() {
  var raw [56]byte
  for i,t:=range [7][3]int{""" + values + """} {
    binary.LittleEndian.PutUint64(raw[i*8:],uint64(probe(t[0],t[1],t[2])))
  }
  if n,e:=os.Stdout.Write(raw[:]); n!=len(raw)||e!=nil { panic("write") }
}
"""
    reference = compile_reference((("control.go", source),), tmp_path / "reference",
                                  compiler=os.environ["PCC_GO_REFERENCE"],
                                  cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    assert run(owned) == run(reference) == Execution(0, struct.pack("<7q", 3, 8, -1, -1, -1, -1, -1), b"")


def test_prefix_scan_rejects_out_of_range_byte_before_emitting():
    from pcc.frontends.go.slice_lowering import allocate_fixed_byte_span, fixed_byte_view, scan_fixed_byte_prefix

    module = ir.Module()
    fn = ir.Function(module, ir.FunctionType(ir.VoidType(), ()), name="probe")
    builder = ir.IRBuilder(fn.append_basic_block("entry"))
    failure = fn.append_basic_block("failure")
    span = allocate_fixed_byte_span(builder, FixedByteExtent(0, 4, byte_backing_plan()))
    view = fixed_byte_view(builder, span)
    before = str(module)
    with pytest.raises(ValueError, match="byte"):
        scan_fixed_byte_prefix(builder, view, 256, failure, while_equal=True)
    assert str(module) == before


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
def test_original_trim_loops_match_owned_native_span_scan(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.x86_64_asm_driver import assemble_file
    from pcc.frontends.go.slice_lowering import (
        allocate_fixed_byte_span, append_fixed_byte_words, fixed_byte_view, read_fixed_byte,
        scan_fixed_byte_prefix, slice_fixed_bytes,
    )

    words = ((0x42412020, 0x45440043), (0x20202020, 0x20202020), (0x11223300, 0),
             (0x44434241, 0x48474645), (0x80202020, 0xFF00EE40))
    module = ir.Module()
    module.triple = DEFAULT_TARGET
    i32, i64 = ir.IntType(32), ir.IntType(64)
    fn = ir.Function(module, ir.FunctionType(i64, (i32, i32)), name="probe")
    builder = ir.IRBuilder(fn.append_basic_block("entry"))
    failure = fn.append_basic_block("bounds_failure")
    span = allocate_fixed_byte_span(builder, FixedByteExtent(0, 8, byte_backing_plan()))
    span = append_fixed_byte_words(builder, span, ByteAppendSummary((0, 8, 16, 24)), fn.args)
    original = fixed_byte_view(builder, span)
    spaces = scan_fixed_byte_prefix(builder, original, 32, failure, while_equal=True)
    tail = slice_fixed_bytes(builder, original, spaces, original.length, failure)
    size = scan_fixed_byte_prefix(builder, tail, 0, failure, while_equal=False)
    view = slice_fixed_bytes(builder, tail, ir.Constant(i64, 0), size, failure)
    # Observe the remaining bytes as a rolling scalar value, not an escaping
    # pointer/string. The same source control folds the original returned copy.
    enter = builder.block
    loop = fn.append_basic_block("observe")
    compare = fn.append_basic_block("observe_byte")
    done = fn.append_basic_block("observed")
    builder.branch(loop)
    builder.position_at_end(loop)
    index, value = builder.phi(i64), builder.phi(i64)
    index.add_incoming(ir.Constant(i64, 0), enter)
    value.add_incoming(view.length, enter)
    builder.cbranch(builder.icmp_unsigned("<", index, view.length), compare, done)
    builder.position_at_end(compare)
    observed = builder.zext(read_fixed_byte(builder, view, index, failure), i64)
    next_index = builder.add(index, ir.Constant(i64, 1))
    next_value = builder.add(builder.mul(value, ir.Constant(i64, 257)), observed)
    step = builder.block
    builder.branch(loop)
    index.add_incoming(next_index, step)
    value.add_incoming(next_value, step)
    builder.position_at_end(done)
    builder.ret(value)
    builder.position_at_end(failure)
    builder.ret(ir.Constant(i64, -1))

    start = """.intel_syntax noprefix
.text
.globl _start
_start:
  sub rsp, 48
"""
    for n, (a, b) in enumerate(words):
        start += f"  mov edi, {a}\n  mov esi, {b}\n  call probe\n  mov QWORD PTR [rsp + {n*8}], rax\n"
    start += """  mov eax, 1
  mov edi, 1
  mov rsi, rsp
  mov edx, 40
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
    raw = (FIXTURE / "src/internal/cpu/cpu_x86.go").read_text()
    trim = raw[raw.index("// Trim leading spaces."):raw.index("\n}\n\nfunc appendBytes(")]
    pairs = ",".join(f"{{{x},{y}}}" for x, y in words)
    source = """package main
import ("encoding/binary";"os")
func trim(data []byte)string {
""" + trim + """
}
func main(){
  var result [40]byte
  for i,pair:=range [5][2]uint32{""" + pairs + """} {
    var data [8]byte
    binary.LittleEndian.PutUint32(data[:4],pair[0]); binary.LittleEndian.PutUint32(data[4:],pair[1])
    s:=trim(data[:]); v:=uint64(len(s))
    for k:=0;k<len(s);k++ { v=v*257+uint64(s[k]) }
    binary.LittleEndian.PutUint64(result[i*8:],v)
  }
  if n,e:=os.Stdout.Write(result[:]); n!=40||e!=nil { panic("write") }
}
"""
    reference = compile_reference((("control.go", source),), tmp_path / "reference",
                                  compiler=os.environ["PCC_GO_REFERENCE"],
                                  cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    got, want = run(owned), run(reference)
    assert got == want and got.returncode == 0 and len(got.stdout) == 40 and got.stderr == b""


def test_copy_rejects_the_same_owner_before_emitting():
    from pcc.frontends.go.slice_lowering import allocate_fixed_byte_span, copy_fixed_byte_view, fixed_byte_view

    module = ir.Module()
    fn = ir.Function(module, ir.FunctionType(ir.VoidType(), ()), name="probe")
    builder = ir.IRBuilder(fn.append_basic_block("entry"))
    failure = fn.append_basic_block("failure")
    span = allocate_fixed_byte_span(builder, FixedByteExtent(0, 4, byte_backing_plan()))
    before = str(module)
    with pytest.raises(ValueError, match="distinct"):
        copy_fixed_byte_view(builder, fixed_byte_view(builder, span), span, failure)
    assert str(module) == before


@pytest.mark.integration
@pytest.mark.pcc_gate(env="PCC_GO_REFERENCE")
def test_copied_bytes_survive_source_overwrite_and_short_capacity_is_unwritten(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.x86_64_asm_driver import assemble_file
    from pcc.frontends.go.slice_lowering import (
        allocate_fixed_byte_span, append_fixed_byte_words, copy_fixed_byte_view, fixed_byte_view, slice_fixed_bytes,
    )

    module = ir.Module()
    module.triple = DEFAULT_TARGET
    i8, i32, i64 = ir.IntType(8), ir.IntType(32), ir.IntType(64)
    fn = ir.Function(module, ir.FunctionType(i64, (ir.ArrayType(i8, 3).as_pointer(), i64)), name="probe")
    builder = ir.IRBuilder(fn.append_basic_block("entry"))
    failure = fn.append_basic_block("bounds_failure")
    empty = allocate_fixed_byte_span(builder, FixedByteExtent(0, 8, byte_backing_plan()))
    output = allocate_fixed_byte_span(builder, FixedByteExtent(0, 3, byte_backing_plan()))
    summary = ByteAppendSummary((0, 8, 16, 24))
    span = append_fixed_byte_words(builder, empty, summary, (
        ir.Constant(i32, 0x42412020), ir.Constant(i32, 0x11110043),
    ))
    view = slice_fixed_bytes(builder, fixed_byte_view(builder, span), ir.Constant(i64, 2), fn.args[1], failure)
    copied = copy_fixed_byte_view(builder, view, output, failure)
    assert copied.span.storage is output.storage and copied.span.storage is not span.storage
    append_fixed_byte_words(builder, empty, summary, (ir.Constant(i32, 0),) * 2)
    builder.store(builder.load(output.storage), fn.args[0])
    builder.ret(copied.length)
    builder.position_at_end(failure)
    builder.store(builder.load(output.storage), fn.args[0])
    builder.ret(ir.Constant(i64, -1))
    start = """.intel_syntax noprefix
.text
.globl _start
_start:
  sub rsp, 32
  mov QWORD PTR [rsp + 8], 0
  mov QWORD PTR [rsp + 24], 0
  lea rdi, [rsp + 8]
  mov esi, 5
  call probe
  mov QWORD PTR [rsp], rax
  lea rdi, [rsp + 24]
  mov esi, 6
  call probe
  mov QWORD PTR [rsp + 16], rax
  mov eax, 1
  mov edi, 1
  mov rsi, rsp
  mov edx, 32
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
    source = """package main
import ("encoding/binary";"os")
func main(){
  var raw [32]byte
  for i,high:=range [2]int{5,6} {
    b:=[8]byte{' ',' ','A','B','C',0,17,17}
    if high-2>3 { binary.LittleEndian.PutUint64(raw[i*16:],^uint64(0)); continue }
    s:=string(b[2:high])
    for k:=range b { b[k]=0 }
    binary.LittleEndian.PutUint64(raw[i*16:],uint64(len(s)))
    copy(raw[i*16+8:],s)
  }
  if n,e:=os.Stdout.Write(raw[:]); n!=32||e!=nil { panic("write") }
}
"""
    reference = compile_reference((("control.go", source),), tmp_path / "reference",
                                  compiler=os.environ["PCC_GO_REFERENCE"],
                                  cache=os.environ["PCC_GO_REFERENCE_CACHE"])
    expected = struct.pack("<q", 3) + b"ABC" + bytes(5) + struct.pack("<q", -1) + bytes(8)
    assert run(owned) == run(reference) == Execution(0, expected, b"")
